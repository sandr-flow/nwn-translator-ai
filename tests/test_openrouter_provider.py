"""Tests for OpenRouterProvider."""

import json
from unittest.mock import MagicMock, patch
import httpx
import pytest
from openai import AuthenticationError, BadRequestError, InternalServerError

from src.nwn_translator.async_utils import run_async
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
        """HTTP 400 rejecting reasoning must retry once without extra_body."""
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
        br = BadRequestError("Reasoning is not supported by this model", response=resp, body={})

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [br, mock_response]
        p.client = mock_client

        result = p.translate("a", "english", "russian")
        assert result.success is True
        assert mock_client.chat.completions.create.call_count == 2
        second = mock_client.chat.completions.create.call_args_list[1].kwargs
        assert "extra_body" not in second


class TestOpenRouterBatchTranslate:
    """Verify batch payload construction."""

    def test_batch_payload_prefers_hint_and_ncs_hint_over_type(self):
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            provider = OpenRouterProvider(api_key=FAKE_KEY)
        captured = {}

        async def complete(system_content, user_prompt, **kwargs):
            captured["user_prompt"] = user_prompt
            return '{"0": "Первая", "1": "Вторая", "2": "Третья"}'

        provider._chat_completion_json_async = complete
        items = [
            TranslationItem(
                original="First",
                metadata={"type": "ncs_string", "ncs_hint": "SpeakString"},
            ),
            TranslationItem(
                original="Second",
                metadata={"type": "ncs_string", "hint": "SetCustomToken"},
            ),
            TranslationItem(original="Third", metadata={"type": "item_name"}),
        ]

        result = run_async(
            provider.translate_batch_async(items, "english", "russian"),
            timeout=5.0,
        )

        assert [r.translated for r in result] == ["Первая", "Вторая", "Третья"]
        payload = json.loads(captured["user_prompt"].split("\n\n", 1)[1])
        assert payload["0"]["hint"] == "SpeakString"
        assert payload["1"]["hint"] == "SetCustomToken"
        assert payload["2"]["hint"] == "item_name"


class TestCreateProvider:
    """Verify create_provider returns OpenRouter."""

    def test_create_provider_returns_openrouter(self):
        from src.nwn_translator.ai_providers import create_provider

        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            p = create_provider(FAKE_KEY)
        assert p.get_provider_name() == "openrouter"


class TestOpenRouterRetryOn5xx:
    """5xx responses must retry with backoff; other 4xx must not."""

    def _make_provider(self, side_effect) -> OpenRouterProvider:
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            provider = OpenRouterProvider(api_key=FAKE_KEY)
        mock_msg = MagicMock()
        mock_msg.content = '{"translation": "Готово"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            e if e is not None else mock_response for e in side_effect
        ]
        provider.client = mock_client
        return provider

    @staticmethod
    def _status_error(cls, status: int):
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        return cls("boom", response=httpx.Response(status, request=req), body=None)

    @pytest.fixture(autouse=True)
    def _no_backoff_sleep(self, monkeypatch):
        monkeypatch.setattr(OpenRouterProvider.translate.retry, "sleep", lambda *_: None)

    def test_5xx_retries_and_succeeds_on_second_attempt(self):
        ise = self._status_error(InternalServerError, 502)
        p = self._make_provider([ise, None])
        result = p.translate("text", "english", "russian")
        assert result.success is True
        assert result.translated == "Готово"
        assert p.client.chat.completions.create.call_count == 2

    def test_5xx_exhausts_attempts_then_reraises(self):
        ise = self._status_error(InternalServerError, 500)
        p = self._make_provider([ise, ise, ise])
        with pytest.raises(InternalServerError):
            p.translate("text", "english", "russian")
        assert p.client.chat.completions.create.call_count == 3

    def test_4xx_does_not_retry(self):
        auth = self._status_error(AuthenticationError, 401)
        p = self._make_provider([auth])
        with pytest.raises(OpenRouterError):
            p.translate("text", "english", "russian")
        assert p.client.chat.completions.create.call_count == 1


class TestReasoningFallbackMemory:
    """A 'reasoning not supported' 400 is remembered; unrelated 400s propagate."""

    def _make_provider(self) -> OpenRouterProvider:
        with patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI"):
            return OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="medium")

    @staticmethod
    def _mock_response():
        mock_msg = MagicMock()
        mock_msg.content = '{"translation": "x"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    @staticmethod
    def _bad_request(message: str) -> BadRequestError:
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        return BadRequestError(message, response=httpx.Response(400, request=req), body={})

    def test_reasoning_rejection_is_remembered_for_the_session(self):
        p = self._make_provider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            self._bad_request("Reasoning is not supported by this model"),
            self._mock_response(),
            self._mock_response(),
        ]
        p.client = mock_client

        assert p.translate("a", "english", "russian").success is True
        assert p.translate("b", "english", "russian").success is True

        calls = mock_client.chat.completions.create.call_args_list
        assert len(calls) == 3  # 400 + fallback + single second-request call
        assert "extra_body" in calls[0].kwargs
        assert "extra_body" not in calls[1].kwargs
        assert "extra_body" not in calls[2].kwargs

    def test_unrelated_400_propagates_without_fallback(self):
        p = self._make_provider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = self._bad_request(
            "This model's maximum context length is exceeded"
        )
        p.client = mock_client

        with pytest.raises(OpenRouterError):
            p.translate("a", "english", "russian")
        assert mock_client.chat.completions.create.call_count == 1
        assert "extra_body" in mock_client.chat.completions.create.call_args.kwargs

    def test_async_path_respects_remembered_flag(self):
        p = self._make_provider()
        p._reasoning_unsupported = True
        mock_async_client = MagicMock()

        async def create(**kwargs):
            create.calls.append(kwargs)
            return self._mock_response()

        create.calls = []
        mock_async_client.chat.completions.create = create
        p._thread_local.async_client = mock_async_client
        p._thread_local.last_loop_id = None

        async def run():
            p._thread_local.async_client = mock_async_client
            p._thread_local.last_loop_id = id(__import__("asyncio").get_running_loop())
            return await p._chat_completions_create_async(model="m", messages=[])

        run_async(run(), timeout=5.0)
        assert len(create.calls) == 1
        assert "extra_body" not in create.calls[0]
