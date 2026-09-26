"""Tests for the batched request component of the terminology stages."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Set

import pytest

from nwn_translator.llm_batches import LlmStage, chunks
from nwn_translator.telemetry import current_llm_phase


def _stage(**overrides) -> LlmStage:
    values = dict(phase="test_phase", label="Test", batch_size=2, batch_timeout=10.0)
    values.update(overrides)
    return LlmStage(**values)


def _parse(raw: str, expected: Set[str]) -> Dict[str, str]:
    return {key: raw for key in raw.split(",") if key in expected}


class TestChunks:
    def test_exact_multiple(self):
        assert chunks([f"t{i}" for i in range(50)], 25) == [
            [f"t{i}" for i in range(25)],
            [f"t{i}" for i in range(25, 50)],
        ]

    def test_remainder(self):
        assert [len(b) for b in chunks([f"t{i}" for i in range(30)], 25)] == [25, 5]

    def test_empty(self):
        assert chunks([], 25) == []


class TestRunTimeout:
    def test_scales_with_batches_up_to_the_cap(self):
        stage = _stage(batch_timeout=360.0, max_run_timeout=900.0)
        assert stage.run_timeout(1) == 360.0
        assert stage.run_timeout(3) == 900.0

    def test_uncapped_by_default(self):
        assert _stage(batch_timeout=300.0).run_timeout(4) == 1200.0


class TestRun:
    def test_results_keep_batch_order_and_exceptions(self):
        async def worker(sem, number, batch):
            if number == 2:
                raise ValueError("boom")
            await asyncio.sleep(0.01 * (3 - number))
            return (number, batch)

        results = _stage().run(["a", "b", "c"], worker, concurrency=3)

        assert results[0] == (1, "a")
        assert isinstance(results[1], ValueError)
        assert results[2] == (3, "c")

    def test_overall_timeout_keeps_finished_batches(self):
        async def worker(sem, number, batch):
            if number == 2:
                await asyncio.sleep(5)
            return batch

        results = _stage(batch_timeout=0.1).run(["a", "b", "c"], worker, concurrency=3)

        assert (results[0], results[2]) == ("a", "c")
        assert isinstance(results[1], TimeoutError)

    def test_concurrency_limits_requests_in_flight(self):
        in_flight: List[int] = []
        peak = 0
        stage = _stage()

        async def send() -> str:
            nonlocal peak
            in_flight.append(1)
            peak = max(peak, len(in_flight))
            await asyncio.sleep(0.01)
            in_flight.pop()
            return "ok"

        async def worker(sem, number, batch):
            return await stage.request(sem, send)

        assert stage.run(list(range(5)), worker, concurrency=2) == ["ok"] * 5
        assert peak == 2

    def test_slot_per_batch_keeps_the_slot_for_every_request_of_a_batch(self):
        stage = _stage(slot_per_batch=True)
        order: List[tuple] = []

        async def worker(slot, number, batch):
            for attempt in (1, 2):

                async def send() -> str:
                    order.append((number, attempt))
                    await asyncio.sleep(0.01)
                    return "ok"

                await stage.request(slot, send)
            return number

        assert stage.run(["a", "b", "c"], worker, concurrency=1) == [1, 2, 3]
        assert order == [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]


class TestRequest:
    def test_request_is_tagged_with_the_stage_phase(self):
        seen: List[str] = []
        stage = _stage(phase="glossary")

        async def send() -> str:
            seen.append(current_llm_phase("none"))
            return "{}"

        async def main() -> str:
            return await stage.request(asyncio.Semaphore(1), send)

        assert asyncio.run(main()) == "{}"
        assert seen == ["glossary"]


def _fill(stage: LlmStage, keys: List[str], replies: List[object]):
    """Run ``fill_keys`` on *keys*; each reply is a string or an exception to raise."""
    requests: List[tuple] = []
    remaining = set(keys)

    def prepare(asked, accepted, attempt):
        requests.append((list(asked), dict(accepted), attempt))

        async def send():
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return send

    async def main():
        return await stage.fill_keys(asyncio.Semaphore(1), remaining, prepare, _parse, name="t")

    accepted = asyncio.run(main())
    return accepted, remaining, requests


class TestFillKeys:
    def test_retries_ask_only_for_missing_keys(self):
        stage = _stage(max_attempts=3)

        accepted, remaining, requests = _fill(stage, ["b", "A", "c"], ["A", "c", "b"])

        assert accepted == {"A": "A", "c": "c", "b": "b"}
        assert remaining == set()
        assert requests == [
            (["A", "b", "c"], {}, 1),
            (["b", "c"], {"A": "A"}, 2),
            (["b"], {"A": "A", "c": "c"}, 3),
        ]

    def test_missing_keys_stay_in_remaining(self):
        accepted, remaining, requests = _fill(_stage(max_attempts=2), ["a", "b"], ["a", "x"])

        assert accepted == {"a": "a"}
        assert remaining == {"b"}
        assert len(requests) == 2

    def test_failed_request_ends_the_batch_without_retry_on_error(self):
        accepted, remaining, requests = _fill(
            _stage(max_attempts=3), ["a", "b"], [RuntimeError("down"), "a,b"]
        )

        assert accepted == {}
        assert remaining == {"a", "b"}
        assert len(requests) == 1

    def test_failed_request_is_retried_with_retry_on_error(self):
        accepted, remaining, requests = _fill(
            _stage(max_attempts=3, retry_on_error=True),
            ["a", "b"],
            [RuntimeError("down"), "a,b"],
        )

        assert accepted == {"a": "a,b", "b": "a,b"}
        assert remaining == set()
        assert [attempt for _keys, _accepted, attempt in requests] == [1, 2]

    def test_error_building_a_request_propagates_without_retry(self):
        stage = _stage(max_attempts=3, retry_on_error=True)
        built = 0

        def prepare(asked, accepted, attempt):
            nonlocal built
            built += 1
            raise KeyError("bad record")

        async def main():
            return await stage.fill_keys(asyncio.Semaphore(1), {"a"}, prepare, _parse, name="t")

        with pytest.raises(KeyError, match="bad record"):
            asyncio.run(main())
        assert built == 1

    def test_timeout_of_one_request_counts_as_a_failed_attempt(self, monkeypatch):
        import nwn_translator.llm_batches as module

        monkeypatch.setattr(module, "GLOSSARY_LLM_TIMEOUT", 0.01)
        stage = _stage(max_attempts=2, retry_on_error=True)
        calls = 0

        def prepare(asked, accepted, attempt):
            async def send():
                nonlocal calls
                calls += 1
                if attempt == 1:
                    await asyncio.sleep(1)
                return ",".join(asked)

            return send

        async def main():
            return await stage.fill_keys(asyncio.Semaphore(1), {"a"}, prepare, _parse, name="t")

        assert asyncio.run(main()) == {"a": "a"}
        assert calls == 2


@pytest.mark.parametrize("attempts", [1, 2])
def test_no_request_when_nothing_is_missing(attempts):
    accepted, remaining, requests = _fill(_stage(max_attempts=attempts), [], [])
    assert (accepted, remaining, requests) == ({}, set(), [])
