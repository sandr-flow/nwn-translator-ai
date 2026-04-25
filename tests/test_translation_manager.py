"""Unit tests for TranslationManager."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock

import pytest

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

    def translate(
        text,
        source_lang,
        target_lang,
        context=None,
        glossary_block=None,
        content_profile=None,
    ):
        translated = translations.get(text, text)
        return TranslationResult(translated=translated, original=text, success=True)

    provider.translate.side_effect = translate

    async def translate_async(
        text,
        source_lang,
        target_lang,
        context=None,
        glossary_block=None,
        content_profile=None,
    ):
        return translate(text, source_lang, target_lang, context, glossary_block)

    provider.translate_async = AsyncMock(side_effect=translate_async)
    provider.close_async_client = AsyncMock(return_value=None)

    # Default gate: approve every entry. Tests that care about rejection can
    # override provider.classify_ncs_translate_gate_batch_async after creation.
    async def gate_approve_all(entries, *, source_lang):
        return {str(e["key"]): {"translate": True, "reason": "test_approve"} for e in entries}

    provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_approve_all)
    return provider


def _make_ncs_item(
    text: str,
    *,
    item_id: str = "script:off_20",
    needs_llm_gate: bool = False,
    confidence: str = "high",
    hint: str = "SpeakString",
    offset: int = 0x20,
) -> TranslatableItem:
    return TranslatableItem(
        text=text,
        context=f"NCS string; hint={hint}",
        item_id=item_id,
        location="script.ncs",
        metadata={
            "type": "ncs_string",
            "offset": offset,
            "confidence": confidence,
            "needs_llm_gate": needs_llm_gate,
            "ncs_hint": hint,
        },
    )


class CapturingWriter:
    def __init__(self) -> None:
        self.entries = []

    def write(self, entry: Dict[str, Any]) -> None:
        self.entries.append(entry)


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


class TestNcsFailClosed:
    """NCS fail-closed policy and diagnostics."""

    def test_high_confidence_player_facing_ncs_is_translated_by_item_id(self):
        item = _make_ncs_item("Look out, behind you!")
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        provider = _make_provider({"Look out, behind you!": "RU: Look out, behind you!"})
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(content)

        assert result == {"Look out, behind you!": "RU: Look out, behind you!"}
        assert manager.ncs_translations_by_item_id == {item.item_id: "RU: Look out, behind you!"}
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["approved"] == 1
        assert stats["translated"] == 1
        assert stats["skipped_fail_closed"] == 0

    def test_ambiguous_ncs_rejected_by_llm_gate_skips_translation(self):
        item = _make_ncs_item(
            "Something happened nearby.",
            needs_llm_gate=True,
            confidence="medium",
            hint="ambiguous_bytecode",
        )
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        provider = _make_provider({"Something happened nearby.": "RU"})

        async def gate_reject(entries, *, source_lang):
            return {
                str(e["key"]): {"translate": False, "reason": "ambiguous_conservative"}
                for e in entries
            }

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_reject)
        writer = CapturingWriter()
        manager = TranslationManager(_make_config(translation_log_writer=writer), provider)

        result = manager.translate_content(content)

        assert result == {}
        provider.translate_async.assert_not_called()
        assert manager.ncs_translations_by_item_id == {}
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["total"] == 1
        assert stats["extracted"] == 1
        assert stats["skipped_fail_closed"] == 1
        assert stats["approved"] == 0
        assert stats["samples"][0]["reason"] == "gate_rejected:ambiguous_conservative"
        diagnostic_entries = [
            entry for entry in writer.entries if entry.get("event") == "ncs_diagnostic"
        ]
        assert len(diagnostic_entries) == 1
        assert "original" not in diagnostic_entries[0]

    def test_file_log_writer_records_ncs_diagnostic_jsonl_without_original(self, tmp_path):
        item = _make_ncs_item(
            "Something happened nearby.",
            needs_llm_gate=True,
            confidence="medium",
            hint="ambiguous_bytecode",
        )
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        log_path = tmp_path / "translations.jsonl"
        provider = _make_provider({item.text: "RU"})

        async def gate_reject(entries, *, source_lang):
            return {
                str(e["key"]): {"translate": False, "reason": "ambiguous_conservative"}
                for e in entries
            }

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_reject)
        manager = TranslationManager(_make_config(translation_log=log_path), provider)

        manager.translate_content(content)

        records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert records == [
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
        assert "original" not in records[0]

    def test_hard_veto_wins_even_when_ncs_gate_is_skipped(self):
        item = _make_ncs_item(
            "DetermineClassToUse: This character is invalid.",
            needs_llm_gate=True,
            confidence="low",
            hint="ambiguous_bytecode",
        )
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        provider = _make_provider({item.text: "RU"})
        manager = TranslationManager(_make_config(skip_ncs_llm_gate=True), provider)

        result = manager.translate_content(content)

        assert result == {}
        provider.translate_async.assert_not_called()
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["skipped_hard_veto"] == 1
        assert stats["approved"] == 0
        assert stats["samples"][0]["reason"] == "code_identifier"

    def test_timeout_on_approved_ncs_item_gets_one_minimal_retry(self):
        item = _make_ncs_item("The seal is breaking!")
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        calls = []

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            calls.append(
                {
                    "text": text,
                    "context": context,
                    "glossary_block": glossary_block,
                    "content_profile": content_profile,
                }
            )
            if len(calls) == 1:
                await asyncio.sleep(1)
            return TranslationResult(translated="RU: The seal is breaking!", original=text)

        provider = Mock()
        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        async def gate_approve_all(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test_approve"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_approve_all)
        manager = TranslationManager(_make_config(), provider)
        manager._ITEM_TIMEOUT = 0.01
        manager._GATHER_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 2.0

        result = manager.translate_content(content)

        assert result == {"The seal is breaking!": "RU: The seal is breaking!"}
        assert provider.translate_async.call_count == 2
        assert "NCS timeout fallback" in calls[1]["context"]
        assert calls[1]["glossary_block"] is None
        assert manager.ncs_translations_by_item_id == {item.item_id: "RU: The seal is breaking!"}
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["timeout"] == 1
        assert stats["retry_recovered"] == 1
        assert stats["translated"] == 1

    def test_failed_ncs_timeout_retry_records_diagnostics_without_patchable_item(self):
        item = _make_ncs_item("The portal resists you!")
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            await asyncio.sleep(1)
            return TranslationResult(translated="RU", original=text)

        provider = Mock()
        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        async def gate_approve_all(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test_approve"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_approve_all)
        manager = TranslationManager(_make_config(), provider)
        manager._ITEM_TIMEOUT = 0.01
        manager._GATHER_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 2.0

        result = manager.translate_content(content)

        assert result == {}
        assert manager.ncs_translations_by_item_id == {}
        assert provider.translate_async.call_count == 2
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["timeout"] == 1
        assert stats["failed"] == 1
        assert stats["retry_recovered"] == 0
        reasons = {sample["reason"] for sample in stats["samples"]}
        assert "translation_timeout_retry_failed" in reasons
        assert "translation_failed" in reasons


class TestTranslationCache:
    """Tests for translation deduplication and statistics."""

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
        fail = TranslationResult(translated="", original="Boom", success=False, error="API error")
        provider.translate.return_value = fail
        provider.translate_async = AsyncMock(return_value=fail)
        provider.close_async_client = AsyncMock(return_value=None)
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result == {}
        stats = manager.get_statistics()
        assert stats["total_errors"] == 1


class TestPassthroughEmptyAfterSanitize:
    """Strings with no translatable content skip the API entirely."""

    def test_token_only_item_bypasses_provider(self):
        """A string like '<FirstName>' sanitizes to only placeholders -> no API."""
        items = [TranslatableItem(text="<FirstName>", item_id="t:0")]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("nothing.uti"),
        )
        provider = _make_provider({})
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result["<FirstName>"] == "<FirstName>"
        provider.translate_async.assert_not_called()

    def test_punctuation_only_item_bypasses_provider(self):
        """Pure punctuation/whitespace also short-circuits the API."""
        items = [TranslatableItem(text="... - !", item_id="t:1")]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("nothing.uti"),
        )
        provider = _make_provider({})
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result["... - !"] == "... - !"
        provider.translate_async.assert_not_called()

    def test_real_text_still_goes_to_provider(self):
        """Regular text with tokens mixed in must still be translated."""
        items = [TranslatableItem(text="Hello <FirstName>!", item_id="t:2")]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("hello.uti"),
        )
        provider = _make_provider({})
        manager = TranslationManager(_make_config(), provider)
        manager.translate_content(content)

        assert provider.translate_async.call_count == 1


class TestBatchDedupBySanitized:
    """The batch payload must not include duplicate sanitized texts."""

    def test_duplicate_short_names_dedup_in_batch_payload(self):
        items = [
            TranslatableItem(
                text="Guard",
                item_id=f"g:{i}",
                metadata={"type": "creature_first_name"},
            )
            for i in range(3)
        ]
        items.append(
            TranslatableItem(
                text="Captain",
                item_id="c:0",
                metadata={"type": "creature_first_name"},
            )
        )
        content = ExtractedContent(
            content_type="creature",
            items=items,
            source_file=Path("guards.utc"),
        )

        provider = Mock()

        async def translate_batch_async(
            items,
            source_lang,
            target_lang,
            glossary_block=None,
            content_profile=None,
        ):
            return [
                TranslationResult(
                    translated=f"TR:{batch_item.original}",
                    original=batch_item.original,
                    success=True,
                )
                for batch_item in items
            ]

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            return TranslationResult(translated=f"TR:{text}", original=text, success=True)

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)

        from src.nwn_translator.translators.token_handler import sanitize_text

        uncached = []
        for item in items:
            sanitized, handler = sanitize_text(item.text, preserve_tokens=True)
            uncached.append(
                {
                    "item": item,
                    "sanitized": sanitized,
                    "full_sanitized": sanitized,
                    "handler": handler,
                }
            )

        translations: dict = {}
        manager._translate_uncached_concurrent(uncached, translations)

        assert provider.translate_batch_async.call_count == 1
        call = provider.translate_batch_async.call_args
        batch_items = call.kwargs.get("items") or call.args[0]
        originals = [batch_item.original for batch_item in batch_items]
        assert originals.count("Guard") == 1
        assert originals.count("Captain") == 1
        assert translations["Guard"] == "TR:Guard"
        assert translations["Captain"] == "TR:Captain"


class TestTokenMismatchRecovery:
    """Token/tag mismatches trigger retries and cleanup, not English fallback."""

    def test_invalid_inline_tags_retry_to_exact_match_and_cache(self):
        text = "<StartHighlight>[Shudder.]</Start>"
        content = ExtractedContent(
            content_type="dialog",
            items=[TranslatableItem(text=text, item_id="dlg:0")],
            source_file=Path("test.dlg"),
        )

        provider = Mock()
        attempts = {"count": 0}

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return TranslationResult(
                    translated="<StartAction>[Вздрогнуть.]</StartAction>",
                    original=text,
                    success=True,
                )
            return TranslationResult(
                translated=text.replace("[Shudder.]", "[Вздрогнуть.]"),
                original=text,
                success=True,
            )

        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result[text] == "<StartHighlight>[Вздрогнуть.]</Start>"
        assert provider.translate_async.call_count == 2
        assert len(list(manager.translation_cache.items())) == 1

    def test_cleanup_only_result_is_not_cached(self):
        text = "<StartHighlight>[Shudder.]</Start>"
        broken = "<StartAction>[Вздрогнуть.]</StartAction>"
        content = ExtractedContent(
            content_type="dialog",
            items=[TranslatableItem(text=text, item_id="dlg:1")],
            source_file=Path("test.dlg"),
        )

        provider = Mock()
        provider.translate_async = AsyncMock(
            side_effect=[
                TranslationResult(translated=broken, original=text, success=True),
                TranslationResult(translated=broken, original=text, success=True),
                TranslationResult(translated=broken, original=text, success=True),
            ]
        )
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        assert result[text] == "[Вздрогнуть.]"
        # Initial call + 1 retry reproducing the identical mismatch short-circuits
        # the remaining retry and goes straight to cleanup.
        assert provider.translate_async.call_count == 2
        assert len(list(manager.translation_cache.items())) == 0
