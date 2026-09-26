"""Tests for AI provider base types and create_provider (OpenRouter)."""

from unittest.mock import MagicMock

import pytest

from nwn_translator.async_utils import run_async
from nwn_translator.ai_providers import openrouter_provider
from nwn_translator.ai_providers.base import TranslationItem, TranslationResult
from nwn_translator.ai_providers import (
    create_provider,
    create_provider_for_config,
    detect_provider_from_key,
    provider_label,
)
from nwn_translator.config import TranslationConfig
from nwn_translator.telemetry import RunMetricsRecorder
from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from nwn_translator.ai_providers.polza_provider import PolzaProvider


class TestCreateProvider:
    """Tests for create_provider factory."""

    def test_create_returns_openrouter(self):
        """create_provider must return OpenRouterProvider."""
        p = create_provider("sk-or-test", model="openai/gpt-4o", player_gender="female")
        assert isinstance(p, OpenRouterProvider)
        assert p.model == "openai/gpt-4o"
        assert p.player_gender == "female"

    def test_create_returns_polza_for_pza_prefix(self, monkeypatch):
        """``pza…`` keys route to PolzaProvider with the Polza base URL and no extra headers."""
        client_cls = MagicMock()
        monkeypatch.setattr(openrouter_provider, "AsyncOpenAI", client_cls)
        p = create_provider("pza-abcdef1234567890", model="openai/gpt-4o")

        async def touch_client():
            return p.async_client

        run_async(touch_client(), timeout=5.0)
        assert isinstance(p, PolzaProvider)
        assert p.get_provider_name() == "polza"
        assert client_cls.call_args.kwargs["base_url"] == "https://polza.ai/api/v1"
        assert client_cls.call_args.kwargs["default_headers"] == {}

    def test_create_falls_back_to_openrouter_for_unknown_prefix(self):
        """Unrecognised keys default to OpenRouter (safe fallback)."""
        p = create_provider("just-random-chars", model="openai/gpt-4o")
        assert isinstance(p, OpenRouterProvider)
        assert not isinstance(p, PolzaProvider)

    def test_unknown_keyword_is_rejected(self):
        with pytest.raises(TypeError):
            create_provider("sk-or-test", site_name="typo")

    def test_for_config_takes_the_key_model_and_prompt_settings_of_the_run(self, tmp_path):
        config = TranslationConfig(
            api_key="pza-abcdef1234567890",
            model="openai/gpt-4o",
            input_file=tmp_path / "m.mod",
            player_gender="female",
        )
        recorder = RunMetricsRecorder()

        p = create_provider_for_config(config, recorder)

        assert isinstance(p, PolzaProvider)
        assert (p.model, p.player_gender) == ("openai/gpt-4o", "female")
        assert p.metrics_recorder is recorder


class TestProviderLabel:
    def test_labels(self):
        assert provider_label("openrouter") == "OpenRouter"
        assert provider_label("polza") == "POLZA.AI"
        assert provider_label("") == ""
        assert provider_label("unknown") == ""


class TestDetectProviderFromKey:
    """Tests for detect_provider_from_key."""

    def test_empty_key_returns_empty(self):
        assert detect_provider_from_key("") == ""
        assert detect_provider_from_key(None) == ""
        assert detect_provider_from_key("   ") == ""

    def test_openrouter_prefix(self):
        assert detect_provider_from_key("sk-or-v1-abc") == "openrouter"

    def test_polza_prefix(self):
        assert detect_provider_from_key("pza-abcdef1234567890") == "polza"
        assert detect_provider_from_key("pza_abcdef1234567890") == "polza"

    def test_unknown_prefix_falls_back_to_default(self):
        assert detect_provider_from_key("sk-abc-123") == "openrouter"


class TestTranslationItem:
    """Tests for TranslationItem."""

    def test_create_simple_item(self):
        """Test creating a simple translation item."""
        item = TranslationItem(original="Hello world")
        assert item.original == "Hello world"
        assert item.context is None
        assert item.metadata == {}

    def test_create_item_with_context(self):
        """Test creating item with context."""
        item = TranslationItem(original="Hello", context="Greeting", metadata={"speaker": "NPC"})
        assert item.context == "Greeting"
        assert item.metadata["speaker"] == "NPC"


class TestTranslationResult:
    """Tests for TranslationResult."""

    def test_create_successful_result(self):
        """Test creating a successful result."""
        result = TranslationResult(translated="Hola", original="Hello", success=True)
        assert result.translated == "Hola"
        assert result.original == "Hello"
        assert result.success
        assert result.error is None

    def test_create_failed_result(self):
        """Test creating a failed result."""
        result = TranslationResult(
            translated="", original="Hello", success=False, error="API error"
        )
        assert not result.success
        assert result.error == "API error"
