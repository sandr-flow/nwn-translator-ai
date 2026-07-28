"""Async helper utilities."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Coroutine, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: Default overall timeout for a single ``run_async`` invocation (seconds).
#: Generous upper bound; individual callers can override.
DEFAULT_TIMEOUT: float = 300.0

_thread_state = threading.local()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    """Return this thread's persistent event loop, creating it on first use.

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
    """Close this thread's persistent event loop (end-of-run hygiene).

    The next ``run_async`` call on the thread creates a fresh loop.
    """
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_thread_state, "loop", None)
    if loop is None:
        return
    _thread_state.loop = None
    if not loop.is_closed():
        loop.close()
    asyncio.set_event_loop(None)


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel every remaining task on *loop* and await their cancellation."""
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
    """Run an async coroutine from synchronous code on the thread's loop.

    The loop persists between calls (see :func:`_get_thread_loop`), so async
    resources bound to it — notably the provider's HTTP client — are reused.
    Call :func:`shutdown_thread_loop` when a run is finished.

    Args:
        coro: The coroutine to execute.
        timeout: Maximum seconds to wait for *coro* to complete.
            ``None`` disables the timeout.  Default: :data:`DEFAULT_TIMEOUT`.
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
