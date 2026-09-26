"""Batched JSON requests of the terminology stages.

Entity extraction, glossary curation and glossary building all split their
input into batches, send each batch to the model under one concurrency limit
and parse a JSON reply per request. :class:`LlmStage` holds the policy of one
stage (metrics phase, batch size, attempts, concurrency slots, timeouts) and
runs its batches; :meth:`LlmStage.fill_keys` is the retry loop that asks again
for the keys a reply left out.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    TypeVar,
    Union,
)

from .async_utils import run_async
from .config import GLOSSARY_LLM_TIMEOUT, GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from .telemetry import llm_phase

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider

logger = logging.getLogger(__name__)

#: Ceiling of the overall deadline of entity extraction and glossary building (seconds).
RUN_TIMEOUT_CAP = 900.0

B = TypeVar("B")
R = TypeVar("R")
V = TypeVar("V")

#: What a request holds while it runs: a slot of the run's concurrency limit,
#: or a no-op when its batch already holds one.
Slot = AbstractAsyncContextManager[Any]

#: Worker of one batch: ``(request slot, 1-based batch number, batch) -> result``.
BatchWorker = Callable[[Slot, int, B], Awaitable[R]]

#: Request builder of one attempt: ``(keys sorted by str.lower, accepted so far, attempt)
#: -> function starting the request``.
KeyRequest = Callable[[List[str], Dict[str, V], int], Callable[[], Awaitable[str]]]

#: Reply parser: ``(reply, keys still expected) -> answered keys``.
KeyParser = Callable[[str, Set[str]], Dict[str, V]]

#: Observer of a finished attempt: ``(attempt, keys it answered)``; the count is
#: 0 when the request failed or the reply had no usable key.
AttemptObserver = Callable[[int, int], None]


def chunks(items: Sequence[B], size: int) -> List[List[B]]:
    """Splits *items* into consecutive lists of at most *size* elements.

    Args:
        items: Items in request order.
        size: Maximum batch length.

    Returns:
        The batches; empty when *items* is empty.
    """
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def json_request(
    provider: "TranslationProvider", system_prompt: str, user_prompt: str
) -> Awaitable[str]:
    """Starts the JSON chat request of entity extraction and curation.

    Args:
        provider: Model provider.
        system_prompt: System message.
        user_prompt: User message.

    Returns:
        The provider coroutine (reasoning disabled, glossary token budget).
    """
    return provider.complete_json_chat_async(
        system_prompt,
        user_prompt,
        max_tokens=GLOSSARY_MAX_TOKENS,
        temperature=GLOSSARY_TEMPERATURE,
        use_reasoning=False,
    )


def _clip(text: str, limit: int) -> str:
    """Shortens *text* to *limit* characters for a log line."""
    return text[:limit] + "…" if len(text) > limit else text


@dataclass(frozen=True)
class LlmStage:
    """Request policy of one batched terminology stage.

    Attributes:
        phase: ``llm_phase`` label of the stage's requests in the run metrics.
        label: Stage name in log lines.
        batch_size: Maximum items per batch.
        run_timeout_per_batch: What each batch adds to the overall deadline of
            :meth:`run` (seconds). No single batch is held to it: one batch
            may use the time of others.
        max_attempts: Requests per batch in :meth:`fill_keys`; each retry asks
            only for the keys still missing.
        retry_on_error: Retry after a failed request; otherwise the batch stops
            at its first failed request.
        slot_per_batch: A batch takes its concurrency slot before its first
            request and keeps it until its last, so its retry runs before
            other batches start; otherwise each request waits for a slot of
            its own.
        max_run_timeout: Ceiling of the overall deadline of one :meth:`run`
            (seconds).
    """

    phase: str
    label: str
    batch_size: int
    run_timeout_per_batch: float
    max_attempts: int = 1
    retry_on_error: bool = False
    slot_per_batch: bool = False
    max_run_timeout: float = math.inf

    def run_timeout(self, batch_count: int) -> float:
        """Returns the overall deadline of a run.

        Args:
            batch_count: Number of batches in the run.

        Returns:
            :attr:`run_timeout_per_batch` times *batch_count*, capped at
            :attr:`max_run_timeout` (seconds).
        """
        return min(self.run_timeout_per_batch * batch_count, self.max_run_timeout)

    def run(
        self,
        batches: Sequence[B],
        worker: BatchWorker[B, R],
        *,
        concurrency: int,
    ) -> List[Union[R, BaseException]]:
        """Runs *worker* on every batch concurrently and return the results in batch order.

        When :meth:`run_timeout` runs out, the unfinished batches are cancelled
        and the finished ones keep their results.

        Args:
            batches: Batches in request order.
            worker: Coroutine function processing one batch; it passes its
                slot to :meth:`request` or :meth:`fill_keys`. With
                :attr:`slot_per_batch` it runs while its batch holds a slot.
            concurrency: Maximum requests (with :attr:`slot_per_batch`,
                batches) in flight.

        Returns:
            One entry per batch: its result, the exception its worker raised,
            or a ``TimeoutError`` when it was still running at the deadline.
        """
        limit = self.run_timeout(len(batches))

        async def run_all() -> List[Union[R, BaseException]]:
            sem = asyncio.Semaphore(max(1, concurrency))

            async def run_batch(number: int, batch: B) -> R:
                if not self.slot_per_batch:
                    return await worker(sem, number, batch)
                async with sem:
                    return await worker(nullcontext(), number, batch)

            tasks = [
                asyncio.ensure_future(run_batch(number, batch))
                for number, batch in enumerate(batches, 1)
            ]
            if not tasks:
                return []
            _done, pending = await asyncio.wait(tasks, timeout=limit)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if pending:
                logger.warning(
                    "%s: %d of %d batch(es) unfinished after the overall limit of %.0fs",
                    self.label,
                    len(pending),
                    len(tasks),
                    limit,
                )
            results: List[Union[R, BaseException]] = []
            for task in tasks:
                if task in pending:
                    results.append(TimeoutError(f"unfinished after {limit:.0f}s"))
                elif task.cancelled():
                    results.append(asyncio.CancelledError())
                else:
                    error = task.exception()
                    results.append(error if error is not None else task.result())
            return results

        return run_async(run_all(), timeout=None)

    async def request(self, slot: Slot, send: Callable[[], Awaitable[str]]) -> str:
        """Sends one request while holding *slot*, tagged with the stage's metrics phase.

        Args:
            slot: The worker's request slot (see :meth:`run`).
            send: Starts the provider request.

        Returns:
            The model reply.

        Raises:
            asyncio.TimeoutError: When the request exceeds ``GLOSSARY_LLM_TIMEOUT``
                (the builtin ``TimeoutError`` on Python 3.11+).
            Exception: Whatever the provider raises.
        """
        async with slot:
            # The phase must be set before wait_for runs the request, so the
            # provider's metrics see it.
            with llm_phase(self.phase):
                return await asyncio.wait_for(send(), timeout=GLOSSARY_LLM_TIMEOUT)

    async def fill_keys(
        self,
        slot: Slot,
        remaining: Set[str],
        prepare: KeyRequest[V],
        parse: KeyParser[V],
        *,
        name: str,
        on_attempt: Optional[AttemptObserver] = None,
    ) -> Dict[str, V]:
        """Requests the keys in *remaining* until each is answered or the attempts run out.

        Every attempt asks for the keys still missing, sorted by ``str.lower``;
        the answered ones leave *remaining* in place, so the caller sees the
        unanswered keys afterwards. The caller builds *remaining*: its iteration
        order decides the order of keys equal under ``str.lower`` and the order
        in which :func:`parse` may report answers.

        Args:
            slot: The worker's request slot (see :meth:`run`).
            remaining: Keys to answer; updated in place.
            prepare: Builds the request of one attempt. It runs before the
                request waits for its slot and outside the failure handling,
                so an error in it propagates instead of counting as a failed
                request.
            parse: Extracts the answered keys from a reply.
            name: Batch name for log lines.
            on_attempt: Called after every attempt, once *remaining* is
                updated.

        Returns:
            Answered key -> value, in answer order.

        Raises:
            Exception: Whatever *prepare*, *parse* or *on_attempt* raises.
        """
        total = len(remaining)
        accepted: Dict[str, V] = {}
        for attempt in range(1, self.max_attempts + 1):
            if not remaining:
                break
            keys = sorted(remaining, key=str.lower)
            if attempt > 1:
                logger.info(
                    "%s: retrying %d missing key(s), attempt %d/%d",
                    name,
                    len(keys),
                    attempt,
                    self.max_attempts,
                )
            send = prepare(keys, accepted, attempt)
            started = time.monotonic()
            try:
                raw = await self.request(slot, send)
            except Exception as exc:
                logger.warning(
                    "%s attempt %d/%d: request failed after %.1fs: %s",
                    name,
                    attempt,
                    self.max_attempts,
                    time.monotonic() - started,
                    exc,
                )
                if on_attempt:
                    on_attempt(attempt, 0)
                if self.retry_on_error:
                    continue
                break
            elapsed = time.monotonic() - started
            parsed = parse(raw, remaining)
            accepted.update(parsed)
            remaining -= set(parsed)
            if parsed:
                logger.info(
                    "%s attempt %d/%d: %d key(s) in %.1fs, %d/%d answered",
                    name,
                    attempt,
                    self.max_attempts,
                    len(parsed),
                    elapsed,
                    len(accepted),
                    total,
                )
            else:
                logger.warning(
                    "%s attempt %d/%d: no usable keys in %.1fs. Raw (truncated): %s",
                    name,
                    attempt,
                    self.max_attempts,
                    elapsed,
                    _clip(raw, 600),
                )
            if on_attempt:
                on_attempt(attempt, len(parsed))
        if remaining:
            missing = sorted(remaining)
            logger.warning(
                "%s: %d/%d key(s) still missing after all attempts: %s",
                name,
                len(missing),
                total,
                ", ".join(missing[:15]) + ("…" if len(missing) > 15 else ""),
            )
        return accepted
