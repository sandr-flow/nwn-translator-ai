"""The persistent per-thread event loop behind ``run_async``.

The loop survives across calls so loop-bound resources, such as the provider's
AsyncOpenAI client and its connection pool, are reused rather than rebuilt for
every batch and retry.
"""

import asyncio

import pytest

from nwn_translator.async_utils import close_thread_resources, run_async, shutdown_thread_loop


@pytest.fixture(autouse=True)
def fresh_thread_loop():
    """Isolate every test from loops left behind by other tests."""
    shutdown_thread_loop()
    yield
    shutdown_thread_loop()


async def _running_loop():
    return asyncio.get_running_loop()


def test_timeout_raises_and_fast_coroutines_return():
    async def value(result):
        return result

    async def slow():
        await asyncio.sleep(10)

    assert run_async(value(42), timeout=5.0) == 42
    assert run_async(value("ok"), timeout=None) == "ok"
    with pytest.raises(TimeoutError, match="timed out"):
        run_async(slow(), timeout=0.2)


def test_one_loop_survives_calls_timeouts_and_errors():
    async def boom():
        raise ValueError("boom")

    async def slow():
        await asyncio.sleep(10)

    loop = run_async(_running_loop())
    assert all(run_async(_running_loop()) is loop for _ in range(3))
    with pytest.raises(TimeoutError):
        run_async(slow(), timeout=0.1)
    with pytest.raises(ValueError):
        run_async(boom())
    assert run_async(_running_loop()) is loop


def test_shutdown_forces_a_new_loop_and_is_idempotent():
    # Hold the loop object itself: comparing ids would false-match when the
    # freed loop's address is reused by the new one.
    before = run_async(_running_loop())
    shutdown_thread_loop()
    shutdown_thread_loop()
    after = run_async(_running_loop())
    assert after is not before
    assert before.is_closed() and not after.is_closed()


def test_close_thread_resources_closes_the_client_on_the_loop_then_the_loop():
    class Provider:
        closed_on = None

        async def close_async_client(self):
            Provider.closed_on = asyncio.get_running_loop()

    loop = run_async(_running_loop())
    close_thread_resources(Provider())
    assert Provider.closed_on is loop
    assert loop.is_closed()


@pytest.mark.parametrize("close_fails", [True, False])
def test_the_loop_is_closed_even_without_a_working_client(close_fails):
    class FailingProvider:
        async def close_async_client(self):
            raise RuntimeError("close failed")

    loop = run_async(_running_loop())
    close_thread_resources(FailingProvider() if close_fails else object())
    assert loop.is_closed()
