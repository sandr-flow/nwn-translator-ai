"""Mapping API errors: rate limits, budgets and Retry-After hints."""

import httpx
from openai import APIStatusError, BadRequestError

from nwn_translator.ai_providers.base import RateLimitError
from nwn_translator.ai_providers.errors import (
    OpenRouterError,
    is_rate_or_budget_error,
    map_api_error,
    retry_after_seconds,
    wait_with_retry_after,
)

_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def test_exhausted_in_flight_budget_is_a_rate_limit():
    message = "402 in_flight_budget_exhausted Retry-After: 120"
    assert is_rate_or_budget_error(Exception(f"Error code: {message}"))
    assert retry_after_seconds(Exception(f"Error code: {message}")) == 120.0
    error = map_api_error(Exception(message), "X")
    assert isinstance(error, RateLimitError)
    assert error.retry_after_seconds == 120.0
    assert str(error) == f"X rate limit exceeded: {message}"


def test_status_code_decides_over_digits_in_the_message():
    too_long = BadRequestError(
        "This endpoint's maximum context length is 1048576 tokens; you requested 1402913",
        response=httpx.Response(400, request=_REQUEST),
        body=None,
    )
    assert not is_rate_or_budget_error(too_long)
    assert isinstance(map_api_error(too_long, "OpenRouter"), OpenRouterError)
    budget = APIStatusError(
        "Payment required", response=httpx.Response(402, request=_REQUEST), body=None
    )
    assert is_rate_or_budget_error(budget)


def test_retry_after_header_and_precedence():
    limited = APIStatusError(
        "Retry-After: 9",
        response=httpx.Response(429, request=_REQUEST, headers={"Retry-After": "30"}),
        body=None,
    )
    assert retry_after_seconds(limited) == 30.0
    assert retry_after_seconds(RateLimitError("Retry-After: 9", retry_after_seconds=5)) == 5
    assert retry_after_seconds(RateLimitError("Retry-After: 9", retry_after_seconds=0)) == 9
    assert retry_after_seconds(Exception("no hint")) is None


def test_other_errors_keep_the_provider_label():
    error = map_api_error(Exception("permission denied"), "POLZA.AI")
    assert isinstance(error, OpenRouterError)
    assert str(error) == "POLZA.AI translation failed: permission denied"


def test_wait_is_at_least_the_retry_after_hint():
    class _Outcome:
        failed = True

        def exception(self):
            return RateLimitError("budget", retry_after_seconds=120)

    class _State:
        outcome = _Outcome()
        attempt_number = 1

    assert 120.0 <= wait_with_retry_after(_State()) <= 126.0
