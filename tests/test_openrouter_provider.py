"""Tests for OpenRouterProvider."""

from unittest.mock import MagicMock, patch
import httpx
import pytest
from openai import BadRequestError

from src.nwn_translator.ai_providers.openrouter_provider import (
    OpenRouterProvider,
    OpenRouterError,
)
from src.nwn_translator.ai_providers.base import TranslationItem, RateLimitError

FAKE_KEY = "sk-or-v1-test1234"


class TestOpenRouterProviderInit:
    """Verify provider initialisation."""

    def test_invalid_reasoning_effort_raises(self):
        """Unknown reasoning_effort must raise ValueError."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            with pytest.raises(ValueError, match="Invalid reasoning_effort"):
                OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="invalid")

    def test_provider_name(self):
        """get_provider_name() must return 'openrouter'."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = OpenRouterProvider(api_key=FAKE_KEY)
        assert p.get_provider_name() == "openrouter"

    def test_default_model(self):
        """Default model must match OpenRouterProvider.DEFAULT_MODEL."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = OpenRouterProvider(api_key=FAKE_KEY)
        assert p.model == OpenRouterProvider.DEFAULT_MODEL

    def test_custom_model(self):
        """Custom model slug is stored correctly."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = OpenRouterProvider(api_key=FAKE_KEY, model="anthropic/claude-3.5-sonnet")
        assert p.model == "anthropic/claude-3.5-sonnet"

    def test_base_url_passed_to_client(self):
        """OpenAI client must be created with OpenRouter base_url."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI") as mock_openai_cls:
            OpenRouterProvider(api_key=FAKE_KEY)
        call_kwargs = mock_openai_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"

    def test_http_referer_header_present(self):
        """HTTP-Referer header must be forwarded to the OpenAI client."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI") as mock_openai_cls:
            OpenRouterProvider(api_key=FAKE_KEY)
        headers = mock_openai_cls.call_args.kwargs.get("default_headers", {})
        assert "HTTP-Referer" in headers

    def test_missing_api_key_raises(self):
        """Empty API key must raise ProviderError."""
        from src.nwn_translator.ai_providers.base import ProviderError

        with pytest.raises(ProviderError):
            OpenRouterProvider(api_key="")


class TestOpenRouterTranslate:
    """Verify translate() method behaviour."""

    def _make_provider(self, translated_text: str = "Translated") -> OpenRouterProvider:
        """Build a provider with a mocked OpenAI client."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI") as mock_cls:
            provider = OpenRouterProvider(api_key=FAKE_KEY)

        # Inject mock client after init
        mock_msg = MagicMock()
        mock_msg.content = translated_text
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider.client = mock_client
        return provider

    def test_translate_success(self):
        """Successful translation returns correct TranslationResult."""
        p = self._make_provider("Привет, мир")
        result = p.translate("Hello, world", "english", "russian")
        assert result.success is True
        assert result.translated == "Привет, мир"
        assert result.original == "Hello, world"

    def test_translate_empty_text(self):
        """Empty input must return empty result without calling the API."""
        p = self._make_provider()
        result = p.translate("", "english", "russian")
        assert result.success is True
        assert result.translated == ""
        p.client.chat.completions.create.assert_not_called()

    def test_translate_with_context(self):
        """Context is forwarded to the prompt builder."""
        p = self._make_provider("Меч")
        result = p.translate("Sword", "english", "russian", context="Item: sword_01")
        assert result.success is True
        # The create() call receives the messages; verify it was called once
        p.client.chat.completions.create.assert_called_once()
        messages = p.client.chat.completions.create.call_args.kwargs["messages"]
        assert any("sword_01" in m["content"] for m in messages)

    def test_translate_rate_limit_raises(self):
        """HTTP 429 responses must raise RateLimitError."""
        p = self._make_provider()
        p.client.chat.completions.create.side_effect = Exception("429 rate_limit exceeded")
        with pytest.raises(RateLimitError):
            p.translate("text", "english", "russian")

    def test_translate_api_error_raises(self):
        """Non-rate-limit API errors must raise OpenRouterError."""
        p = self._make_provider()
        p.client.chat.completions.create.side_effect = Exception("Internal server error")
        with pytest.raises(OpenRouterError):
            p.translate("text", "english", "russian")

    def test_translate_includes_reasoning_extra_body(self):
        """When reasoning_effort is set, chat.completions.create gets extra_body."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="medium")
        mock_msg = MagicMock()
        mock_msg.content = '{"translation": "x"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        p.client = mock_client
        result = p.translate("a", "english", "russian")
        assert result.success is True
        kw = mock_client.chat.completions.create.call_args.kwargs
        assert kw["extra_body"] == {"reasoning": {"effort": "medium"}}

    def test_translate_bad_request_retries_without_reasoning(self):
        """HTTP 400 with reasoning must retry once without extra_body."""
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="high")

        mock_msg = MagicMock()
        mock_msg.content = '{"translation": "y"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        resp = httpx.Response(400, request=req)
        br = BadRequestError("nope", response=resp, body={"error": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [br, mock_response]
        p.client = mock_client

        result = p.translate("a", "english", "russian")
        assert result.success is True
        assert mock_client.chat.completions.create.call_count == 2
        second = mock_client.chat.completions.create.call_args_list[1].kwargs
        assert "extra_body" not in second


class TestCreateProvider:
    """Verify create_provider returns OpenRouter."""

    def test_create_provider_returns_openrouter(self):
        from src.nwn_translator.ai_providers import create_provider

        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = create_provider(FAKE_KEY)
        assert p.get_provider_name() == "openrouter"
