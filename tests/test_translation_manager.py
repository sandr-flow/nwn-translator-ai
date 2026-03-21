"""Unit tests for TranslationManager."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.nwn_translator.config import TranslationConfig
from src.nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from src.nwn_translator.translators.translation_manager import TranslationManager


@dataclass
class TranslationResult:
    """Minimal stub matching ai_providers.base.TranslationResult."""

    translated: str
    original: str
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _make_config(**kwargs) -> TranslationConfig:
    """Return a minimal TranslationConfig with sensible defaults."""
    defaults = dict(
        api_key="test-key",
        model="deepseek/deepseek-v3.2",
        source_lang="english",
        target_lang="russian",
        input_file=Path("test.mod"),
    )
    defaults.update(kwargs)
    return TranslationConfig(**defaults)


def _make_provider(translations: dict) -> Mock:
    """Return a mock provider that translates via the given dict."""
    provider = Mock()

    def translate(text, source_lang, target_lang, context=None, glossary_block=None):
        translated = translations.get(text, text)
        return TranslationResult(translated=translated, original=text, success=True)

    provider.translate.side_effect = translate

    async def translate_async(
        text, source_lang, target_lang, context=None, glossary_block=None
    ):
        return translate(text, source_lang, target_lang, context, glossary_block)

    provider.translate_async = AsyncMock(side_effect=translate_async)
    return provider


class TestTranslateContent:
    """Tests for TranslationManager.translate_content()."""

    def test_dialog_all_items_translated(self):
        """All dialog nodes must be translated, not just the first one."""
        items = [
            TranslatableItem(text="Hello!", context="NPC line", item_id="dlg:entry:0"),
            TranslatableItem(text="Who are you?", context="NPC line", item_id="dlg:entry:1"),
            TranslatableItem(text="Just passing.", context="Player reply", item_id="dlg:reply:0"),
        ]
        content = ExtractedContent(
            content_type="dialog",
            items=items,
            source_file=Path("test.dlg"),
        )
        translations_map = {
            "Hello!": "Привет!",
            "Who are you?": "Кто ты?",
            "Just passing.": "Просто мимо.",
        }

        provider = _make_provider(translations_map)
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert len(result) == 3
        assert result["Hello!"] == "Привет!"
        assert result["Who are you?"] == "Кто ты?"
        assert result["Just passing."] == "Просто мимо."
        # Provider must have been called once per item (async path)
        assert provider.translate_async.call_count == 3

    def test_dialog_empty_items_returns_empty(self):
        """Empty dialog must return empty translation map."""
        content = ExtractedContent(
            content_type="dialog",
            items=[],
            source_file=Path("empty.dlg"),
        )
        provider = _make_provider({})
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)
        assert result == {}
        provider.translate.assert_not_called()
        provider.translate_async.assert_not_called()

    def test_non_dialog_items_translated(self):
        """Non-dialog content types also get all items translated."""
        items = [
            TranslatableItem(text="Sword of Fire", item_id="item:name"),
            TranslatableItem(text="A blazing blade.", item_id="item:desc"),
        ]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("sword.uti"),
        )
        translations_map = {
            "Sword of Fire": "Огненный меч",
            "A blazing blade.": "Пылающий клинок.",
        }
        provider = _make_provider(translations_map)
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert len(result) == 2
        assert result["Sword of Fire"] == "Огненный меч"


class TestTranslationCache:
    """Tests for translation deduplication (step 3.2 — not yet implemented)."""

    def test_statistics_increment(self):
        """items_translated statistic must match actual successful translations."""
        items = [TranslatableItem(text="Hello", item_id="x:0")]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("test.uti"),
        )
        provider = _make_provider({"Hello": "Привет"})
        manager = TranslationManager(_make_config(), provider)
        manager.translate_content(content)

        stats = manager.get_statistics()
        assert stats["items_translated"] == 1
        assert stats["total_errors"] == 0

    def test_failed_translation_recorded_as_error(self):
        """When provider returns success=False the error must be recorded."""
        items = [TranslatableItem(text="Boom", item_id="x:0")]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("test.uti"),
        )
        provider = Mock()
        fail = TranslationResult(
            translated="", original="Boom", success=False, error="API error"
        )
        provider.translate.return_value = fail
        provider.translate_async = AsyncMock(return_value=fail)
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result == {}
        stats = manager.get_statistics()
        assert stats["total_errors"] == 1
