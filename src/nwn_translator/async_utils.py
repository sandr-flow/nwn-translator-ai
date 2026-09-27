"""Coroutines run from synchronous code on one persistent event loop per thread."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Coroutine, Optional, TypeVar

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: Default overall timeout (seconds) of one ``run_async`` call.
DEFAULT_TIMEOUT: float = 300.0

#: Seconds allowed for closing a provider's HTTP client when a thread is done.
CLOSE_CLIENT_TIMEOUT: float = 30.0

_thread_state = threading.local()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    """Returns this thread's persistent event loop, creating it on first use.

    One loop per thread lets loop-bound resources (the provider's ``AsyncOpenAI``
    client and its connection pool) survive across ``run_async`` calls.
    """
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_thread_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_state.loop = loop
        asyncio.set_event_loop(loop)
    return loop


def shutdown_thread_loop() -> None:
    """Closes this thread's event loop; the next ``run_async`` call creates a new one."""
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_thread_state, "loop", None)
    if loop is None:
        return
    _thread_state.loop = None
    if not loop.is_closed():
        loop.close()
    asyncio.set_event_loop(None)


def close_thread_resources(provider: TranslationProvider) -> None:
    """Closes *provider*'s HTTP client on this thread's loop, then the loop.

    Call it when a thread has finished its ``run_async`` work; otherwise the loop
    and its client outlive the thread. A failure to close the client is logged at
    debug level and the loop is closed anyway.

    Args:
        provider: Model provider whose requests ran on this thread.
    """
    try:
        run_async(provider.close_async_client(), timeout=CLOSE_CLIENT_TIMEOUT)
    except Exception:
        logger.debug("Closing the provider's HTTP client failed", exc_info=True)
    finally:
        shutdown_thread_loop()


def run_async(
    coro: Coroutine[object, object, T],
    *,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
) -> T:
    """Runs a coroutine from synchronous code on the thread's persistent loop.

    Tasks the coroutine leaves behind are cancelled afterwards. Call
    :func:`shutdown_thread_loop` when the thread is done.

    Args:
        coro: The coroutine to execute.
        timeout: Maximum seconds for *coro*; ``None`` or a non-positive value
            disables the limit.

    Returns:
        The coroutine's result.

    Raises:
        TimeoutError: If the coroutine raises one, including the expiry of *timeout*.
    """
    loop = _get_thread_loop()
    limited = timeout is not None and timeout > 0
    t0 = time.monotonic()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout) if limited else coro)
    except asyncio.TimeoutError:
        msg = f"run_async timed out after {time.monotonic() - t0:.1f}s (limit {timeout}s)"
        logger.error(msg)
        raise TimeoutError(msg) from None
    finally:
        with contextlib.suppress(Exception):
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
