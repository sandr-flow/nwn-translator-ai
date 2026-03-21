"""Async helper utilities."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Coroutine, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: Default overall timeout for a single ``run_async`` invocation (seconds).
#: Generous upper bound; individual callers can override.
DEFAULT_TIMEOUT: float = 300.0


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
    cleanup: Optional[Callable[[], Coroutine]] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
) -> T:
    """Run an async coroutine from synchronous code.

    Args:
        coro: The coroutine to execute.
        cleanup: Optional async callable invoked **before** the loop is closed.
            Use this to tear down async resources (e.g. ``AsyncOpenAI`` clients)
            while the event loop is still alive, preventing
            ``RuntimeError: Event loop is closed`` from httpx on Windows.
        timeout: Maximum seconds to wait for *coro* to complete.
            ``None`` disables the timeout.  Default: :data:`DEFAULT_TIMEOUT`.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        if timeout is not None and timeout > 0:
            wrapped = asyncio.wait_for(coro, timeout=timeout)
        else:
            wrapped = coro
        t0 = time.monotonic()
        try:
            return loop.run_until_complete(wrapped)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            msg = (
                f"run_async timed out after {elapsed:.1f}s "
                f"(limit {timeout}s)"
            )
            logger.error(msg)
            raise TimeoutError(msg) from None
    finally:
        try:
            if cleanup is not None:
                try:
                    loop.run_until_complete(cleanup())
                except Exception:
                    pass
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)
