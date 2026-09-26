"""Model requests of the batch translator under one timeout and retry policy.

Every request goes through :func:`logged_model_call` with the bound provider
method, so the translation log names the task and records its arguments. A
single request that times out is retried once in the same semaphore slot: a
script string with the fallback request (explicit script context), anything else
with the same request. A batch whose results fail is halved recursively until
single failed leaves remain; those are left to the caller's fallback pass.
A failed single or batch request never raises: it comes back as an unsuccessful
result. Only a cancelled run (:class:`~nwn_translator.config.TranslationCancelled`)
and a pass that exceeds its overall budget (:class:`TimeoutError`) raise.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Coroutine, List, Optional, Sequence, Tuple, TypeVar

from ..ai_providers import TranslationProvider, TranslationResult
from ..async_utils import run_async
from ..config import TranslationConfig
from ..prompts._builder import CONTENT_PROFILE_SCRIPT_MESSAGE
from ..prompts.token_retry import (
    PRESERVE_INLINE_MARKUP,
    PRESERVE_PLACEHOLDERS,
    expected_artifacts_line,
    previous_mismatch_lines,
)
from ..translation_logging import TranslationLogWriter, logged_model_call
from .ncs_diagnostics import NcsDiagnostics
from .work_plan import Terminology, WorkItem, batch_terminology, content_profile

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Called once per item when its first-pass request is finished.
Done = Optional[Callable[[WorkItem], None]]

#: Bounds of the slack added to a queued budget (half of one call), so a short
#: call still gets a few seconds and a long one at most a minute.
_MIN_QUEUE_SLACK = 5.0
_MAX_QUEUE_SLACK = 60.0


@dataclass(frozen=True)
class CallLimits:
    """Timeouts and retry budget of translation requests.

    A pass may run as long as its queued requests need (see
    :func:`queued_timeout`) plus a pad, and never less than its floor.

    Attributes:
        item_timeout: Seconds for one single-string request.
        batch_timeout: Seconds for one batch request.
        min_pass_timeout: Lower bound of the main pass budget. Larger queues
            scale the budget up.
        main_pass_pad: Seconds the main pass gets beyond its queued requests,
            for scheduling and result handling between them.
        fallback_pass_pad: The same headroom for a fallback pass, which sends
            fewer and only single requests.
        token_retries: Extra requests for an answer that broke tokens or tags.
    """

    item_timeout: float = 120.0
    batch_timeout: float = 180.0
    min_pass_timeout: float = 660.0
    main_pass_pad: float = 60.0
    fallback_pass_pad: float = 30.0
    token_retries: int = 2

    @property
    def min_fallback_pass_timeout(self) -> float:
        """Lower bound of a fallback pass budget: half the main pass floor."""
        return self.min_pass_timeout / 2


@dataclass(frozen=True)
class SingleRequest:
    """Arguments of one ``translate_async`` request besides the text.

    Attributes:
        context: Prompt context.
        glossary_block: Glossary block, or None when no term matches.
        content_profile: Prompt profile.
    """

    context: Optional[str]
    glossary_block: Optional[str]
    content_profile: str


def queued_timeout(work_units: int, per_call_timeout: float, concurrency: int) -> float:
    """Returns the time *work_units* calls need when *concurrency* run at once.

    Args:
        work_units: Calls queued behind one semaphore.
        per_call_timeout: Longest time one call may hold its slot.
        concurrency: Semaphore size.

    Returns:
        Waves times the per-call timeout, plus slack of half a call within
        5 to 60 seconds; 0 for no work.
    """
    if work_units <= 0:
        return 0.0
    waves = (work_units + concurrency - 1) // concurrency
    slack = max(_MIN_QUEUE_SLACK, min(_MAX_QUEUE_SLACK, per_call_timeout * 0.5))
    return waves * per_call_timeout + slack


class ModelCaller:
    """Sends the translation requests of one run.

    Attributes:
        config: Run settings (languages, concurrency, cancellation).
        provider: Model provider.
        log_writer: Translation log of the run.
        diagnostics: Recorder of script-string outcomes.
        terminology: Glossary lookup of the run.
        limits: Timeouts and retry budget.
    """

    def __init__(
        self,
        config: TranslationConfig,
        provider: TranslationProvider,
        log_writer: TranslationLogWriter,
        diagnostics: NcsDiagnostics,
        terminology: Terminology,
        limits: CallLimits,
    ):
        """Creates a caller for one run.

        Args:
            config: Run settings (languages, concurrency, cancellation).
            provider: Model provider.
            log_writer: Translation log of the run.
            diagnostics: Recorder of script-string outcomes.
            terminology: Glossary lookup of the run.
            limits: Timeouts and retry budget.
        """
        self.config = config
        self.provider = provider
        self.log_writer = log_writer
        self.diagnostics = diagnostics
        self.terminology = terminology
        self.limits = limits

    @property
    def concurrency(self) -> int:
        """Requests allowed in flight at once."""
        return max(1, int(self.config.max_concurrent_requests))

    # ── request builders ─────────────────────────────────────────────────
    def plain_request(self, work: WorkItem) -> SingleRequest:
        """Returns the regular request of an item: its context, terms and profile.

        Args:
            work: Item to translate.

        Returns:
            The request arguments besides the text.
        """
        return SingleRequest(
            context=work.item.context,
            glossary_block=self.terminology([work.sanitized, work.item.context]),
            content_profile=work.profile,
        )

    def ncs_fallback_request(self, work: WorkItem) -> SingleRequest:
        """Returns the request that retries a script string on its own.

        The context restates where the literal comes from and that code must stay
        untranslated.

        Args:
            work: Script string to translate.

        Returns:
            The request arguments besides the text.
        """
        item = work.item
        meta = item.metadata
        return SingleRequest(
            context=(
                "NCS timeout fallback. Translate only if this is player-visible script text. "
                "Do not translate identifiers, tags, resrefs, variables, debug logs, or code. "
                f"file={item.key[0]}; item_id={item.item_id}; offset={meta.get('offset')}; "
                f"confidence={meta.get('confidence')}; hint={meta.get('ncs_hint')}.\n"
                + (item.context or "")
            ),
            glossary_block=self.terminology([item.text, item.context]),
            content_profile=CONTENT_PROFILE_SCRIPT_MESSAGE,
        )

    def token_retry_request(self, work: WorkItem, attempt: int) -> SingleRequest:
        """Returns the request that retries an answer which broke tokens or tags.

        The context adds strict preservation rules, the expected artifacts and how
        the previous answer (``work.mismatch``) broke them.

        Args:
            work: Item whose last answer was rejected.
            attempt: Retry number, starting at 1.

        Returns:
            The request arguments besides the text.
        """
        report = work.mismatch
        parts: List[str] = []
        if work.item.context:
            parts.append(work.item.context)
        parts.append(PRESERVE_PLACEHOLDERS)
        parts.append(PRESERVE_INLINE_MARKUP)
        parts.append(
            "If the line contains dialog action markers like <<...>> or -...-, preserve "
            "the surrounding markers exactly and translate only the inner text. Do not "
            "invent new angle-bracket pseudo-tags such as <sir/madam>."
        )
        parts.extend(expected_artifacts_line(work.handler.get_expected_artifact_sequence()))
        if report is not None and report.mismatch_type == "foreign_script":
            parts.append(
                "Your previous answer contained characters from a foreign script "
                f"(such as Chinese). Write the translation in {self.config.target_lang} "
                "using only that language's alphabet."
            )
        parts.extend(previous_mismatch_lines(report))
        parts.append(f"Retry attempt {attempt} of {self.limits.token_retries}.")
        return replace(self.plain_request(work), context="\n".join(parts))

    # ── single requests ──────────────────────────────────────────────────
    def _call(self, work: WorkItem, request: SingleRequest) -> Awaitable[TranslationResult]:
        """Starts one logged ``translate_async`` request for *work*."""
        return logged_model_call(
            self.log_writer,
            self.provider.translate_async,
            trace_context={"occurrence": work.key},
            text=work.sanitized,
            source_lang=self.config.source_lang,
            target_lang=self.config.target_lang,
            context=request.context,
            glossary_block=request.glossary_block,
            content_profile=request.content_profile,
        )

    async def _ask(self, work: WorkItem, request: SingleRequest) -> TranslationResult:
        """Sends one request within the item timeout; raises on timeout or error."""
        return await asyncio.wait_for(self._call(work, request), timeout=self.limits.item_timeout)

    @staticmethod
    def _failed(work: WorkItem, error: str) -> TranslationResult:
        """Returns the unsuccessful result of *work* with *error*."""
        return TranslationResult(
            translated="", original=work.sanitized, success=False, error=error, metadata={}
        )

    async def _ask_or_fail(
        self,
        work: WorkItem,
        request: SingleRequest,
        *,
        timeout_error: str,
        error_prefix: str = "",
    ) -> TranslationResult:
        """Sends one request; a timeout or error becomes an unsuccessful result."""
        try:
            return await self._ask(work, request)
        except asyncio.TimeoutError:
            return self._failed(work, timeout_error)
        except Exception as exc:
            return self._failed(work, error_prefix + str(exc))

    async def translate_one(
        self, sem: asyncio.Semaphore, work: WorkItem, done: Done = None
    ) -> TranslationResult:
        """Translates one item with its regular request and the timeout retry.

        Args:
            sem: Semaphore of the pass.
            work: Item to translate.
            done: Called with *work* when finished.

        Returns:
            The result of the request or of its timeout retry.

        Raises:
            TranslationCancelled: The run was cancelled before the request.
        """
        request = self.plain_request(work)
        async with sem:
            self.config.raise_if_cancelled()
            try:
                result = await self._ask(work, request)
            except asyncio.TimeoutError:
                logger.warning(
                    "Translate timeout (%.0fs) for '%s…'",
                    self.limits.item_timeout,
                    work.sanitized[:40],
                )
                result = await self._retry_after_timeout(work, request)
            except Exception as exc:
                result = self._failed(work, str(exc))
        if done is not None:
            done(work)
        return result

    async def _retry_after_timeout(
        self, work: WorkItem, request: SingleRequest
    ) -> TranslationResult:
        """Retries a timed-out request once; a script string uses its fallback request."""
        timeout = self.limits.item_timeout
        if not work.is_ncs:
            result = await self._ask_or_fail(
                work,
                request,
                timeout_error=f"Timeout after {timeout}s (retry timed out)",
                error_prefix="Timeout retry failed: ",
            )
            if result.success:
                logger.info("Timeout retry recovered translation for '%s…'", work.sanitized[:40])
            return result
        self.diagnostics.timeout(work.item)
        try:
            result = await self._ask(work, self.ncs_fallback_request(work))
        except asyncio.TimeoutError:
            result = self._failed(work, f"Timeout after {timeout}s")
        except Exception as exc:
            result = self._failed(work, str(exc))
        else:
            if not result.success:
                # Only an error or a timeout is a failed retry here; the fallback
                # pass of failed batches also counts an unsuccessful answer.
                return result
        self.diagnostics.retry_outcome(work.item, result.success, result.error)
        return result

    async def translate_ncs_fallback(
        self, sem: asyncio.Semaphore, work: WorkItem
    ) -> TranslationResult:
        """Translates a script string that failed in a batch with its fallback request.

        Args:
            sem: Semaphore of the pass.
            work: Script string to translate.

        Returns:
            The result; a timeout or error becomes an unsuccessful result.

        Raises:
            TranslationCancelled: The run was cancelled before the request.
        """
        async with sem:
            self.config.raise_if_cancelled()
            return await self._ask_or_fail(
                work,
                self.ncs_fallback_request(work),
                timeout_error=(
                    f"NCS single-item fallback timeout after {self.limits.item_timeout}s"
                ),
            )

    def ask_token_retry(self, work: WorkItem, attempt: int) -> TranslationResult:
        """Sends one token retry request synchronously, without a timeout retry.

        Args:
            work: Item whose last answer was rejected.
            attempt: Retry number, starting at 1.

        Returns:
            The result; a timeout or error becomes an unsuccessful result.
        """
        request = self.token_retry_request(work, attempt)

        async def call() -> TranslationResult:
            return await self._call(work, request)

        try:
            return run_async(call(), timeout=self.limits.item_timeout)
        except Exception as exc:
            return self._failed(work, str(exc))

    # ── batch requests ───────────────────────────────────────────────────
    async def translate_batch(
        self, sem: asyncio.Semaphore, batch: List[WorkItem], done: Done = None
    ) -> List[TranslationResult]:
        """Translates a batch and narrow its failures by halving.

        When two or more results fail, the failed items are split in two halves,
        left first, and each half is sent again the same way. A single failed item
        is left for the fallback pass. At most ``2n - 1`` requests per batch.

        Args:
            sem: Semaphore of the pass.
            batch: Items in payload order.
            done: Called with every item of the top-level batch when finished.

        Returns:
            One result per item, in batch order.

        Raises:
            TranslationCancelled: The run was cancelled before a request.
        """
        results = await self._ask_batch(sem, batch)
        failed = [index for index, result in enumerate(results) if not result.success]
        if len(failed) > 1:
            middle = len(failed) // 2
            logger.info("Splitting %d failed batch items into two parts", len(failed))
            for indices in (failed[:middle], failed[middle:]):
                recovered = await self.translate_batch(sem, [batch[i] for i in indices])
                for index, result in zip(indices, recovered):
                    results[index] = result
        if done is not None:
            for work in batch:
                done(work)
        return results

    async def _ask_batch(
        self, sem: asyncio.Semaphore, batch: List[WorkItem]
    ) -> List[TranslationResult]:
        """Sends one batch request; returns exactly one result per item."""
        items = [work.translation_item() for work in batch]
        glossary_block = batch_terminology(batch, self.terminology)
        profile = content_profile(batch)
        timeout = self.limits.batch_timeout
        async with sem:
            self.config.raise_if_cancelled()
            try:
                results = await asyncio.wait_for(
                    logged_model_call(
                        self.log_writer,
                        self.provider.translate_batch_async,
                        trace_context={"occurrences": [work.key for work in batch]},
                        items=items,
                        source_lang=self.config.source_lang,
                        target_lang=self.config.target_lang,
                        glossary_block=glossary_block,
                        content_profile=profile,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Batch translate timeout (%.0fs) for %d items", timeout, len(items))
                return [self._failed(work, f"Batch timeout after {timeout}s") for work in batch]
            except Exception as exc:
                return [self._failed(work, str(exc)) for work in batch]
        results = results[: len(batch)]
        results.extend(
            self._failed(work, "Missing translation result in batch response")
            for work in batch[len(results) :]
        )
        return results

    # ── passes ───────────────────────────────────────────────────────────
    def run_main_pass(
        self, singles: Sequence[WorkItem], batches: Sequence[List[WorkItem]], done: Done
    ) -> Tuple[List[TranslationResult], List[TranslationResult]]:
        """Sends every single and batch request of the plan concurrently.

        Args:
            singles: Items sent one per request.
            batches: Batch requests.
            done: Called once per item when its request (or batch) is finished.

        Returns:
            Results of *singles* in order, and results of all batch items in
            flattened batch order.

        Raises:
            TranslationCancelled: The run was cancelled.
            TimeoutError: The pass exceeded its overall budget.
        """

        async def run_all() -> Tuple[List[TranslationResult], List[List[TranslationResult]]]:
            sem = asyncio.Semaphore(self.concurrency)
            single_results, batch_results = await asyncio.gather(
                asyncio.gather(*[self.translate_one(sem, work, done) for work in singles]),
                asyncio.gather(*[self.translate_batch(sem, batch, done) for batch in batches]),
            )
            return list(single_results), list(batch_results)

        single_results, batch_results = self._run_pass(
            run_all(),
            singles=len(singles),
            single_slot=self._retrying_slot,
            batch_calls=sum(2 * len(batch) - 1 for batch in batches),
            floor=self.limits.min_pass_timeout,
            pad=self.limits.main_pass_pad,
        )
        return single_results, [result for results in batch_results for result in results]

    def run_fallback_pass(
        self, work: Sequence[WorkItem], *, scripts: bool
    ) -> List[TranslationResult]:
        """Sends one request per item of *work* concurrently, under half the main budget.

        Args:
            work: Failed batch items.
            scripts: *work* holds script strings, sent with their fallback request;
                otherwise every item gets its regular request and timeout retry.

        Returns:
            One result per item, in order.

        Raises:
            TranslationCancelled: The run was cancelled.
            TimeoutError: The pass exceeded its overall budget.
        """

        async def run_all() -> List[TranslationResult]:
            sem = asyncio.Semaphore(self.concurrency)
            if scripts:
                calls = [self.translate_ncs_fallback(sem, w) for w in work]
            else:
                calls = [self.translate_one(sem, w) for w in work]
            return list(await asyncio.gather(*calls))

        return self._run_pass(
            run_all(),
            singles=len(work),
            single_slot=self.limits.item_timeout if scripts else self._retrying_slot,
            batch_calls=0,
            floor=self.limits.min_fallback_pass_timeout,
            pad=self.limits.fallback_pass_pad,
        )

    @property
    def _retrying_slot(self) -> float:
        """Longest semaphore hold of :meth:`translate_one`: a request and its timeout retry."""
        return 2 * self.limits.item_timeout

    def _run_pass(
        self,
        coro: Coroutine[Any, Any, _T],
        *,
        singles: int,
        single_slot: float,
        batch_calls: int,
        floor: float,
        pad: float,
    ) -> _T:
        """Runs one pass with a budget that covers its queued requests.

        Args:
            coro: The pass.
            singles: Single requests in the pass.
            single_slot: Longest time one single request holds its slot.
            batch_calls: Upper bound of batch requests, halving included.
            floor: Smallest budget.
            pad: Seconds added to the queued time.

        Returns:
            What the pass returns.

        Raises:
            TranslationCancelled: The run was cancelled.
            TimeoutError: The pass exceeded its budget.
        """
        queue = (
            queued_timeout(singles, single_slot, self.concurrency)
            + queued_timeout(batch_calls, self.limits.batch_timeout, self.concurrency)
            + pad
        )
        return run_async(coro, timeout=max(floor, queue))
