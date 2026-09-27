"""GlossaryBuilder: glossary requests, retries of missing names and reply parsing."""

import asyncio
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from nwn_translator import glossary_builder
from nwn_translator.config import GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.glossary_builder import (
    GlossaryBuilder,
    glossary_key_variants,
    parse_glossary_json,
)
from nwn_translator.prompts import build_glossary_system_prompt


class _Provider:
    """Answers each glossary request with the next reply; an exception reply is raised."""

    def __init__(self, replies: List[Any]) -> None:
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply(kwargs["glossary_keys"]) if callable(reply) else reply


def _build(world_or_names, replies, target_lang="russian", progress=None, concurrency=1):
    if isinstance(world_or_names, WorldContext):
        world = world_or_names
    else:
        world = WorldContext()
        world.extracted_names = list(world_or_names.items())
    provider = _Provider(replies)
    config = SimpleNamespace(target_lang=target_lang, max_concurrent_requests=concurrency)
    return GlossaryBuilder().build(world, provider, config, progress), provider


def test_request_and_retry_with_the_accepted_forms():
    world = WorldContext()
    world.npcs["dawn"] = NPCInfo("dawn", "Dawn", "Ioza", "", "Human", "Female", "dawn_conv")
    registry = world.candidates
    registry.add("Dawn Ioza", category="character", source="utc_name", context="A priestess.")
    registry.add("sword-one", category="nickname", source="entity_extractor")
    registry.add("Melee vs Mayhem", category="term", source="entity_extractor")
    registry.add("MVM", category="term", source="entity_extractor", context="Play MVM!")
    registry.add("Dark Forest", category="location", source="are_name")
    registry.mark_curated("MVM", decision="alias_of", alias_of="Melee vs Mayhem")

    glossary, provider = _build(
        world,
        [
            '{"glossary": {"Dark Forest (location)": "Тёмный лес"}}',
            '{"Dawn Ioza": "Доун Иоза", "Melee vs Mayhem": "Битва", "MVM": "БТВ",'
            ' "sword-one": "ты с мечом"}',
        ],
    )

    assert glossary.entries == {
        "Dark Forest": "Тёмный лес",
        "Dawn Ioza": "Доун Иоза",
        "Melee vs Mayhem": "Битва",
        "MVM": "БТВ",
        "sword-one": "ты с мечом",
    }
    assert glossary.aliases == {"MVM": "Melee vs Mayhem"}
    header = (
        "Translate every name below. Keys in your JSON must be the English name only, "
        "without the parenthesized category hint:\n\n"
    )
    lines = {
        "Dark Forest": "- Dark Forest (location)",
        "Dawn Ioza": "- Dawn Ioza (character, A priestess., NPC fields: FirstName='Dawn', "
        "LastName='Ioza', gender=Female)",
        "Melee vs Mayhem": "- Melee vs Mayhem (term)",
        "MVM": "- MVM (term, alias of Melee vs Mayhem; preserve abbreviation or wordplay, "
        "Play MVM!)",
        "sword-one": "- sword-one (nickname, vocative epithet; translate meaning, not a name)",
    }
    order = ["Dark Forest", "Dawn Ioza", "Melee vs Mayhem", "MVM", "sword-one"]
    first, second = provider.calls
    assert first["user"] == header + "\n".join(lines[name] for name in order)
    assert first["glossary_keys"] == order
    assert second["user"] == (
        header
        + "\n".join(lines[name] for name in order[1:])
        + '\n\nAlready accepted forms in this family/batch: {"Dark Forest": "Тёмный лес"}'
    )
    assert second["glossary_keys"] == order[1:]
    for call in (first, second):
        assert call["system"] == build_glossary_system_prompt("russian")
        assert (call["max_tokens"], call["temperature"]) == (
            GLOSSARY_MAX_TOKENS,
            GLOSSARY_TEMPERATURE,
        )
        assert set(call) == {"system", "user", "glossary_keys", "max_tokens", "temperature"}


@pytest.mark.parametrize(
    "npc, name, category, parts",
    [
        # Character lines carry the gender and the name fields of the NPC.
        (
            NPCInfo("g", "Dawn", "Ioza", "", "Human", "Female", "dlg"),
            "Dawn",
            "character",
            ["female", "FirstName"],
        ),
        (
            NPCInfo("jade", "Jade", "Falcon", "", "Elf", "Female", "dlg"),
            "Jade Falcon",
            "character",
            ["Jade", "Falcon", "Female"],
        ),
        (None, "sword-one", "nickname", ["epithet", "translate meaning"]),
    ],
)
def test_request_line_of_a_name(npc, name, category, parts):
    world = WorldContext()
    if npc:
        world.npcs[npc.tag] = npc
    world.extracted_names = [(name, category)]
    _glossary, provider = _build(world, [lambda keys: json.dumps({key: key for key in keys})])
    line = next(l for l in provider.calls[0]["user"].splitlines() if l.startswith(f"- {name} ("))
    for part in parts:
        assert part in (line.lower() if part == "female" else line)


def test_partial_answers_are_merged_and_only_missing_names_are_asked_again():
    glossary, provider = _build(
        {"Alpha": "character", "Beta": "location", "Gamma": "item"},
        [json.dumps({"Alpha": "Альфа"}), json.dumps({"Beta": "Бета", "Gamma": "Гамма"})],
    )
    assert glossary.entries == {"Alpha": "Альфа", "Beta": "Бета", "Gamma": "Гамма"}
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "names, answer, target_lang",
    [
        (
            {"Perin": "character", "Dark Forest": "location"},
            {"Perin": "Перин", "Dark Forest": "Dark Forest"},
            "russian",
        ),
        (
            {"Almraiven": "location", "Perin": "character"},
            {"Almraiven": "Almraiven", "Perin": "Perin"},
            "french",
        ),
    ],
)
def test_unchanged_names_are_valid_answers(names, answer, target_lang):
    """An unchanged name or abbreviation is a decision, not a failed reply."""
    glossary, provider = _build(names, [json.dumps(answer), "{}"], target_lang)
    assert glossary.entries == answer
    assert len(provider.calls) == 1


def test_progress_reports_every_attempt_and_its_outcome():
    messages = []
    glossary, _provider = _build(
        {"Alpha": "character", "Beta": "location"},
        [TimeoutError("timeout"), json.dumps({"Alpha": "Альфа"}), "not json"],
        progress=lambda *args: messages.append(args),
    )
    assert glossary.entries == {"Alpha": "Альфа"}
    assert messages == [
        ("scanning", 0, 1, "Glossary glossary (attempt 1/3)…"),
        ("scanning", 0, 1, "Glossary glossary: attempt 1 failed, retrying…"),
        ("scanning", 0, 1, "Glossary glossary (attempt 2/3)…"),
        ("scanning", 0, 1, "Glossary glossary: 1/2 names done"),
        ("scanning", 0, 1, "Glossary glossary (attempt 3/3)…"),
        ("scanning", 0, 1, "Glossary glossary: attempt 3 failed, retrying…"),
    ]


@pytest.mark.parametrize("reply", [TimeoutError("timeout"), "not json at all"])
def test_a_batch_that_never_answers_leaves_an_empty_glossary(caplog, reply):
    caplog.set_level(logging.WARNING)
    glossary, provider = _build({"Perin": "character"}, [reply])
    assert glossary.entries == {}
    assert len(provider.calls) == 3
    if isinstance(reply, str):
        assert "no usable entries" in caplog.text
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert "Glossary glossary returned no usable entries after 3 attempts" in errors


def test_overall_timeout_keeps_the_finished_batches(monkeypatch):
    monkeypatch.setattr(
        glossary_builder, "_STAGE", replace(glossary_builder._STAGE, run_timeout_per_batch=0.1)
    )

    class _SlowSmallBatch(_Provider):
        async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
            if len(kwargs["glossary_keys"]) < 80:
                await asyncio.sleep(5)
            return json.dumps({key: key.upper() for key in kwargs["glossary_keys"]})

    names = {f"Name{i:03d}": "character" for i in range(81)}
    world = WorldContext()
    world.extracted_names = list(names.items())
    config = SimpleNamespace(target_lang="russian", max_concurrent_requests=2)
    glossary = GlossaryBuilder().build(world, _SlowSmallBatch([]), config)

    assert glossary.entries == {name: name.upper() for name in list(names)[:80]}


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected_keys, entries",
    [
        # Short keys survive invisible characters and category suffixes.
        (
            '{"Kit": "\\u041d\\u0430\\u0431\\u043e\\u0440"}',
            {"Kit\u200b (item)"},
            {"Kit\u200b (item)": "Набор"},
        ),
        # Prose and examples after the object do not poison it.
        (
            'Here is the translation:\n{"Kit": "\\u041d\\u0430\\u0431\\u043e\\u0440"}\n'
            'Example format: {"Other": "Value"}',
            {"Kit"},
            {"Kit": "Набор"},
        ),
        # A name quoted in the game data is answered with the bare name as its key;
        # the quotes of the source survive into the translation.
        (
            '{"Thesis Paper Room": "Комната диссертаций"}',
            {'"Thesis Paper Room"'},
            {'"Thesis Paper Room"': '"Комната диссертаций"'},
        ),
        (
            '{"Thesis Paper Room": "Комната диссертаций"}',
            {"«Thesis Paper Room»"},
            {"«Thesis Paper Room»": "«Комната диссертаций»"},
        ),
        # Quotes the model already returned are not doubled.
        (
            '{"Thesis Paper Room": "\\"Комната диссертаций\\""}',
            {'"Thesis Paper Room"'},
            {'"Thesis Paper Room"': '"Комната диссертаций"'},
        ),
        # Quote stripping composes with the ``(suffix)`` handling.
        (
            '{"Planar Studies Section": "Секция изучения планов"}',
            {'"Planar Studies Section (Restricted)"'},
            {'"Planar Studies Section (Restricted)"': '"Секция изучения планов"'},
        ),
        (
            '{"Dewey Plowshare": "Дьюи Плаушер"}',
            {"Dewey Plowshare"},
            {"Dewey Plowshare": "Дьюи Плаушер"},
        ),
    ],
)
def test_parse_glossary_json(raw, expected_keys, entries):
    assert parse_glossary_json(raw, expected_keys) == entries


def test_inner_quotes_are_part_of_the_name():
    """Only a matched pair wrapping the whole name is noise."""
    assert glossary_key_variants('He said "hi"') == ['He said "hi"']
