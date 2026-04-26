"""Tests for numbered short-label template batching."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.nwn_translator.ai_providers import TranslationResult
from src.nwn_translator.config import TranslationConfig
from src.nwn_translator.extractors import ExtractedContent, TranslatableItem
from src.nwn_translator.translators.translation_manager import TranslationManager


def _config():
    return TranslationConfig(
        api_key="test",
        source_lang="english",
        target_lang="russian",
        quiet=True,
        max_concurrent_requests=1,
    )


def _provider():
    provider = SimpleNamespace(model="fake/model")
    provider.close_async_client = AsyncMock(return_value=None)

    async def gate(entries, *, source_lang):
        return {str(entry["key"]): {"translate": True, "reason": "test"} for entry in entries}

    provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
    return provider


def test_numbered_labels_translate_one_template_and_restore_numbers():
    provider = _provider()

    async def translate_batch_async(
        items, source_lang, target_lang, glossary_block=None, content_profile=None
    ):
        assert [item.original for item in items] == ["Food __NWN_NUMBER__"]
        return [
            TranslationResult(
                translated="Еда __NWN_NUMBER__",
                original=items[0].original,
                success=True,
            )
        ]

    provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
    provider.translate_async = AsyncMock()
    manager = TranslationManager(_config(), provider)
    content = ExtractedContent(
        content_type="test",
        source_file=None,
        items=[
            TranslatableItem("Food 1", metadata={"type": "item_name"}),
            TranslatableItem("Food 005", metadata={"type": "item_name"}),
        ],
    )

    translations = manager.translate_content(content)

    assert translations["Food 1"] == "Еда 1"
    assert translations["Food 005"] == "Еда 005"
    provider.translate_batch_async.assert_called_once()
    provider.translate_async.assert_not_called()


def test_numbered_template_missing_placeholder_falls_back_individually():
    provider = _provider()
    provider.translate_batch_async = AsyncMock(
        return_value=[
            TranslationResult(translated="Еда", original="Food __NWN_NUMBER__", success=True)
        ]
    )
    provider.translate_async = AsyncMock(
        side_effect=lambda **kw: TranslationResult(
            translated="RU:" + kw["text"],
            original=kw["text"],
            success=True,
        )
    )
    manager = TranslationManager(_config(), provider)
    content = ExtractedContent(
        content_type="test",
        source_file=None,
        items=[TranslatableItem("Food 1", metadata={"type": "item_name"})],
    )

    translations = manager.translate_content(content)

    assert translations["Food 1"] == "RU:Food 1"
    assert provider.translate_async.call_count == 1


def test_unsafe_numbered_sentence_does_not_template():
    assert (
        TranslationManager._numbered_template_for_item(
            {
                "sanitized": "Quest 1",
                "item": TranslatableItem("Quest 1", metadata={"type": "item_name"}),
            }
        )
        is None
    )


def test_numbered_proper_name_does_not_template():
    assert (
        TranslationManager._numbered_template_for_item(
            {
                "sanitized": "Quite Long Proper Name 00",
                "item": TranslatableItem(
                    "Quite Long Proper Name 00",
                    metadata={"type": "item_name"},
                ),
            }
        )
        is None
    )
