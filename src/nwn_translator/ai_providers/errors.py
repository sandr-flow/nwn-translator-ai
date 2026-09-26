"""Retry policy and error mapping for OpenAI-compatible gateways."""

import logging
import random
import re
from typing import Any, Iterator, Optional

from openai import APIConnectionError, BadRequestError, InternalServerError
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ProviderError, RateLimitError

logger = logging.getLogger(__name__)

#: Errors retried with backoff. ``APIConnectionError`` includes timeouts and
#: ``InternalServerError`` covers every HTTP status >= 500.
TRANSIENT_ERRORS = (RateLimitError, APIConnectionError, InternalServerError)

#: Attempts per retried call, the first one included.
TRANSIENT_ATTEMPTS = 3

_EXPONENTIAL_WAIT = wait_exponential(multiplier=1, min=2, max=120)

_RETRY_AFTER_RE = re.compile(r"retry[- ]after[:\s=]+(\d+(?:\.\d+)?)", re.IGNORECASE)


class OpenRouterError(ProviderError):
    """Non-transient API failure reported by an OpenAI-compatible gateway."""


def _retry_after_candidates(exc: BaseException) -> Iterator[Any]:
    """Yield raw ``Retry-After`` values of *exc*, most authoritative first."""
    if isinstance(exc, RateLimitError):
        yield exc.retry_after_seconds
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        yield headers.get("retry-after")
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        yield match.group(1)


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Return the gateway's ``Retry-After`` hint carried by *exc*.

    Args:
        exc: Exception raised by a request.

    Returns:
        The first positive value among ``RateLimitError.retry_after_seconds``, the
        ``retry-after`` response header and a "Retry-After: N" phrase in the
        message; ``None`` when there is none.
    """
    for raw in _retry_after_candidates(exc):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def wait_with_retry_after(retry_state: RetryCallState) -> float:
    """Return the backoff before the next attempt.

    Exponential backoff (2-120 s), floored at the failed attempt's ``Retry-After``
    hint. A hinted wait gets up to 5 % jitter so that concurrent requests told to
    wait the same time do not all resume in the same second.

    Args:
        retry_state: Tenacity state of the failed attempt.

    Returns:
        Seconds to sleep.
    """
    base = float(_EXPONENTIAL_WAIT(retry_state))
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None and outcome.failed else None
    hint = retry_after_seconds(exc) if exc is not None else None
    if hint is None:
        return base
    return max(base, hint) * random.uniform(1.0, 1.05)


#: Decorator retrying a coroutine on :data:`TRANSIENT_ERRORS` with
#: :func:`wait_with_retry_after`; the last error is re-raised. ``functools.wraps``
#: keeps the wrapped method's ``__name__``, which the translation log records.
TRANSIENT_RETRY = retry(
    stop=stop_after_attempt(TRANSIENT_ATTEMPTS),
    wait=wait_with_retry_after,
    retry=retry_if_exception_type(TRANSIENT_ERRORS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


def is_rate_or_budget_error(exc: BaseException) -> bool:
    """Tell whether *exc* reports a rate limit or exhausted in-flight budget.

    The message is searched only when the error carries no HTTP status: a 400 whose
    text merely contains "429" or "402" (a token count) is not a rate limit.

    Args:
        exc: Exception raised by a request.

    Returns:
        ``True`` for HTTP 429/402, or for a status-less error mentioning a rate
        limit, 429, 402 or ``in_flight_budget``.
    """
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status in (429, 402)
    message = str(exc).lower()
    return any(marker in message for marker in ("rate_limit", "429", "402", "in_flight_budget"))


def map_api_error(exc: BaseException, label: str) -> ProviderError:
    """Translate a non-transient request exception into a provider error.

    Args:
        exc: Exception raised by the API client.
        label: Provider label for the message (``"OpenRouter"``, ``"POLZA.AI"``).

    Returns:
        A :class:`RateLimitError` carrying the ``Retry-After`` hint for rate-limit
        and budget errors, an :class:`OpenRouterError` otherwise. The caller raises
        it ``from exc``.
    """
    if is_rate_or_budget_error(exc):
        return RateLimitError(
            f"{label} rate limit exceeded: {exc}", retry_after_seconds=retry_after_seconds(exc)
        )
    return OpenRouterError(f"{label} translation failed: {exc}")


def is_reasoning_rejection(error: BadRequestError) -> bool:
    """Tell whether a 400 says the model does not accept a ``reasoning`` field.

    Models that make reasoning mandatory ("cannot be disabled") and errors about a
    particular effort value are not rejections of the field itself.

    Args:
        error: The HTTP 400 raised for a request that carried reasoning parameters.

    Returns:
        ``True`` when resending the request without reasoning parameters is correct.
    """
    msg = str(error).lower()
    if "cannot be disabled" in msg or "reasoning is mandatory" in msg:
        return False
    return (
        "reasoning" in msg
        and "effort" not in msg
        and ("not supported" in msg or "unsupported" in msg)
    )
