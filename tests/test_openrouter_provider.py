"""Tests for OpenRouterProvider."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai import AuthenticationError, BadRequestError, InternalServerError

from nwn_translator.async_utils import run_async
from nwn_translator.ai_providers import errors, openrouter_provider
from nwn_translator.ai_providers.base import ProviderError, RateLimitError, TranslationItem
from nwn_translator.ai_providers.errors import OpenRouterError
from nwn_translator.ai_providers.openrouter_provider import (
    OpenRouterProvider,
    parse_single_translation,
)
from nwn_translator.telemetry import RunMetricsRecorder

FAKE_KEY = "sk-or-v1-test1234"
_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def _response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _status_error(cls, status: int, message: str = "boom"):
    return cls(message, response=httpx.Response(status, request=_REQUEST), body=None)


class FakeAPI:
    """Stand-in for ``AsyncOpenAI``: records clients and requests, replays replies.

    A reply is a string (the message content), an exception to raise or a ready
    response object; once the queue is empty every request gets
    ``{"translation": "ok"}``.
    """

    def __init__(self) -> None:
        self.clients: list = []
        self.calls: list = []
        self.replies: list = []

    def __call__(self, **client_kwargs):
        self.clients.append(client_kwargs)

        async def create(**kwargs):
            self.calls.append(kwargs)
            reply = self.replies.pop(0) if self.replies else '{"translation": "ok"}'
            if isinstance(reply, BaseException):
                raise reply
            return _response(reply) if isinstance(reply, str) else reply

        async def close():
            return None

        completions = SimpleNamespace(create=create)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions), close=close)


@pytest.fixture
def api(monkeypatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(openrouter_provider, "AsyncOpenAI", fake)
    return fake


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(errors, "_EXPONENTIAL_WAIT", lambda _state: 0)


def _translate(provider: OpenRouterProvider, text: str = "text", **kwargs):
    return run_async(provider.translate_async(text, "english", "russian", **kwargs), timeout=5.0)


class TestOpenRouterProviderInit:
    """Verify provider initialisation."""

    def test_invalid_reasoning_effort_raises(self):
        with pytest.raises(ValueError, match="Invalid reasoning_effort"):
            OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="invalid")

    def test_missing_api_key_is_reported_before_the_effort(self):
        with pytest.raises(ProviderError, match="API key is required"):
            OpenRouterProvider(api_key=" ", reasoning_effort="invalid")

    def test_provider_name(self):
        assert OpenRouterProvider(api_key=FAKE_KEY).get_provider_name() == "openrouter"

    def test_default_model(self):
        assert OpenRouterProvider(api_key=FAKE_KEY).model == "google/gemini-3.8-flash"

    def test_popular_models_pool(self):
        assert OpenRouterProvider.DEFAULT_MODEL == "google/gemini-3.8-flash"
        assert OpenRouterProvider.POPULAR_MODELS == [
            "google/gemini-3.1-flash-lite",
            "google/gemini-3.5-flash-lite",
            "google/gemini-3.8-flash",
            "openai/gpt-5.6-luna",
        ]

    def test_custom_model(self):
        p = OpenRouterProvider(api_key=FAKE_KEY, model="anthropic/claude-3.5-sonnet")
        assert p.model == "anthropic/claude-3.5-sonnet"

    def test_client_uses_base_url_headers_and_no_sdk_retries(self, api):
        p = OpenRouterProvider(api_key=FAKE_KEY)
        _translate(p)
        (client_kwargs,) = api.clients
        assert client_kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert client_kwargs["api_key"] == FAKE_KEY
        assert "HTTP-Referer" in client_kwargs["default_headers"]
        assert client_kwargs["max_retries"] == 0


class TestOpenRouterTranslate:
    """Verify translate_async() behaviour."""

    def test_translate_success(self, api):
        api.replies.append('{"translation": "Привет, мир"}')
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY), "Hello, world")
        assert result.success is True
        assert result.translated == "Привет, мир"
        assert result.original == "Hello, world"
        assert result.metadata == {"model": "google/gemini-3.8-flash"}

    def test_translate_empty_text(self, api):
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY), "  ")
        assert result.success is True
        assert result.translated == ""
        assert api.calls == []

    def test_single_request_shape(self, api):
        p = OpenRouterProvider(api_key=FAKE_KEY, model="vendor/custom")
        _translate(p, "Sword", context="Item: sword_01")
        (call,) = api.calls
        assert call["messages"][1] == {
            "role": "user",
            "content": "Context Hint: Item: sword_01\n\nText to translate from english:\n\nSword",
        }
        assert call["temperature"] == 0.6
        assert call["max_tokens"] == 32768
        assert call["response_format"] == {"type": "json_object"}
        assert "stream" not in call
        assert "extra_body" not in call

    def test_translate_rate_limit_raises(self, api, no_backoff):
        api.replies.extend([Exception("429 rate_limit exceeded")] * 3)
        with pytest.raises(RateLimitError):
            _translate(OpenRouterProvider(api_key=FAKE_KEY))
        assert len(api.calls) == 3

    def test_translate_api_error_raises(self, api):
        api.replies.append(Exception("Internal server error"))
        with pytest.raises(OpenRouterError, match="OpenRouter translation failed"):
            _translate(OpenRouterProvider(api_key=FAKE_KEY))

    def test_translate_includes_reasoning_extra_body(self, api):
        _translate(OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="medium"))
        (call,) = api.calls
        assert call["extra_body"] == {"reasoning": {"effort": "medium"}}
        assert call["reasoning_effort"] == "medium"

    def test_translate_off_clamps_gemini_38_to_low(self, api):
        """Gemini 3.8 Flash has no none; Off must send low, not omit (omit = medium)."""
        _translate(OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="none"))
        (call,) = api.calls
        assert call["extra_body"] == {"reasoning": {"effort": "low"}}
        assert call["reasoning_effort"] == "low"

    def test_translate_off_sends_none_when_model_allows_it(self, api):
        p = OpenRouterProvider(
            api_key=FAKE_KEY, model="openai/gpt-5.6-luna", reasoning_effort="none"
        )
        _translate(p)
        (call,) = api.calls
        assert call["extra_body"] == {"reasoning": {"effort": "none"}}
        assert call["reasoning_effort"] == "none"

    def test_use_reasoning_false_clamps_gemini_38_to_low(self, api):
        """JSON-only calls must not omit reasoning (Gemini 3.8 would default to medium)."""
        p = OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="high")
        run_async(
            p.complete_json_chat_async(
                "S", "U", max_tokens=10, temperature=0.1, use_reasoning=False
            ),
            timeout=5.0,
        )
        (call,) = api.calls
        assert call["extra_body"] == {"reasoning": {"effort": "low"}}
        assert call["reasoning_effort"] == "low"
        assert call["stream"] is False

    def test_translate_bad_request_retries_without_reasoning(self, api):
        """HTTP 400 rejecting reasoning must retry once without extra_body."""
        api.replies.append(
            _status_error(BadRequestError, 400, "Reasoning is not supported by this model")
        )
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="high"))
        assert result.success is True
        assert len(api.calls) == 2
        assert "extra_body" not in api.calls[1]


class TestOpenRouterBatchTranslate:
    """Verify batch payload construction."""

    def test_batch_payload_prefers_hint_and_ncs_hint_over_type(self, api):
        api.replies.append('{"0": "Первая", "1": "Вторая", "2": "Третья"}')
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
            OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(items, "english", "russian"),
            timeout=5.0,
        )

        assert [r.translated for r in result] == ["Первая", "Вторая", "Третья"]
        assert all(
            r.metadata == {"model": "google/gemini-3.8-flash", "batch": True} for r in result
        )
        payload = json.loads(api.calls[0]["messages"][1]["content"].split("\n\n", 1)[1])
        assert payload["0"]["hint"] == "SpeakString"
        assert payload["1"]["hint"] == "SetCustomToken"
        assert payload["2"]["hint"] == "item_name"
        assert api.calls[0]["stream"] is False

    def test_batch_payload_includes_optional_context(self, api):
        api.replies.append('{"0": "Диван", "1": "Меч"}')
        items = [
            TranslationItem(
                original="The sofa seems warm and inviting.",
                context="Description of placeable 'Couch'",
                metadata={"type": "placeable_description"},
            ),
            TranslationItem(original="Sword"),
        ]
        result = run_async(
            OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(items, "english", "russian"),
            timeout=5.0,
        )

        assert [r.translated for r in result] == ["Диван", "Меч"]
        payload = json.loads(api.calls[0]["messages"][1]["content"].split("\n\n", 1)[1])
        assert payload["0"]["text"] == "The sofa seems warm and inviting."
        assert payload["0"]["hint"] == "placeable_description"
        assert payload["0"]["context"] == "Description of placeable 'Couch'"
        # No hint and no context — the entry stays a plain string.
        assert payload["1"] == "Sword"

    def test_batch_system_prompt_caches_batch_rules_with_the_stable_half(self, api):
        run_async(
            OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(
                [TranslationItem("A")], "english", "russian", glossary_block="GLOSSARY: X"
            ),
            timeout=5.0,
        )
        cached, variable = api.calls[0]["messages"][0]["content"]
        assert cached["cache_control"] == {"type": "ephemeral"}
        assert cached["text"].endswith("Do NOT wrap in markdown. Output ONLY the JSON object.\n")
        assert "BATCH MODE" in cached["text"]
        assert variable == {"type": "text", "text": "GLOSSARY: X"}

    def test_batch_parses_raw_newlines_inside_values(self, api):
        api.replies.append('{"0": "строка1\nстрока2"}')
        result = run_async(
            OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(
                [TranslationItem(original="line1\nline2")], "english", "russian"
            ),
            timeout=5.0,
        )
        assert result[0].success is True
        assert result[0].translated == "строка1\nстрока2"

    def test_parse_error_is_reported_per_item_with_the_decoder_message(self, api):
        api.replies.append('```json\n{"0": "Труба')
        result = run_async(
            OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(
                [TranslationItem("Pipe"), TranslationItem("Horn")], "english", "russian"
            ),
            timeout=5.0,
        )
        assert [r.error for r in result] == [
            "Batch JSON parse error: Unterminated string starting at: line 1 column 7 (char 6)"
        ] * 2
        assert all(r.metadata == {} and not r.success for r in result)


class TestOpenRouterRetryOn5xx:
    """5xx responses must retry with backoff; other 4xx must not."""

    @pytest.fixture(autouse=True)
    def _no_backoff(self, no_backoff):
        return None

    def test_5xx_retries_and_succeeds_on_second_attempt(self, api):
        api.replies.extend([_status_error(InternalServerError, 502), '{"translation": "Готово"}'])
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY))
        assert result.success is True
        assert result.translated == "Готово"
        assert len(api.calls) == 2

    def test_5xx_exhausts_attempts_then_reraises(self, api):
        api.replies.extend([_status_error(InternalServerError, 500)] * 3)
        with pytest.raises(InternalServerError):
            _translate(OpenRouterProvider(api_key=FAKE_KEY))
        assert len(api.calls) == 3

    def test_4xx_does_not_retry(self, api):
        api.replies.append(_status_error(AuthenticationError, 401))
        with pytest.raises(OpenRouterError):
            _translate(OpenRouterProvider(api_key=FAKE_KEY))
        assert len(api.calls) == 1


class TestReasoningFallbackMemory:
    """A 'reasoning not supported' 400 is remembered; unrelated 400s propagate."""

    def test_reasoning_rejection_is_remembered_for_the_session(self, api):
        p = OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="medium")
        api.replies.append(
            _status_error(BadRequestError, 400, "Reasoning is not supported by this model")
        )
        assert _translate(p, "a").success is True
        assert _translate(p, "b").success is True

        assert len(api.calls) == 3  # 400 + fallback + single second-request call
        assert "extra_body" in api.calls[0]
        assert "extra_body" not in api.calls[1]
        assert "extra_body" not in api.calls[2]

    def test_unrelated_400_propagates_without_fallback(self, api):
        p = OpenRouterProvider(api_key=FAKE_KEY, reasoning_effort="medium")
        api.replies.append(
            _status_error(BadRequestError, 400, "This model's maximum context length is exceeded")
        )
        with pytest.raises(OpenRouterError):
            _translate(p, "a")
        assert len(api.calls) == 1
        assert "extra_body" in api.calls[0]


class TestNoJsonResponseRejected:
    """Model chatter without a JSON object must fail the item, not ship as a translation."""

    def test_translate_chatter_without_json_fails(self, api):
        api.replies.extend(["Sure! Here is the translation: Привет, мир"] * 2)
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY), "Hello, world")
        assert result.success is False
        assert result.translated == ""
        assert result.error == "Model returned empty or unparseable JSON"
        assert result.metadata == {"model": "google/gemini-3.8-flash"}

    def test_parse_rejects_chatter_but_keeps_fenced_json(self):
        parse = parse_single_translation
        assert parse("I cannot translate this content.") == ""
        assert parse("") == ""
        assert parse('{"translation": ""}') == ""
        assert parse('{"translation": ["x"]}') == ""
        assert parse('```json\n{"translation": "Привет"}\n```') == "Привет"
        assert parse('Sure! {"translation": "Привет"}') == "Привет"
        assert parse('{"translation": "строка1\nстрока2"}') == "строка1\nстрока2"


class TestTranslateAsyncJsonRetry:
    """translate_async retries once when the model returns unparseable JSON."""

    def test_translate_async_retries_once_on_unparseable_json(self, api):
        api.replies.extend(
            ["Sure, here is the translation without JSON", '{"translation": "Список покупок"}']
        )
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY), "Shopping list")
        assert result.success is True
        assert result.translated == "Список покупок"
        assert len(api.calls) == 2
        assert api.calls[0] == api.calls[1]

    def test_translate_async_unparseable_twice_fails(self, api):
        api.replies.extend(["not json at all"] * 2)
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY), "Shopping list")
        assert result.success is False
        assert "unparseable" in (result.error or "")
        assert len(api.calls) == 2


class TestTransientRetryScope:
    """A transient error repeats only the failed request, not the whole task."""

    @pytest.fixture(autouse=True)
    def _no_backoff(self, no_backoff):
        return None

    def test_gate_retries_only_the_failed_sub_request(self, api):
        api.replies.extend(
            [
                "not json",  # whole batch, first budget
                "not json",  # whole batch, doubled budget
                '{"0": {"translate": true, "reason": "left"}}',
                _status_error(InternalServerError, 502),  # right half
                '{"0": {"translate": false, "reason": "right"}}',
            ]
        )
        entries = [{"key": "0", "text": "A"}, {"key": "1", "text": "B"}]
        verdicts = run_async(
            OpenRouterProvider(api_key=FAKE_KEY).classify_ncs_translate_gate_batch_async(
                entries, source_lang="english"
            ),
            timeout=5.0,
        )
        assert verdicts == {
            "0": {"translate": True, "reason": "left"},
            "1": {"translate": False, "reason": "right"},
        }
        assert [call["max_tokens"] for call in api.calls] == [8192, 16384, 8192, 8192, 8192]

    def test_transient_error_does_not_reset_the_json_attempts(self, api):
        api.replies.extend(["no json", _status_error(InternalServerError, 502), "no json"])
        result = _translate(OpenRouterProvider(api_key=FAKE_KEY))
        assert result.error == "Model returned empty or unparseable JSON"
        assert len(api.calls) == 3

    def test_glossary_requests_are_not_retried(self, api):
        api.replies.append(_status_error(InternalServerError, 502))
        with pytest.raises(InternalServerError):
            run_async(
                OpenRouterProvider(api_key=FAKE_KEY).complete_glossary_chat_async(
                    "S", "U", glossary_keys=["A"], max_tokens=10, temperature=0.3
                ),
                timeout=5.0,
            )
        assert len(api.calls) == 1

    def test_task_methods_keep_their_names(self):
        names = [
            OpenRouterProvider.translate_async.__name__,
            OpenRouterProvider.translate_batch_async.__name__,
            OpenRouterProvider.complete_json_chat_async.__name__,
            OpenRouterProvider.complete_glossary_chat_async.__name__,
            OpenRouterProvider.classify_ncs_translate_gate_batch_async.__name__,
        ]
        assert names == [
            "translate_async",
            "translate_batch_async",
            "complete_json_chat_async",
            "complete_glossary_chat_async",
            "classify_ncs_translate_gate_batch_async",
        ]


class TestBatchErrorMapping:
    def test_api_error_is_prefixed_once(self, api):
        denied = _status_error(AuthenticationError, 401, "denied")
        api.replies.append(denied)
        with pytest.raises(OpenRouterError) as exc_info:
            run_async(
                OpenRouterProvider(api_key=FAKE_KEY).translate_batch_async(
                    [TranslationItem("A")], "english", "russian"
                ),
                timeout=5.0,
            )
        assert str(exc_info.value) == "OpenRouter translation failed: denied"
        assert exc_info.value.__cause__ is denied


class TestRequestMetrics:
    def test_one_metric_per_attempt_with_prompt_split(self, api):
        recorder = RunMetricsRecorder()
        p = OpenRouterProvider(api_key=FAKE_KEY, metrics_recorder=recorder)
        api.replies.extend(["no json", '{"translation": "ok"}'])
        _translate(p, "Hello", glossary_block="GLOSSARY: X")
        first, second = recorder.requests
        assert (first.phase, first.batch_size, first.glossary_chars) == (
            "generic_single",
            1,
            len("GLOSSARY: X"),
        )
        assert first.variable_chars == len("GLOSSARY: X")
        assert first.success and second.success
        assert first.estimated_output_tokens == 2  # ceil(len("no json") / 4)

    def test_every_failed_single_attempt_is_recorded(self, api, no_backoff):
        recorder = RunMetricsRecorder()
        p = OpenRouterProvider(api_key=FAKE_KEY, metrics_recorder=recorder)
        limited = _status_error(openai.RateLimitError, 429, "slow down")
        api.replies.extend([limited, _status_error(InternalServerError, 502)])
        assert _translate(p).success
        assert [(m.success, m.error, m.phase) for m in recorder.requests] == [
            (False, "slow down", "generic_single"),
            (False, "boom", "generic_single"),
            (True, None, "generic_single"),
        ]

    def test_non_transient_failure_is_recorded(self, api):
        recorder = RunMetricsRecorder()
        p = OpenRouterProvider(api_key=FAKE_KEY, metrics_recorder=recorder)
        api.replies.append(_status_error(AuthenticationError, 401, "denied"))
        with pytest.raises(OpenRouterError):
            run_async(
                p.translate_batch_async([TranslationItem("A")], "english", "russian"),
                timeout=5.0,
            )
        (metric,) = recorder.requests
        assert (metric.success, metric.error, metric.batch_size) == (False, "denied", 1)

    @pytest.mark.parametrize("choices", [[], None])
    def test_reply_without_choices_is_a_failed_attempt(self, api, choices):
        recorder = RunMetricsRecorder()
        p = OpenRouterProvider(api_key=FAKE_KEY, metrics_recorder=recorder)
        api.replies.append(SimpleNamespace(choices=choices, usage=None))
        with pytest.raises(OpenRouterError, match="^OpenRouter translation failed: "):
            run_async(
                p.translate_batch_async([TranslationItem("A")], "english", "russian"),
                timeout=5.0,
            )
        (metric,) = recorder.requests
        assert (metric.success, metric.phase, metric.estimated_output_tokens) == (
            False,
            "generic_batch",
            0,
        )
        assert metric.error

    def test_transient_batch_failure_is_recorded(self, api, no_backoff):
        recorder = RunMetricsRecorder()
        p = OpenRouterProvider(api_key=FAKE_KEY, metrics_recorder=recorder)
        api.replies.append(_status_error(InternalServerError, 503))
        run_async(
            p.translate_batch_async([TranslationItem("A")], "english", "russian"), timeout=5.0
        )
        failed, ok = recorder.requests
        assert (failed.success, failed.phase, failed.error) == (False, "generic_batch", "boom")
        assert ok.success
