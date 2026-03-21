"""Async helper utilities."""

from __future__ import annotations

import asyncio
from typing import Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[object, object, T]) -> T:
    """Run an async coroutine from synchronous code.

    Tries ``asyncio.run()`` first; falls back to creating a new event loop
    when a loop is already running (e.g. inside a thread spawned by an
    async framework).
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)
