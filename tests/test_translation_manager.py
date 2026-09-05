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
from src.nwn_translator.translators.token_handler import TokenHandler
from src.nwn_translator.translators.translation_manager import (
    TranslationManager,
    _is_empty_after_sanitize,
)


class TestEmptyAfterSanitize:
    """The passthrough gate must not swallow real words between placeholders."""

    @pytest.mark.parametrize(
        "text",
        [
            "<StartAction>Attack</Start>",
            "<StartHighlight>Partir</Start>",
            "<StartAction>Yes</Start>",
        ],
    )
    def test_single_word_inside_inline_tags_is_translatable(self, text):
        """A lone word wrapped in tags used to be misclassified as empty:
        the permissive placeholder regex greedily matched across the word."""
        sanitized = TokenHandler().sanitize(text).sanitized_text
        assert not _is_empty_after_sanitize(sanitized)

    @pytest.mark.parametrize(
        "text",
        [
            '"<Deity>!"',
            "<FirstName>.",
            "<CUSTOM101>",
            ". . .",
            "...",
            "########",
        ],
    )
    def test_tokens_and_punctuation_only_stay_passthrough(self, text):
        sanitized = TokenHandler().sanitize(text).sanitized_text
        assert _is_empty_after_sanitize(sanitized)

    def test_multi_word_tagged_text_is_translatable(self):
        sanitized = TokenHandler().sanitize("<StartAction>Attack the guard</Start> now")
        assert not _is_empty_after_sanitize(sanitized.sanitized_text)

    def test_mangled_bracket_placeholders_still_stripped(self):
        assert _is_empty_after_sanitize("[[NWN_TOKEN_abcdef01_0]]!")
        assert _is_empty_after_sanitize("<[NWN_INLINE_deadbeef_2]>...")
        assert not _is_empty_after_sanitize("[[NWN_TOKEN_abcdef01_0]]Word[[NWN_TOKEN_abcdef01_1]]")


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

    async def translate_batch_async(
        items,
        source_lang,
        target_lang,
        glossary_block=None,
        content_profile=None,
    ):
        return [
            TranslationResult(
                translated=translations.get(item.original, item.original),
                original=item.original,
                success=True,
                metadata={"batch": True},
            )
            for item in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
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
        # Untyped short strings ride the medium batch tier in one call.
        assert provider.translate_batch_async.call_count == 1
        provider.translate_async.assert_not_called()

    def test_empty_model_output_is_rejected(self):
        items = [
            TranslatableItem(text="Sword of Fire", item_id="item:name"),
        ]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("sword.uti"),
        )
        provider = _make_provider({"Sword of Fire": ""})
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)
        assert "Sword of Fire" not in result

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

    @pytest.mark.parametrize("approved", [False, True])
    def test_unproven_natural_word_requires_gate_approval(self, approved):
        item = _make_ncs_item("Good", needs_llm_gate=True, confidence="low")
        item.metadata.update(proven_player=False, player_candidate=True)
        content = ExtractedContent(
            content_type="ncs_script", items=[item], source_file=Path("script.ncs")
        )
        provider = _make_provider({"Good": "Translated word"})
        provider.classify_ncs_translate_gate_batch_async = AsyncMock(
            return_value={"0": {"translate": approved, "reason": "checked_source"}}
        )
        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)
        provider.classify_ncs_translate_gate_batch_async.assert_called_once()
        assert result == ({"Good": "Translated word"} if approved else {})
        if not approved:
            provider.translate_async.assert_not_called()
            provider.translate_batch_async.assert_not_called()

    def test_unproven_word_is_not_auto_approved_when_gate_is_disabled(self):
        item = _make_ncs_item("Good", needs_llm_gate=True, confidence="low")
        item.metadata.update(proven_player=False, player_candidate=True)
        content = ExtractedContent(
            content_type="ncs_script", items=[item], source_file=Path("script.ncs")
        )
        provider = _make_provider({"Good": "Translated word"})
        manager = TranslationManager(_make_config(skip_ncs_llm_gate=True), provider)
        assert manager.translate_content(content) == {}
        provider.classify_ncs_translate_gate_batch_async.assert_not_called()
        provider.translate_async.assert_not_called()
        provider.translate_batch_async.assert_not_called()

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
        text = "The seal is breaking beyond the old ward stones and the eastern gate! " * 2
        item = _make_ncs_item(text)
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
        provider.translate_batch_async = AsyncMock(return_value=[])
        provider.close_async_client = AsyncMock(return_value=None)

        async def gate_approve_all(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test_approve"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate_approve_all)
        manager = TranslationManager(_make_config(), provider)
        manager._ITEM_TIMEOUT = 0.01
        manager._GATHER_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 2.0

        result = manager.translate_content(content)

        assert result == {text: "RU: The seal is breaking!"}
        assert provider.translate_async.call_count == 2
        assert "NCS timeout fallback" in calls[1]["context"]
        assert calls[1]["glossary_block"] is None
        assert manager.ncs_translations_by_item_id == {item.item_id: "RU: The seal is breaking!"}
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["timeout"] == 1
        assert stats["retry_recovered"] == 1
        assert stats["translated"] == 1

    def test_failed_ncs_timeout_retry_records_diagnostics_without_patchable_item(self):
        text = "The portal resists you while the tower wards grind against the seal! " * 2
        item = _make_ncs_item(text)
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
        provider.translate_batch_async = AsyncMock(return_value=[])
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


class TestNcsBatchTranslation:
    """Approved short NCS strings use the dedicated post-gate batch path."""

    def test_approved_ncs_batches_and_rejected_items_are_not_sent(self):
        approved = _make_ncs_item("The gate opens.", item_id="script:off_10", offset=0x10)
        rejected = _make_ncs_item(
            "Debug state changed.",
            item_id="script:off_20",
            needs_llm_gate=True,
            confidence="medium",
            hint="ambiguous_bytecode",
            offset=0x20,
        )
        content = ExtractedContent(
            content_type="ncs_script",
            items=[approved, rejected],
            source_file=Path("script.ncs"),
        )
        provider = _make_provider({"The gate opens.": "Ворота открываются."})

        async def gate(entries, *, source_lang):
            return {
                str(e["key"]): {
                    "translate": e["text"] == "The gate opens.",
                    "reason": "test",
                }
                for e in entries
            }

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(content)

        assert result == {"The gate opens.": "Ворота открываются."}
        assert manager.ncs_translations_by_item_id == {"script:off_10": "Ворота открываются."}
        provider.translate_batch_async.assert_called_once()
        provider.translate_async.assert_not_called()
        sent_items = provider.translate_batch_async.call_args.kwargs["items"]
        assert [item.original for item in sent_items] == ["The gate opens."]

    def test_ncs_dynamic_batch_sizes_and_single_fallback_by_length(self):
        short_items = [
            _make_ncs_item(f"Short player line {i}.", item_id=f"script:short_{i}", offset=i)
            for i in range(21)
        ]
        medium_items = [
            _make_ncs_item(
                f"Medium player-facing message {i} with enough words for the second bucket.",
                item_id=f"script:medium_{i}",
                offset=100 + i,
            )
            for i in range(11)
        ]
        long_text = (
            "This player-facing script message is long enough to stay outside NCS batch mode. " * 2
        )
        multiline_text = "First player line.\nSecond player line."
        long_item = _make_ncs_item(long_text, item_id="script:long", offset=300)
        multiline_item = _make_ncs_item(multiline_text, item_id="script:multiline", offset=320)
        content = ExtractedContent(
            content_type="ncs_script",
            items=[*short_items, *medium_items, long_item, multiline_item],
            source_file=Path("script.ncs"),
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
                    translated=f"TR:{item.original}",
                    original=item.original,
                    success=True,
                    metadata={"batch": True},
                )
                for item in items
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

        async def gate(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(content)

        assert result[short_items[0].text] == f"TR:{short_items[0].text}"
        assert result[long_text] == f"TR:{long_text}"
        batch_sizes = [
            len(call.kwargs["items"]) for call in provider.translate_batch_async.call_args_list
        ]
        # 21 short (<50) fit one batch of 30; 11 medium (50-99) fit one of 15.
        assert batch_sizes == [21, 11]
        assert provider.translate_async.call_count == 2
        assert {call.kwargs["text"] for call in provider.translate_async.call_args_list} == {
            long_text,
            multiline_text,
        }

    def test_ncs_batch_dedups_by_sanitized_text_and_hint(self):
        items = [
            _make_ncs_item(
                "The lever moves.",
                item_id="script:off_10",
                hint="SpeakString",
                offset=0x10,
            ),
            _make_ncs_item(
                "The lever moves.",
                item_id="script:off_20",
                hint="SpeakString",
                offset=0x20,
            ),
            _make_ncs_item(
                "The lever moves.",
                item_id="script:off_30",
                hint="SetCustomToken",
                offset=0x30,
            ),
        ]
        content = ExtractedContent(
            content_type="ncs_script",
            items=items,
            source_file=Path("script.ncs"),
        )
        provider = _make_provider({"The lever moves.": "Рычаг движется."})
        manager = TranslationManager(_make_config(), provider)

        manager.translate_content(content)

        provider.translate_batch_async.assert_called_once()
        sent_items = provider.translate_batch_async.call_args.kwargs["items"]
        assert [item.original for item in sent_items] == [
            "The lever moves.",
            "The lever moves.",
        ]
        assert [item.metadata["hint"] for item in sent_items] == [
            "SpeakString",
            "SetCustomToken",
        ]
        assert manager.ncs_translations_by_item_id == {
            "script:off_10": "Рычаг движется.",
            "script:off_20": "Рычаг движется.",
            "script:off_30": "Рычаг движется.",
        }

    def test_ncs_batch_failure_splits_then_minimal_single_fallback_recovers(self):
        items = [
            _make_ncs_item("The first ward fails.", item_id="script:off_10", offset=0x10),
            _make_ncs_item("The second ward fails.", item_id="script:off_20", offset=0x20),
        ]
        content = ExtractedContent(
            content_type="ncs_script",
            items=items,
            source_file=Path("script.ncs"),
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
                    translated="",
                    original=item.original,
                    success=False,
                    error="Batch JSON parse error",
                )
                for item in items
            ]

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            assert "NCS timeout fallback" in context
            assert glossary_block is None
            return TranslationResult(translated=f"TR:{text}", original=text, success=True)

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        async def gate(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(content)

        assert result == {
            "The first ward fails.": "TR:The first ward fails.",
            "The second ward fails.": "TR:The second ward fails.",
        }
        assert [
            len(call.kwargs["items"]) for call in provider.translate_batch_async.call_args_list
        ] == [
            2,
            1,
            1,
        ]
        assert provider.translate_async.call_count == 2
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["translated"] == 2
        assert stats["failed"] == 0

    def test_ncs_batch_timeout_records_timeout_and_recovery(self):
        item = _make_ncs_item("The ward flickers.", item_id="script:off_10", offset=0x10)
        content = ExtractedContent(
            content_type="ncs_script",
            items=[item],
            source_file=Path("script.ncs"),
        )
        provider = Mock()

        async def translate_batch_async(
            items,
            source_lang,
            target_lang,
            glossary_block=None,
            content_profile=None,
        ):
            await asyncio.sleep(1)
            return []

        async def translate_async(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            return TranslationResult(translated="Мерцает оберег.", original=text, success=True)

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(side_effect=translate_async)
        provider.close_async_client = AsyncMock(return_value=None)

        async def gate(entries, *, source_lang):
            return {str(e["key"]): {"translate": True, "reason": "test"} for e in entries}

        provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
        manager = TranslationManager(_make_config(), provider)
        manager._BATCH_CALL_TIMEOUT = 0.01
        manager._ITEM_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 2.0

        result = manager.translate_content(content)

        assert result == {"The ward flickers.": "Мерцает оберег."}
        stats = manager.get_statistics()["ncs_diagnostics"]
        assert stats["timeout"] == 1
        assert stats["retry_recovered"] == 1
        assert stats["translated"] == 1


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
        assert "Boom" in manager.failed_originals

    def test_empty_translation_after_retries_marks_failed_original(self):
        """A successful API call that yields an empty line is recorded as a failure."""
        text = "The ancient seal on the northern gate begins to crack and crumble as you approach."
        empty = TranslationResult(translated="", original=text, success=True)
        provider = Mock()
        provider.translate.return_value = empty
        provider.translate_async = AsyncMock(return_value=empty)
        provider.translate_batch_async = AsyncMock(return_value=[empty])
        provider.close_async_client = AsyncMock(return_value=None)
        manager = TranslationManager(_make_config(), provider)
        content = ExtractedContent(
            content_type="item",
            items=[TranslatableItem(text=text, item_id="x:0")],
            source_file=Path("test.uti"),
        )

        result = manager.translate_content(content)

        assert result == {}
        assert text in manager.failed_originals
        assert manager.get_statistics()["total_errors"] >= 1


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

        # Untyped short string rides the medium batch tier, not passthrough.
        assert provider.translate_batch_async.call_count == 1
        provider.translate_async.assert_not_called()


class TestMediumBatchTier:
    """Medium-length strings (up to 1000 chars) ride the batch path."""

    @staticmethod
    def _item_data(sanitized: str, item: TranslatableItem) -> dict:
        return {"sanitized": sanitized, "item": item}

    def test_medium_classification_boundaries(self):
        desc = TranslatableItem(text="x", metadata={"type": "placeable_description"})
        assert TranslationManager._is_medium_item(self._item_data("x" * 1000, desc))
        assert not TranslationManager._is_medium_item(self._item_data("x" * 1001, desc))

        short_name = TranslatableItem(text="Sword", metadata={"type": "item_name"})
        # Whitelisted short names stay in the short tier.
        assert not TranslationManager._is_medium_item(self._item_data("Sword", short_name))

        untyped = TranslatableItem(text="Sword")
        # Short but non-whitelisted strings now batch via the medium tier.
        assert TranslationManager._is_medium_item(self._item_data("Sword", untyped))

        ncs = _make_ncs_item("x" * 120)
        assert not TranslationManager._is_medium_item(self._item_data("x" * 120, ncs))

    def test_three_tiers_and_long_route_correctly(self):
        very_short = TranslatableItem(
            text="Guard", metadata={"type": "creature_first_name"}, item_id="vs"
        )
        short = TranslatableItem(
            text="Sword of the Ancient Flames", metadata={"type": "item_name"}, item_id="s"
        )
        medium_text = (
            "The sofa seems warm and inviting, perfect for a short nap by the fire "
            "after a long day of adventuring in the dungeon."
        )
        medium = TranslatableItem(
            text=medium_text,
            context="Description of placeable 'Couch'",
            metadata={"type": "placeable_description"},
            item_id="m",
        )
        long_text = "A remarkably long placeable description sentence. " * 22
        long_item = TranslatableItem(
            text=long_text, metadata={"type": "placeable_description"}, item_id="l"
        )
        translations = {
            "Guard": "Страж",
            "Sword of the Ancient Flames": "Меч Древнего Пламени",
            medium_text: "Диван выглядит уютным.",
            long_text: "Длинное описание.",
        }
        provider = _make_provider(translations)
        manager = TranslationManager(_make_config(), provider)
        content = ExtractedContent(
            content_type="mixed",
            items=[very_short, short, medium, long_item],
            source_file=Path("x.git"),
        )

        result = manager.translate_content(content)

        assert result["Guard"] == "Страж"
        assert result["Sword of the Ancient Flames"] == "Меч Древнего Пламени"
        assert result[medium_text] == "Диван выглядит уютным."
        assert result[long_text] == "Длинное описание."
        assert provider.translate_async.call_count == 1  # only the long item
        assert provider.translate_batch_async.call_count == 3  # one batch per tier
        batch_items = [
            item
            for call in provider.translate_batch_async.call_args_list
            for item in call.kwargs["items"]
        ]
        medium_sent = [item for item in batch_items if item.original == medium_text]
        assert medium_sent and medium_sent[0].context == "Description of placeable 'Couch'"

    def test_medium_batch_failure_falls_back_individually(self):
        medium_text = "The sofa seems warm and inviting, perfect for a short nap."
        provider = _make_provider({medium_text: "Диван выглядит уютным."})

        async def failing_batch(
            items,
            source_lang,
            target_lang,
            glossary_block=None,
            content_profile=None,
        ):
            return [
                TranslationResult(
                    translated="", original=item.original, success=False, error="boom"
                )
                for item in items
            ]

        provider.translate_batch_async = AsyncMock(side_effect=failing_batch)
        manager = TranslationManager(_make_config(), provider)
        content = ExtractedContent(
            content_type="placeable",
            items=[
                TranslatableItem(
                    text=medium_text,
                    metadata={"type": "placeable_description"},
                    item_id="m",
                )
            ],
            source_file=Path("x.utp"),
        )

        result = manager.translate_content(content)

        assert result[medium_text] == "Диван выглядит уютным."
        assert provider.translate_batch_async.call_count == 1
        assert provider.translate_async.call_count == 1  # individual fallback

    def test_medium_batches_respect_char_budget(self, monkeypatch):
        monkeypatch.setattr(TranslationManager, "_BATCH_MEDIUM_CHAR_BUDGET", 300)
        texts = [
            f"A fairly long unique description number {i} that easily clears the "
            "short threshold and lands in the medium tier of the batch splitter."
            for i in range(4)
        ]
        provider = _make_provider({t: f"Перевод {i}" for i, t in enumerate(texts)})
        manager = TranslationManager(_make_config(), provider)
        content = ExtractedContent(
            content_type="placeable",
            items=[
                TranslatableItem(
                    text=t, metadata={"type": "placeable_description"}, item_id=f"m{i}"
                )
                for i, t in enumerate(texts)
            ],
            source_file=Path("x.utp"),
        )

        result = manager.translate_content(content)

        for i, t in enumerate(texts):
            assert result[t] == f"Перевод {i}"
        # ~131 chars per item with a 300-char budget → two items per batch.
        batch_sizes = [
            len(call.kwargs["items"]) for call in provider.translate_batch_async.call_args_list
        ]
        assert batch_sizes == [2, 2]


class TestGenericTimeoutRetry:
    """A timed-out non-NCS single item is retried once before it is dropped."""

    @staticmethod
    def _long_item(text: str) -> ExtractedContent:
        return ExtractedContent(
            content_type="placeable",
            items=[
                TranslatableItem(text=text, metadata={"type": "placeable_description"}, item_id="l")
            ],
            source_file=Path("x.utp"),
        )

    def test_timeout_then_success_recovers(self, monkeypatch):
        monkeypatch.setattr(TranslationManager, "_ITEM_TIMEOUT", 0.05)
        long_text = "A remarkably long placeable description sentence. " * 22
        calls = {"n": 0}

        async def slow_then_fast(text, source_lang, target_lang, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(0.5)
            return TranslationResult(translated="Длинное описание.", original=text)

        provider = Mock()
        provider.translate_async = AsyncMock(side_effect=slow_then_fast)
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(self._long_item(long_text))

        assert result[long_text] == "Длинное описание."
        assert calls["n"] == 2
        assert manager.stats["errors"] == []

    def test_double_timeout_records_error(self, monkeypatch):
        monkeypatch.setattr(TranslationManager, "_ITEM_TIMEOUT", 0.05)
        long_text = "A remarkably long placeable description sentence. " * 22

        async def always_slow(text, source_lang, target_lang, **kwargs):
            await asyncio.sleep(0.5)
            return TranslationResult(translated="late", original=text)

        provider = Mock()
        provider.translate_async = AsyncMock(side_effect=always_slow)
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(self._long_item(long_text))

        assert long_text not in result
        assert len(manager.stats["errors"]) == 1
        assert "retry timed out" in manager.stats["errors"][0]


class TestForeignScriptRetry:
    """CJK characters in a translation trigger the mismatch retry path."""

    def test_cjk_answer_is_retried_and_clean_answer_accepted(self):
        long_text = "The Auren Society is not welcome in this city, stranger. " * 20
        provider = Mock()
        provider.translate_async = AsyncMock(
            side_effect=[
                TranslationResult(translated="Общество здесь не欢迎но!", original=long_text),
                TranslationResult(translated="Общество здесь не приветствуют!", original=long_text),
            ]
        )
        manager = TranslationManager(_make_config(), provider)
        content = ExtractedContent(
            content_type="placeable",
            items=[
                TranslatableItem(
                    text=long_text, metadata={"type": "placeable_description"}, item_id="l"
                )
            ],
            source_file=Path("x.utp"),
        )

        result = manager.translate_content(content)

        assert result[long_text] == "Общество здесь не приветствуют!"
        assert provider.translate_async.call_count == 2
        retry_context = provider.translate_async.call_args_list[1].kwargs["context"]
        assert "foreign script" in retry_context


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

    def test_color_marker_only_string_skips_api(self):
        text = (
            "<c\u044f\u044f\u044f><c\u044f\u044f ><c\u044f \u044f><c \u044f\u044f>"
            "<c\u044f  ><c \u044f ><c  \u044f>"
        )
        content = ExtractedContent(
            content_type="item",
            items=[TranslatableItem(text=text, item_id="COLORS_name")],
            source_file=Path("invisobj.utp"),
        )
        provider = _make_provider({})
        manager = TranslationManager(_make_config(), provider)

        result = manager.translate_content(content)

        assert result == {text: text}
        provider.translate_async.assert_not_called()
        provider.translate_batch_async.assert_not_called()


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
