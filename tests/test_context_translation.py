"""Tests for contextual dialog retry ordering."""

import logging
from pathlib import Path

from src.nwn_translator.config import TRANSLATION_MAX_TOKENS, TranslationConfig
from src.nwn_translator.context.world_context import WorldContext
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
    monkeypatch.setattr(context_module, "translation_log_writer_for_config", lambda *_a, **_k: _NullWriter())


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
    assert "The previous answer for test.dlg was not valid JSON or was truncated." in provider.calls[1]["user_prompt"]
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
    assert "changed, dropped, or omitted preserved NWN tags/tokens" in provider.calls[1]["user_prompt"]
    assert "pending dialog retry JSON looks truncated" in caplog.text
