"""Tests for Phase 3 token-economy optimisations.

Covers:
- Phase 3.4: content-profile-driven prompt shrinking (short_label vs default).
- Phase 3.5: adaptive batch sizing (very-short items get a larger batch size).
"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pytest

from src.nwn_translator.config import TranslationConfig
from src.nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from src.nwn_translator.prompts import (
    build_translation_system_prompt,
    build_translation_system_prompt_parts,
)
from src.nwn_translator.prompts._builder import (
    CONTENT_PROFILE_DEFAULT,
    CONTENT_PROFILE_SHORT_LABEL,
)
from src.nwn_translator.translators.translation_manager import TranslationManager

# ---------------------------------------------------------------------------
# 3.4 — prompt-builder profile selection
# ---------------------------------------------------------------------------


class TestShortLabelProfileShrinkage:
    """The short_label profile drops speech-style / gender rules."""

    def test_short_label_is_shorter_than_default(self):
        default_stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_DEFAULT
        )
        short_stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert len(short_stable) < len(default_stable)
        # Target savings: at least 10% of stable prefix.
        savings_ratio = 1.0 - len(short_stable) / len(default_stable)
        assert savings_ratio >= 0.1, f"Only {savings_ratio:.1%} savings, expected >=10%"

    def test_short_label_has_no_speech_style_rules(self):
        stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert "PRESERVE SPEECH STYLE" not in stable
        assert "low-INT" not in stable

    def test_short_label_has_no_player_gender_rule(self):
        stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert "PLAYER CHARACTER" not in stable

    def test_short_label_keeps_token_preservation(self):
        stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert "TAG/TOKEN PRESERVATION" in stable

    def test_short_label_keeps_proper_names_block(self):
        stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert "PROPER NAMES" in stable

    def test_short_label_keeps_glossary_usage_rules(self):
        stable, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_SHORT_LABEL
        )
        assert "GLOSSARY USAGE" in stable

    def test_default_profile_is_unchanged_without_explicit_arg(self):
        """Passing no profile must match the explicit default one (backwards compat)."""
        no_arg = build_translation_system_prompt("russian", "male")
        explicit = build_translation_system_prompt(
            "russian", "male", content_profile=CONTENT_PROFILE_DEFAULT
        )
        assert no_arg == explicit

    def test_invalid_profile_falls_back_to_default(self):
        stable_bad, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile="nonsense"
        )
        stable_default, _ = build_translation_system_prompt_parts(
            "russian", "male", content_profile=CONTENT_PROFILE_DEFAULT
        )
        assert stable_bad == stable_default

    def test_profile_does_not_affect_variable_half(self):
        """Glossary block stays in the variable half regardless of profile."""
        glossary = 'GLOSSARY:\n* "Dark Ranger" -> Тёмный рейнджер'
        _, var_default = build_translation_system_prompt_parts(
            "russian",
            "male",
            glossary_block=glossary,
            content_profile=CONTENT_PROFILE_DEFAULT,
        )
        _, var_short = build_translation_system_prompt_parts(
            "russian",
            "male",
            glossary_block=glossary,
            content_profile=CONTENT_PROFILE_SHORT_LABEL,
        )
        assert var_default == var_short
        assert "Dark Ranger" in var_short

    def test_profile_stable_is_byte_identical_across_glossaries(self):
        """Stable prefix MUST NOT depend on glossary contents (cache invariant)."""
        stable_a, _ = build_translation_system_prompt_parts(
            "russian",
            "male",
            glossary_block='GLOSSARY:\n* "Zephirax" -> Зефиракс',
            content_profile=CONTENT_PROFILE_SHORT_LABEL,
        )
        stable_b, _ = build_translation_system_prompt_parts(
            "russian",
            "male",
            glossary_block='GLOSSARY:\n* "Qartheel" -> Картил',
            content_profile=CONTENT_PROFILE_SHORT_LABEL,
        )
        assert stable_a == stable_b


# ---------------------------------------------------------------------------
# 3.4 — TranslationManager profile selection
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    translated: str
    original: str
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _make_config(**kwargs) -> TranslationConfig:
    defaults = dict(
        api_key="test-key",
        model="deepseek/deepseek-v3.2",
        source_lang="english",
        target_lang="russian",
        input_file=Path("test.mod"),
    )
    defaults.update(kwargs)
    return TranslationConfig(**defaults)


class TestTranslationManagerProfileSelection:
    """The manager picks the correct content profile per item / per batch."""

    def _make_item(self, text: str, type_: str = "") -> dict:
        item = TranslatableItem(text=text, item_id=f"x:{text}", metadata={"type": type_})
        from src.nwn_translator.translators.token_handler import sanitize_text

        san, h = sanitize_text(text, preserve_tokens=True)
        return {"item": item, "sanitized": san, "handler": h}

    def test_short_label_for_batchable_type(self):
        d = self._make_item("Guard", "creature_first_name")
        assert TranslationManager._content_profile_for_item(d) == CONTENT_PROFILE_SHORT_LABEL

    def test_default_for_non_batchable_type(self):
        d = self._make_item("Long item description...", "item_description")
        assert TranslationManager._content_profile_for_item(d) == CONTENT_PROFILE_DEFAULT

    def test_batch_all_short_labels_yields_short_profile(self):
        batch = [
            self._make_item("Guard", "creature_first_name"),
            self._make_item("Captain", "creature_first_name"),
            self._make_item("Main Gate", "area_name"),
        ]
        assert TranslationManager._content_profile_for_batch(batch) == CONTENT_PROFILE_SHORT_LABEL

    def test_batch_mixed_falls_back_to_default(self):
        batch = [
            self._make_item("Guard", "creature_first_name"),
            self._make_item("A long description.", "item_description"),
        ]
        assert TranslationManager._content_profile_for_batch(batch) == CONTENT_PROFILE_DEFAULT

    def test_batch_empty_returns_default(self):
        assert TranslationManager._content_profile_for_batch([]) == CONTENT_PROFILE_DEFAULT


class TestContentProfilePropagatedToProvider:
    """translate_batch_async must receive the profile argument."""

    def test_short_label_batch_passes_short_profile(self):
        items = [
            TranslatableItem(
                text=f"Guard{i}",
                item_id=f"g:{i}",
                metadata={"type": "creature_first_name"},
            )
            for i in range(3)
        ]
        content = ExtractedContent(
            content_type="creature",
            items=items,
            source_file=Path("guards.utc"),
        )
        provider = Mock()
        provider.translate_batch_async = AsyncMock(
            return_value=[
                _Result(translated=f"TR:{it.text}", original=it.text, success=True) for it in items
            ]
        )
        provider.translate_async = AsyncMock(
            side_effect=lambda **kw: _Result(
                translated=f"TR:{kw['text']}", original=kw["text"], success=True
            )
        )
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        manager.translate_content(content)

        assert provider.translate_batch_async.call_count >= 1
        kwargs = provider.translate_batch_async.call_args.kwargs
        assert kwargs.get("content_profile") == CONTENT_PROFILE_SHORT_LABEL


# ---------------------------------------------------------------------------
# 3.5 — adaptive batch sizing
# ---------------------------------------------------------------------------


class TestAdaptiveBatchSize:
    """Very-short items group into larger batches than regular-short items."""

    def test_very_short_items_use_larger_batch(self):
        # 31 one-word items (sanitized length <=20) → should be one batch of
        # 30 + one of 1 (total 2 batches), not 3 batches of 15+15+1.
        items = [
            TranslatableItem(
                text=f"Guard{i}",  # <=7 chars
                item_id=f"g:{i}",
                metadata={"type": "creature_first_name"},
            )
            for i in range(31)
        ]
        content = ExtractedContent(
            content_type="creature",
            items=items,
            source_file=Path("guards.utc"),
        )
        provider = Mock()

        async def translate_batch_async(
            items, source_lang, target_lang, glossary_block=None, content_profile=None
        ):
            return [_Result(translated=f"TR:{it.original}", original=it.original) for it in items]

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(
            side_effect=lambda **kw: _Result(translated=f"TR:{kw['text']}", original=kw["text"])
        )
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        manager.translate_content(content)

        # 31 items → one batch of 30 (very-short) + one batch of 1.
        assert provider.translate_batch_async.call_count == 2
        sizes = [
            len(call.kwargs.get("items") or call.args[0])
            for call in provider.translate_batch_async.call_args_list
        ]
        assert sorted(sizes) == [1, 30]

    def test_regular_short_items_keep_batch_size_15(self):
        # 16 items whose sanitized length is 21+ chars → two batches of 15+1.
        # Build names that are intentionally >20 chars.
        items = [
            TranslatableItem(
                text=f"Long Guardsman Name #{i:02d}",  # 23+ chars
                item_id=f"g:{i}",
                metadata={"type": "creature_first_name"},
            )
            for i in range(16)
        ]
        content = ExtractedContent(
            content_type="creature",
            items=items,
            source_file=Path("guards.utc"),
        )
        provider = Mock()

        async def translate_batch_async(
            items, source_lang, target_lang, glossary_block=None, content_profile=None
        ):
            return [_Result(translated=f"TR:{it.original}", original=it.original) for it in items]

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(
            side_effect=lambda **kw: _Result(translated=f"TR:{kw['text']}", original=kw["text"])
        )
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        manager.translate_content(content)

        assert provider.translate_batch_async.call_count == 2
        sizes = sorted(
            len(call.kwargs.get("items") or call.args[0])
            for call in provider.translate_batch_async.call_args_list
        )
        assert sizes == [1, 15]

    def test_mixed_short_items_split_into_two_batch_groups(self):
        """Short items with mixed lengths are partitioned before batching."""
        items = []
        # 21 very-short items
        for i in range(21):
            items.append(
                TranslatableItem(
                    text=f"W{i:02d}",
                    item_id=f"s:{i}",
                    metadata={"type": "creature_first_name"},
                )
            )
        # 16 regular-short items
        for i in range(16):
            items.append(
                TranslatableItem(
                    text=f"Quite Long Proper Name {i:02d}",
                    item_id=f"l:{i}",
                    metadata={"type": "creature_first_name"},
                )
            )
        content = ExtractedContent(
            content_type="creature",
            items=items,
            source_file=Path("mix.utc"),
        )
        provider = Mock()

        async def translate_batch_async(
            items, source_lang, target_lang, glossary_block=None, content_profile=None
        ):
            return [_Result(translated=f"TR:{it.original}", original=it.original) for it in items]

        provider.translate_batch_async = AsyncMock(side_effect=translate_batch_async)
        provider.translate_async = AsyncMock(
            side_effect=lambda **kw: _Result(translated=f"TR:{kw['text']}", original=kw["text"])
        )
        provider.close_async_client = AsyncMock(return_value=None)

        manager = TranslationManager(_make_config(), provider)
        result = manager.translate_content(content)

        # Very-short: 21 → one batch of 30 (unfilled), regular-short: 16 → 15 + 1.
        sizes = sorted(
            len(call.kwargs.get("items") or call.args[0])
            for call in provider.translate_batch_async.call_args_list
        )
        assert sizes == [1, 15, 21]
        # Every item got translated, despite being split across two groups.
        assert len(result) == len(items)
