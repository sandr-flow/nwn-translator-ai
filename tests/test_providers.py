"""Tests for AI provider base types and create_provider (OpenRouter)."""

import pytest
from unittest.mock import patch

from src.nwn_translator.ai_providers.base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
)
from src.nwn_translator.ai_providers import create_provider
from src.nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider


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

    def translate_batch(self, items, source_lang, target_lang):
        results = []
        for item in items:
            result = self.translate(
                item.original, source_lang, target_lang, item.context
            )
            results.append(result)
        return results


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

    def test_translate_batch(self):
        """Test batch translation."""
        provider = MockAIProvider(api_key="test-key")
        items = [
            TranslationItem(original="Hello"),
            TranslationItem(original="Goodbye"),
        ]
        results = provider.translate_batch(items, "english", "spanish")
        assert len(results) == 2
        assert all(r.success for r in results)


class TestCreateProvider:
    """Tests for create_provider factory."""

    def test_create_returns_openrouter(self):
        """create_provider must return OpenRouterProvider."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = create_provider("sk-or-test", model="openai/gpt-4o")
        assert isinstance(p, OpenRouterProvider)
        assert p.model == "openai/gpt-4o"


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
