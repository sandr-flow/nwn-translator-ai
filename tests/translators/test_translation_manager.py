"""TranslationManager: requests, script gating, fallbacks, statistics and log events."""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.ai_providers.batch_payload import build_batch_payload, batch_payload_chars
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.extractors.creature_extractor import CreatureExtractor
from nwn_translator.extractors.item_extractor import ItemExtractor
from nwn_translator.glossary import Glossary, terminology_block
from nwn_translator.prompts.token_retry import PRESERVE_INLINE_MARKUP, PRESERVE_PLACEHOLDERS
from nwn_translator.translators.model_calls import CallLimits
from nwn_translator.translators.token_handler import sanitize_text
from nwn_translator.translators.translation_manager import (
    TranslationManager,
    unescape_literal_newlines,
)
from nwn_translator.translators.work_plan import BatchLimits
from tests.support.fakes import (
    RecordingWriter,
    failing_batch,
    gate_answering,
    make_config,
    translation_provider,
)
from tests.support.ncs import consts, extract_script, retn

LONG_DESCRIPTION = "A remarkably long placeable description sentence. " * 22
SHUDDER = "<StartHighlight>[Shudder.]</Start>"


def _item(text: str, item_id: str = "i", location: Optional[str] = None, context=None, **meta):
    return TranslatableItem(text, context, item_id, location, meta)


def _ncs(
    text, *, item_id="script:off_20", gate=False, confidence="high", hint="SpeakString", offset=0x20
):
    return TranslatableItem(
        text,
        f"NCS string; hint={hint}",
        item_id,
        "script.ncs",
        {
            "type": "ncs_string",
            "offset": offset,
            "confidence": confidence,
            "needs_llm_gate": gate,
            "ncs_hint": hint,
        },
    )


def _run(
    provider,
    *items,
    source="test.uti",
    glossary=None,
    progress=None,
    batch=None,
    calls=None,
    **config,
):
    """Translate *items* with a fresh manager; return the result and the manager."""
    manager = TranslationManager(make_config(**config), provider, glossary)
    if batch is not None:
        manager.batch_limits = batch
    if calls is not None:
        manager.call_limits = calls
    content = ExtractedContent("combined", list(items), Path(source))
    return manager.translate_content(content, item_progress=progress), manager


def _answers(items, answers: Dict[str, str]) -> Dict[Any, str]:
    return {item.key: answers[item.text] for item in items if item.text in answers}


def _sizes(mock) -> list:
    return [len(call.kwargs["items"]) for call in mock.call_args_list]


def _sent(mock) -> list:
    return [item.original for call in mock.call_args_list for item in call.kwargs["items"]]


def _samples(manager) -> list:
    return [
        (s["reason"], s.get("error"))
        for s in manager.get_statistics()["ncs_diagnostics"]["samples"]
    ]


def _ncs_stats(manager, *names) -> tuple:
    stats = manager.get_statistics()["ncs_diagnostics"]
    return tuple(stats[name] for name in names)


def _single(answer=None, *, slow_calls=(), delay=1.0, fail=None):
    """A ``translate_async`` answering ``answer(text)`` (default: ``TR:text``).

    Calls whose 1-based number is in *slow_calls* first sleep *delay* seconds;
    *fail* (an exception or a result) replaces the answer of every later call.
    """
    count = 0

    async def translate(text, source_lang, target_lang, **kwargs):
        nonlocal count
        count += 1
        if count in slow_calls:
            await asyncio.sleep(delay)
        if fail is not None and count > 1:
            if isinstance(fail, Exception):
                raise fail
            return fail
        return TranslationResult(translated=(answer or (lambda t: f"TR:{t}"))(text), original=text)

    return AsyncMock(side_effect=translate)


class _Progress:
    """Collects the file name of every progress bump."""

    def __init__(self) -> None:
        self.files: list = []

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        self.files.append(filename)


# ---------------------------------------------------------------------------
# Requests and results
# ---------------------------------------------------------------------------


def test_short_untyped_strings_share_one_batch_request():
    items = [
        _item("Hello!", "dlg:entry:0", context="NPC line"),
        _item("Who are you?", "dlg:entry:1", context="NPC line"),
        _item("Just passing.", "dlg:reply:0", context="Player reply"),
        _item("Hello <FirstName>!", "t:2"),  # tokens alone do not make a passthrough
    ]
    answers = {"Hello!": "Привет!", "Who are you?": "Кто ты?", "Just passing.": "Просто мимо."}
    provider = translation_provider(answers)

    result, _manager = _run(provider, *items, source="test.dlg")

    assert {k: v for k, v in result.items() if k[1] != "t:2"} == _answers(items, answers)
    assert provider.translate_batch_async.call_count == 1
    provider.translate_async.assert_not_called()
    assert _run(provider)[0] == {}


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_text_keeps_the_context_and_resource_of_each_occurrence(reverse):
    items = [
        TranslatableItem("Commoner", "Female", "same_title", "woman.utc"),
        TranslatableItem("Commoner", "Male", "same_title", "man.utc"),
    ]
    if reverse:
        items.reverse()
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [
            TranslationResult(
                original=i.original,
                translated="Простолюдинка" if i.context == "Female" else "Простолюдин",
            )
            for i in items
        ]
    )

    result, _manager = _run(provider, *items, source="module", quiet=True)

    assert result == {
        ("woman.utc", "same_title"): "Простолюдинка",
        ("man.utc", "same_title"): "Простолюдин",
    }
    assert sum(_sizes(provider.translate_batch_async)) == 2


def test_failure_is_scoped_to_one_occurrence_of_equal_text():
    female = TranslatableItem("Commoner", "Female", "title", "female.utc")
    male = TranslatableItem("Commoner", "Male", "title", "male.utc")
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [
            TranslationResult(
                original=i.original,
                translated="Woman" if i.context == "Female" else "",
                success=i.context == "Female",
                error="rejected",
            )
            for i in items
        ]
    )
    provider.translate_async = AsyncMock(
        return_value=TranslationResult("", "Commoner", success=False, error="rejected")
    )

    result, manager = _run(provider, female, male, source="module")

    assert result == {female.key: "Woman"}
    assert manager.failed_items == {male.key}


def test_equal_requests_share_one_answer_that_fans_out_with_a_reuse_trace():
    guards = [_item("Guard", "g", f"{name}.utc") for name in ("a", "b", "c")]
    names = [_item("Guard", f"g:{i}", type="creature_first_name") for i in range(3)]
    names.append(_item("Captain", "c:0", type="creature_first_name"))
    writer = RecordingWriter()
    provider = translation_provider({"Guard": "Страж", "Captain": "Капитан"})

    result, manager = _run(provider, *guards, translation_log_writer=writer)

    assert result == {item.key: "Страж" for item in guards}
    assert writer.events("translation_reuse") == [
        {"event": "translation_reuse", "occurrence": item.key, "representative": guards[0].key}
        for item in guards[1:]
    ]
    assert manager.get_statistics()["items_translated"] == 1

    provider = translation_provider({"Guard": "Страж", "Captain": "Капитан"})
    result, _manager = _run(provider, *names, source="guards.utc")
    assert provider.translate_batch_async.call_count == 1
    assert sorted(_sent(provider.translate_batch_async)) == ["Captain", "Guard"]
    assert result == _answers(names, {"Guard": "Страж", "Captain": "Капитан"})


def test_failed_representative_marks_its_duplicates_failed():
    items = [_item("Boom", "x", f"{name}.uti") for name in ("a", "b")]
    fail = TranslationResult(translated="", original="Boom", success=False, error="API error")
    provider = Mock()
    provider.translate_async = AsyncMock(return_value=fail)
    provider.translate_batch_async = AsyncMock(return_value=[fail])

    result, manager = _run(provider, *items)

    assert result == {}
    assert manager.failed_items == {item.key for item in items}
    assert manager.get_statistics()["errors"] == ["Translation failed for x: API error"]


def test_statistics_count_accepted_and_failed_requests():
    _result, manager = _run(translation_provider({"Hello": "Привет"}), _item("Hello", "x:0"))
    stats = manager.get_statistics()
    assert (stats["items_translated"], stats["total_errors"]) == (1, 0)

    fail = TranslationResult(translated="", original="Boom", success=False, error="API error")
    item = _item("Boom", "x:0")
    result, manager = _run(_single_only(AsyncMock(return_value=fail)), item)
    assert result == {}
    assert manager.get_statistics()["total_errors"] == 1
    assert item.key in manager.failed_items


@pytest.mark.parametrize(
    "text",
    [
        "Sword of Fire",
        "The ancient seal on the northern gate begins to crack and crumble as you approach.",
    ],
)
def test_empty_answer_is_a_failure(text):
    empty = TranslationResult(translated="", original=text, success=True)
    provider = translation_provider()
    provider.translate_async = AsyncMock(return_value=empty)
    provider.translate_batch_async = AsyncMock(return_value=[empty])
    item = _item(text, "x:0")

    result, manager = _run(provider, item)

    assert result == {}
    assert item.key in manager.failed_items
    assert manager.get_statistics()["total_errors"] >= 1


@pytest.mark.parametrize(
    "text",
    [
        "<FirstName>",
        "... - !",
        "<cяяя><cяя ><cя я><c яя>" "<cя  ><c я ><c  я>",  # colour markers only
    ],
)
def test_text_without_words_skips_the_model(text):
    provider = translation_provider()
    item = _item(text, "t:0")
    result, _manager = _run(provider, item)
    assert result == {item.key: text}
    provider.translate_async.assert_not_called()
    provider.translate_batch_async.assert_not_called()


def test_passthrough_that_does_not_survive_acceptance_is_a_failure():
    # A lone combining mark has nothing to translate; normalization drops it.
    item = _item("́", "mark", "a.uti")
    provider = translation_provider()

    result, manager = _run(provider, item)

    assert result == {}
    assert manager.failed_items == {item.key}
    assert manager.get_statistics()["errors"] == [
        "Translation failed for mark: text without translatable content was rejected"
    ]
    provider.translate_async.assert_not_called()
    provider.translate_batch_async.assert_not_called()


def test_literal_newlines_of_an_answer_become_line_breaks_when_the_source_has_them():
    assert unescape_literal_newlines("Mine\nStaff only", "Шахта\\nТолько") == "Шахта\nТолько"
    assert unescape_literal_newlines("No break", "a\\nb") == "a\\nb"


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def test_profiles_pack_separately_and_oversized_text_goes_alone():
    medium_text = (
        "The sofa seems warm and inviting, perfect for a short nap by the fire "
        "after a long day of adventuring in the dungeon."
    )
    oversized_text = "An oversized description that exceeds the batch text budget. " * 120
    items = [
        _item("Guard", "vs", type="creature_first_name"),
        _item("Sword of the Ancient Flames", "s", type="item_name"),
        _item(
            medium_text,
            "m",
            context="Description of placeable 'Couch'",
            type="placeable_description",
        ),
        _item(LONG_DESCRIPTION, "l", type="placeable_description"),
        _item(oversized_text, "o", type="placeable_description"),
    ]
    answers = {
        "Guard": "Страж",
        "Sword of the Ancient Flames": "Меч Древнего Пламени",
        medium_text: "Диван выглядит уютным.",
        LONG_DESCRIPTION: "Длинное описание.",
        oversized_text: "Огромное описание.",
    }
    provider = translation_provider(answers)

    result, _manager = _run(provider, *items, source="x.git")

    assert result == _answers(items, answers)
    assert provider.translate_async.call_count == 1  # only the oversized text
    calls = provider.translate_batch_async.call_args_list
    assert {
        c.kwargs["content_profile"]: [i.original for i in c.kwargs["items"]] for c in calls
    } == {
        "short_label": ["Guard", "Sword of the Ancient Flames"],
        "default": [medium_text, LONG_DESCRIPTION],
    }
    sent = [i for c in calls for i in c.kwargs["items"] if i.original == medium_text]
    assert sent[0].context == "Description of placeable 'Couch'"


def test_failed_batch_falls_back_to_single_requests():
    text = "The sofa seems warm and inviting, perfect for a short nap."
    provider = translation_provider({text: "Диван выглядит уютным."})
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch("boom"))
    item = _item(text, "m", type="placeable_description")

    result, _manager = _run(provider, item, source="x.utp")

    assert result == {item.key: "Диван выглядит уютным."}
    assert (provider.translate_batch_async.call_count, provider.translate_async.call_count) == (
        1,
        1,
    )


def test_batches_respect_the_text_budget():
    texts = [
        f"A fairly long unique description number {i} that easily clears the "
        "short threshold and lands in the medium tier of the batch splitter."
        for i in range(4)
    ]
    provider = translation_provider({t: f"Перевод {i}" for i, t in enumerate(texts)})
    items = [_item(t, f"m{i}", type="placeable_description") for i, t in enumerate(texts)]

    result, _manager = _run(provider, *items, batch=BatchLimits(text_chars=300))

    assert result == {item.key: f"Перевод {i}" for i, item in enumerate(items)}
    # About 131 characters per item with a 300-character budget: two per batch.
    assert _sizes(provider.translate_batch_async) == [2, 2]


def test_label_batches_fill_up_to_the_item_cap_whatever_the_length():
    cap = BatchLimits().max_items
    labels = [_item(f"Guard{i}", f"g:{i}", type="creature_first_name") for i in range(cap + 1)]
    provider = translation_provider()
    _run(provider, *labels, source="guards.utc")
    assert sorted(_sizes(provider.translate_batch_async)) == [1, cap]

    mixed = [_item(f"W{i:02d}", f"s:{i}", type="creature_first_name") for i in range(21)]
    mixed += [
        _item(f"Quite Long Proper Name {i:02d}", f"l:{i}", type="creature_first_name")
        for i in range(16)
    ]
    provider = translation_provider()
    result, _manager = _run(provider, *mixed, source="mix.utc")
    assert _sizes(provider.translate_batch_async) == [37]
    assert len(result) == len(mixed)


def test_structural_fields_share_one_request_despite_different_lengths():
    loc = lambda text: {"StrRef": -1, "Value": text}  # noqa: E731
    extracted = ItemExtractor().extract(
        Path("sword.uti"),
        {
            "LocalizedName": loc("Silver Sword"),
            "Description": loc("A fine blade. " * 100),
            "DescIdentified": loc("An enchanted silver blade."),
        },
    )
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [TranslationResult(original=i.original, translated=f"TR:{i.original}") for i in items]
        if len(build_batch_payload(items)["groups"]) == 1
        else []
    )

    result = TranslationManager(make_config(quiet=True), provider).translate_content(extracted)

    assert result == {i.key: f"TR:{i.text}" for i in extracted.items}
    provider.translate_batch_async.assert_called_once()
    provider.translate_async.assert_not_called()


def test_name_fields_stay_together_across_length_tiers():
    content = CreatureExtractor().extract(
        Path("jade.utc"),
        {
            "Tag": "jade",
            "Gender": 1,
            "Race": 1,
            "FirstName": {"Value": "Jade"},
            "LastName": {"Value": "Falcon with a deliberately long family name"},
        },
    )
    provider = translation_provider()
    glossary = Glossary({"Jade Falcon with a deliberately long family name": "Name"})

    result = TranslationManager(make_config(quiet=True), provider, glossary).translate_content(
        content
    )

    assert len(result) == 2
    provider.translate_batch_async.assert_called_once()
    sent = provider.translate_batch_async.call_args.kwargs["items"]
    assert {item.metadata["name_field"] for item in sent} == {"FirstName", "LastName"}
    assert all("Female" in item.context for item in sent)


def test_numbered_labels_and_prefix_names_reach_the_provider_intact():
    texts = ["FDwarf6", "FDwarf7", "Shadow", "Shadow Lord"]
    items = [
        _item(text, str(i), "area.git", "Visible character name") for i, text in enumerate(texts)
    ]
    provider = translation_provider({t: f"TR:{t}" for t in texts})

    result, _manager = _run(
        provider, *items, glossary=Glossary({"Shadow Lord": "Повелитель теней"})
    )

    assert sorted(_sent(provider.translate_batch_async)) == sorted(texts)
    assert result == {item.key: "TR:" + item.text for item in items}


def test_glossary_budget_splits_requests_without_dropping_terms():
    glossary = Glossary({f"Entity {i}": "Long canonical form " * 8 for i in range(8)})
    items = [_item(name, str(i), "items.git", "item") for i, name in enumerate(glossary.entries)]
    provider = translation_provider()

    result, _manager = _run(
        provider, *items, glossary=glossary, batch=BatchLimits(glossary_chars=600)
    )

    assert len(result) == len(items)
    assert provider.translate_batch_async.call_count > 1
    for call in provider.translate_batch_async.call_args_list:
        for item in call.kwargs["items"]:
            assert item.original in call.kwargs["glossary_block"]


def test_partial_group_answer_does_not_shift_the_next_group():
    items = [
        TranslatableItem(text, "", text, file, {"translation_group": "root"})
        for file, text in [("a.uti", "First"), ("a.uti", "Missing"), ("b.uti", "Second")]
    ]
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [TranslationResult(original=items[0].original, translated="TR:" + items[0].original)]
    )
    provider.translate_async = AsyncMock(
        return_value=TranslationResult("", "Missing", success=False, error="test failure")
    )

    result, manager = _run(provider, *items, batch=BatchLimits(max_items=2))

    assert result == {items[0].key: "TR:First", items[2].key: "TR:Second"}
    assert items[1].key in manager.failed_items


@pytest.mark.parametrize("successful_size", [20, 5])
def test_bulk_failure_is_split_recursively_without_single_requests(successful_size):
    items = [
        TranslatableItem(
            f"Name {i}",
            "",
            str(i),
            "area.git",
            {"type": "creature_first_name", "translation_group": f"creature[{i}]"},
        )
        for i in range(40)
    ]
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [
            TranslationResult(
                original=i.original,
                translated="TR:" + i.original if len(items) <= successful_size else "",
                success=len(items) <= successful_size,
                error="Malformed batch response",
            )
            for i in items
        ]
    )

    result, _manager = _run(provider, *items)

    sizes = _sizes(provider.translate_batch_async)
    assert sizes[:2] == [40, 20]
    assert sizes.count(successful_size) == 40 // successful_size
    assert min(sizes) == successful_size
    provider.translate_async.assert_not_called()
    assert result == {i.key: "TR:" + i.text for i in items}


def test_recursive_recovery_retries_only_the_failed_positions():
    items = [TranslatableItem(f"Name {i}", "context", str(i), "a.utc") for i in range(6)]
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [
            TranslationResult(
                original=i.original,
                translated="TR:" + i.original,
                success=len(items) <= 2 or i.original in {"Name 0", "Name 5"},
                error="Missing result",
            )
            for i in items
        ]
    )

    result, _manager = _run(provider, *items)

    sent = [
        [i.original for i in c.kwargs["items"]]
        for c in provider.translate_batch_async.call_args_list
    ]
    assert sent == [[i.text for i in items], ["Name 1", "Name 2"], ["Name 3", "Name 4"]]
    assert result == {i.key: "TR:" + i.text for i in items}
    provider.translate_async.assert_not_called()


# ---------------------------------------------------------------------------
# Timeouts and failures of single requests
# ---------------------------------------------------------------------------


def _single_only(translate_async) -> Mock:
    """A provider without a batch endpoint: every batch fails over to single requests."""
    provider = Mock()
    provider.translate_async = translate_async
    provider.close_async_client = AsyncMock(return_value=None)
    return provider


def test_timed_out_single_request_is_retried_once():
    provider = _single_only(_single(lambda t: "Длинное описание.", slow_calls={1}, delay=0.5))
    item = _item(LONG_DESCRIPTION, "l", type="placeable_description")

    result, manager = _run(provider, item, calls=CallLimits(item_timeout=0.05))

    assert result == {item.key: "Длинное описание."}
    assert provider.translate_async.call_count == 2
    assert manager.stats["errors"] == []

    provider.translate_async = _single(slow_calls={1, 2}, delay=0.5)
    result, manager = _run(provider, item, calls=CallLimits(item_timeout=0.05))
    assert result == {}
    assert len(manager.stats["errors"]) == 1
    assert "retry timed out" in manager.stats["errors"][0]


def test_hanging_single_request_is_recorded_as_an_error():
    provider = _single_only(_single(slow_calls={1, 2}, delay=999))
    result, manager = _run(
        provider,
        _item("Hello world", "test:0"),
        calls=CallLimits(item_timeout=0.2, min_pass_timeout=2.0),
    )
    assert result == {}
    assert manager.get_statistics()["total_errors"] >= 1


def test_queued_long_items_are_not_limited_by_a_fixed_outer_timeout():
    """The outer timeout scales with the work queued behind the semaphore."""
    provider = _single_only(_single(slow_calls={1, 2, 3}, delay=0.05))
    items = [
        _item(f"This is a deliberately long line queued behind the semaphore {i}.", f"test:{i}")
        for i in range(3)
    ]

    result, _manager = _run(
        provider,
        *items,
        calls=CallLimits(item_timeout=1.0, min_pass_timeout=0.08),
        max_concurrent_requests=1,
    )

    assert result == {item.key: f"TR:{item.text}" for item in items}


def test_long_item_error_is_recorded_without_a_retry():
    item = _item(
        "A remarkably long placeable description sentence. " * 130,
        "l",
        "x.utp",
        type="item_description",
    )
    provider = translation_provider()
    provider.translate_async = AsyncMock(side_effect=RuntimeError("provider down"))

    result, manager = _run(provider, item)

    assert (result, manager.failed_items) == ({}, {item.key})
    assert manager.stats["errors"] == ["Translation failed for l: provider down"]
    assert provider.translate_async.call_count == 1
    provider.translate_batch_async.assert_not_called()

    provider.translate_async = _single(slow_calls={1}, fail=RuntimeError("provider down"))
    result, manager = _run(provider, item, calls=CallLimits(item_timeout=0.01))
    assert (result, manager.failed_items) == ({}, {item.key})
    assert manager.stats["errors"] == [
        "Translation failed for l: Timeout retry failed: provider down"
    ]
    assert provider.translate_async.call_count == 2


def test_foreign_script_answer_is_retried():
    text = "The Auren Society is not welcome in this city, stranger. " * 20
    provider = _single_only(
        AsyncMock(
            side_effect=[
                TranslationResult(translated="Общество здесь не欢迎но!", original=text),
                TranslationResult(translated="Общество здесь не приветствуют!", original=text),
            ]
        )
    )
    item = _item(text, "l", type="placeable_description")

    result, _manager = _run(provider, item)

    assert result == {item.key: "Общество здесь не приветствуют!"}
    assert provider.translate_async.call_count == 2
    assert "foreign script" in provider.translate_async.call_args_list[1].kwargs["context"]


def test_mismatched_markup_is_retried_until_exact(caplog):
    broken = "<StartAction>[Вздрогнуть.]</StartAction>"
    answers = iter([broken])

    async def translate(text, source_lang, target_lang, **kwargs):
        answer = next(answers, None) or text.replace("[Shudder.]", "[Вздрогнуть.]")
        return TranslationResult(translated=answer, original=text)

    provider = _single_only(AsyncMock(side_effect=translate))
    item = _item(SHUDDER, "dlg:0")

    result, _manager = _run(provider, item, source="test.dlg")

    assert result == {item.key: "<StartHighlight>[Вздрогнуть.]</Start>"}
    assert provider.translate_async.call_count == 2
    assert "accepted cleaned translation" not in caplog.text


def test_repeated_markup_mismatch_is_accepted_after_cleanup(caplog):
    broken = TranslationResult(
        translated="<StartAction>[Вздрогнуть.]</StartAction>", original=SHUDDER
    )
    provider = _single_only(AsyncMock(side_effect=[broken] * 3))
    item = _item(SHUDDER, "dlg:1")

    result, _manager = _run(provider, item, source="test.dlg")

    assert result == {item.key: "[Вздрогнуть.]"}
    # The retry that reproduces the same mismatch goes straight to cleanup.
    assert provider.translate_async.call_count == 2
    assert "accepted cleaned translation" in caplog.text


def test_failed_token_retry_does_not_end_the_retries(caplog):
    sanitized, _ = sanitize_text(SHUDDER)
    item = _item(SHUDDER, "d", "a.uti")
    provider = translation_provider()
    provider.translate_batch_async.side_effect = lambda items, **kw: (
        [TranslationResult("<StartAction>[Вздрогнуть.]</StartAction>", i.original) for i in items]
    )
    provider.translate_async = AsyncMock(
        side_effect=[
            RuntimeError("provider down"),
            TranslationResult(sanitized.replace("Shudder.", "Вздрогнуть."), ""),
        ]
    )

    result, manager = _run(provider, item)

    assert result == {item.key: "<StartHighlight>[Вздрогнуть.]</Start>"}
    assert manager.stats["errors"] == []
    assert "Token retry failed for d on attempt 1: provider down" in caplog.text
    contexts = [call.kwargs["context"] for call in provider.translate_async.call_args_list]
    assert [context.splitlines()[-1] for context in contexts] == [
        "Retry attempt 1 of 2.",
        "Retry attempt 2 of 2.",
    ]
    # The failed attempt leaves the first answer's mismatch in the next prompt.
    assert "Previous restored artifact sequence: <StartAction> | </StartAction>" in contexts[1]


def test_token_retry_requests_keep_their_exact_arguments():
    text = SHUDDER + " Drizzt"
    sanitized, _ = sanitize_text(text)
    glossary = Glossary({"Drizzt": "Дзирт"})
    item = TranslatableItem(text, "Line of Drizzt", "i", "a.dlg")
    provider = translation_provider()
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch())
    provider.translate_async = AsyncMock(
        side_effect=[
            TranslationResult(translated=sanitized.replace("Shudder.", "欢迎"), original=""),
            TranslationResult(
                translated="<StartAction>[Вздрогнуть.]</StartAction> Дзирт", original=""
            ),
            TranslationResult(
                translated=sanitized.replace("Shudder.", "Вздрогнуть.").replace("Drizzt", "Дзирт"),
                original="",
            ),
        ]
    )

    result, _manager = _run(provider, item, glossary=glossary)

    assert result == {item.key: "<StartHighlight>[Вздрогнуть.]</Start> Дзирт"}
    block = terminology_block([sanitized, item.context], "russian", glossary)
    assert "Дзирт" in block
    common = "\n".join(
        [
            "Line of Drizzt",
            PRESERVE_PLACEHOLDERS,
            PRESERVE_INLINE_MARKUP,
            "If the line contains dialog action markers like <<...>> or -...-, preserve "
            "the surrounding markers exactly and translate only the inner text. Do not "
            "invent new angle-bracket pseudo-tags such as <sir/madam>.",
            "Expected preserved artifacts after restoration: <StartHighlight> | </Start>",
        ]
    )
    contexts = [
        "Line of Drizzt",
        common + "\nYour previous answer contained characters from a foreign script "
        "(such as Chinese). Write the translation in russian using only that "
        "language's alphabet.\nPrevious mismatch type: foreign_script.\n"
        "Previous restored artifact sequence: <StartHighlight> | </Start>\n"
        "Retry attempt 1 of 2.",
        common + "\nPrevious mismatch type: value_mismatch.\n"
        "Previous restored artifact sequence: <StartAction> | </StartAction>\n"
        "Retry attempt 2 of 2.",
    ]
    assert [call.kwargs for call in provider.translate_async.call_args_list] == [
        {
            "text": sanitized,
            "source_lang": "english",
            "target_lang": "russian",
            "context": context,
            "glossary_block": block,
            "content_profile": "default",
        }
        for context in contexts
    ]
    batch_call = provider.translate_batch_async.call_args
    assert batch_call.kwargs["glossary_block"] == block
    assert batch_call.kwargs["content_profile"] == "default"


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_one_progress_bump_per_item_with_its_file_name():
    rejected = _ncs("Debug state changed.", gate=True, confidence="low")
    rejected.location = "s.ncs"
    items = [
        rejected,
        _item("Sword", "n", "a.uti"),
        _item("Sword", "n", "b.uti"),
        _item("<FirstName>", "t", "c.uti"),
        _item("Long text. " * 700, "l", "d.utp"),
        _item("   ", "blank", "e.uti"),
    ]
    provider = translation_provider()
    provider.classify_ncs_translate_gate_batch_async.side_effect = gate_answering(lambda e: False)
    progress = _Progress()

    result, _manager = _run(provider, *items, progress=progress, max_concurrent_requests=1)

    assert len(result) == 4
    assert progress.files[:2] == ["s.ncs", "c.uti"]
    assert sorted(progress.files[2:4]) == ["a.uti", "d.utp"]
    assert progress.files[4:] == ["b.uti"]


def test_batch_fallback_and_token_retry_do_not_bump_again():
    items = [_item("First line.", "1", "a.uti"), _item(SHUDDER, "2", "a.uti")]
    provider = translation_provider({"First line.": "Первая строка."})
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch())
    answers = iter(["<StartAction>[Вздрогнуть.]</StartAction>", None])

    async def translate_async(text, source_lang, target_lang, **kwargs):
        answer = next(answers, None) if "Shudder" in text else None
        if answer is None:
            answer = text.replace("[Shudder.]", "[Вздрогнуть.]").replace(
                "First line.", "Первая строка."
            )
        return TranslationResult(translated=answer, original=text)

    provider.translate_async = AsyncMock(side_effect=translate_async)
    progress = _Progress()

    result, _manager = _run(provider, *items, progress=progress)

    assert result == {
        items[0].key: "Первая строка.",
        items[1].key: "<StartHighlight>[Вздрогнуть.]</Start>",
    }
    # One batch, one call per halved leaf, two fallbacks and one token retry.
    assert (provider.translate_batch_async.call_count, provider.translate_async.call_count) == (
        3,
        3,
    )
    assert progress.files == ["a.uti", "a.uti"]


# ---------------------------------------------------------------------------
# Script strings: the gate, fail-closed diagnostics and script batches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("approved", [False, True])
def test_unproven_word_requires_gate_approval(approved):
    item = _ncs("Good", gate=True, confidence="low")
    item.metadata.update(proven_player=False, player_candidate=True)
    provider = translation_provider({"Good": "Translated word"})
    provider.classify_ncs_translate_gate_batch_async = AsyncMock(
        return_value={"0": {"translate": approved, "reason": "checked_source"}}
    )

    result, _manager = _run(provider, item, source="script.ncs")

    provider.classify_ncs_translate_gate_batch_async.assert_called_once()
    assert result == ({item.key: "Translated word"} if approved else {})
    if not approved:
        provider.translate_async.assert_not_called()
        provider.translate_batch_async.assert_not_called()


@pytest.mark.parametrize("word", [True, False])
def test_disabled_gate_rejects_unproven_strings(tmp_path, word):
    if word:
        item = _ncs("Good", gate=True, confidence="low")
        item.metadata.update(proven_player=False, player_candidate=True)
        content = [item]
    else:
        sentence = "This looks exactly like a spoken sentence."
        content = extract_script(tmp_path, consts(sentence), retn()).items
    provider = translation_provider({item.text: "Must not be used." for item in content})

    result, _manager = _run(provider, *content, skip_ncs_llm_gate=True)

    assert result == {}
    provider.classify_ncs_translate_gate_batch_async.assert_not_called()
    provider.translate_async.assert_not_called()
    provider.translate_batch_async.assert_not_called()


def test_high_confidence_player_line_is_translated_by_item_id():
    item = _ncs("Look out, behind you!")
    provider = translation_provider({"Look out, behind you!": "RU: Look out, behind you!"})

    result, manager = _run(provider, item)

    assert result == {("script.ncs", item.item_id): "RU: Look out, behind you!"}
    assert _ncs_stats(manager, "approved", "translated", "skipped_fail_closed") == (1, 1, 0)


def test_gate_rejection_is_logged_without_the_original(tmp_path):
    item = _ncs(
        "Something happened nearby.", gate=True, confidence="medium", hint="ambiguous_bytecode"
    )
    log_path = tmp_path / "translations.jsonl"
    provider = translation_provider({item.text: "RU"})
    provider.classify_ncs_translate_gate_batch_async.side_effect = gate_answering(
        lambda e: False, "ambiguous_conservative"
    )

    result, manager = _run(provider, item, translation_log=log_path)

    assert result == {}
    provider.translate_async.assert_not_called()
    stats = manager.get_statistics()["ncs_diagnostics"]
    assert (
        stats["total"],
        stats["extracted"],
        stats["skipped_fail_closed"],
        stats["approved"],
    ) == (1, 1, 1, 0)
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["request_id"] == records[1]["request_id"]
    assert [r for r in records if r.get("event") == "ncs_diagnostic"] == [
        {
            "event": "ncs_diagnostic",
            "file": "script.ncs",
            "item_id": "script:off_20",
            "offset": 32,
            "confidence": "medium",
            "ncs_hint": "ambiguous_bytecode",
            "reason": "gate_rejected:ambiguous_conservative",
            "text_prefix": "Something happened nearby.",
        }
    ]


def test_hard_veto_wins_even_when_the_gate_is_skipped():
    item = _ncs(
        "DetermineClassToUse: This character is invalid.",
        gate=True,
        confidence="low",
        hint="ambiguous_bytecode",
    )
    provider = translation_provider({item.text: "RU"})

    result, manager = _run(provider, item, skip_ncs_llm_gate=True)

    assert result == {}
    provider.translate_async.assert_not_called()
    assert _ncs_stats(manager, "skipped_hard_veto", "approved") == (1, 0)
    assert manager.get_statistics()["ncs_diagnostics"]["samples"][0]["reason"] == "code_identifier"


LONG_SCRIPT = "The seal is breaking beyond the old ward stones and the eastern gate! " * 20


def test_timed_out_script_line_gets_one_minimal_retry():
    item = _ncs(LONG_SCRIPT)
    provider = translation_provider()
    provider.translate_async = _single(lambda t: "RU: The seal is breaking!", slow_calls={1})

    result, manager = _run(
        provider, item, calls=CallLimits(item_timeout=0.01, min_pass_timeout=2.0)
    )

    assert result == {item.key: "RU: The seal is breaking!"}
    assert provider.translate_async.call_count == 2
    retry = provider.translate_async.call_args_list[1].kwargs
    assert "NCS timeout fallback" in retry["context"]
    assert retry["glossary_block"] is None
    assert _ncs_stats(manager, "timeout", "retry_recovered", "translated") == (1, 1, 1)


def test_script_line_whose_retry_also_times_out_is_not_patched():
    item = _ncs("The portal resists you while the tower wards grind against the seal! " * 20)
    provider = translation_provider()
    provider.translate_async = _single(slow_calls={1, 2})

    result, manager = _run(
        provider, item, calls=CallLimits(item_timeout=0.01, min_pass_timeout=2.0)
    )

    assert result == {}
    assert provider.translate_async.call_count == 2
    assert _ncs_stats(manager, "timeout", "failed", "retry_recovered") == (1, 1, 0)
    reasons = {reason for reason, _error in _samples(manager)}
    assert {"translation_timeout_retry_failed", "translation_failed"} <= reasons


@pytest.mark.parametrize(
    "retry, samples",
    [
        (
            RuntimeError("provider down"),
            [
                ("translation_timeout_retry_failed", "provider down"),
                ("translation_failed", "provider down"),
            ],
        ),
        # An unsuccessful answer (unlike an error or a timeout) records no retry
        # outcome here, while the fallback pass of failed batches records one.
        (
            TranslationResult("", "", success=False, error="provider down"),
            [("translation_failed", "provider down")],
        ),
    ],
)
def test_failed_timeout_retry_of_a_long_script_line(retry, samples):
    item = _ncs(LONG_SCRIPT)
    provider = translation_provider()
    provider.translate_async = _single(slow_calls={1}, fail=retry)

    result, manager = _run(provider, item, calls=CallLimits(item_timeout=0.01))

    assert (result, manager.failed_items) == ({}, {item.key})
    assert manager.stats["errors"] == ["Translation failed for script:off_20: provider down"]
    assert _samples(manager) == [
        ("gate_approved:test_approve", None),
        ("translation_timeout", None),
        *samples,
    ]
    assert _ncs_stats(manager, "timeout", "retry_recovered", "failed") == (1, 0, 1)
    assert provider.translate_async.call_count == 2
    assert provider.translate_async.call_args.kwargs["context"].startswith("NCS timeout fallback.")
    provider.translate_batch_async.assert_not_called()


@pytest.mark.parametrize(
    "fallback, error",
    [
        (RuntimeError("provider down"), "provider down"),
        (None, "NCS single-item fallback timeout after 0.01s"),
    ],
)
def test_failed_fallback_of_a_script_batch(fallback, error):
    item = _ncs("The gate opens.", item_id="script:off_10", offset=0x10)
    provider = translation_provider()
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch("Batch JSON parse error"))
    provider.translate_async = (
        _single(slow_calls={1}) if fallback is None else AsyncMock(side_effect=fallback)
    )

    result, manager = _run(provider, item, calls=CallLimits(item_timeout=0.01))

    assert (result, manager.failed_items) == ({}, {item.key})
    assert manager.stats["errors"] == [f"Translation failed for script:off_10: {error}"]
    # The batch failed without a timeout, so no timeout outcome is recorded.
    assert _samples(manager) == [
        ("gate_approved:test_approve", None),
        ("translation_failed", error),
    ]
    assert (provider.translate_batch_async.call_count, provider.translate_async.call_count) == (
        1,
        1,
    )


def test_approved_script_lines_are_batched_and_rejected_ones_are_not_sent():
    approved = _ncs("The gate opens.", item_id="script:off_10", offset=0x10)
    rejected = _ncs(
        "Debug state changed.", gate=True, confidence="medium", hint="ambiguous_bytecode"
    )
    provider = translation_provider({"The gate opens.": "Ворота открываются."})
    provider.classify_ncs_translate_gate_batch_async.side_effect = gate_answering(
        lambda e: e["text"] == "The gate opens."
    )

    result, _manager = _run(provider, approved, rejected)

    assert result == {("script.ncs", "script:off_10"): "Ворота открываются."}
    assert _sent(provider.translate_batch_async) == ["The gate opens."]
    provider.translate_async.assert_not_called()


def test_script_batch_sizes_and_single_requests_by_length():
    short = [
        _ncs(f"Short player line {i}.", item_id=f"script:short_{i}", offset=i) for i in range(21)
    ]
    medium = [
        _ncs(
            f"Medium player-facing message {i} with enough words for the second bucket.",
            item_id=f"script:medium_{i}",
            offset=100 + i,
        )
        for i in range(11)
    ]
    long_text = (
        "This player-facing script message is long enough to stay outside NCS batch mode. " * 20
    )
    multiline = "First player line.\nSecond player line."
    items = [
        *short,
        *medium,
        _ncs(long_text, item_id="script:long", offset=300),
        _ncs(multiline, item_id="script:multiline", offset=320),
    ]
    provider = translation_provider({i.text: f"TR:{i.text}" for i in items})

    result, _manager = _run(provider, *items, batch=BatchLimits(payload_chars=100000))

    assert result == {item.key: f"TR:{item.text}" for item in items}
    assert _sizes(provider.translate_batch_async) == [33]
    assert [call.kwargs["text"] for call in provider.translate_async.call_args_list] == [long_text]


@pytest.mark.parametrize("length,count,expected_calls", [(30, 65, 2), (1000, 8, 2)])
def test_script_batches_span_single_message_scripts(length, count, expected_calls):
    items = []
    for index in range(count):
        item = _ncs(f"Player message {index}: ".ljust(length, "."), item_id="line:0", offset=0)
        item.location = f"script_{index}.ncs"
        items.append(item)
    provider = translation_provider({item.text: f"TR:{item.text}" for item in items})

    result, manager = _run(provider, *items)

    assert result == {item.key: f"TR:{item.text}" for item in items}
    calls = provider.translate_batch_async.call_args_list
    assert len(calls) == expected_calls
    for call in calls:
        batch = call.kwargs["items"]
        assert len(batch) <= manager.batch_limits.max_items
        assert sum(len(i.original) for i in batch) <= manager.batch_limits.text_chars
        assert batch_payload_chars(batch) <= manager.batch_limits.payload_chars
    provider.translate_async.assert_not_called()


def test_script_batch_budget_counts_the_context():
    items = []
    for index in range(5):
        item = _ncs(f"Player line {index}.", item_id="line:0", offset=0)
        item.location = f"script_{index}.ncs"
        item.context = "Script context. " * 25
        items.append(item)
    provider = translation_provider({item.text: f"TR:{item.text}" for item in items})

    result, _manager = _run(provider, *items, batch=BatchLimits(payload_chars=1000))

    assert len(result) == 5
    assert _sizes(provider.translate_batch_async) == [1, 1, 1, 1, 1]
    for call in provider.translate_batch_async.call_args_list:
        assert batch_payload_chars(call.kwargs["items"]) <= 1000
    provider.translate_async.assert_not_called()


def test_script_lines_share_answers_only_with_the_same_hint():
    items = [
        _ncs("The lever moves.", item_id="script:off_10", offset=0x10),
        _ncs("The lever moves.", item_id="script:off_20", offset=0x20),
        _ncs("The lever moves.", item_id="script:off_30", hint="SetCustomToken", offset=0x30),
    ]
    provider = translation_provider({"The lever moves.": "Рычаг движется."})

    result, _manager = _run(provider, *items)

    sent = provider.translate_batch_async.call_args.kwargs["items"]
    provider.translate_batch_async.assert_called_once()
    assert [item.original for item in sent] == ["The lever moves."] * 2
    assert [item.metadata["ncs_hint"] for item in sent] == ["SpeakString", "SetCustomToken"]
    assert result == {item.key: "Рычаг движется." for item in items}


def test_failed_script_batch_is_split_then_sent_as_minimal_single_requests():
    items = [
        _ncs("The first ward fails.", item_id="script:off_10", offset=0x10),
        _ncs("The second ward fails.", item_id="script:off_20", offset=0x20),
    ]
    provider = translation_provider({item.text: f"TR:{item.text}" for item in items})
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch("Batch JSON parse error"))

    result, manager = _run(provider, *items)

    assert result == {item.key: f"TR:{item.text}" for item in items}
    assert _sizes(provider.translate_batch_async) == [2, 1, 1]
    assert provider.translate_async.call_count == 2
    for call in provider.translate_async.call_args_list:
        assert "NCS timeout fallback" in call.kwargs["context"]
        assert call.kwargs["glossary_block"] is None
    assert _ncs_stats(manager, "translated", "failed") == (2, 0)


def test_timed_out_script_batch_is_recovered_by_single_requests():
    item = _ncs("The ward flickers.", item_id="script:off_10", offset=0x10)
    provider = translation_provider({"The ward flickers.": "Мерцает оберег."})

    async def hanging_batch(items, source_lang, target_lang, **kwargs):
        await asyncio.sleep(1)
        return []

    provider.translate_batch_async = AsyncMock(side_effect=hanging_batch)

    result, manager = _run(
        provider, item, calls=CallLimits(item_timeout=1.0, batch_timeout=0.01, min_pass_timeout=2.0)
    )

    assert result == {item.key: "Мерцает оберег."}
    assert _ncs_stats(manager, "timeout", "retry_recovered", "translated") == (1, 1, 1)

    first, second = (
        _ncs("The first ward flickers.", item_id="s:1", offset=1),
        _ncs("The second ward flickers.", item_id="s:2", offset=2),
    )
    provider.translate_async = AsyncMock(
        side_effect=lambda text, *args, **kwargs: (
            TranslationResult(translated="Первый.", original=text)
            if "first" in text
            else TranslationResult(translated="", original=text, success=False, error="nope")
        )
    )

    result, manager = _run(
        provider, first, second, source="s.ncs", calls=CallLimits(batch_timeout=0.01)
    )

    assert result == {first.key: "Первый."}
    # Samples follow the gate, the fallback run and the result processing in turn.
    stats = manager.get_statistics()["ncs_diagnostics"]
    assert [(s["item_id"], s["reason"]) for s in stats["samples"]] == [
        ("s:1", "gate_approved:test_approve"),
        ("s:2", "gate_approved:test_approve"),
        ("s:1", "translation_timeout"),
        ("s:2", "translation_timeout"),
        ("s:1", "translation_timeout_retry_recovered"),
        ("s:2", "translation_timeout_retry_failed"),
        ("s:2", "translation_failed"),
    ]
    assert stats["samples"][5]["error"] == "nope"
    assert _ncs_stats(manager, "timeout", "retry_recovered", "failed", "translated") == (2, 1, 1, 1)


def test_script_fallback_request_and_batch_metadata():
    item = _ncs("The gate opens.", item_id="script:off_10", offset=0x10)
    provider = translation_provider()
    provider.translate_batch_async = AsyncMock(side_effect=failing_batch())

    _run(provider, item)

    sent = provider.translate_batch_async.call_args.kwargs["items"][0]
    assert list(sent.metadata.items()) == [
        ("type", "ncs_string"),
        ("offset", 0x10),
        ("confidence", "high"),
        ("needs_llm_gate", False),
        ("ncs_hint", "SpeakString"),
        ("batch_resource", "script.ncs"),
        ("translation_group", "script"),
        ("batch_context", "NCS string; hint=SpeakString"),
        ("approved_neighbors", []),
    ]
    assert provider.translate_async.call_args.kwargs == {
        "text": "The gate opens.",
        "source_lang": "english",
        "target_lang": "russian",
        "context": "NCS timeout fallback. Translate only if this is player-visible script "
        "text. Do not translate identifiers, tags, resrefs, variables, debug logs, or "
        "code. file=script.ncs; item_id=script:off_10; offset=16; confidence=high; "
        "hint=SpeakString.\nNCS string; hint=SpeakString",
        "glossary_block": None,
        "content_profile": "script_message",
    }


def test_approved_script_context_is_local_and_does_not_change_the_extraction():
    items = [
        TranslatableItem(
            text,
            "script",
            f"line:{index}",
            file,
            {
                "type": "ncs_string",
                "offset": index,
                "proven_player": True,
                "ncs_hint": "SpeakString",
            },
        )
        for index, (file, text) in enumerate(
            [
                ("song.ncs", "I climbed the tower."),
                ("song.ncs", "I fell from the tower."),
                ("song.ncs", "Internal debug message."),
                ("other.ncs", "Unrelated speech here."),
            ]
        )
    ]
    provider = translation_provider({item.text: "TR:" + item.text for item in items})
    provider.classify_ncs_translate_gate_batch_async.side_effect = gate_answering(
        lambda e: e["text"] != "Internal debug message."
    )

    result, _manager = _run(provider, *items, quiet=True)

    first = next(
        i
        for c in provider.translate_batch_async.call_args_list
        for i in c.kwargs["items"]
        if i.original == items[0].text
    )
    assert items[1].text in first.context
    assert items[2].text not in first.context and items[3].text not in first.context
    assert "not proven execution order" in first.context
    assert all(item.context == "script" for item in items)
    provider.translate_batch_async.assert_called_once()
    assert result == {item.key: "TR:" + item.text for item in items if item is not items[2]}


def test_log_connects_requests_results_and_reuse(tmp_path):
    path = tmp_path / "trace.jsonl"
    provider = translation_provider({"Hello": "Greetings"})
    items = [TranslatableItem("Hello", "same context", "name", file) for file in ("a.utc", "b.utc")]

    _run(provider, *items, translation_log=path, quiet=True)

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    request = next(e for e in events if e.get("event") == "model_request")
    response = next(e for e in events if e.get("event") == "model_response")
    reuse = next(e for e in events if e.get("event") == "translation_reuse")
    assert request["request_id"] == response["request_id"]
    assert request["context"]["occurrences"] == [["a.utc", "name"]]
    assert reuse["occurrence"] == ["b.utc", "name"]
    assert "test-key" not in path.read_text(encoding="utf-8")
