"""Tests for contextual dialog retry ordering."""

import logging
import threading
from pathlib import Path

import pytest

from src.nwn_translator.config import (
    TRANSLATION_MAX_TOKENS,
    TranslationCancelled,
    TranslationConfig,
)
from src.nwn_translator.context.world_context import NPCInfo, WorldContext
from src.nwn_translator.extractors.base import DialogNode
from src.nwn_translator.translators import context_translator as context_module
from src.nwn_translator.translators.context_translator import (
    ContextualTranslationManager,
    _DIALOG_TRUNCATION_MAX_TOKENS,
)


def _make_config(**kwargs) -> TranslationConfig:
    defaults = dict(
        api_key="test-key",
        model="fake/model",
        source_lang="english",
        target_lang="russian",
        input_file=Path("input.mod"),
    )
    defaults.update(kwargs)
    return TranslationConfig(**defaults)


class _NullWriter:
    def write(self, _entry):
        return None


class _FakeOpenRouter:
    model = "fake/model"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def make_system_message_content(self, stable, variable, stable_suffix=None):
        parts = [stable]
        if stable_suffix:
            parts.append(stable_suffix)
        if variable:
            parts.append(variable)
        return "\n\n".join(p for p in parts if p)

    async def complete_json_chat_async(
        self,
        system_prompt,
        user_prompt,
        *,
        max_tokens=0,
        temperature=0.0,
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
        return self._responses.pop(0)

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


class _FakeDialogExtractor:
    def __init__(self, tree):
        self._tree = tree

    def build_dialog_tree(self, _parsed_data):
        return self._tree


def _patch_dialog_environment(monkeypatch, tree):
    monkeypatch.setattr(context_module, "OpenRouterProvider", _FakeOpenRouter)
    monkeypatch.setattr(context_module, "DialogExtractor", lambda: _FakeDialogExtractor(tree))
    monkeypatch.setattr(
        context_module, "translation_log_writer_for_config", lambda *_a, **_k: _NullWriter()
    )


def test_initial_invalid_json_truncation_retries_original_prompt_first(monkeypatch, caplog):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    _patch_dialog_environment(monkeypatch, tree)
    provider = _FakeOpenRouter(
        [
            '{"E1":"broken',
            '{"E1":"Привет"}',
        ]
    )
    manager = ContextualTranslationManager(
        _make_config(),
        provider,
        WorldContext(),
    )

    caplog.set_level(logging.WARNING)
    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {"Hello there": "Привет"}
    assert len(provider.calls) == 2
    assert provider.calls[0]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[1]["max_tokens"] == _DIALOG_TRUNCATION_MAX_TOKENS
    assert provider.calls[0]["user_prompt"] == provider.calls[1]["user_prompt"]
    assert "was not valid JSON or was truncated" not in provider.calls[1]["user_prompt"]
    assert "truncation-like invalid JSON" in caplog.text


def test_initial_invalid_json_non_truncation_uses_repair_prompt(monkeypatch, caplog):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    _patch_dialog_environment(monkeypatch, tree)
    provider = _FakeOpenRouter(
        [
            "not-json-at-all",
            '{"E1":"Привет"}',
        ]
    )
    manager = ContextualTranslationManager(
        _make_config(),
        provider,
        WorldContext(),
    )

    caplog.set_level(logging.WARNING)
    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {"Hello there": "Привет"}
    assert len(provider.calls) == 2
    assert provider.calls[0]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[1]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[0]["user_prompt"] != provider.calls[1]["user_prompt"]
    assert (
        "The previous answer for test.dlg was not valid JSON or was truncated."
        in provider.calls[1]["user_prompt"]
    )
    assert "non-truncation invalid JSON" in caplog.text


def test_pending_keys_truncation_retries_same_retry_prompt_with_higher_tokens(monkeypatch, caplog):
    tree = [
        DialogNode(
            node_id=1,
            text="Hello there",
            is_entry=True,
            replies=[DialogNode(node_id=2, text="Who are you?", is_entry=False)],
        )
    ]
    _patch_dialog_environment(monkeypatch, tree)
    provider = _FakeOpenRouter(
        [
            '{"E1":"Привет"}',
            '{"R2":"Кто т',
            '{"R2":"Кто ты?"}',
        ]
    )
    manager = ContextualTranslationManager(
        _make_config(),
        provider,
        WorldContext(),
    )

    caplog.set_level(logging.WARNING)
    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {
        "Hello there": "Привет",
        "Who are you?": "Кто ты?",
    }
    assert len(provider.calls) == 3
    assert provider.calls[1]["max_tokens"] == TRANSLATION_MAX_TOKENS
    assert provider.calls[2]["max_tokens"] == _DIALOG_TRUNCATION_MAX_TOKENS
    assert provider.calls[1]["user_prompt"] == provider.calls[2]["user_prompt"]
    assert (
        "changed, dropped, or omitted preserved NWN tags/tokens" in provider.calls[1]["user_prompt"]
    )
    assert "pending dialog retry JSON looks truncated" in caplog.text


def test_large_dialog_is_translated_in_chunks(monkeypatch):
    tree = [
        DialogNode(
            node_id=1,
            text="Hello there, traveler.",
            is_entry=True,
            replies=[
                DialogNode(node_id=2, text="Who are you?", is_entry=False),
                DialogNode(node_id=3, text="Goodbye.", is_entry=False),
            ],
        )
    ]
    _patch_dialog_environment(monkeypatch, tree)
    monkeypatch.setattr(context_module, "_DIALOG_CHUNK_MAX_KEYS", 1)
    provider = _FakeOpenRouter(
        [
            '{"E1":"Привет, путник."}',
            '{"R2":"Кто ты?"}',
            '{"R3":"Прощай."}',
        ]
    )
    manager = ContextualTranslationManager(
        _make_config(),
        provider,
        WorldContext(),
    )

    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {
        "Hello there, traveler.": "Привет, путник.",
        "Who are you?": "Кто ты?",
        "Goodbye.": "Прощай.",
    }
    assert len(provider.calls) == 3
    assert "[E1]" in provider.calls[0]["user_prompt"]
    assert "[R2]" not in provider.calls[0]["user_prompt"]
    assert "[R2]" in provider.calls[1]["user_prompt"]
    assert "[R3]" not in provider.calls[1]["user_prompt"]
    assert "[R3]" in provider.calls[2]["user_prompt"]


def test_chunked_dialog_retries_missing_keys_after_merge(monkeypatch):
    tree = [
        DialogNode(
            node_id=1,
            text="Hello there",
            is_entry=True,
            replies=[DialogNode(node_id=2, text="Who are you?", is_entry=False)],
        )
    ]
    _patch_dialog_environment(monkeypatch, tree)
    monkeypatch.setattr(context_module, "_DIALOG_CHUNK_MAX_KEYS", 1)
    provider = _FakeOpenRouter(
        [
            '{"E1":"Привет"}',
            "{}",
            '{"R2":"Кто ты?"}',
        ]
    )
    manager = ContextualTranslationManager(
        _make_config(),
        provider,
        WorldContext(),
    )

    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {
        "Hello there": "Привет",
        "Who are you?": "Кто ты?",
    }
    assert len(provider.calls) == 3
    assert "[E1]" in provider.calls[0]["user_prompt"]
    assert "[R2]" in provider.calls[1]["user_prompt"]
    assert (
        "changed, dropped, or omitted preserved NWN tags/tokens" in provider.calls[2]["user_prompt"]
    )
    assert "keys exactly R2" in provider.calls[2]["user_prompt"]


def test_cancel_before_first_chunk_raises_without_api_calls(monkeypatch):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    _patch_dialog_environment(monkeypatch, tree)
    provider = _FakeOpenRouter([])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: True),
        provider,
        WorldContext(),
    )

    with pytest.raises(TranslationCancelled):
        manager.translate_dialog(Path("test.dlg"), parsed_data={})
    assert provider.calls == []


def test_cancel_between_chunks_stops_run_and_propagates(monkeypatch):
    tree = [
        DialogNode(
            node_id=1,
            text="Hello there, traveler.",
            is_entry=True,
            replies=[DialogNode(node_id=2, text="Who are you?", is_entry=False)],
        )
    ]
    _patch_dialog_environment(monkeypatch, tree)
    monkeypatch.setattr(context_module, "_DIALOG_CHUNK_MAX_KEYS", 1)
    provider = _FakeOpenRouter(['{"E1":"Привет, путник."}', '{"R2":"Кто ты?"}'])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: len(provider.calls) >= 1),
        provider,
        WorldContext(),
    )

    with pytest.raises(TranslationCancelled):
        manager.translate_dialog(Path("test.dlg"), parsed_data={})
    assert len(provider.calls) == 1  # second chunk was never requested


def test_generic_api_error_still_degrades_to_partial_result(monkeypatch, caplog):
    tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
    _patch_dialog_environment(monkeypatch, tree)

    class _ExplodingProvider(_FakeOpenRouter):
        async def complete_json_chat_async(self, *args, **kwargs):
            raise RuntimeError("network exploded")

    provider = _ExplodingProvider([])
    manager = ContextualTranslationManager(
        _make_config(cancel_check=lambda: False),
        provider,
        WorldContext(),
    )

    caplog.set_level(logging.ERROR)
    result = manager.translate_dialog(Path("test.dlg"), parsed_data={})

    assert result == {}
    assert "Contextual translation failed" in caplog.text


class _KeyedFakeOpenRouter(_FakeOpenRouter):
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
        max_tokens=0,
        temperature=0.0,
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


class _ParsedDataDialogExtractor:
    """Read the fake tree from parsed_data so each file can differ."""

    def build_dialog_tree(self, parsed_data):
        return parsed_data["tree"]


class TestTranslateDialogs:
    """Concurrent multi-file dialog orchestration."""

    @staticmethod
    def _patch_env(monkeypatch):
        monkeypatch.setattr(context_module, "OpenRouterProvider", _FakeOpenRouter)
        monkeypatch.setattr(context_module, "DialogExtractor", _ParsedDataDialogExtractor)
        monkeypatch.setattr(
            context_module, "translation_log_writer_for_config", lambda *_a, **_k: _NullWriter()
        )

    @staticmethod
    def _file(name, node_id, text):
        tree = [DialogNode(node_id=node_id, text=text, is_entry=True)]
        return (Path(name), {"tree": tree}, 1)

    def test_aggregates_translations_across_files(self, monkeypatch):
        self._patch_env(monkeypatch)
        provider = _KeyedFakeOpenRouter(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
                "Thanks": '{"E3":"Спасибо"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=3),
            provider,
            WorldContext(),
        )
        files = [
            self._file("a.dlg", 1, "Hello"),
            self._file("b.dlg", 2, "Goodbye"),
            self._file("c.dlg", 3, "Thanks"),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert errors == []
        assert translations == {"Hello": "Привет", "Goodbye": "Прощай", "Thanks": "Спасибо"}
        assert len(provider.calls) == 3

    def test_file_error_is_isolated(self, monkeypatch):
        self._patch_env(monkeypatch)
        provider = _KeyedFakeOpenRouter(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E3":"Прощай"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=2),
            provider,
            WorldContext(),
        )
        boom = RuntimeError("prepare exploded")
        real_prepare = manager._prepare_dialog

        def failing_prepare(file_path, parsed_data):
            if file_path.name == "bad.dlg":
                raise boom
            return real_prepare(file_path, parsed_data)

        monkeypatch.setattr(manager, "_prepare_dialog", failing_prepare)
        files = [
            self._file("a.dlg", 1, "Hello"),
            self._file("bad.dlg", 2, "Kaboom"),
            self._file("b.dlg", 3, "Goodbye"),
        ]

        translations, errors = manager.translate_dialogs(files)

        assert translations == {"Hello": "Привет", "Goodbye": "Прощай"}
        assert [(path.name, exc) for path, exc in errors] == [("bad.dlg", boom)]

    def test_cancellation_aborts_pool_and_skips_queued_files(self, monkeypatch):
        self._patch_env(monkeypatch)
        provider = _KeyedFakeOpenRouter(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(
                max_concurrent_requests=1,
                cancel_check=lambda: len(provider.calls) >= 1,
            ),
            provider,
            WorldContext(),
        )
        files = [
            self._file("a.dlg", 1, "Hello"),
            self._file("b.dlg", 2, "Goodbye"),
        ]

        with pytest.raises(TranslationCancelled):
            manager.translate_dialogs(files)
        assert len(provider.calls) == 1  # second file was never requested

    def test_concurrent_progress_bumps_are_aggregated(self, monkeypatch):
        self._patch_env(monkeypatch)

        class _CountingProgress:
            def __init__(self):
                self.total = 0
                self._lock = threading.Lock()

            def bump(self, by=1, filename=None):
                with self._lock:
                    self.total += by

        provider = _KeyedFakeOpenRouter(
            {
                "Hello": '{"E1":"Привет"}',
                "Goodbye": '{"E2":"Прощай"}',
                "Thanks": '{"E3":"Спасибо"}',
            }
        )
        manager = ContextualTranslationManager(
            _make_config(max_concurrent_requests=3),
            provider,
            WorldContext(),
        )
        progress = _CountingProgress()
        files = [
            self._file("a.dlg", 1, "Hello"),
            self._file("b.dlg", 2, "Goodbye"),
            self._file("c.dlg", 3, "Thanks"),
        ]

        translations, errors = manager.translate_dialogs(files, item_progress=progress)

        assert errors == []
        assert len(translations) == 3
        assert progress.total == 3  # one budgeted item per file, none lost

    def test_empty_file_list_returns_empty(self, monkeypatch):
        self._patch_env(monkeypatch)
        manager = ContextualTranslationManager(
            _make_config(),
            _KeyedFakeOpenRouter({}),
            WorldContext(),
        )

        assert manager.translate_dialogs([]) == ({}, [])


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

    def _manager(self, world: WorldContext) -> ContextualTranslationManager:
        return ContextualTranslationManager(
            _make_config(),
            _FakeOpenRouter([]),
            world,
        )

    def test_owner_and_tagged_speakers_resolved(self):
        manager = self._manager(self._world_with_npcs())
        node_map = {
            "E0": DialogNode(node_id=0, text="Hello", is_entry=True),
            "E1": DialogNode(node_id=1, text="Hmpf", speaker="stumpy_tag", is_entry=True),
            "R0": DialogNode(node_id=0, text="Hi", is_entry=False),
        }

        block = manager._build_speakers_block("Severina", node_map)

        assert block.startswith("DIALOG SPEAKERS:")
        assert "- Lines marked [NPC]: spoken by Severina (Dwarf, Female)" in block
        assert "- Lines marked [stumpy_tag]: spoken by Stumpy (Dwarf, Male)" in block
        assert "grammatical forms" in block

    def test_empty_when_no_speaker_matches(self):
        manager = self._manager(self._world_with_npcs())
        node_map = {"E0": DialogNode(node_id=0, text="Hello", is_entry=True)}

        assert manager._build_speakers_block("unrelated", node_map) == ""

    def test_empty_without_world_npcs(self):
        manager = self._manager(WorldContext())
        node_map = {"E0": DialogNode(node_id=0, text="Hello", is_entry=True)}

        assert manager._build_speakers_block("severina", node_map) == ""

    def test_translate_dialog_injects_block_into_system_prompt(self, monkeypatch):
        tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
        _patch_dialog_environment(monkeypatch, tree)
        provider = _FakeOpenRouter(['{"E1":"Привет"}'])
        manager = ContextualTranslationManager(
            _make_config(),
            provider,
            self._world_with_npcs(),
        )

        result = manager.translate_dialog(Path("severina.dlg"), parsed_data={})

        assert result == {"Hello there": "Привет"}
        system_prompt = provider.calls[0]["system_prompt"]
        assert "DIALOG SPEAKERS:" in system_prompt
        assert "Severina (Dwarf, Female)" in system_prompt

    def test_translate_dialog_without_matching_npc_omits_block(self, monkeypatch):
        tree = [DialogNode(node_id=1, text="Hello there", is_entry=True)]
        _patch_dialog_environment(monkeypatch, tree)
        provider = _FakeOpenRouter(['{"E1":"Привет"}'])
        manager = ContextualTranslationManager(
            _make_config(),
            provider,
            self._world_with_npcs(),
        )

        manager.translate_dialog(Path("unrelated.dlg"), parsed_data={})

        assert "DIALOG SPEAKERS:" not in provider.calls[0]["system_prompt"]
