"""Tests for AI provider base types and create_provider (OpenRouter)."""

import pytest
from unittest.mock import patch

from src.nwn_translator.ai_providers.base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
)
from src.nwn_translator.ai_providers import create_provider, detect_provider_from_key
from src.nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from src.nwn_translator.ai_providers.polza_provider import PolzaProvider


class MockAIProvider(BaseAIProvider):
    """Mock AI provider for testing."""

    def get_default_model(self) -> str:
        return "mock-model"

    def get_provider_name(self) -> str:
        return "mock"

    def translate(self, text, source_lang, target_lang, context=None, glossary_block=None):
        return TranslationResult(
            translated=f"[{target_lang}] {text}",
            original=text,
            success=True,
        )


class TestBaseAIProvider:
    """Tests for BaseAIProvider."""

    def test_init_requires_api_key(self):
        """Test that provider requires API key."""
        with pytest.raises(ProviderError):
            MockAIProvider(api_key="")

    def test_get_provider_name(self):
        """Test getting provider name."""
        provider = MockAIProvider(api_key="test-key")
        assert provider.get_provider_name() == "mock"

    def test_get_default_model(self):
        """Test getting default model."""
        provider = MockAIProvider(api_key="test-key")
        assert provider.get_default_model() == "mock-model"

    def test_translate_simple_text(self):
        """Test translating simple text."""
        provider = MockAIProvider(api_key="test-key")
        result = provider.translate("Hello", "english", "spanish")
        assert result.success
        assert "spanish" in result.translated
        assert result.original == "Hello"

    def test_translate_with_context(self):
        """Test translating with context."""
        provider = MockAIProvider(api_key="test-key")
        result = provider.translate(
            "Hello", "english", "spanish", context="Greeting"
        )
        assert result.success



class TestCreateProvider:
    """Tests for create_provider factory."""

    def test_create_returns_openrouter(self):
        """create_provider must return OpenRouterProvider."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = create_provider("sk-or-test", model="openai/gpt-4o")
        assert isinstance(p, OpenRouterProvider)
        assert p.model == "openai/gpt-4o"

    def test_create_returns_polza_for_pza_prefix(self):
        """``pza…`` keys route to PolzaProvider with the Polza base URL."""
        with patch(
            "src.nwn_translator.ai_providers.openrouter_provider.OpenAI"
        ) as mock_openai_cls:
            p = create_provider("pza-abcdef1234567890", model="openai/gpt-4o")
        assert isinstance(p, PolzaProvider)
        assert p.get_provider_name() == "polza"
        assert mock_openai_cls.call_args.kwargs["base_url"] == "https://polza.ai/api/v1"

    def test_create_falls_back_to_openrouter_for_unknown_prefix(self):
        """Unrecognised keys default to OpenRouter (safe fallback)."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = create_provider("just-random-chars", model="openai/gpt-4o")
        assert isinstance(p, OpenRouterProvider)
        assert not isinstance(p, PolzaProvider)


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
        item = TranslationItem(
            original="Hello", context="Greeting", metadata={"speaker": "NPC"}
        )
        assert item.context == "Greeting"
        assert item.metadata["speaker"] == "NPC"


class TestTranslationResult:
    """Tests for TranslationResult."""

    def test_create_successful_result(self):
        """Test creating a successful result."""
        result = TranslationResult(
            translated="Hola", original="Hello", success=True
        )
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
