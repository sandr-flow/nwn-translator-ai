"""Batch translation of every non-dialog string of a module.

:class:`TranslationManager` takes the extracted occurrences of a run, lets the
script gate decide which script literals may change, sends each distinct request
once (see :mod:`.work_plan` and :mod:`.model_calls`) and fans every accepted answer
out to all occurrences that share it. An answer is accepted only when it carries
exactly the source's tokens and tags; otherwise the request is retried and the
last answer is finally cleaned up.
"""

import logging
from dataclasses import replace
from typing import (
    Any,
    Callable,
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Protocol,
    Set,
)

from ..ai_providers import TranslationProvider, TranslationResult
from ..config import TranslationConfig
from ..extractors.base import ExtractedContent, Occurrence, TranslatableItem, Translations
from ..glossary import Glossary, restore_wrapping_quotes, terminology_block
from ..translation_logging import (
    TranslationLogWriter,
    translation_log_writer_for_config,
    write_trace,
)
from .model_calls import CallLimits, ModelCaller
from .ncs_diagnostics import NcsDiagnostics, new_ncs_diagnostics
from .script_gate import ScriptGate, add_script_context
from .token_handler import sanitize_text
from .work_plan import BatchLimits, WorkItem, dedup_key, plan_work

logger = logging.getLogger(__name__)


class ItemProgress(Protocol):
    """Per-item progress counter of a run."""

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        """Count *by* finished items of *filename*.

        Args:
            by: Items finished.
            filename: Resource the items belong to.
        """


def unescape_literal_newlines(original: str, translated: str) -> str:
    """Turn ``\\n`` sequences of a model answer into newlines when the source has them.

    Args:
        original: Source text.
        translated: Model answer.

    Returns:
        *translated*, with literal ``\\r\\n``, ``\\n`` and ``\\r`` replaced by
        newlines only when *original* contains a line break.
    """
    if "\n" not in original and "\r" not in original:
        return translated
    if "\\n" not in translated and "\\r" not in translated:
        return translated
    return translated.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


class TranslationManager:
    """Translates occurrences in deduplicated single and batch requests.

    Attributes:
        config: Run settings.
        provider: Model provider.
        glossary: Proper-name glossary offered to the model, if any.
        batch_limits: Budgets of one batch request.
        call_limits: Timeouts and retry budget of requests.
        stats: ``items_translated`` (accepted distinct requests), ``errors``
            (one line per rejected request) and ``ncs_diagnostics``.
        failed_items: Occurrences whose request was rejected, with every
            occurrence that shares the request. Script literals refused by the
            gate are not failures.
    """

    def __init__(
        self,
        config: TranslationConfig,
        provider: TranslationProvider,
        glossary: Optional[Glossary] = None,
        log_writer: Optional[TranslationLogWriter] = None,
    ):
        """Create a manager for one run.

        Args:
            config: Run settings.
            provider: Model provider.
            glossary: Proper-name glossary offered to the model, if any.
            log_writer: Log writer of the run; by default the one *config*
                names.
        """
        self.config = config
        self.provider = provider
        self.glossary = glossary
        self.batch_limits = BatchLimits()
        self.call_limits = CallLimits()
        if log_writer is None:
            log_writer = translation_log_writer_for_config(
                config.translation_log, config.translation_log_writer
            )
        self._log_writer = log_writer
        self.stats: Dict[str, Any] = {
            "items_translated": 0,
            "errors": [],
            "ncs_diagnostics": new_ncs_diagnostics(),
        }
        self.failed_items: Set[Occurrence] = set()
        self._diagnostics = NcsDiagnostics(self.stats["ncs_diagnostics"], self._log_writer)

    def translate_content(
        self,
        content: ExtractedContent,
        item_progress: Optional[ItemProgress] = None,
    ) -> Translations:
        """Translate every non-blank occurrence of *content*.

        Only requests with equal text, context, profile, hint and terminology share
        an answer; every answer stays addressed by resource and item id. Rejected
        requests are recorded in :attr:`stats` and :attr:`failed_items`.

        Args:
            content: Occurrences to translate (usually of several resources).
            item_progress: Counter bumped once per non-blank occurrence.

        Returns:
            Accepted translation per occurrence.

        Raises:
            ValueError: An occurrence has no resource or item id.
            TranslationCancelled: The run was cancelled.
            TimeoutError: A pass exceeded its overall budget.
        """
        work = [self._prepare(item) for item in content.items if item.has_text()]
        if not work:
            return {}

        def bump(filename: str) -> None:
            if item_progress is not None:
                item_progress.bump(filename=filename)

        ncs_count = sum(1 for w in work if w.is_ncs)
        if ncs_count:
            self._diagnostics.count("total", ncs_count)
            self._diagnostics.count("extracted", ncs_count)
        gate = ScriptGate(self.config, self.provider, self._log_writer, self._diagnostics)
        approvals = gate.decide([w.item for w in work])
        approved = [w for w in work if not w.is_ncs or approvals.get(w.key, False)]
        add_script_context([w.item for w in approved if w.is_ncs])
        for w in work:
            if w.is_ncs and not approvals.get(w.key, False):
                bump(w.key[0])

        groups: Dict[Hashable, List[WorkItem]] = {}
        for w in approved:
            groups.setdefault(dedup_key(w, self._terminology), []).append(w)
        if not groups:
            return {}
        translations = self._translate_distinct(
            [group[0] for group in groups.values()], lambda w: bump(w.key[0])
        )
        for group in groups.values():
            representative = group[0].key
            for duplicate in group[1:]:
                if representative in translations:
                    translations[duplicate.key] = translations[representative]
                    write_trace(
                        self._log_writer,
                        {
                            "event": "translation_reuse",
                            "occurrence": duplicate.key,
                            "representative": representative,
                        },
                    )
                elif representative in self.failed_items:
                    self.failed_items.add(duplicate.key)
                bump(duplicate.key[0])
        return translations

    def get_statistics(self) -> Dict[str, Any]:
        """Return :attr:`stats` plus ``total_errors``."""
        return {**self.stats, "total_errors": len(self.stats["errors"])}

    def _prepare(self, item: TranslatableItem) -> WorkItem:
        """Sanitize one occurrence; its copy names its resource for batch payloads."""
        sanitized, handler = sanitize_text(item.text, preserve_tokens=self.config.preserve_tokens)
        prepared = replace(item, metadata={**item.metadata, "batch_resource": item.key[0]})
        return WorkItem(item=prepared, sanitized=sanitized, handler=handler)

    def _terminology(self, texts: Iterable[Optional[str]]) -> Optional[str]:
        """Return the glossary block for *texts*, or None when no term matches.

        The provider treats None and an empty string alike (it falls back to the race
        terms of the text); None is kept so that the logged request arguments stay
        unchanged. Missing texts (an item without context) match nothing.

        Args:
            texts: Texts of one request; None entries are skipped.

        Returns:
            The glossary block, or None.
        """
        present = (text for text in texts if text)
        return terminology_block(present, self.config.target_lang, self.glossary) or None

    def _translate_distinct(
        self, work: List[WorkItem], done: Callable[[WorkItem], None]
    ) -> Translations:
        """Translate distinct requests: passthrough, main pass, then fallbacks.

        Results are processed in a fixed order: long singles, batch results (failed
        batch items are set aside), failed script strings through their fallback
        request, then the other failed batch items one by one.

        Args:
            work: Distinct requests, in input order.
            done: Called once per item when its first-pass request is finished.

        Returns:
            Accepted translation per request.
        """
        translations: Translations = {}
        plan = plan_work(work, self.batch_limits, self._terminology)
        caller = ModelCaller(
            self.config,
            self.provider,
            self._log_writer,
            self._diagnostics,
            self._terminology,
            self.call_limits,
        )

        if plan.passthrough:
            logger.info(
                "Skipping API for %d item(s) with no translatable content "
                "(tokens/punctuation only)",
                len(plan.passthrough),
            )
        for w in plan.passthrough:
            # The sanitized form restores to the source itself.
            translated = self._accept(w, w.sanitized)
            if translated is None:
                self._record_rejected(w, "text without translatable content was rejected")
            else:
                translations[w.key] = translated
            done(w)

        single_results, batch_results = caller.run_main_pass(plan.singles, plan.batches, done)
        if plan.batches:
            logger.info(
                "Batch-translated %d items in %d batch(es), %d long items individually",
                sum(len(batch) for batch in plan.batches),
                len(plan.batches),
                len(plan.singles),
            )
        for w, result in zip(plan.singles, single_results):
            self._process(caller, translations, w, result)

        failed_ncs: List[WorkItem] = []
        timed_out: Set[Occurrence] = set()
        failed_other: List[WorkItem] = []
        batch_work = [w for batch in plan.batches for w in batch]
        for w, result in zip(batch_work, batch_results):
            if result.success:
                self._process(caller, translations, w, result)
            elif w.is_ncs:
                failed_ncs.append(w)
                if result.error and "timeout" in result.error.lower():
                    timed_out.add(w.key)
            else:
                failed_other.append(w)

        if failed_ncs:
            ncs_results = self._retry_failed_scripts(caller, failed_ncs, timed_out)
            for w, result in zip(failed_ncs, ncs_results):
                self._process(caller, translations, w, result)
        if failed_other:
            logger.info("Retrying %d failed batch items individually", len(failed_other))
            other_results = caller.run_fallback_pass(failed_other, scripts=False)
            for w, result in zip(failed_other, other_results):
                self._process(caller, translations, w, result)
        return translations

    def _retry_failed_scripts(
        self, caller: ModelCaller, failed: List[WorkItem], timed_out: Set[Occurrence]
    ) -> List[TranslationResult]:
        """Send failed script strings with their fallback request, one per request.

        Script strings whose batch timed out are recorded as timeouts before the
        pass and as recovered or failed after it.

        Args:
            caller: Request sender of the run.
            failed: Script strings whose batch requests failed.
            timed_out: Those whose batch request timed out.

        Returns:
            One fallback result per string, in order.
        """
        for w in failed:
            if w.key in timed_out:
                self._diagnostics.timeout(w.item)
        results = caller.run_fallback_pass(failed, scripts=True)
        for w, result in zip(failed, results):
            if w.key in timed_out:
                self._diagnostics.retry_outcome(w.item, result.success, result.error)
        return results

    def _process(
        self,
        caller: ModelCaller,
        translations: Translations,
        work: WorkItem,
        result: TranslationResult,
    ) -> None:
        """Accept a model result into *translations*, retrying a broken answer.

        A request that fails or whose answers are all rejected is recorded as
        rejected instead.

        Args:
            caller: Request sender of the run.
            translations: Accepted translations of the run.
            work: Item the result belongs to.
            result: Model result.
        """
        if not result.success:
            self._record_rejected(work, result.error)
            return
        model = result.metadata.get("model", self.config.model)
        translated = self._accept(work, result.translated, model=model)
        if translated is None:
            translated = self._retry_token_mismatch(caller, work, result.translated, model)
        if translated is None:
            self._record_rejected(work, "rejected after retries")
        else:
            translations[work.key] = translated

    def _accept(
        self,
        work: WorkItem,
        answer: str,
        *,
        model: Optional[str] = None,
        allow_cleanup: bool = False,
    ) -> Optional[str]:
        """Restore and validate one answer; count and log it when accepted.

        A rejected answer's validation report is kept in ``work.mismatch`` for the
        retry prompt.

        Args:
            work: Item the answer belongs to.
            answer: Model answer with placeholders.
            model: Model that answered; the configured model when unknown.
            allow_cleanup: Accept a mismatched answer after removing broken artifacts.

        Returns:
            The final translation, or None when the answer is rejected.
        """
        item = work.item
        outcome = work.handler.finalize_translation(answer, allow_cleanup=allow_cleanup)
        if not outcome.exact_valid and not outcome.used_cleanup:
            work.mismatch = outcome.mismatch_report
            logger.warning(
                "%s: token/tag mismatch for %s (%s). expected=%s actual=%s",
                item.key[0],
                item.item_id,
                outcome.mismatch_report.mismatch_type,
                outcome.mismatch_report.expected_sequence,
                outcome.mismatch_report.actual_sequence,
            )
            return None
        translated = unescape_literal_newlines(item.text, outcome.final_text)
        translated = restore_wrapping_quotes(item.text, translated)
        if item.text.strip() and not translated.strip():
            logger.warning("Empty translation rejected for %s", item.item_id)
            return None
        self.stats["items_translated"] += 1
        if outcome.used_cleanup:
            logger.warning(
                "%s: accepted cleaned translation for %s after token/tag mismatch cleanup.",
                item.key[0],
                item.item_id,
            )
        if work.is_ncs:
            self._diagnostics.count("translated")
        write_trace(
            self._log_writer,
            {
                "original": item.text,
                "translated": translated,
                "context": item.context,
                "model": model or self.config.model,
                "file": item.key[0],
                "item_id": item.item_id,
            },
        )
        return translated

    def _retry_token_mismatch(
        self, caller: ModelCaller, work: WorkItem, first_answer: str, model: Optional[str]
    ) -> Optional[str]:
        """Retry a rejected answer with stricter prompts, then accept a cleaned one.

        Retries stop early when the model repeats the same broken artifact
        sequence. Requests are sent one at a time, in result order.

        Args:
            caller: Request sender of the run.
            work: Item whose answer was rejected.
            first_answer: The rejected answer.
            model: Model that gave it.

        Returns:
            The accepted translation, or None when even the cleaned answer is rejected.
        """
        last_answer = first_answer
        last_model = model or self.config.model
        for attempt in range(1, self.call_limits.token_retries + 1):
            previous = work.mismatch
            result = caller.ask_token_retry(work, attempt)
            if not result.success:
                logger.warning(
                    "Token retry failed for %s on attempt %d: %s",
                    work.item.item_id,
                    attempt,
                    result.error,
                )
                continue
            last_answer = result.translated
            last_model = result.metadata.get("model", last_model)
            translated = self._accept(work, result.translated, model=last_model)
            if translated is not None:
                return translated
            if (
                previous is not None
                and work.mismatch is not None
                and previous.actual_sequence == work.mismatch.actual_sequence
            ):
                # A deterministic repeat of the same mismatch; more retries waste calls.
                logger.debug(
                    "Token retry produced identical mismatch for %s; skipping remaining retries.",
                    work.item.item_id,
                )
                break
        return self._accept(work, last_answer, model=last_model, allow_cleanup=True)

    def _record_rejected(self, work: WorkItem, error: Optional[str]) -> None:
        """Record a request that was sent to the model but never accepted."""
        item = work.item
        error_msg = f"Translation failed for {item.item_id}: {error}"
        if work.is_ncs:
            self._diagnostics.record(
                item, reason="translation_failed", count_field="failed", error=error
            )
        self.failed_items.add(item.key)
        self.stats["errors"].append(error_msg)
        logger.warning(error_msg)
