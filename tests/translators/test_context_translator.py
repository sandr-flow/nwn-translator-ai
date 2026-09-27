"""Contextual dialog translation: requests, recovery, grouping, cancellation and progress."""

import asyncio
import logging
import threading
from pathlib import Path

import pytest

from nwn_translator.ai_providers.base import RateLimitError, TranslationResult
from nwn_translator.config import TRANSLATION_MAX_TOKENS, TranslationCancelled
from nwn_translator.context.dialog_formatter import iter_nodes
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import DialogNode
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.translators import context_translator, dialog_plan
from nwn_translator.translators.context_translator import ContextualTranslationManager
from tests.support.dialogs import dlg
from tests.support.fakes import DialogProvider, RecordingWriter, make_config

#: Recovery budget of these tests. The real one equals ``TRANSLATION_MAX_TOKENS``,
#: which would hide the budget a request was sent with.
RECOVERY = TRANSLATION_MAX_TOKENS + 1000
FIRST = TRANSLATION_MAX_TOKENS
HELLO = {("test.dlg", "test:entry:1"): "Привет"}
HELLO_WHO = {**HELLO, ("test.dlg", "test:reply:2"): "Кто ты?"}
REPAIR = "The previous answer for test.dlg was not valid JSON or was truncated."
TOKEN_RETRY = "changed, dropped, or omitted preserved NWN tags/tokens"


@pytest.fixture(autouse=True)
def _distinct_recovery_budget(monkeypatch):
    monkeypatch.setattr(context_translator, "_RECOVERY_MAX_TOKENS", RECOVERY)


@pytest.fixture
def one_key_chunks(monkeypatch):
    monkeypatch.setattr(dialog_plan, "CHUNK_MAX_KEYS", 1)


def _node(node_id, text, is_entry=True, *replies, speaker=""):
    return DialogNode(
        node_id=node_id, text=text, speaker=speaker, is_entry=is_entry, replies=list(replies)
    )


def _hello():
    return _node(1, "Hello there")


def _hello_who():
    return _node(1, "Hello there", True, _node(2, "Who are you?", False))


def _three_lines():
    return _node(
        1, "Hello there", True, _node(2, "Who are you?", False), _node(3, "Goodbye.", False)
    )


def _manager(responses, world=None, translate_line=None, **config):
    provider = DialogProvider(responses, translate_line)
    manager = ContextualTranslationManager(make_config(**config), provider, world or WorldContext())
    return manager, provider


def _translate(manager, name, *roots):
    """Translate one dialog file whose tree is *roots*; return its translations."""
    translations, errors = manager.translate_dialogs([(Path(name), dlg(*roots), 0)])
    assert errors == []
    return translations


def _file(name, node_id, text, budget=1):
    return (Path(name), dlg(_node(node_id, text)), budget)


def _expected(files, answers):
    """Map every node whose text has an answer to that answer, by occurrence."""
    expected = {}
    for path, parsed, _budget in files:
        for _key, node in iter_nodes(DialogExtractor().build_dialog_tree(parsed)):
            if node.text in answers:
                kind = "entry" if node.is_entry else "reply"
                expected[(path.name, f"{path.stem}:{kind}:{node.node_id}")] = answers[node.text]
    return expected


def _budgets(provider):
    return [call["max_tokens"] for call in provider.calls]


def _prompts(provider):
    return [call["user_prompt"] for call in provider.calls]


class _CountingProgress:
    def __init__(self):
        self.total = 0
        self._lock = threading.Lock()

    def bump(self, by=1, filename=None):
        with self._lock:
            self.total += by


# ---------------------------------------------------------------------------
# Recovery from answers that do not parse
# ---------------------------------------------------------------------------


def test_truncated_answer_is_asked_again_unchanged_with_the_recovery_budget(caplog):
    caplog.set_level(logging.WARNING)
    manager, provider = _manager(['{"E1":"broken', '{"E1":"Привет"}'])

    assert _translate(manager, "test.dlg", _hello()) == HELLO
    assert _budgets(provider) == [FIRST, RECOVERY]
    first, second = _prompts(provider)
    assert first == second and REPAIR not in second
    assert "truncation-like invalid JSON" in caplog.text


def test_two_truncated_answers_end_with_a_repair_of_the_second():
    manager, provider = _manager(['{"E1":"first cut', '{"E1":"second cut', '{"E1":"Привет"}'])

    assert _translate(manager, "test.dlg", _hello()) == HELLO
    assert _budgets(provider) == [FIRST, RECOVERY, RECOVERY]
    repair = _prompts(provider)[2]
    assert REPAIR in repair and repair.endswith('{"E1":"second cut')


def test_other_invalid_json_gets_a_repair_prompt(caplog):
    caplog.set_level(logging.WARNING)
    manager, provider = _manager(["not-json-at-all", '{"E1":"Привет"}'])

    assert _translate(manager, "test.dlg", _hello()) == HELLO
    assert _budgets(provider) == [FIRST, FIRST]
    first, second = _prompts(provider)
    assert first != second and REPAIR in second
    assert "non-truncation invalid JSON" in caplog.text


def test_failed_repair_is_sent_again_with_the_recovery_budget():
    manager, provider = _manager(["not-json-at-all", "still not json", '{"E1":"Привет"}'])

    assert _translate(manager, "test.dlg", _hello()) == HELLO
    assert _budgets(provider) == [FIRST, FIRST, RECOVERY]
    # The repair prompt quotes the first answer and is not rebuilt.
    _first, repair, again = _prompts(provider)
    assert repair == again and again.endswith("not-json-at-all")


def test_chunk_that_never_parses_leaves_its_lines_to_the_pending_retry():
    manager, provider = _manager(["no", "no", "no", '{"E1":"Привет"}'])

    assert _translate(manager, "test.dlg", _hello()) == HELLO
    assert len(provider.calls) == 4
    assert "keys exactly E1" in _prompts(provider)[3]


def test_truncated_pending_retry_is_asked_again_with_the_recovery_budget(caplog):
    caplog.set_level(logging.WARNING)
    manager, provider = _manager(['{"E1":"Привет"}', '{"R2":"Кто т', '{"R2":"Кто ты?"}'])

    assert _translate(manager, "test.dlg", _hello_who()) == HELLO_WHO
    assert _budgets(provider)[1:] == [FIRST, RECOVERY]
    _first, retry, again = _prompts(provider)
    assert retry == again and TOKEN_RETRY in retry
    assert "pending dialog retry JSON looks truncated" in caplog.text


def test_pending_keys_are_retried_in_string_order():
    """E10 sorts before E2 in the retry prompt: keys are compared as strings."""
    nodes = [_node(i, f"Line {i}") for i in (2, 10, 3)]
    manager, provider = _manager(["{}", '{"E10":"a", "E2":"b", "E3":"c"}'])

    _translate(manager, "test.dlg", *nodes)

    retry = _prompts(provider)[1]
    assert "keys: E10, E2, E3." in retry
    assert retry.index("[E10]") < retry.index("[E2]") < retry.index("[E3]")


def test_answers_read_only_the_keys_that_were_asked(one_key_chunks):
    """A chunk answer may echo context-only ids; they are not accepted or logged."""
    writer = RecordingWriter()
    manager, provider = _manager(
        ['{"E1":"Привет", "R2":"FROM-CONTEXT"}', '{"R2":"Кто ты?", "E1":"OVERWRITE"}'],
        translation_log_writer=writer,
    )

    assert _translate(manager, "test.dlg", _hello_who()) == HELLO_WHO
    assert len(provider.calls) == 2
    assert [row["translated"] for row in writer.rows()] == ["Привет", "Кто ты?"]


def test_pending_retry_answer_keeps_the_lines_accepted_earlier():
    writer = RecordingWriter()
    manager, provider = _manager(
        ['{"E1":"Привет"}', '{"R2":"Кто ты?", "E1":"OVERWRITE"}'], translation_log_writer=writer
    )

    assert _translate(manager, "test.dlg", _hello_who()) == HELLO_WHO
    assert "keys exactly R2" in _prompts(provider)[1]
    assert [row["translated"] for row in writer.rows()] == ["Привет", "Кто ты?"]


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


def test_large_dialog_is_translated_in_chunks_with_context_neighbours(one_key_chunks):
    tree = _node(
        1,
        "Hello there, traveler.",
        True,
        _node(2, "Who are you?", False),
        _node(3, "Goodbye.", False),
    )
    manager, provider = _manager(
        ['{"E1":"Привет, путник."}', '{"R2":"Кто ты?"}', '{"R3":"Прощай."}']
    )

    assert _translate(manager, "test.dlg", tree) == {
        ("test.dlg", "test:entry:1"): "Привет, путник.",
        ("test.dlg", "test:reply:2"): "Кто ты?",
        ("test.dlg", "test:reply:3"): "Прощай.",
    }
    first, second, third = _prompts(provider)
    assert "[E1]" in first and "[R2] [Player]:" not in first
    assert "-> Player Reply [R2]" in first
    assert "Context R2 (Player): Who are you?" in first
    assert "[R2]" in second and "[R3] [Player]:" not in second
    assert "[R3]" in third


def test_missing_keys_of_the_chunks_are_retried_after_the_merge(one_key_chunks):
    manager, provider = _manager(['{"E1":"Привет"}', "{}", '{"R2":"Кто ты?"}'])

    assert _translate(manager, "test.dlg", _hello_who()) == HELLO_WHO
    first, second, retry = _prompts(provider)
    assert "[E1]" in first and "[R2]" in second
    assert TOKEN_RETRY in retry and "keys exactly R2" in retry


def test_failing_chunk_does_not_stop_the_other_chunks(one_key_chunks):
    manager, provider = _manager([TimeoutError("run_async timed out"), '{"R2":"Кто ты?"}'])

    assert _translate(manager, "test.dlg", _hello_who()) == {
        ("test.dlg", "test:reply:2"): "Кто ты?"
    }
    assert manager.failed_items == {("test.dlg", "test:entry:1")}
    assert len(provider.calls) == 2


def test_request_error_degrades_to_a_partial_result(caplog):
    caplog.set_level(logging.ERROR)
    manager, provider = _manager([RuntimeError("network exploded")], cancel_check=lambda: False)

    assert _translate(manager, "test.dlg", _node(1, "Hello")) == {}
    assert ("test.dlg", "test:entry:1") in manager.failed_items
    assert "dialog chunk 1/1 request failed: network exploded" in caplog.text
    assert len(provider.calls) == 1


def test_rate_limit_in_a_chunk_stops_the_file(one_key_chunks, caplog):
    caplog.set_level(logging.ERROR)
    manager, provider = _manager(['{"E1":"Привет"}', RateLimitError("402 budget")])
    progress = _CountingProgress()

    translations, errors = manager.translate_dialogs(
        [(Path("test.dlg"), dlg(_three_lines()), 3)], item_progress=progress
    )

    assert (translations, errors) == ({("test.dlg", "test:entry:1"): "Привет"}, [])
    assert manager.failed_items == {("test.dlg", "test:reply:2"), ("test.dlg", "test:reply:3")}
    assert len(provider.calls) == 2  # the third chunk was never requested
    assert progress.total == 3
    assert "Contextual translation failed for test.dlg: 402 budget" in caplog.text


def test_error_outside_a_request_keeps_accepted_lines_and_fails_the_rest(monkeypatch, caplog):
    def broken_prompt(*_args, **_kwargs):
        raise RuntimeError("prompt exploded")

    monkeypatch.setattr(context_translator, "token_retry_prompt", broken_prompt)
    caplog.set_level(logging.ERROR)
    manager, provider = _manager(['{"E1":"Привет"}'])
    progress = _CountingProgress()

    translations, errors = manager.translate_dialogs(
        [(Path("test.dlg"), dlg(_hello_who()), 2)], item_progress=progress
    )

    assert (translations, errors) == ({("test.dlg", "test:entry:1"): "Привет"}, [])
    assert manager.failed_items == {("test.dlg", "test:reply:2")}
    assert len(provider.calls) == 1
    assert progress.total == 2
    assert "Contextual translation failed for test.dlg: prompt exploded" in caplog.text


# ---------------------------------------------------------------------------
# Single-line retries
# ---------------------------------------------------------------------------


def _hello_line(text):
    return text.replace("Hello", "Привет")


@pytest.mark.parametrize("pending_answer", [RuntimeError("provider down"), "{}"])
def test_failing_or_empty_pending_retry_still_retries_lines_one_by_one(pending_answer):
    manager, provider = _manager(["{}", pending_answer], translate_line=_hello_line)

    assert _translate(manager, "test.dlg", _hello()) == {
        ("test.dlg", "test:entry:1"): "Привет there"
    }
    assert (len(provider.calls), len(provider.line_calls)) == (2, 1)
    assert manager.failed_items == set()


def test_failing_line_retry_falls_back_to_the_cleaned_last_answer(caplog):
    def line_down(_text):
        raise RuntimeError("line down")

    caplog.set_level(logging.WARNING)
    manager, _provider = _manager(['{"E1":"Привет."}'] * 2, translate_line=line_down)

    result = _translate(manager, "test.dlg", _node(1, "Hello <FirstName>."))

    assert result == {("test.dlg", "test:entry:1"): "Привет."}
    assert "individual dialog retry failed for E1: line down" in caplog.text
    assert manager.failed_items == set()


def test_rate_limit_in_the_pending_retry_skips_the_line_retries():
    tree = _node(1, "Hello <FirstName>.")
    manager, provider = _manager(
        ['{"E1":"Привет."}', RateLimitError("402 budget")], translate_line=_hello_line
    )

    translations, errors = manager.translate_dialogs([(Path("test.dlg"), dlg(tree), 1)])

    assert (translations, errors) == ({}, [])
    assert manager.failed_items == {("test.dlg", "test:entry:1")}
    assert (len(provider.calls), provider.line_calls) == (2, [])


def test_line_retry_accepts_an_answer_that_keeps_the_tokens():
    manager, provider = _manager(['{"E1":"Привет."}'] * 2, translate_line=_hello_line)

    result = _translate(manager, "test.dlg", _node(1, "Hello <FirstName>."))

    assert result == {("test.dlg", "test:entry:1"): "Привет <FirstName>."}
    (line_call,) = provider.line_calls
    context = line_call["context"]
    assert context.startswith("Dialog node E1 in test.dlg.\nSpeaker: NPC.\n")
    assert "Expected preserved artifacts after restoration: <FirstName>" in context
    assert "Previous mismatch type: count_mismatch." in context
    assert context.endswith("Retry attempt 2 of 2.")
    assert manager.failed_items == set()


def test_line_that_keeps_breaking_its_tokens_is_accepted_cleaned():
    writer = RecordingWriter()
    manager, _provider = _manager(
        ['{"E1":"Привет."}'] * 2,
        translate_line=lambda _text: "Привет.",
        translation_log_writer=writer,
    )

    result = _translate(manager, "test.dlg", _node(1, "Hello <FirstName>."))

    assert result == {("test.dlg", "test:entry:1"): "Привет."}
    assert [row["translated"] for row in writer.rows()] == ["Привет."]
    assert manager.failed_items == set()


def _goodbye_tree():
    return _node(1, "Hello there", True, _node(3, "END DIALOG", False))


def test_empty_player_reply_is_retried_and_recovers():
    manager, provider = _manager(['{"E1":"Привет", "R3":""}', '{"R3":"Закончить разговор."}'])

    result = _translate(manager, "test.dlg", _goodbye_tree())

    assert result == {
        ("test.dlg", "test:entry:1"): "Привет",
        ("test.dlg", "test:reply:3"): "Закончить разговор.",
    }
    assert len(provider.calls) == 2


def test_empty_player_reply_that_stays_empty_is_failed_not_blanked():
    writer = RecordingWriter()
    manager, provider = _manager(
        ['{"E1":"Привет", "R3":""}', '{"R3":""}'], translation_log_writer=writer
    )

    async def empty_line(text, source_lang, target_lang, **kwargs):
        return TranslationResult(translated="", original=text, success=False, error="empty")

    provider.translate_async = empty_line

    result = _translate(manager, "test.dlg", _goodbye_tree())

    assert result == {("test.dlg", "test:entry:1"): "Привет"}
    assert manager.failed_items == {("test.dlg", "test:reply:3")}
    assert not any(
        entry.get("original") == "END DIALOG" and not str(entry.get("translated") or "").strip()
        for entry in writer.entries
    )


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_the_first_chunk_sends_nothing():
    manager, provider = _manager([], cancel_check=lambda: True)
    with pytest.raises(TranslationCancelled):
        _translate(manager, "test.dlg", _hello())
    assert provider.calls == []


def test_cancel_between_chunks_stops_the_run(one_key_chunks):
    provider = DialogProvider(['{"E1":"Привет, путник."}', '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(
        make_config(cancel_check=lambda: len(provider.calls) >= 1), provider, WorldContext()
    )
    with pytest.raises(TranslationCancelled):
        _translate(manager, "test.dlg", _hello_who())
    assert len(provider.calls) == 1  # the second chunk was never requested


# ---------------------------------------------------------------------------
# Several files
# ---------------------------------------------------------------------------

THREE_FILES = [
    _file("a.dlg", 1, "Hello"),
    _file("b.dlg", 2, "Goodbye"),
    _file("c.dlg", 3, "Thanks"),
]
THREE_ANSWERS = {
    "Hello": '{"E1":"Привет"}',
    "Goodbye": '{"E2":"Прощай"}',
    "Thanks": '{"E3":"Спасибо"}',
}


@pytest.fixture
def single_files(monkeypatch):
    """Keep every file on the single-file path; grouping is tested on its own."""
    monkeypatch.setattr(dialog_plan, "SMALL_DIALOG_CHARS", 0)


def test_files_are_translated_concurrently_with_aggregated_progress(single_files):
    manager, provider = _manager(THREE_ANSWERS, max_concurrent_requests=3)
    progress = _CountingProgress()

    translations, errors = manager.translate_dialogs(THREE_FILES, item_progress=progress)

    assert errors == []
    assert translations == _expected(
        THREE_FILES, {"Hello": "Привет", "Goodbye": "Прощай", "Thanks": "Спасибо"}
    )
    assert len(provider.calls) == 3
    assert progress.total == 3  # one budgeted item per file, none lost


def test_a_failing_file_is_isolated(single_files, monkeypatch):
    manager, _provider = _manager(
        {"Hello": '{"E1":"Привет"}', "Goodbye": '{"E3":"Прощай"}'}, max_concurrent_requests=2
    )
    boom = RuntimeError("prepare exploded")
    real_prepare = context_translator.prepare_dialog

    def failing_prepare(file_path, *args, **kwargs):
        if file_path.name == "bad.dlg":
            raise boom
        return real_prepare(file_path, *args, **kwargs)

    monkeypatch.setattr(context_translator, "prepare_dialog", failing_prepare)
    files = [
        _file("a.dlg", 1, "Hello"),
        _file("bad.dlg", 2, "Kaboom"),
        _file("b.dlg", 3, "Goodbye"),
    ]

    translations, errors = manager.translate_dialogs(files)

    assert translations == _expected(files, {"Hello": "Привет", "Goodbye": "Прощай"})
    assert [(path.name, exc) for path, exc in errors] == [("bad.dlg", boom)]


def test_cancellation_skips_the_queued_files(single_files):
    provider = DialogProvider({"Hello": '{"E1":"Привет"}', "Goodbye": '{"E2":"Прощай"}'})
    config = make_config(max_concurrent_requests=1, cancel_check=lambda: len(provider.calls) >= 1)
    manager = ContextualTranslationManager(config, provider, WorldContext())

    with pytest.raises(TranslationCancelled):
        manager.translate_dialogs([_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")])
    assert len(provider.calls) == 1  # the second file was never requested


def test_worker_threads_close_their_client_and_event_loop(single_files):
    class _LoopRecordingProvider(DialogProvider):
        def __init__(self, responses):
            super().__init__(responses)
            self.request_loops = set()
            self.closed_by = []

        async def complete_json_chat_async(self, *args, **kwargs):
            self.request_loops.add(asyncio.get_running_loop())
            return await super().complete_json_chat_async(*args, **kwargs)

        async def close_async_client(self):
            self.closed_by.append(threading.get_ident())

    provider = _LoopRecordingProvider(THREE_ANSWERS)
    manager = ContextualTranslationManager(
        make_config(max_concurrent_requests=2), provider, WorldContext()
    )

    translations, errors = manager.translate_dialogs(THREE_FILES)

    assert errors == [] and len(translations) == 3
    assert len(provider.closed_by) == 2  # one per worker thread
    assert threading.get_ident() not in provider.closed_by
    assert provider.request_loops and all(loop.is_closed() for loop in provider.request_loops)


class _Interrupt(BaseException):
    """Stands in for ``KeyboardInterrupt``, which pytest handles on its own."""


def _dialog_workers():
    return [thread for thread in threading.enumerate() if thread.name.startswith("dialog-")]


def test_an_interrupted_caller_still_waits_for_the_workers(single_files, monkeypatch):
    class _SlowProvider(DialogProvider):
        async def complete_json_chat_async(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            return await super().complete_json_chat_async(*args, **kwargs)

    real_join = threading.Thread.join
    interrupted = []

    def join(thread, timeout=None):
        if thread.name.startswith("dialog-") and not interrupted:
            interrupted.append(thread.name)
            raise _Interrupt  # like Ctrl+C while the calling thread waits
        real_join(thread, timeout)

    monkeypatch.setattr(threading.Thread, "join", join)
    manager = ContextualTranslationManager(
        make_config(max_concurrent_requests=2), _SlowProvider(THREE_ANSWERS), WorldContext()
    )

    with pytest.raises(_Interrupt):
        manager.translate_dialogs(THREE_FILES)
    assert interrupted and _dialog_workers() == []


def test_files_without_lines_report_their_budget(single_files):
    manager, provider = _manager({})
    progress = _CountingProgress()
    files = [
        (Path("empty.dlg"), dlg(_node(0, "  ")), 2),
        (Path("none.dlg"), {"StructType": "DLG"}, 3),
    ]

    assert manager.translate_dialogs(files, item_progress=progress) == ({}, [])
    assert progress.total == 5
    assert provider.calls == []
    assert manager.translate_dialogs([]) == ({}, [])


# ---------------------------------------------------------------------------
# Groups of small files
# ---------------------------------------------------------------------------

GROUP_ANSWER = '{"a.dlg": {"E1": "Привет"}, "b.dlg": {"E2": "Прощай"}}'
TWO_FILES = [_file("a.dlg", 1, "Hello"), _file("b.dlg", 2, "Goodbye")]
TWO_ANSWERS = {"Hello": "Привет", "Goodbye": "Прощай"}


def test_group_prompt_has_file_headers_and_scoped_speakers():
    world = WorldContext()
    world.npcs["sev_tag"] = NPCInfo("sev_tag", "Severina", "", "", "Dwarf", "Female", "severina")
    manager, provider = _manager(
        {"=== FILE:": '{"severina.dlg": {"E1": "Привет"}, "other.dlg": {"E2": "Прощай"}}'}, world
    )
    files = [_file("severina.dlg", 1, "Hello"), _file("other.dlg", 2, "Goodbye")]

    translations, errors = manager.translate_dialogs(files)

    assert (translations, errors) == (_expected(files, TWO_ANSWERS), [])
    (call,) = provider.calls
    assert "=== FILE: severina.dlg ===" in call["user_prompt"]
    assert "=== FILE: other.dlg ===" in call["user_prompt"]
    assert "do not let tone, wording, or context leak" in call["user_prompt"]
    assert "DIALOG SPEAKERS:" in call["system_prompt"]
    assert (
        "- In severina.dlg, lines marked [NPC]: spoken by Severina (Dwarf, Female)"
        in call["system_prompt"]
    )


def test_group_answer_is_split_back_into_files_with_progress_and_log_rows():
    writer = RecordingWriter()
    manager, _provider = _manager({"=== FILE:": GROUP_ANSWER}, translation_log_writer=writer)
    progress = _CountingProgress()

    translations, errors = manager.translate_dialogs(TWO_FILES, item_progress=progress)

    assert (translations, errors) == (_expected(TWO_FILES, TWO_ANSWERS), [])
    assert progress.total == 2
    by_file = {row["file"]: (row["original"], row["translated"]) for row in writer.rows()}
    assert by_file == {"a.dlg": ("Hello", "Привет"), "b.dlg": ("Goodbye", "Прощай")}


def test_file_missing_from_the_group_answer_is_translated_alone():
    manager, provider = _manager(['{"a.dlg": {"E1": "Привет"}}', '{"E2": "Прощай"}'])

    translations, errors = manager.translate_dialogs(TWO_FILES)

    assert (translations, errors) == (_expected(TWO_FILES, TWO_ANSWERS), [])
    group, single = _prompts(provider)
    assert "=== FILE:" in group and "=== FILE:" not in single and "b.dlg" in single


def test_equal_nodes_of_a_partial_group_keep_their_addresses():
    manager, provider = _manager(
        ['{"a.dlg": {"E1": "Первый"}, "b.dlg": {"E1": "Третий"}}', '{"E2": "Второй"}']
    )
    files = [
        (Path("a.dlg"), dlg(_node(1, "Same"), _node(2, "Same")), 2),
        _file("b.dlg", 1, "Same"),
    ]

    translations, errors = manager.translate_dialogs(files)

    assert not errors
    assert translations == {
        ("a.dlg", "a:entry:1"): "Первый",
        ("a.dlg", "a:entry:2"): "Второй",
        ("b.dlg", "b:entry:1"): "Третий",
    }
    retry = _prompts(provider)[1]
    assert "[E2]" in retry and "[E1]" not in retry


def test_group_that_never_parses_falls_back_to_single_files(caplog):
    caplog.set_level(logging.WARNING)
    manager, provider = _manager(
        ["not-json-at-all", "still-not-json", '{"E1": "Привет"}', '{"E2": "Прощай"}']
    )

    translations, errors = manager.translate_dialogs(TWO_FILES)

    assert (translations, errors) == (_expected(TWO_FILES, TWO_ANSWERS), [])
    assert len(provider.calls) == 4
    assert "invalid JSON; retrying with repair prompt" in caplog.text
    assert "a.dlg, b.dlg" in _prompts(provider)[1]
    assert "falling back to single-file translation" in caplog.text


def test_truncated_group_answer_is_requested_again_unchanged():
    manager, provider = _manager(['{"a.dlg": {"E1": "При', GROUP_ANSWER])

    translations, errors = manager.translate_dialogs(TWO_FILES)

    assert (translations, errors) == (_expected(TWO_FILES, TWO_ANSWERS), [])
    first, second = provider.calls
    assert first["user_prompt"] == second["user_prompt"]
    assert first["system_prompt"] == second["system_prompt"]
    assert second["max_tokens"] == RECOVERY


def test_group_rate_limit_fails_the_files_without_a_fallback():
    limit = RateLimitError("budget exhausted")
    manager, provider = _manager([limit])
    progress = _CountingProgress()
    files = [_file("a.dlg", 1, "Hello", 2), _file("b.dlg", 2, "Goodbye", 3)]

    translations, errors = manager.translate_dialogs(files, item_progress=progress)

    assert translations == {}
    assert [(path.name, exc) for path, exc in errors] == [("a.dlg", limit), ("b.dlg", limit)]
    assert manager.failed_items == {("a.dlg", "a:entry:1"), ("b.dlg", "b:entry:2")}
    assert len(provider.calls) == 1
    assert progress.total == 5


def test_failed_group_request_falls_back_to_single_files(caplog):
    caplog.set_level(logging.WARNING)
    manager, provider = _manager(
        [RuntimeError("group exploded"), '{"E1": "Привет"}', '{"E2": "Прощай"}']
    )
    progress = _CountingProgress()

    translations, errors = manager.translate_dialogs(TWO_FILES, item_progress=progress)

    assert (translations, errors) == (_expected(TWO_FILES, TWO_ANSWERS), [])
    assert "request failed (group exploded); falling back to single files" in caplog.text
    assert [("=== FILE:" in prompt) for prompt in _prompts(provider)] == [True, False, False]
    assert progress.total == 2


def test_a_raising_group_fallback_is_reported_per_file(monkeypatch):
    boom = RuntimeError("speakers exploded")

    def broken_speakers(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(context_translator, "speaker_lines", broken_speakers)
    manager, provider = _manager([])

    translations, errors = manager.translate_dialogs(TWO_FILES)

    assert translations == {}
    assert [(path.name, exc) for path, exc in errors] == [("a.dlg", boom), ("b.dlg", boom)]
    assert provider.calls == []


def test_an_exception_escaping_a_single_file_job_fails_that_file(single_files, monkeypatch):
    boom = RuntimeError("speakers exploded")
    real_speakers = context_translator.speaker_lines

    def speakers(world, stem, *args, **kwargs):
        if stem == "bad":
            raise boom
        return real_speakers(world, stem, *args, **kwargs)

    monkeypatch.setattr(context_translator, "speaker_lines", speakers)
    manager, _provider = _manager(THREE_ANSWERS)
    files = [
        _file("a.dlg", 1, "Hello"),
        _file("bad.dlg", 2, "Goodbye"),
        _file("c.dlg", 3, "Thanks"),
    ]

    translations, errors = manager.translate_dialogs(files)

    assert translations == _expected([files[0], files[2]], {"Hello": "Привет", "Thanks": "Спасибо"})
    assert [(path.name, exc) for path, exc in errors] == [("bad.dlg", boom)]


def test_large_file_stays_single_while_small_files_group():
    big_text = "Long line of dialog text. " * 60  # longer than SMALL_DIALOG_CHARS
    manager, provider = _manager(
        {"=== FILE:": GROUP_ANSWER, "Long line of dialog": '{"E3": "Длинная строка"}'}
    )
    files = [*TWO_FILES, _file("big.dlg", 3, big_text)]

    translations, errors = manager.translate_dialogs(files)

    assert errors == []
    assert translations == _expected(files, {**TWO_ANSWERS, big_text: "Длинная строка"})
    assert len(provider.calls) == 2
    assert sum("=== FILE:" in prompt for prompt in _prompts(provider)) == 1


# ---------------------------------------------------------------------------
# Speakers in the dialog prompt
# ---------------------------------------------------------------------------


def test_dialog_request_names_the_speakers_of_its_file():
    world = WorldContext()
    world.npcs["sev_tag"] = NPCInfo("sev_tag", "Severina", "", "", "Dwarf", "Female", "severina")
    manager, provider = _manager(['{"E1":"Привет"}'] * 2, world)

    assert _translate(manager, "severina.dlg", _hello()) == {
        ("severina.dlg", "severina:entry:1"): "Привет"
    }
    system_prompt = provider.calls[0]["system_prompt"]
    assert "DIALOG SPEAKERS:" in system_prompt
    assert "Severina (Dwarf, Female)" in system_prompt

    _translate(manager, "unrelated.dlg", _hello())
    assert "DIALOG SPEAKERS:" not in provider.calls[1]["system_prompt"]
