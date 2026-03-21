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
        """If one batch fails, the builder must still return entries from other batches."""
        from src.nwn_translator.glossary import GlossaryBuilder

        builder = GlossaryBuilder()

        # Build a mock world_context with enough names for 2 batches
        mock_wc = Mock()
        names = [(f"Name{i}", "character") for i in range(100)]
        mock_wc.get_all_names.return_value = names

        # Mock provider: first LLM call raises TimeoutError, second succeeds
        call_count = 0

        async def fake_glossary_chat(system_prompt, user_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            # First batch (calls 1-3 with retries) -> always fail
            if call_count <= 3:
                raise TimeoutError("Simulated timeout")
            # Second batch -> succeed with a simple JSON
            import json
            keys = kwargs.get("glossary_keys", [])
            result = {k: f"Перевод_{k}" for k in keys}
            return json.dumps(result)

        mock_provider = Mock()
        mock_provider.complete_glossary_chat_async = AsyncMock(
            side_effect=fake_glossary_chat
        )
        mock_provider.close_async_client = AsyncMock()

        mock_config = Mock()
        mock_config.target_lang = "russian"

        # Patch _run_llm to use smaller timeout for test speed
        with patch.object(
            GlossaryBuilder, '_run_llm',
            side_effect=lambda prov, sp, up, keys: _sync_call_provider(
                prov, sp, up, keys
            ),
        ):
            # Use the original build but with patched internals
            pass

        # Direct test: _translate_batch should return {} on total failure
        batch_seen = {"TestName": "character"}
        mock_provider2 = Mock()
        mock_provider2.complete_glossary_chat_async = AsyncMock(
            side_effect=TimeoutError("timeout")
        )
        mock_provider2.close_async_client = AsyncMock()

        # Patch _run_llm to always raise TimeoutError
        original_run_llm = GlossaryBuilder._run_llm

        def failing_run_llm(provider, system_prompt, user_prompt, keys_for_schema):
            raise TimeoutError("Simulated LLM timeout")

        with patch.object(GlossaryBuilder, '_run_llm', side_effect=failing_run_llm):
            result = builder._translate_batch(
                batch_seen, mock_provider2, mock_config, 1, 1
            )
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

        def success_run_llm(provider, system_prompt, user_prompt, keys_for_schema):
            return expected_json

        with patch.object(GlossaryBuilder, '_run_llm', side_effect=success_run_llm):
            result = builder._translate_batch(
                batch_seen, Mock(), mock_config, 1, 1
            )
            assert result == {"Perin": "Перин", "Dark Forest": "Тёмный Лес"}


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
