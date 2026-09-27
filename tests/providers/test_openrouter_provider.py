"""OpenRouterProvider requests, parsing, retries and metrics against a fake API."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai import AuthenticationError, BadRequestError, InternalServerError

from nwn_translator.ai_providers import errors, openrouter_provider
from nwn_translator.ai_providers.base import ProviderError, RateLimitError, TranslationItem
from nwn_translator.ai_providers.errors import OpenRouterError
from nwn_translator.ai_providers.openrouter_provider import (
    OpenRouterProvider,
    parse_single_translation,
)
from nwn_translator.async_utils import run_async, shutdown_thread_loop
from nwn_translator.telemetry import RunMetricsRecorder

FAKE_KEY = "sk-or-v1-test1234"
MODEL = "google/gemini-3.8-flash"
_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


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
            if isinstance(reply, str):
                message = SimpleNamespace(content=reply)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
            return reply

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


def _provider(**kwargs) -> OpenRouterProvider:
    return OpenRouterProvider(api_key=FAKE_KEY, **kwargs)


def _translate(provider: OpenRouterProvider, text: str = "text", **kwargs):
    return run_async(provider.translate_async(text, "english", "russian", **kwargs), timeout=5.0)


def _batch(provider: OpenRouterProvider, items, **kwargs):
    return run_async(
        provider.translate_batch_async(items, "english", "russian", **kwargs), timeout=5.0
    )


def _payload(call) -> dict:
    return json.loads(call["messages"][1]["content"].split("\n\n", 1)[1])


# ---------------------------------------------------------------------------
# Construction and the HTTP client
# ---------------------------------------------------------------------------


def test_construction_and_models():
    with pytest.raises(ValueError, match="Invalid reasoning_effort"):
        _provider(reasoning_effort="invalid")
    with pytest.raises(ProviderError, match="API key is required"):
        OpenRouterProvider(api_key=" ", reasoning_effort="invalid")
    provider = _provider()
    assert (provider.get_provider_name(), provider.model) == ("openrouter", MODEL)
    assert _provider(model="anthropic/claude-3.5-sonnet").model == "anthropic/claude-3.5-sonnet"
    assert OpenRouterProvider.DEFAULT_MODEL == MODEL
    assert OpenRouterProvider.POPULAR_MODELS == [
        "google/gemini-3.1-flash-lite",
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.8-flash",
        "openai/gpt-5.6-luna",
    ]


def test_client_uses_base_url_headers_and_no_sdk_retries(api):
    _translate(_provider())
    (client_kwargs,) = api.clients
    assert client_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert client_kwargs["api_key"] == FAKE_KEY
    assert "HTTP-Referer" in client_kwargs["default_headers"]
    assert client_kwargs["max_retries"] == 0


def test_one_client_per_thread_loop(api):
    """Sequential calls reuse the client; a new loop after a shutdown gets its own."""
    shutdown_thread_loop()
    provider = _provider()

    async def touch_client():
        return provider.async_client

    try:
        clients = [run_async(touch_client()) for _ in range(5)]
        assert len(api.clients) == 1
        assert all(client is clients[0] for client in clients)
        shutdown_thread_loop()
        run_async(touch_client())
        assert len(api.clients) == 2
    finally:
        shutdown_thread_loop()


@pytest.mark.parametrize(
    "variable, content",
    [
        (
            " VARIABLE\n",
            [
                {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "VARIABLE"},
            ],
        ),
        ("  ", "STABLE"),
    ],
)
def test_system_message_puts_the_cache_breakpoint_after_the_stable_part(variable, content):
    assert OpenRouterProvider.make_system_message_content("STABLE", variable) == content


def test_disabled_prompt_cache_sends_one_string(monkeypatch):
    """``NWN_TRANSLATE_PROMPT_CACHE=0`` joins the parts into a plain string."""
    monkeypatch.setattr(openrouter_provider, "PROMPT_CACHE_BREAKPOINTS_ENABLED", False)
    assert OpenRouterProvider.make_system_message_content("STABLE", "VAR") == "STABLE\n\nVAR"


def test_task_methods_keep_their_names():
    """The names are written into the translation log."""
    assert [
        OpenRouterProvider.translate_async.__name__,
        OpenRouterProvider.translate_batch_async.__name__,
        OpenRouterProvider.complete_json_chat_async.__name__,
        OpenRouterProvider.complete_glossary_chat_async.__name__,
        OpenRouterProvider.classify_ncs_translate_gate_batch_async.__name__,
    ] == [
        "translate_async",
        "translate_batch_async",
        "complete_json_chat_async",
        "complete_glossary_chat_async",
        "classify_ncs_translate_gate_batch_async",
    ]


# ---------------------------------------------------------------------------
# Single translations
# ---------------------------------------------------------------------------


def test_single_translation_request_and_result(api):
    api.replies.append('{"translation": "Привет, мир"}')
    result = _translate(_provider(model="vendor/custom"), "Hello, world", context="Item: sword_01")
    assert (result.success, result.translated, result.original) == (
        True,
        "Привет, мир",
        "Hello, world",
    )
    assert result.metadata == {"model": "vendor/custom"}
    (call,) = api.calls
    assert call["messages"][1] == {
        "role": "user",
        "content": "Context Hint: Item: sword_01\n\nText to translate from english:\n\nHello, world",
    }
    assert (call["temperature"], call["max_tokens"]) == (0.6, 32768)
    assert call["response_format"] == {"type": "json_object"}
    assert "stream" not in call and "extra_body" not in call


def test_blank_text_makes_no_request(api):
    result = _translate(_provider(), "  ")
    assert (result.success, result.translated, api.calls) == (True, "", [])


@pytest.mark.parametrize(
    "model, effort, sent",
    [
        (MODEL, "medium", "medium"),
        # Gemini 3.8 Flash has no "none": Off sends low, since an omitted effort means medium.
        (MODEL, "none", "low"),
        ("openai/gpt-5.6-luna", "none", "none"),
    ],
)
def test_reasoning_effort_is_sent_as_the_model_allows(api, model, effort, sent):
    _translate(_provider(model=model, reasoning_effort=effort))
    (call,) = api.calls
    assert call["extra_body"] == {"reasoning": {"effort": sent}}
    assert call["reasoning_effort"] == sent


def test_json_call_without_reasoning_still_clamps_gemini_38(api):
    """JSON-only calls must not omit reasoning (Gemini 3.8 would default to medium)."""
    provider = _provider(reasoning_effort="high")
    run_async(
        provider.complete_json_chat_async(
            "S", "U", max_tokens=10, temperature=0.1, use_reasoning=False
        ),
        timeout=5.0,
    )
    (call,) = api.calls
    assert call["extra_body"] == {"reasoning": {"effort": "low"}}
    assert (call["reasoning_effort"], call["stream"]) == ("low", False)


def test_rejected_reasoning_is_dropped_for_the_rest_of_the_session(api):
    provider = _provider(reasoning_effort="medium")
    api.replies.append(
        _status_error(BadRequestError, 400, "Reasoning is not supported by this model")
    )
    assert _translate(provider, "a").success is True
    assert _translate(provider, "b").success is True
    assert len(api.calls) == 3  # the 400, its retry without reasoning, the second text
    assert ["extra_body" in call for call in api.calls] == [True, False, False]


def test_unrelated_bad_request_propagates_without_the_fallback(api):
    api.replies.append(
        _status_error(BadRequestError, 400, "This model's maximum context length is exceeded")
    )
    with pytest.raises(OpenRouterError):
        _translate(_provider(reasoning_effort="medium"), "a")
    assert len(api.calls) == 1 and "extra_body" in api.calls[0]


def test_errors_are_mapped(api, no_backoff):
    api.replies.extend([Exception("429 rate_limit exceeded")] * 3)
    with pytest.raises(RateLimitError):
        _translate(_provider())
    assert len(api.calls) == 3
    api.replies.append(Exception("Internal server error"))
    with pytest.raises(OpenRouterError, match="OpenRouter translation failed"):
        _translate(_provider())


def test_unparseable_answer_is_asked_once_more_then_fails(api):
    api.replies.extend(
        ["Sure, here is the translation without JSON", '{"translation": "Список покупок"}']
    )
    result = _translate(_provider(), "Shopping list")
    assert (result.success, result.translated) == (True, "Список покупок")
    assert len(api.calls) == 2 and api.calls[0] == api.calls[1]

    api.calls.clear()
    api.replies.extend(["Sure! Here is the translation: Привет, мир"] * 2)
    result = _translate(_provider(), "Hello, world")
    assert (result.success, result.translated) == (False, "")
    assert result.error == "Model returned empty or unparseable JSON"
    assert result.metadata == {"model": MODEL}
    assert len(api.calls) == 2


@pytest.mark.parametrize(
    "raw, translation",
    [
        ("I cannot translate this content.", ""),
        ("", ""),
        ('{"translation": ""}', ""),
        ('{"translation": ["x"]}', ""),
        ('```json\n{"translation": "Привет"}\n```', "Привет"),
        ('Sure! {"translation": "Привет"}', "Привет"),
        ('{"translation": "строка1\nстрока2"}', "строка1\nстрока2"),
    ],
)
def test_single_answer_parsing_rejects_chatter(raw, translation):
    assert parse_single_translation(raw) == translation


# ---------------------------------------------------------------------------
# Transient errors
# ---------------------------------------------------------------------------


def test_5xx_is_retried_with_backoff_and_4xx_is_not(api, no_backoff):
    api.replies.extend([_status_error(InternalServerError, 502), '{"translation": "Готово"}'])
    result = _translate(_provider())
    assert (result.success, result.translated, len(api.calls)) == (True, "Готово", 2)

    api.calls.clear()
    api.replies.extend([_status_error(InternalServerError, 500)] * 3)
    with pytest.raises(InternalServerError):
        _translate(_provider())
    assert len(api.calls) == 3

    api.calls.clear()
    api.replies.append(_status_error(AuthenticationError, 401))
    with pytest.raises(OpenRouterError):
        _translate(_provider())
    assert len(api.calls) == 1


def test_gate_retries_only_the_failed_sub_request(api, no_backoff):
    api.replies.extend(
        [
            "not json",  # the whole batch, first budget
            "not json",  # the whole batch, doubled budget
            '{"0": {"translate": true, "reason": "left"}}',
            _status_error(InternalServerError, 502),  # the right half
            '{"0": {"translate": false, "reason": "right"}}',
        ]
    )
    entries = [{"key": "0", "text": "A"}, {"key": "1", "text": "B"}]
    verdicts = run_async(
        _provider().classify_ncs_translate_gate_batch_async(entries, source_lang="english"),
        timeout=5.0,
    )
    assert verdicts == {
        "0": {"translate": True, "reason": "left"},
        "1": {"translate": False, "reason": "right"},
    }
    assert [call["max_tokens"] for call in api.calls] == [8192, 16384, 8192, 8192, 8192]


def test_transient_error_does_not_reset_the_json_attempts(api, no_backoff):
    api.replies.extend(["no json", _status_error(InternalServerError, 502), "no json"])
    assert _translate(_provider()).error == "Model returned empty or unparseable JSON"
    assert len(api.calls) == 3


def test_glossary_requests_are_not_retried(api, no_backoff):
    api.replies.append(_status_error(InternalServerError, 502))
    with pytest.raises(InternalServerError):
        run_async(
            _provider().complete_glossary_chat_async(
                "S", "U", glossary_keys=["A"], max_tokens=10, temperature=0.3
            ),
            timeout=5.0,
        )
    assert len(api.calls) == 1


# ---------------------------------------------------------------------------
# Batch translations
# ---------------------------------------------------------------------------


def test_batch_payload_hints_and_context(api):
    api.replies.append('{"0": "Первая", "1": "Вторая", "2": "Третья", "3": "Диван", "4": "Меч"}')
    items = [
        TranslationItem("First", metadata={"type": "ncs_string", "ncs_hint": "SpeakString"}),
        TranslationItem("Second", metadata={"type": "ncs_string", "hint": "SetCustomToken"}),
        TranslationItem("Third", metadata={"type": "item_name"}),
        TranslationItem(
            "The sofa seems warm and inviting.",
            context="Description of placeable 'Couch'",
            metadata={"type": "placeable_description"},
        ),
        TranslationItem("Sword"),
    ]

    result = _batch(_provider(), items)

    assert [r.translated for r in result] == ["Первая", "Вторая", "Третья", "Диван", "Меч"]
    assert all(r.metadata == {"model": MODEL, "batch": True} for r in result)
    payload = _payload(api.calls[0])
    assert [payload[key]["hint"] for key in "0123"] == [
        "SpeakString",
        "SetCustomToken",
        "item_name",
        "placeable_description",
    ]
    assert payload["3"]["text"] == "The sofa seems warm and inviting."
    assert payload["3"]["context"] == "Description of placeable 'Couch'"
    assert payload["4"] == "Sword"  # no hint and no context: a plain string
    assert api.calls[0]["stream"] is False


def test_batch_rules_are_cached_with_the_stable_half(api):
    _batch(_provider(), [TranslationItem("A")], glossary_block="GLOSSARY: X")
    cached, variable = api.calls[0]["messages"][0]["content"]
    assert cached["cache_control"] == {"type": "ephemeral"}
    assert cached["text"].endswith("Do NOT wrap in markdown. Output ONLY the JSON object.\n")
    assert "BATCH MODE" in cached["text"]
    assert variable == {"type": "text", "text": "GLOSSARY: X"}


@pytest.mark.parametrize("wrapped", [False, True])
def test_batch_answers_survive_a_single_item_wrapper(api, wrapped):
    values = {"0": "Рабб", "1": "Хилл", "2": "Описание"}
    api.replies.append(
        json.dumps({"translation": values} if wrapped else values, ensure_ascii=False)
    )
    items = [TranslationItem(text) for text in ["Rabb", "Hill", "Description"]]

    results = _batch(_provider(), items)

    assert [r.translated for r in results] == list(values.values())
    assert all(r.success for r in results)
    assert len(api.calls) == 1
    assert "exactly ONE key" not in api.calls[0]["messages"][1]["content"]


def test_raw_newlines_inside_batch_values_parse(api):
    api.replies.append('{"0": "строка1\nстрока2"}')
    (result,) = _batch(_provider(), [TranslationItem(original="line1\nline2")])
    assert (result.success, result.translated) == (True, "строка1\nстрока2")


@pytest.mark.parametrize(
    "response",
    [
        {"translation": "A combined paragraph"},
        {"translation": ["First", "Second"]},
        {"translation": {"7": "Unrequested ID"}},
        {"translation": {"0": "Ambiguous"}, "items": {"0": "Different"}},
    ],
)
def test_wrapper_recovery_does_not_invent_addresses(api, response):
    api.replies.append(json.dumps(response))
    results = _batch(_provider(), [TranslationItem("A"), TranslationItem("B")])
    assert len(results) == 2
    assert not any(r.success for r in results)


def test_parse_error_is_reported_per_item_with_the_decoder_message(api):
    api.replies.append('```json\n{"0": "Труба')
    result = _batch(_provider(), [TranslationItem("Pipe"), TranslationItem("Horn")])
    assert [r.error for r in result] == [
        "Batch JSON parse error: Unterminated string starting at: line 1 column 7 (char 6)"
    ] * 2
    assert all(r.metadata == {} and not r.success for r in result)


def test_batch_api_error_is_prefixed_once(api):
    denied = _status_error(AuthenticationError, 401, "denied")
    api.replies.append(denied)
    with pytest.raises(OpenRouterError) as exc_info:
        _batch(_provider(), [TranslationItem("A")])
    assert str(exc_info.value) == "OpenRouter translation failed: denied"
    assert exc_info.value.__cause__ is denied


# ---------------------------------------------------------------------------
# Request metrics
# ---------------------------------------------------------------------------


def test_one_metric_per_attempt_with_the_prompt_split(api):
    recorder = RunMetricsRecorder()
    api.replies.extend(["no json", '{"translation": "ok"}'])
    _translate(_provider(metrics_recorder=recorder), "Hello", glossary_block="GLOSSARY: X")
    first, second = recorder.requests
    assert (first.phase, first.batch_size, first.glossary_chars) == (
        "generic_single",
        1,
        len("GLOSSARY: X"),
    )
    assert first.variable_chars == len("GLOSSARY: X")
    assert first.success and second.success
    assert first.estimated_output_tokens == 2  # ceil(len("no json") / 4)


def test_every_failed_single_attempt_is_recorded(api, no_backoff):
    recorder = RunMetricsRecorder()
    limited = _status_error(openai.RateLimitError, 429, "slow down")
    api.replies.extend([limited, _status_error(InternalServerError, 502)])
    assert _translate(_provider(metrics_recorder=recorder)).success
    assert [(m.success, m.error, m.phase) for m in recorder.requests] == [
        (False, "slow down", "generic_single"),
        (False, "boom", "generic_single"),
        (True, None, "generic_single"),
    ]


@pytest.mark.parametrize(
    "reply",
    [
        _status_error(AuthenticationError, 401, "denied"),
        SimpleNamespace(choices=[], usage=None),
        SimpleNamespace(choices=None, usage=None),
    ],
)
def test_failed_batch_request_is_recorded(api, reply):
    recorder = RunMetricsRecorder()
    api.replies.append(reply)
    with pytest.raises(OpenRouterError, match="^OpenRouter translation failed: "):
        _batch(_provider(metrics_recorder=recorder), [TranslationItem("A")])
    (metric,) = recorder.requests
    assert (metric.success, metric.phase, metric.batch_size) == (False, "generic_batch", 1)
    assert metric.estimated_output_tokens == 0
    assert metric.error
    if isinstance(reply, Exception):
        assert metric.error == "denied"


def test_transient_batch_failure_is_recorded(api, no_backoff):
    recorder = RunMetricsRecorder()
    api.replies.append(_status_error(InternalServerError, 503))
    _batch(_provider(metrics_recorder=recorder), [TranslationItem("A")])
    failed, ok = recorder.requests
    assert (failed.success, failed.phase, failed.error) == (False, "generic_batch", "boom")
    assert ok.success
