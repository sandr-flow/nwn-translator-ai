"""Tests for the persistent per-thread event loop behind ``run_async``.

The loop must survive across calls so loop-bound resources — notably the
provider's AsyncOpenAI client and its httpx connection pool — are reused
instead of being rebuilt for every batch and retry.
"""

import asyncio

import pytest

from src.nwn_translator.async_utils import run_async, shutdown_thread_loop
from src.nwn_translator.ai_providers import openrouter_provider
from src.nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider


@pytest.fixture(autouse=True)
def fresh_thread_loop():
    """Isolate every test from loops left behind by other tests."""
    shutdown_thread_loop()
    yield
    shutdown_thread_loop()


async def _current_loop_id() -> int:
    return id(asyncio.get_running_loop())


class TestPersistentLoop:
    """run_async reuses one event loop per thread."""

    def test_sequential_calls_share_one_loop(self):
        ids = {run_async(_current_loop_id()) for _ in range(3)}
        assert len(ids) == 1

    def test_loop_survives_timeout(self):
        async def slow():
            await asyncio.sleep(10)

        before = run_async(_current_loop_id())
        with pytest.raises(TimeoutError):
            run_async(slow(), timeout=0.1)
        assert run_async(_current_loop_id()) == before

    def test_loop_survives_coroutine_exception(self):
        async def boom():
            raise ValueError("boom")

        before = run_async(_current_loop_id())
        with pytest.raises(ValueError):
            run_async(boom())
        assert run_async(_current_loop_id()) == before

    def test_shutdown_thread_loop_forces_new_loop(self):
        async def get_loop():
            return asyncio.get_running_loop()

        # Hold the loop object itself: comparing id() values would false-match
        # when the freed loop's address is reused by the new one.
        before = run_async(get_loop())
        shutdown_thread_loop()
        after = run_async(get_loop())
        assert after is not before
        assert before.is_closed()
        assert not after.is_closed()

    def test_shutdown_without_loop_is_noop(self):
        shutdown_thread_loop()
        shutdown_thread_loop()


class TestClientReuse:
    """K sequential run_async calls must not create K HTTP clients."""

    def test_async_client_constructed_once_across_calls(self, monkeypatch):
        constructed = []

        class CountingClient:
            def __init__(self, **kwargs):
                constructed.append(kwargs)

            async def close(self):
                pass

        monkeypatch.setattr(openrouter_provider, "AsyncOpenAI", CountingClient)
        provider = OpenRouterProvider(api_key="test-key", model="test-model")

        async def touch_client():
            return provider.async_client

        clients = [run_async(touch_client()) for _ in range(5)]
        assert len(constructed) == 1
        assert all(c is clients[0] for c in clients)

    def test_new_loop_after_shutdown_gets_new_client(self, monkeypatch):
        constructed = []

        class CountingClient:
            def __init__(self, **kwargs):
                constructed.append(kwargs)

            async def close(self):
                pass

        monkeypatch.setattr(openrouter_provider, "AsyncOpenAI", CountingClient)
        provider = OpenRouterProvider(api_key="test-key", model="test-model")

        async def touch_client():
            return provider.async_client

        run_async(touch_client())
        shutdown_thread_loop()
        run_async(touch_client())
        assert len(constructed) == 2
