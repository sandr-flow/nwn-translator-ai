"""Run coroutines from synchronous code on one persistent event loop per thread."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Coroutine, Optional, TypeVar

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: Default overall timeout for a single ``run_async`` invocation (seconds).
#: Generous upper bound; individual callers can override.
DEFAULT_TIMEOUT: float = 300.0

#: Seconds allowed for closing a provider's HTTP client when a thread is done.
CLOSE_CLIENT_TIMEOUT: float = 30.0

_thread_state = threading.local()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    """Returns this thread's persistent event loop, creating it on first use.

    Reusing one loop per thread lets loop-bound resources (the provider's
    ``AsyncOpenAI`` client and its httpx connection pool) survive across
    ``run_async`` calls instead of being rebuilt for every batch and retry.
    """
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_thread_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_state.loop = loop
        asyncio.set_event_loop(loop)
    return loop


def shutdown_thread_loop() -> None:
    """Closes this thread's persistent event loop (end-of-run hygiene).

    The next ``run_async`` call on the thread creates a fresh loop.
    """
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_thread_state, "loop", None)
    if loop is None:
        return
    _thread_state.loop = None
    if not loop.is_closed():
        loop.close()
    asyncio.set_event_loop(None)


def close_thread_resources(provider: TranslationProvider) -> None:
    """Closes *provider*'s HTTP client on this thread's loop, then the loop.

    Call it when a thread has finished its ``run_async`` work: the loop, and
    the client bound to it, would otherwise stay open after the thread ends.
    A failure to close the client is logged at debug level; the loop is
    closed anyway.

    Args:
        provider: Model provider whose requests ran on this thread.
    """
    try:
        run_async(provider.close_async_client(), timeout=CLOSE_CLIENT_TIMEOUT)
    except Exception:
        logger.debug("Closing the provider's HTTP client failed", exc_info=True)
    finally:
        shutdown_thread_loop()


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancels every remaining task on *loop* and awaits their cancellation."""
    to_cancel = asyncio.all_tasks(loop)
    if not to_cancel:
        return
    for task in to_cancel:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*to_cancel, return_exceptions=True))


def run_async(
    coro: Coroutine[object, object, T],
    *,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
) -> T:
    """Runs an async coroutine from synchronous code on the thread's loop.

    The loop persists between calls (see :func:`_get_thread_loop`), so async
    resources bound to it — notably the provider's HTTP client — are reused.
    Call :func:`shutdown_thread_loop` when a run is finished.

    Args:
        coro: The coroutine to execute.
        timeout: Maximum seconds to wait for *coro* to complete; ``None`` or a
            non-positive value disables the timeout. Default: :data:`DEFAULT_TIMEOUT`.

    Returns:
        The coroutine's result.

    Raises:
        TimeoutError: When the coroutine raises one, including the expiry of *timeout*.
    """
    loop = _get_thread_loop()
    if timeout is not None and timeout > 0:
        wrapped = asyncio.wait_for(coro, timeout=timeout)
    else:
        wrapped = coro
    t0 = time.monotonic()
    try:
        try:
            return loop.run_until_complete(wrapped)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            msg = f"run_async timed out after {elapsed:.1f}s " f"(limit {timeout}s)"
            logger.error(msg)
            raise TimeoutError(msg) from None
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
