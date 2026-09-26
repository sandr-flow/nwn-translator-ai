"""Tests for timeout and robustness of batch processing.

Covers:
- ``run_async`` timeout behaviour
- Glossary partial-failure resilience
- Translation manager item-level timeout handling
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.nwn_translator.async_utils import run_async

# ---------------------------------------------------------------------------
# run_async timeout
# ---------------------------------------------------------------------------


class TestRunAsyncTimeout:
    """Tests for ``run_async`` timeout wrapper."""

    def test_timeout_raises(self):
        """A coroutine that exceeds the timeout must raise ``TimeoutError``."""

        async def slow():
            await asyncio.sleep(10)
            return "never"

        with pytest.raises(TimeoutError, match="timed out"):
            run_async(slow(), timeout=0.2)

    def test_fast_coroutine_returns_normally(self):
        """A fast coroutine must complete and return its value."""

        async def fast():
            return 42

        assert run_async(fast(), timeout=5.0) == 42

    def test_no_timeout(self):
        """When timeout is None the coroutine runs without a deadline."""

        async def fast():
            return "ok"

        assert run_async(fast(), timeout=None) == "ok"


# ---------------------------------------------------------------------------
# GlossaryBuilder partial failure
# ---------------------------------------------------------------------------


def _build_glossary(names, provider, target_lang="russian", progress_callback=None):
    """Build a glossary for ``name -> category`` *names* with one batch."""
    from src.nwn_translator.context.world_context import WorldContext
    from src.nwn_translator.glossary_builder import GlossaryBuilder

    world = WorldContext()
    world.extracted_names = list(names.items())
    config = SimpleNamespace(target_lang=target_lang, max_concurrent_requests=1)
    return GlossaryBuilder().build(world, provider, config, progress_callback)


class TestGlossaryPartialFailure:
    """Glossary builder must survive individual batch failures."""

    def test_single_batch_failure_does_not_crash(self):
        """If every attempt of a batch times out, the build returns an empty glossary."""
        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=TimeoutError("timeout"))

        glossary = _build_glossary({"TestName": "character"}, mock_provider)

        # Must return an empty glossary, NOT raise RuntimeError
        assert glossary.entries == {}
        assert mock_provider.complete_glossary_chat_async.call_count == 3

    def test_translate_batch_returns_entries_on_success(self):
        """Successful batch returns entries normally."""
        import json

        expected_json = json.dumps({"Perin": "Перин", "Dark Forest": "Тёмный Лес"})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(return_value=expected_json)

        glossary = _build_glossary({"Perin": "character", "Dark Forest": "location"}, mock_provider)
        assert glossary.entries == {"Perin": "Перин", "Dark Forest": "Тёмный Лес"}

    def test_parse_glossary_json_matches_normalized_short_key(self):
        """Short keys must survive invisible whitespace/category quirks."""
        from src.nwn_translator.glossary_builder import parse_glossary_json

        result = parse_glossary_json(
            '{"Kit": "\\u041d\\u0430\\u0431\\u043e\\u0440"}',
            {"Kit\u200b (item)"},
        )

        assert result == {"Kit\u200b (item)": "Набор"}

    def test_parse_glossary_json_uses_first_valid_object(self):
        """Trailing prose/examples after JSON must not poison parsing."""
        from src.nwn_translator.glossary_builder import parse_glossary_json

        raw = (
            'Here is the translation:\n{"Kit": "\\u041d\\u0430\\u0431\\u043e\\u0440"}\n'
            'Example format: {"Other": "Value"}'
        )

        result = parse_glossary_json(raw, {"Kit"})

        assert result == {"Kit": "Набор"}

    def test_unchanged_glossary_form_is_a_valid_answer(self):
        """An unchanged name or abbreviation does not imply a failed response."""
        import json

        call_count = 0

        async def fake_glossary(system_prompt, user_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: one correct, one echo-back
                return json.dumps({"Perin": "Перин", "Dark Forest": "Dark Forest"})
            else:
                # Retry: only missing key, now correct
                return json.dumps({"Dark Forest": "Тёмный Лес"})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=fake_glossary)

        glossary = _build_glossary({"Perin": "character", "Dark Forest": "location"}, mock_provider)
        assert glossary.entries == {"Perin": "Перин", "Dark Forest": "Dark Forest"}
        assert call_count == 1

    def test_partial_results_merged_across_attempts(self):
        """Partial results from multiple attempts must be merged."""
        import json

        call_count = 0

        async def fake_glossary(system_prompt, user_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: only 1 of 3
                return json.dumps({"Alpha": "Альфа"})
            else:
                # Retry: remaining 2
                return json.dumps({"Beta": "Бета", "Gamma": "Гамма"})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=fake_glossary)

        glossary = _build_glossary(
            {"Alpha": "character", "Beta": "location", "Gamma": "item"}, mock_provider
        )
        assert glossary.entries == {"Alpha": "Альфа", "Beta": "Бета", "Gamma": "Гамма"}
        assert call_count == 2

    def test_progress_reports_every_attempt_and_its_outcome(self):
        """Each attempt is announced, then reported as failed or with the names done."""
        import json

        replies = [TimeoutError("timeout"), json.dumps({"Alpha": "Альфа"}), "not json"]

        async def fake_glossary(system_prompt, user_prompt, **kwargs):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=fake_glossary)
        messages = []

        glossary = _build_glossary(
            {"Alpha": "character", "Beta": "location"},
            mock_provider,
            progress_callback=lambda phase, done, total, message: messages.append(
                (phase, done, total, message)
            ),
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

    def test_overall_timeout_keeps_finished_batches(self, monkeypatch):
        """A batch still running at the overall deadline must not discard the others."""
        import json
        from dataclasses import replace

        import src.nwn_translator.glossary_builder as module

        monkeypatch.setattr(module, "_STAGE", replace(module._STAGE, run_timeout_per_batch=0.1))

        async def fake_glossary(system_prompt, user_prompt, *, glossary_keys, **kwargs):
            if len(glossary_keys) < 80:
                await asyncio.sleep(5)
            return json.dumps({key: key.upper() for key in glossary_keys})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=fake_glossary)
        names = {f"Name{i:03d}": "character" for i in range(81)}

        from src.nwn_translator.context.world_context import WorldContext
        from src.nwn_translator.glossary_builder import GlossaryBuilder

        world = WorldContext()
        world.extracted_names = list(names.items())
        config = SimpleNamespace(target_lang="russian", max_concurrent_requests=2)
        glossary = GlossaryBuilder().build(world, mock_provider, config)

        assert glossary.entries == {name: name.upper() for name in list(names)[:80]}


# ---------------------------------------------------------------------------
# TranslationManager timeout handling
# ---------------------------------------------------------------------------


class TestTranslationManagerTimeouts:
    """Translation manager must handle item-level timeouts gracefully."""

    def test_timeout_item_recorded_as_error(self):
        """A timed-out item must be recorded as a failed translation, not crash."""
        from dataclasses import dataclass, field
        from typing import Any, Dict, Optional
        from src.nwn_translator.config import TranslationConfig
        from src.nwn_translator.extractors.base import ExtractedContent, TranslatableItem
        from src.nwn_translator.translators.translation_manager import TranslationManager
        from src.nwn_translator.ai_providers.base import TranslationResult

        config = TranslationConfig(
            api_key="test-key",
            model="test-model",
            source_lang="english",
            target_lang="russian",
            input_file=Path("test.mod"),
        )

        # Provider that times out on translate_async
        provider = Mock()

        async def slow_translate(*args, **kwargs):
            await asyncio.sleep(999)

        provider.translate_async = AsyncMock(side_effect=slow_translate)
        provider.close_async_client = AsyncMock()

        manager = TranslationManager(config, provider)
        # Set very short timeout for testing
        manager._ITEM_TIMEOUT = 0.2
        manager._GATHER_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 2.0

        items = [
            TranslatableItem(text="Hello world", item_id="test:0"),
        ]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("test.uti"),
        )

        result = manager.translate_content(content)

        # The translation must fail gracefully (empty result), not hang
        assert result == {}
        stats = manager.get_statistics()
        assert stats["total_errors"] >= 1

    def test_queued_long_items_are_not_limited_by_fixed_outer_timeout(self):
        """The outer run_async timeout must scale with queued semaphore work."""
        from src.nwn_translator.ai_providers.base import TranslationResult
        from src.nwn_translator.config import TranslationConfig
        from src.nwn_translator.extractors.base import ExtractedContent, TranslatableItem
        from src.nwn_translator.translators.translation_manager import TranslationManager

        config = TranslationConfig(
            api_key="test-key",
            model="test-model",
            source_lang="english",
            target_lang="russian",
            input_file=Path("test.mod"),
            max_concurrent_requests=1,
        )

        provider = Mock()

        async def slow_translate(
            text,
            source_lang,
            target_lang,
            context=None,
            glossary_block=None,
            content_profile=None,
        ):
            await asyncio.sleep(0.05)
            return TranslationResult(translated=f"TR:{text}", original=text, success=True)

        provider.translate_async = AsyncMock(side_effect=slow_translate)
        provider.close_async_client = AsyncMock()

        manager = TranslationManager(config, provider)
        manager._ITEM_TIMEOUT = 1.0
        manager._RUN_ASYNC_TIMEOUT = 0.08

        items = [
            TranslatableItem(
                text=f"This is a deliberately long line queued behind the semaphore {i}.",
                item_id=f"test:{i}",
            )
            for i in range(3)
        ]
        content = ExtractedContent(
            content_type="item",
            items=items,
            source_file=Path("test.uti"),
        )

        result = manager.translate_content(content)

        assert result == {item.key: f"TR:{item.text}" for item in items}


class TestGlossaryEchoBackAcceptance:
    """Names the model insists on keeping unchanged must survive into the glossary."""

    def test_unchanged_names_accepted_without_retries(self):
        """Valid unchanged names do not spend retries."""
        import json

        call_count = 0

        async def fake_glossary(system_prompt, user_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            return json.dumps({"Almraiven": "Almraiven", "Perin": "Perin"})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=fake_glossary)

        glossary = _build_glossary(
            {"Almraiven": "location", "Perin": "character"}, mock_provider, "french"
        )
        assert glossary.entries == {"Almraiven": "Almraiven", "Perin": "Perin"}
        assert call_count == 1  # Identity translations are valid decisions.

    def test_build_degrades_to_empty_glossary_on_garbage(self, caplog):
        """Unusable responses everywhere -> empty glossary + warning, no exception."""
        import logging

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(return_value="not json at all")

        caplog.set_level(logging.WARNING)
        glossary = _build_glossary({"Perin": "character"}, mock_provider)

        assert glossary.entries == {}
        assert mock_provider.complete_glossary_chat_async.call_count == 3
        assert "no usable entries" in caplog.text
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert "Glossary glossary returned no usable entries after 3 attempts" in errors
