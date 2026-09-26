"""Batched JSON requests of the terminology stages.

Entity extraction, glossary curation and glossary building all split their
input into batches, send each batch to the model under one concurrency limit
and parse a JSON reply per request. :class:`LlmStage` holds the policy of one
stage (metrics phase, batch size, attempts, timeouts) and runs its batches;
:meth:`LlmStage.fill_keys` is the retry loop that asks again for the keys a
reply left out.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Sequence, Set, TypeVar, Union

from .async_utils import run_async
from .config import GLOSSARY_LLM_TIMEOUT, GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from .telemetry import llm_phase

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider

logger = logging.getLogger(__name__)

#: Ceiling of the overall budget of entity extraction and glossary building (seconds).
RUN_TIMEOUT_CAP = 900.0

B = TypeVar("B")
R = TypeVar("R")
V = TypeVar("V")

#: Worker of one batch: ``(semaphore, 1-based batch number, batch) -> result``.
BatchWorker = Callable[[asyncio.Semaphore, int, B], Awaitable[R]]

#: Request of one attempt: ``(keys sorted by str.lower, accepted so far, attempt) -> reply``.
KeyRequest = Callable[[List[str], Dict[str, V], int], Awaitable[str]]

#: Reply parser: ``(reply, keys still expected) -> answered keys``.
KeyParser = Callable[[str, Set[str]], Dict[str, V]]


def chunks(items: Sequence[B], size: int) -> List[List[B]]:
    """Split *items* into consecutive lists of at most *size* elements.

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
    """Start the JSON chat request of entity extraction and curation.

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
    """Shorten *text* to *limit* characters for a log line."""
    return text[:limit] + "…" if len(text) > limit else text


@dataclass(frozen=True)
class LlmStage:
    """Request policy of one batched terminology stage.

    Attributes:
        phase: ``llm_phase`` label of the stage's requests in the run metrics.
        label: Stage name in log lines.
        batch_size: Maximum items per batch.
        max_attempts: Requests per batch in :meth:`fill_keys`; each retry asks
            only for the keys still missing.
        retry_on_error: Retry after a failed request; otherwise the batch stops
            at its first failed request.
        batch_timeout: Overall time budget per batch (seconds).
        max_run_timeout: Ceiling of the overall budget of one :meth:`run`.
    """

    phase: str
    label: str
    batch_size: int
    batch_timeout: float
    max_attempts: int = 1
    retry_on_error: bool = False
    max_run_timeout: float = math.inf

    def run_timeout(self, batch_count: int) -> float:
        """Overall time budget of a run over *batch_count* batches (seconds)."""
        return min(self.batch_timeout * batch_count, self.max_run_timeout)

    def run(
        self,
        batches: Sequence[B],
        worker: BatchWorker[B, R],
        *,
        concurrency: int,
    ) -> List[Union[R, BaseException]]:
        """Run *worker* on every batch concurrently and return the results in batch order.

        When :meth:`run_timeout` runs out, the unfinished batches are cancelled
        and the finished ones keep their results.

        Args:
            batches: Batches in request order.
            worker: Coroutine function processing one batch; it passes the
                semaphore to :meth:`request` or :meth:`fill_keys`.
            concurrency: Maximum requests in flight.

        Returns:
            One entry per batch: its result, the exception its worker raised,
            or a ``TimeoutError`` when it was still running at the deadline.
        """
        limit = self.run_timeout(len(batches))

        async def run_all() -> List[Union[R, BaseException]]:
            sem = asyncio.Semaphore(max(1, concurrency))
            tasks = [
                asyncio.ensure_future(worker(sem, number, batch))
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

    async def request(self, sem: asyncio.Semaphore, send: Callable[[], Awaitable[str]]) -> str:
        """Send one request once *sem* admits it, tagged with the stage's metrics phase.

        Args:
            sem: Concurrency limit shared by the run.
            send: Starts the provider request.

        Returns:
            The model reply.

        Raises:
            TimeoutError: When the request exceeds ``GLOSSARY_LLM_TIMEOUT``.
            Exception: Whatever the provider raises.
        """
        async with sem:
            # The phase must be set before wait_for runs the request, so the
            # provider's metrics see it.
            with llm_phase(self.phase):
                return await asyncio.wait_for(send(), timeout=GLOSSARY_LLM_TIMEOUT)

    async def fill_keys(
        self,
        sem: asyncio.Semaphore,
        remaining: Set[str],
        send: KeyRequest[V],
        parse: KeyParser[V],
        *,
        name: str,
    ) -> Dict[str, V]:
        """Request the keys in *remaining* until each is answered or the attempts run out.

        Every attempt asks for the keys still missing, sorted by ``str.lower``;
        the answered ones leave *remaining* in place, so the caller sees the
        unanswered keys afterwards. The caller builds *remaining*: its iteration
        order decides the order of keys equal under ``str.lower`` and the order
        in which :func:`parse` may report answers.

        Args:
            sem: Concurrency limit shared by the run.
            remaining: Keys to answer; updated in place.
            send: Starts the request of one attempt.
            parse: Extracts the answered keys from a reply.
            name: Batch name for log lines.

        Returns:
            Answered key -> value, in answer order.
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
            started = time.monotonic()
            try:
                raw = await self.request(sem, functools.partial(send, keys, accepted, attempt))
            except Exception as exc:
                logger.warning(
                    "%s attempt %d/%d: request failed after %.1fs: %s",
                    name,
                    attempt,
                    self.max_attempts,
                    time.monotonic() - started,
                    exc,
                )
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
