"""Tests for timeout and robustness of batch processing.

Covers:
- ``run_async`` timeout behaviour
- Glossary partial-failure resilience
- Translation manager item-level timeout handling
"""

import asyncio
from pathlib import Path
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

    def test_cleanup_called_on_timeout(self):
        """Cleanup callback must fire even when the coroutine times out."""
        cleanup_called = False

        async def cleanup():
            nonlocal cleanup_called
            cleanup_called = True

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(TimeoutError):
            run_async(slow(), cleanup=cleanup, timeout=0.2)

        assert cleanup_called


# ---------------------------------------------------------------------------
# GlossaryBuilder partial failure
# ---------------------------------------------------------------------------


class TestGlossaryPartialFailure:
    """Glossary builder must survive individual batch failures."""

    def test_single_batch_failure_does_not_crash(self):
        """If one batch fails (all retries timeout), _translate_batch_async returns {}."""
        from src.nwn_translator.glossary import GlossaryBuilder

        builder = GlossaryBuilder()
        batch_seen = {"TestName": "character"}

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(side_effect=TimeoutError("timeout"))
        mock_provider.close_async_client = AsyncMock()

        mock_config = Mock()
        mock_config.target_lang = "russian"

        sem = asyncio.Semaphore(1)

        async def run_test():
            return await builder._translate_batch_async(
                sem,
                batch_seen,
                mock_provider,
                mock_config,
                1,
                1,
                None,
            )

        result = run_async(run_test(), timeout=10.0)
        # Must return empty dict, NOT raise RuntimeError
        assert result == {}

    def test_translate_batch_returns_entries_on_success(self):
        """Successful batch returns entries normally."""
        import json
        from src.nwn_translator.glossary import GlossaryBuilder

        builder = GlossaryBuilder()
        batch_seen = {"Perin": "character", "Dark Forest": "location"}

        mock_config = Mock()
        mock_config.target_lang = "russian"

        expected_json = json.dumps({"Perin": "Перин", "Dark Forest": "Тёмный Лес"})

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(return_value=expected_json)

        sem = asyncio.Semaphore(1)

        async def run_test():
            return await builder._translate_batch_async(
                sem,
                batch_seen,
                mock_provider,
                mock_config,
                1,
                1,
                None,
            )

        result = run_async(run_test(), timeout=10.0)
        assert result == {"Perin": "Перин", "Dark Forest": "Тёмный Лес"}

    def test_echoback_detection_retries_untranslated(self):
        """Echo-backs (value == key) must be excluded and retried."""
        import json
        from src.nwn_translator.glossary import GlossaryBuilder

        builder = GlossaryBuilder()
        batch_seen = {"Perin": "character", "Dark Forest": "location"}

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
        mock_config = Mock()
        mock_config.target_lang = "russian"

        sem = asyncio.Semaphore(1)

        async def run_test():
            return await builder._translate_batch_async(
                sem,
                batch_seen,
                mock_provider,
                mock_config,
                1,
                1,
                None,
            )

        result = run_async(run_test(), timeout=10.0)
        assert result == {"Perin": "Перин", "Dark Forest": "Тёмный Лес"}
        assert call_count == 2  # Must have retried for the echo-back

    def test_partial_results_merged_across_attempts(self):
        """Partial results from multiple attempts must be merged."""
        import json
        from src.nwn_translator.glossary import GlossaryBuilder

        builder = GlossaryBuilder()
        batch_seen = {"Alpha": "character", "Beta": "location", "Gamma": "item"}

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
        mock_config = Mock()
        mock_config.target_lang = "russian"

        sem = asyncio.Semaphore(1)

        async def run_test():
            return await builder._translate_batch_async(
                sem,
                batch_seen,
                mock_provider,
                mock_config,
                1,
                1,
                None,
            )

        result = run_async(run_test(), timeout=10.0)
        assert result == {"Alpha": "Альфа", "Beta": "Бета", "Gamma": "Гамма"}
        assert call_count == 2


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
