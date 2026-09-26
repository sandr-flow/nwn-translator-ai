"""Tests for contextual dialog translation: requests, recovery, grouping and progress."""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Dict, List

import pytest

from src.nwn_translator.ai_providers.base import RateLimitError, TranslationResult
from src.nwn_translator.config import (
    TRANSLATION_MAX_TOKENS,
    TranslationCancelled,
    TranslationConfig,
)
from src.nwn_translator.context.dialog_formatter import iter_nodes
from src.nwn_translator.context.dialog_speakers import speaker_lines
from src.nwn_translator.context.world_context import NPCInfo, WorldContext
from src.nwn_translator.extractors.base import DialogNode
from src.nwn_translator.extractors.dialog_extractor import DialogExtractor
from src.nwn_translator.prompts.dialog import speakers_block
from src.nwn_translator.translators import context_translator as context_module
from src.nwn_translator.translators import dialog_plan
from src.nwn_translator.translators.context_translator import (
    ContextualTranslationManager,
    _RECOVERY_MAX_TOKENS,
)
from src.nwn_translator.translators.dialog_plan import PreparedDialog, pack_groups


class _NullWriter:
    def write(self, _entry):
        return None


class _RecordingWriter:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)

    def rows(self):
        """Translation rows (not model request/response events)."""
        return [entry for entry in self.entries if not entry.get("event")]


def _make_config(**kwargs) -> TranslationConfig:
    defaults = dict(
        api_key="test-key",
        model="fake/model",
        source_lang="english",
        target_lang="russian",
        input_file=Path("input.mod"),
        translation_log_writer=_NullWriter(),
    )
    defaults.update(kwargs)
    return TranslationConfig(**defaults)


class _FakeProvider:
    """Provider double: answers JSON chats from a queue; single lines fail loudly."""

    model = "fake/model"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.line_calls = []

    def make_system_message_content(self, stable, variable=""):
        return "\n\n".join(p for p in (stable, variable) if p)

    async def complete_json_chat_async(
        self,
        system_prompt,
        user_prompt,
        *,
        max_tokens,
        temperature,
        use_reasoning=True,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "use_reasoning": use_reasoning,
            }
        )
        if not self._responses:
            raise AssertionError("No fake responses left for complete_json_chat_async")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def translate_async(
        self,
        text,
        source_lang,
        target_lang,
        context=None,
        glossary_block=None,
        content_profile=None,
    ):
        raise AssertionError("translate_async should not be reached in these tests")

    async def close_async_client(self):
        return None


def _dlg(*roots: DialogNode) -> dict:
    """Parsed .dlg struct whose conversation tree is *roots*.

    Node ids are list indices; unused indices get empty nodes no link reaches.
    """
    tables: Dict[bool, Dict[int, dict]] = {True: {}, False: {}}
    for _key, node in iter_nodes(list(roots)):
        links = [{"Index": child.node_id} for child in node.replies]
        struct = {"Text": {"StrRef": -1, "Value": node.text}}
        if node.is_entry:
            struct.update(Speaker=node.speaker or "", RepliesList=links)
        else:
            struct.update(EntriesList=links)
        tables[node.is_entry][node.node_id] = struct

    def as_list(table: Dict[int, dict]) -> List[dict]:
        return [
            table.get(i, {"Text": {"StrRef": -1, "Value": ""}})
            for i in range(max(table, default=-1) + 1)
        ]

    return {
        "StructType": "DLG",
        "EntryList": as_list(tables[True]),
        "ReplyList": as_list(tables[False]),
        "StartingList": [{"Index": root.node_id} for root in roots],
    }


def _expected_dialog(files, answers):
    """Map every node whose text has an answer to that answer, by occurrence."""
    expected = {}
    for path, parsed, _budget in files:
        for _key, node in iter_nodes(DialogExtractor().build_dialog_tree(parsed)):
            if node.text in answers:
                kind = "entry" if node.is_entry else "reply"
                expected[(path.name, f"{path.stem}:{kind}:{node.node_id}")] = answers[node.text]
    return expected


def _hello_who() -> DialogNode:
    return DialogNode(
        node_id=1,
        text="Hello there",
        is_entry=True,
        replies=[DialogNode(node_id=2, text="Who are you?", is_entry=False)],
    )


def _translate(manager, name, *roots):
    """Translate one dialog file whose tree is *roots*; return its translations."""
    translations, errors = manager.translate_dialogs([(Path(name), _dlg(*roots), 0)])
    assert errors == []
    return translations


def test_initial_invalid_json_truncation_retries_original_prompt_first(caplog):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    provider = _FakeProvider(['{"E1":"broken', '{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    caplog.set_level(logging.WARNING)
    result = _translate(manager, "test.dlg", *tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert len(provider.calls) == 2
    assert provider.calls[0]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[1]["max_tokens"] == _RECOVERY_MAX_TOKENS
    assert provider.calls[0]["user_prompt"] == provider.calls[1]["user_prompt"]
    assert "was not valid JSON or was truncated" not in provider.calls[1]["user_prompt"]
    assert "truncation-like invalid JSON" in caplog.text


def test_truncated_answers_end_with_a_repair_of_the_second_answer():
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    provider = _FakeProvider(['{"E1":"first cut', '{"E1":"second cut', '{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", *tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert [call["max_tokens"] for call in provider.calls] == [
        TRANSLATION_MAX_TOKENS,
        _RECOVERY_MAX_TOKENS,
        _RECOVERY_MAX_TOKENS,
    ]
    repair = provider.calls[2]["user_prompt"]
    assert "The previous answer for test.dlg was not valid JSON or was truncated." in repair
    assert repair.endswith('{"E1":"second cut')


def test_initial_invalid_json_non_truncation_uses_repair_prompt(caplog):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    provider = _FakeProvider(["not-json-at-all", '{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    caplog.set_level(logging.WARNING)
    result = _translate(manager, "test.dlg", *tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert len(provider.calls) == 2
    assert provider.calls[0]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[1]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[0]["user_prompt"] != provider.calls[1]["user_prompt"]
    assert (
        "The previous answer for test.dlg was not valid JSON or was truncated."
        in provider.calls[1]["user_prompt"]
    )
    assert "non-truncation invalid JSON" in caplog.text


def test_failed_repair_is_sent_again_with_the_recovery_budget():
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    provider = _FakeProvider(["not-json-at-all", "still not json", '{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", *tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert [call["max_tokens"] for call in provider.calls] == [
        TRANSLATION_MAX_TOKENS,
        TRANSLATION_MAX_TOKENS,
        _RECOVERY_MAX_TOKENS,
    ]
    # The repair prompt quotes the first answer and is not rebuilt.
    assert provider.calls[1]["user_prompt"] == provider.calls[2]["user_prompt"]
    assert provider.calls[2]["user_prompt"].endswith("not-json-at-all")


def test_chunk_that_never_parses_leaves_its_lines_to_the_pending_retry():
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    provider = _FakeProvider(["no", "no", "no", '{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", *tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert len(provider.calls) == 4
    assert "keys exactly E1" in provider.calls[3]["user_prompt"]


def test_pending_keys_truncation_retries_same_retry_prompt_with_higher_tokens(caplog):
    provider = _FakeProvider(['{"E1":"Привет"}', '{"R2":"Кто т', '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    caplog.set_level(logging.WARNING)
    result = _translate(manager, "test.dlg", _hello_who())

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет",
        ("test.dlg", "test:reply:2"): "Кто ты?",
    }
    assert len(provider.calls) == 3
    assert provider.calls[1]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[2]["max_tokens"] == _RECOVERY_MAX_TOKENS
    assert provider.calls[1]["user_prompt"] == provider.calls[2]["user_prompt"]
    assert (
        "changed, dropped, or omitted preserved NWN tags/tokens" in provider.calls[1]["user_prompt"]
    )
    assert "pending dialog retry JSON looks truncated" in caplog.text


def test_pending_keys_are_retried_in_string_order():
    """E10 sorts before E2 in the retry prompt: keys are compared as strings."""
    nodes = [DialogNode(node_id=i, text=f"Line {i}", is_entry=True) for i in (2, 10, 3)]
    provider = _FakeProvider(["{}", '{"E10":"a", "E2":"b", "E3":"c"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    _translate(manager, "test.dlg", *nodes)

    retry = provider.calls[1]["user_prompt"]
    assert "keys: E10, E2, E3." in retry
    assert retry.index("[E10]") < retry.index("[E2]") < retry.index("[E3]")


def test_large_dialog_is_translated_in_chunks(monkeypatch):
    tree = DialogNode(
        node_id=1,
        text="Hello there, traveler.",
        is_entry=True,
        replies=[
            DialogNode(node_id=2, text="Who are you?", is_entry=False),
            DialogNode(node_id=3, text="Goodbye.", is_entry=False),
        ],
    )
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    provider = _FakeProvider(['{"E1":"Привет, путник."}', '{"R2":"Кто ты?"}', '{"R3":"Прощай."}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", tree)

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет, путник.",
        ("test.dlg", "test:reply:2"): "Кто ты?",
        ("test.dlg", "test:reply:3"): "Прощай.",
    }
    assert len(provider.calls) == 3
    assert "[E1]" in provider.calls[0]["user_prompt"]
    assert "[R2] [Player]:" not in provider.calls[0]["user_prompt"]
    assert "-> Player Reply [R2]" in provider.calls[0]["user_prompt"]
    assert "Context R2 (Player): Who are you?" in provider.calls[0]["user_prompt"]
    assert "[R2]" in provider.calls[1]["user_prompt"]
    assert "[R3] [Player]:" not in provider.calls[1]["user_prompt"]
    assert "[R3]" in provider.calls[2]["user_prompt"]


def test_answer_keys_outside_the_request_are_ignored(monkeypatch):
    """A chunk answer may echo context-only IDs; they are not accepted or logged."""
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    writer = _RecordingWriter()
    provider = _FakeProvider(
        ['{"E1":"Привет", "R2":"FROM-CONTEXT"}', '{"R2":"Кто ты?", "E1":"OVERWRITE"}']
    )
    manager = ContextualTranslationManager(
        _make_config(translation_log_writer=writer), provider, WorldContext()
    )

    result = _translate(manager, "test.dlg", _hello_who())

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет",
        ("test.dlg", "test:reply:2"): "Кто ты?",
    }
    assert len(provider.calls) == 2
    assert [row["translated"] for row in writer.rows()] == ["Привет", "Кто ты?"]


def test_chunked_dialog_retries_missing_keys_after_merge(monkeypatch):
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    provider = _FakeProvider(['{"E1":"Привет"}', "{}", '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", _hello_who())

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет",
        ("test.dlg", "test:reply:2"): "Кто ты?",
    }
    assert len(provider.calls) == 3
    assert "[E1]" in provider.calls[0]["user_prompt"]
    assert "[R2]" in provider.calls[1]["user_prompt"]
    assert (
        "changed, dropped, or omitted preserved NWN tags/tokens" in provider.calls[2]["user_prompt"]
    )
    assert "keys exactly R2" in provider.calls[2]["user_prompt"]


def test_cancel_before_first_chunk_raises_without_api_calls():
    provider = _FakeProvider([])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: True), provider, WorldContext()
    )

    with pytest.raises(TranslationCancelled):
        _translate(manager, "test.dlg", DialogNode(node_id=1, text="Hello there", is_entry=True))
    assert provider.calls == []


def test_cancel_between_chunks_stops_run_and_propagates(monkeypatch):
    tree = DialogNode(
        node_id=1,
        text="Hello there, traveler.",
        is_entry=True,
        replies=[DialogNode(node_id=2, text="Who are you?", is_entry=False)],
    )
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    provider = _FakeProvider(['{"E1":"Привет, путник."}', '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: len(provider.calls) >= 1), provider, WorldContext()
    )

    with pytest.raises(TranslationCancelled):
        _translate(manager, "test.dlg", tree)
    assert len(provider.calls) == 1  # second chunk was never requested


def test_generic_api_error_still_degrades_to_partial_result(caplog):
    provider = _FakeProvider([RuntimeError("network exploded")])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: False), provider, WorldContext()
    )

    caplog.set_level(logging.ERROR)
    result = _translate(manager, "test.dlg", DialogNode(node_id=1, text="Hello", is_entry=True))

    assert result == {}
    assert ("test.dlg", "test:entry:1") in manager.failed_items
    assert "dialog chunk 1/1 request failed: network exploded" in caplog.text
    assert len(provider.calls) == 1


def test_failing_chunk_does_not_stop_the_other_chunks(monkeypatch):
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    provider = _FakeProvider([TimeoutError("run_async timed out"), '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", _hello_who())

    assert result == {("test.dlg", "test:reply:2"): "Кто ты?"}
    assert manager.failed_items == {("test.dlg", "test:entry:1")}
    assert len(provider.calls) == 2


def test_failing_pending_retry_still_retries_lines_one_by_one():
    tree = DialogNode(node_id=1, text="Hello there", is_entry=True)
    provider = _LineRetryFake(
        ["{}", RuntimeError("provider down")], lambda text: text.replace("Hello", "Привет")
    )
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет there"}
    assert len(provider.calls) == 2
    assert len(provider.line_calls) == 1
    assert manager.failed_items == set()


def _three_lines() -> DialogNode:
    return DialogNode(
        node_id=1,
        text="Hello there",
        is_entry=True,
        replies=[
            DialogNode(node_id=2, text="Who are you?", is_entry=False),
            DialogNode(node_id=3, text="Goodbye.", is_entry=False),
        ],
    )


def test_rate_limit_in_a_chunk_stops_the_file(monkeypatch, caplog):
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)
    provider = _FakeProvider(['{"E1":"Привет"}', RateLimitError("402 budget")])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
    progress = _CountingProgress()

    caplog.set_level(logging.ERROR)
    translations, errors = manager.translate_dialogs(
        [(Path("test.dlg"), _dlg(_three_lines()), 3)], item_progress=progress
    )

    assert errors == []
    assert translations == {("test.dlg", "test:entry:1"): "Привет"}
    assert manager.failed_items == {("test.dlg", "test:reply:2"), ("test.dlg", "test:reply:3")}
    assert len(provider.calls) == 2  # the third chunk was never requested
    assert progress.total == 3
    assert "Contextual translation failed for test.dlg: 402 budget" in caplog.text


def test_rate_limit_in_the_pending_retry_skips_the_line_retries():
    tree = DialogNode(node_id=1, text="Hello <FirstName>.", is_entry=True)
    provider = _LineRetryFake(
        ['{"E1":"Привет."}', RateLimitError("402 budget")],
        lambda text: text.replace("Hello", "Привет"),
    )
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    translations, errors = manager.translate_dialogs([(Path("test.dlg"), _dlg(tree), 1)])

    assert (translations, errors) == ({}, [])
    assert manager.failed_items == {("test.dlg", "test:entry:1")}
    assert len(provider.calls) == 2
    assert provider.line_calls == []


def test_error_outside_a_request_keeps_accepted_lines_and_fails_the_rest(monkeypatch, caplog):
    def broken_prompt(*_args, **_kwargs):
        raise RuntimeError("prompt exploded")

    monkeypatch.setattr(context_module, "token_retry_prompt", broken_prompt)
    provider = _FakeProvider(['{"E1":"Привет"}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
    progress = _CountingProgress()

    caplog.set_level(logging.ERROR)
    translations, errors = manager.translate_dialogs(
        [(Path("test.dlg"), _dlg(_hello_who()), 2)], item_progress=progress
    )

    assert errors == []
    assert translations == {("test.dlg", "test:entry:1"): "Привет"}
    assert manager.failed_items == {("test.dlg", "test:reply:2")}
    assert len(provider.calls) == 1
    assert progress.total == 2
    assert "Contextual translation failed for test.dlg: prompt exploded" in caplog.text


def test_pending_retry_answer_keeps_lines_accepted_earlier():
    """The pending retry reads only the keys it asked for, like a chunk answer."""
    writer = _RecordingWriter()
    provider = _FakeProvider(['{"E1":"Привет"}', '{"R2":"Кто ты?", "E1":"OVERWRITE"}'])
    manager = ContextualTranslationManager(
        _make_config(translation_log_writer=writer), provider, WorldContext()
    )

    result = _translate(manager, "test.dlg", _hello_who())

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет",
        ("test.dlg", "test:reply:2"): "Кто ты?",
    }
    assert "keys exactly R2" in provider.calls[1]["user_prompt"]
    assert [row["translated"] for row in writer.rows()] == ["Привет", "Кто ты?"]


class _KeyedFakeProvider(_FakeProvider):
    """Return the response whose marker substring appears in the user prompt.

    Order-independent, so it stays deterministic under concurrent calls.
    """

    def __init__(self, responses_by_marker):
        super().__init__([])
        self._by_marker = dict(responses_by_marker)

    async def complete_json_chat_async(
        self,
        system_prompt,
        user_prompt,
        *,
        max_tokens,
        temperature,
        use_reasoning=True,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "use_reasoning": use_reasoning,
            }
        )
        for marker, response in self._by_marker.items():
            if marker in user_prompt:
                return response
        raise AssertionError(f"No fake response matches prompt: {user_prompt[:120]!r}")


class _CountingProgress:
    def __init__(self):
        self.total = 0
        self._lock = threading.Lock()

    def bump(self, by=1, filename=None):
        with self._lock:
            self.total += by


def _file(name, node_id, text, budget=1):
    return (Path(name), _dlg(DialogNode(node_id=node_id, text=text, is_entry=True)), budget)


class TestTranslateDialogs:
    """Concurrent multi-file dialog orchestration (single-file work units)."""

    @pytest.fixture(autouse=True)
    def _no_grouping(self, monkeypatch):
        # Force every file onto the single-file path; grouping is covered by
        # TestDialogGrouping.
        monkeypatch.setattr(dialog_plan, "SMALL_DIALOG_CHARS", 0)

    def test_aggregates_translations_across_files(self):
        provider = _KeyedFakeProvider(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
                "Thanks": '{"E3":"Спасибо"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=3), provider, WorldContext()
        )
        files = [
            _file("a.dlg", 1, "Hello"),
            _file("b.dlg", 2, "Goodbye"),
            _file("c.dlg", 3, "Thanks"),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(
            files, {"Hello": "Привет", "Goodbye": "Прощай", "Thanks": "Спасибо"}
        )
        assert len(provider.calls) == 3

    def test_file_error_is_isolated(self, monkeypatch):
        provider = _KeyedFakeProvider({"Hello": '{"E1":"Привет"}', "Goodbye": '{"E3":"Прощай"}'})
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=2), provider, WorldContext()
        )
        boom = RuntimeError("prepare exploded")
        real_prepare = context_module.prepare_dialog

        def failing_prepare(file_path, *args, **kwargs):
            if file_path.name == "bad.dlg":
                raise boom
            return real_prepare(file_path, *args, **kwargs)

        monkeypatch.setattr(context_module, "prepare_dialog", failing_prepare)
        files = [
            _file("a.dlg", 1, "Hello"),
            _file("bad.dlg", 2, "Kaboom"),
            _file("b.dlg", 3, "Goodbye"),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert [(path.name, exc) for path, exc in errors] == [("bad.dlg", boom)]

    def test_cancellation_aborts_pool_and_skips_queued_files(self):
        provider = _KeyedFakeProvider({"Hello": '{"E1":"Привет"}', "Goodbye": '{"E2":"Прощай"}'})
        manager = ContextualTranslationManager(
            _make_config(
                max_concurrent_requests=1,
                cancel_check=lambda: len(provider.calls) >= 1,
            ),
            provider,
            WorldContext(),
        )
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        with pytest.raises(TranslationCancelled):
            manager.translate_dialogs(files)
        assert len(provider.calls) == 1  # second file was never requested

    def test_concurrent_progress_bumps_are_aggregated(self):
        provider = _KeyedFakeProvider(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
                "Thanks": '{"E3":"Спасибо"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=3), provider, WorldContext()
        )
        progress = _CountingProgress()
        files = [
            _file("a.dlg", 1, "Hello"),
            _file("b.dlg", 2, "Goodbye"),
            _file("c.dlg", 3, "Thanks"),
        ]

        translations, errors = manager.translate_dialogs(files, item_progress=progress)

        assert errors == []
        assert len(translations) == 3
        assert progress.total == 3  # one budgeted item per file, none lost

    def test_worker_threads_close_their_client_and_event_loop(self):
        class _LoopRecordingProvider(_KeyedFakeProvider):
            def __init__(self, responses_by_marker):
                super().__init__(responses_by_marker)
                self.request_loops = set()
                self.closed_by = []

            async def complete_json_chat_async(self, *args, **kwargs):
                self.request_loops.add(asyncio.get_running_loop())
                return await super().complete_json_chat_async(*args, **kwargs)

            async def close_async_client(self):
                self.closed_by.append(threading.get_ident())

        provider = _LoopRecordingProvider(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
                "Thanks": '{"E3":"Спасибо"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=2), provider, WorldContext()
        )
        files = [
            _file("a.dlg", 1, "Hello"),
            _file("b.dlg", 2, "Goodbye"),
            _file("c.dlg", 3, "Thanks"),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert errors == [] and len(translations) == 3
        assert len(provider.closed_by) == 2  # one per worker thread
        assert threading.get_ident() not in provider.closed_by
        assert provider.request_loops
        assert all(loop.is_closed() for loop in provider.request_loops)

    def test_file_without_lines_reports_its_budget(self):
        provider = _KeyedFakeProvider({})
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        progress = _CountingProgress()
        files = [
            (Path("empty.dlg"), _dlg(DialogNode(node_id=0, text="  ", is_entry=True)), 2),
            (Path("none.dlg"), {"StructType": "DLG"}, 3),
        ]

        assert manager.translate_dialogs(files, item_progress=progress) == ({}, [])
        assert progress.total == 5
        assert provider.calls == []

    def test_empty_file_list_returns_empty(self):
        manager = ContextualTranslationManager(
            _make_config(), _KeyedFakeProvider({}), WorldContext()
        )

        assert manager.translate_dialogs([]) == ({}, [])


class TestDialogGrouping:
    """Grouped translation of small dialog files."""

    @staticmethod
    def _small(name, script):
        return PreparedDialog(Path(name), 1, {}, {}, {}, {}, script)

    # ── packer ──────────────────────────────────────────────────────────

    def test_packer_respects_char_limit(self, monkeypatch):
        monkeypatch.setattr(dialog_plan, "GROUP_TARGET_CHARS", 8000)
        entries = [
            self._small("a.dlg", "x" * 4000),
            self._small("b.dlg", "x" * 4000),
            self._small("c.dlg", "x" * 4000),
        ]

        groups, loners = pack_groups(entries, "russian", None)

        assert [[e.file_path.name for e in g] for g in groups] == [["a.dlg", "b.dlg"]]
        assert [e.file_path.name for e in loners] == ["c.dlg"]

    def test_packer_respects_file_limit(self, monkeypatch):
        monkeypatch.setattr(dialog_plan, "GROUP_MAX_FILES", 2)
        entries = [self._small(f"{n}.dlg", "x" * 10) for n in ("a", "b", "c")]

        groups, loners = pack_groups(entries, "russian", None)

        assert [[e.file_path.name for e in g] for g in groups] == [["a.dlg", "b.dlg"]]
        assert [e.file_path.name for e in loners] == ["c.dlg"]

    def test_packer_single_file_becomes_loner(self):
        groups, loners = pack_groups([self._small("a.dlg", "x" * 10)], "russian", None)

        assert groups == []
        assert [e.file_path.name for e in loners] == ["a.dlg"]

    def test_packer_empty_input(self):
        assert pack_groups([], "russian", None) == ([], [])

    # ── group prompt ────────────────────────────────────────────────────

    def test_group_prompt_headers_and_scoped_speakers(self):
        world = WorldContext()
        world.npcs["sev_tag"] = NPCInfo(
            tag="sev_tag",
            first_name="Severina",
            last_name="",
            description="",
            race="Dwarf",
            gender="Female",
            conversation="severina",
        )
        provider = _KeyedFakeProvider(
            {"=== FILE:": '{"severina.dlg": {"E1": "Привет"}, "other.dlg": {"E2": "Прощай"}}'}
        )
        manager = ContextualTranslationManager(_make_config(), provider, world)
        files = [_file("severina.dlg", 1, "Hello"), _file("other.dlg", 2, "Goodbye")]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert len(provider.calls) == 1
        user_prompt = provider.calls[0]["user_prompt"]
        assert "=== FILE: severina.dlg ===" in user_prompt
        assert "=== FILE: other.dlg ===" in user_prompt
        assert "do not let tone, wording, or context leak" in user_prompt
        system_prompt = provider.calls[0]["system_prompt"]
        assert "DIALOG SPEAKERS:" in system_prompt
        assert (
            "- In severina.dlg, lines marked [NPC]: spoken by Severina (Dwarf, Female)"
            in system_prompt
        )

    # ── demux and fallbacks ─────────────────────────────────────────────

    def test_group_demux_with_progress(self):
        provider = _KeyedFakeProvider(
            {"=== FILE:": '{"a.dlg": {"E1": "Привет"}, "b.dlg": {"E2": "Прощай"}}'}
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        progress = _CountingProgress()
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        translations, errors = manager.translate_dialogs(files, item_progress=progress)

        assert errors == []
        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert progress.total == 2

    def test_group_partial_answer_falls_back_single_file(self):
        provider = _FakeProvider(
            [
                '{"a.dlg": {"E1": "Привет"}}',  # b.dlg missing from the group answer
                '{"E2": "Прощай"}',  # single-file fallback for b.dlg
            ]
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert len(provider.calls) == 2
        assert "=== FILE:" in provider.calls[0]["user_prompt"]
        assert "=== FILE:" not in provider.calls[1]["user_prompt"]
        assert "b.dlg" in provider.calls[1]["user_prompt"]

    def test_equal_nodes_and_partial_group_keep_their_addresses(self):
        provider = _FakeProvider(
            [
                '{"a.dlg": {"E1": "Первый"}, "b.dlg": {"E1": "Третий"}}',
                '{"E2": "Второй"}',
            ]
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        files = [
            (
                Path("a.dlg"),
                _dlg(
                    DialogNode(node_id=1, text="Same", is_entry=True),
                    DialogNode(node_id=2, text="Same", is_entry=True),
                ),
                2,
            ),
            _file("b.dlg", 1, "Same"),
        ]
        translations, errors = manager.translate_dialogs(files)
        assert not errors
        assert translations == {
            ("a.dlg", "a:entry:1"): "Первый",
            ("a.dlg", "a:entry:2"): "Второй",
            ("b.dlg", "b:entry:1"): "Третий",
        }
        retry = provider.calls[1]["user_prompt"]
        assert "[E2]" in retry
        assert "[E1]" not in retry

    def test_group_total_failure_falls_back_per_file(self, caplog):
        provider = _FakeProvider(
            [
                "not-json-at-all",  # group request
                "still-not-json",  # group repair retry
                '{"E1": "Привет"}',  # single a.dlg
                '{"E2": "Прощай"}',  # single b.dlg
            ]
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        caplog.set_level(logging.WARNING)
        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert len(provider.calls) == 4
        assert "invalid JSON; retrying with repair prompt" in caplog.text
        assert "a.dlg, b.dlg" in provider.calls[1]["user_prompt"]
        assert "falling back to single-file translation" in caplog.text

    def test_truncated_group_answer_is_requested_again_unchanged(self):
        provider = _FakeProvider(
            ['{"a.dlg": {"E1": "При', '{"a.dlg": {"E1": "Привет"}, "b.dlg": {"E2": "Прощай"}}']
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(files, {"Hello": "Привет", "Goodbye": "Прощай"})
        assert provider.calls[0]["user_prompt"] == provider.calls[1]["user_prompt"]
        assert provider.calls[1]["max_tokens"] == _RECOVERY_MAX_TOKENS
        assert provider.calls[1]["system_prompt"] == provider.calls[0]["system_prompt"]

    def test_group_rate_limit_marks_files_failed_without_fallback(self):
        limit = RateLimitError("budget exhausted")
        provider = _FakeProvider([limit])
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        progress = _CountingProgress()
        files = [_file("a.dlg", 1, "Hello", 2), _file("b.dlg", 2, "Goodbye", 3)]

        translations, errors = manager.translate_dialogs(files, item_progress=progress)

        assert translations == {}
        assert [(path.name, exc) for path, exc in errors] == [("a.dlg", limit), ("b.dlg", limit)]
        assert manager.failed_items == {("a.dlg", "a:entry:1"), ("b.dlg", "b:entry:2")}
        assert len(provider.calls) == 1
        assert progress.total == 5

    def test_group_log_rows_attributed_to_right_files(self):
        writer = _RecordingWriter()
        provider = _KeyedFakeProvider(
            {"=== FILE:": '{"a.dlg": {"E1": "Привет"}, "b.dlg": {"E2": "Прощай"}}'}
        )
        manager = ContextualTranslationManager(
            _make_config(translation_log_writer=writer), provider, WorldContext()
        )
        files = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]

        manager.translate_dialogs(files)

        by_file = {e["file"]: e for e in writer.rows()}
        assert by_file["a.dlg"]["original"] == "Hello"
        assert by_file["a.dlg"]["translated"] == "Привет"
        assert by_file["b.dlg"]["original"] == "Goodbye"
        assert by_file["b.dlg"]["translated"] == "Прощай"

    # ── integration: mixed sizes ────────────────────────────────────────

    def test_large_file_stays_single_while_small_files_group(self):
        big_text = "Long line of dialog text. " * 60  # ~1560 chars > SMALL_DIALOG_CHARS
        provider = _KeyedFakeProvider(
            {
                "=== FILE:": '{"a.dlg": {"E1": "Привет"}, "b.dlg": {"E2": "Прощай"}}',
                "Long line of dialog": '{"E3": "Длинная строка"}',
            }
        )
        manager = ContextualTranslationManager(_make_config(), provider, WorldContext())
        files = [
            _file("a.dlg", 1, "Hello"),
            _file("b.dlg", 2, "Goodbye"),
            _file("big.dlg", 3, big_text),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == _expected_dialog(
            files,
            {"Hello": "Привет", "Goodbye": "Прощай", big_text: "Длинная строка"},
        )
        assert len(provider.calls) == 2
        group_calls = [c for c in provider.calls if "=== FILE:" in c["user_prompt"]]
        assert len(group_calls) == 1


class TestSpeakersBlock:
    """Speaker gender hints injected into the dialog system prompt."""

    @staticmethod
    def _world_with_npcs() -> WorldContext:
        context = WorldContext()
        context.npcs["sev_tag"] = NPCInfo(
            tag="sev_tag",
            first_name="Severina",
            last_name="",
            description="",
            race="Dwarf",
            gender="Female",
            conversation="severina",
        )
        context.npcs["stumpy_tag"] = NPCInfo(
            tag="stumpy_tag",
            first_name="Stumpy",
            last_name="",
            description="",
            race="Dwarf",
            gender="Male",
            conversation="stumpy",
        )
        return context

    @staticmethod
    def _block(world: WorldContext, stem: str, node_map) -> str:
        return speakers_block(speaker_lines(world, stem, node_map))

    def test_owner_and_tagged_speakers_resolved(self):
        node_map = {
            "E0": DialogNode(node_id=0, text="Hello", is_entry=True),
            "E1": DialogNode(node_id=1, text="Hmpf", speaker="stumpy_tag", is_entry=True),
            "R0": DialogNode(node_id=0, text="Hi", is_entry=False),
        }

        block = self._block(self._world_with_npcs(), "Severina", node_map)

        assert block.startswith("DIALOG SPEAKERS:")
        assert "- Lines marked [NPC]: spoken by Severina (Dwarf, Female)" in block
        assert "- Lines marked [stumpy_tag]: spoken by Stumpy (Dwarf, Male)" in block
        assert "grammatical forms" in block

    def test_empty_when_no_speaker_matches(self):
        node_map = {"E0": DialogNode(node_id=0, text="Hello", is_entry=True)}

        assert self._block(self._world_with_npcs(), "unrelated", node_map) == ""

    def test_empty_without_world_npcs(self):
        node_map = {"E0": DialogNode(node_id=0, text="Hello", is_entry=True)}

        assert self._block(WorldContext(), "severina", node_map) == ""

    def test_dialog_request_injects_block_into_system_prompt(self):
        provider = _FakeProvider(['{"E1":"Привет"}'])
        manager = ContextualTranslationManager(_make_config(), provider, self._world_with_npcs())

        result = _translate(
            manager, "severina.dlg", DialogNode(node_id=1, text="Hello there", is_entry=True)
        )

        assert result == {("severina.dlg", "severina:entry:1"): "Привет"}
        system_prompt = provider.calls[0]["system_prompt"]
        assert "DIALOG SPEAKERS:" in system_prompt
        assert "Severina (Dwarf, Female)" in system_prompt

    def test_dialog_request_without_matching_npc_omits_block(self):
        provider = _FakeProvider(['{"E1":"Привет"}'])
        manager = ContextualTranslationManager(_make_config(), provider, self._world_with_npcs())

        _translate(
            manager, "unrelated.dlg", DialogNode(node_id=1, text="Hello there", is_entry=True)
        )

        assert "DIALOG SPEAKERS:" not in provider.calls[0]["system_prompt"]


def _goodbye_tree() -> DialogNode:
    return DialogNode(
        node_id=1,
        text="Hello there",
        is_entry=True,
        replies=[DialogNode(node_id=3, text="END DIALOG", is_entry=False)],
    )


def test_empty_player_reply_retries_then_recovers():
    provider = _FakeProvider(['{"E1":"Привет", "R3":""}', '{"R3":"Закончить разговор."}'])
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", _goodbye_tree())

    assert result[("test.dlg", "test:entry:1")] == "Привет"
    assert result[("test.dlg", "test:reply:3")] == "Закончить разговор."
    assert len(provider.calls) == 2


def test_empty_player_reply_keeps_original_after_retries():
    class _EmptyLineFake(_FakeProvider):
        async def translate_async(
            self,
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            return TranslationResult(translated="", original=text, success=False, error="empty")

    writer = _RecordingWriter()
    provider = _EmptyLineFake(['{"E1":"Привет", "R3":""}', '{"R3":""}'])
    manager = ContextualTranslationManager(
        _make_config(translation_log_writer=writer), provider, WorldContext()
    )

    result = _translate(manager, "test.dlg", _goodbye_tree())

    assert result.get(("test.dlg", "test:entry:1")) == "Привет"
    assert ("test.dlg", "test:reply:3") not in result
    assert ("test.dlg", "test:reply:3") in manager.failed_items
    assert ("test.dlg", "test:entry:1") not in manager.failed_items
    assert not any(
        entry.get("original") == "END DIALOG" and not str(entry.get("translated") or "").strip()
        for entry in writer.entries
    )


class _LineRetryFake(_FakeProvider):
    """Answers single-line retries with a fixed translation of the sanitized text."""

    def __init__(self, responses, translate):
        super().__init__(responses)
        self._translate = translate

    async def translate_async(
        self,
        text,
        source_lang,
        target_lang,
        context=None,
        glossary_block=None,
        content_profile=None,
    ):
        self.line_calls.append({"text": text, "context": context, "glossary": glossary_block})
        return TranslationResult(translated=self._translate(text), original=text)


def test_line_retry_accepts_an_answer_that_keeps_the_tokens():
    tree = DialogNode(node_id=1, text="Hello <FirstName>.", is_entry=True)
    provider = _LineRetryFake(
        ['{"E1":"Привет."}', '{"E1":"Привет."}'], lambda text: text.replace("Hello", "Привет")
    )
    manager = ContextualTranslationManager(_make_config(), provider, WorldContext())

    result = _translate(manager, "test.dlg", tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет <FirstName>."}
    assert len(provider.line_calls) == 1
    context = provider.line_calls[0]["context"]
    assert context.startswith("Dialog node E1 in test.dlg.\nSpeaker: NPC.\n")
    assert "Expected preserved artifacts after restoration: <FirstName>" in context
    assert "Previous mismatch type: count_mismatch." in context
    assert context.endswith("Retry attempt 2 of 2.")
    assert manager.failed_items == set()


def test_line_that_keeps_breaking_its_tokens_is_accepted_cleaned():
    tree = DialogNode(node_id=1, text="Hello <FirstName>.", is_entry=True)
    provider = _LineRetryFake(['{"E1":"Привет."}', '{"E1":"Привет."}'], lambda _text: "Привет.")
    writer = _RecordingWriter()
    manager = ContextualTranslationManager(
        _make_config(translation_log_writer=writer), provider, WorldContext()
    )

    result = _translate(manager, "test.dlg", tree)

    assert result == {("test.dlg", "test:entry:1"): "Привет."}
    assert [row["translated"] for row in writer.rows()] == ["Привет."]
    assert manager.failed_items == set()
