"""Contextual translation of dialogs (``.dlg``) with their conversation tree.

A dialog is sent as a script that shows every line with its speaker and the
lines it leads to (:mod:`~nwn_translator.context.dialog_formatter`), under a
system prompt with the world context, the glossary and the dialog's
speakers. Large dialogs are split into chunks and small ones share grouped
requests (:mod:`.dialog_plan`). An unparseable answer is re-requested along a
fixed recovery table. Lines that stay missing or come back with broken NWN
tokens are retried together, then one by one, and at last accepted with the
broken tokens cleaned out when that yields a valid text.
"""

import json
import logging
import threading
from collections import deque
from concurrent.futures import Future, as_completed
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
    Deque,
    Dict,
    List,
    NamedTuple,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
)

from ..ai_providers import TranslationProvider
from ..ai_providers.base import RateLimitError, SystemContent
from ..async_utils import close_thread_resources, run_async
from ..config import (
    TRANSLATION_MAX_TOKENS,
    TRANSLATION_TEMPERATURE,
    TranslationCancelled,
    TranslationConfig,
)
from ..context.dialog_formatter import format_nodes, speaker_label
from ..context.dialog_speakers import speaker_lines
from ..context.world_context import WorldContext
from ..extractors.base import Occurrence, Translations
from ..glossary import terminology_block
from ..json_utils import json_extract_first_object, strip_json_markdown_fences
from ..prompts import build_dialog_system_prompt_parts
from ..prompts.dialog import (
    dialog_user_prompt,
    group_repair_prompt,
    group_script,
    group_user_prompt,
    line_retry_context,
    repair_prompt,
    speakers_block,
    token_retry_prompt,
)
from ..telemetry import llm_phase
from ..translation_logging import (
    TranslationLogWriter,
    logged_model_call,
    translation_log_writer_for_config,
    write_trace,
)
from .dialog_plan import Chunk, PreparedDialog, plan_chunks, plan_requests, prepare_dialog
from .token_handler import TokenMismatchReport

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

#: Output budget of the recovery requests. It currently equals
#: ``TRANSLATION_MAX_TOKENS``, so the "higher max_tokens" of the step warnings
#: overstates it: a step that re-sends the original prompt repeats the first
#: request. The step is kept on purpose: the request is sampled
#: (``TRANSLATION_TEMPERATURE``), so a retry can still return a complete answer,
#: and it is part of the fixed recovery sequence whose requests a run sends. The
#: value stays a literal so that a lower first-request budget would give the
#: recovery requests a higher one again.
_RECOVERY_MAX_TOKENS = 32768

#: Result of one pool job: its translations and ``(file, error)`` pairs.
_JobResult = Tuple[Translations, List[Tuple[Path, Exception]]]


class ProgressSink(Protocol):
    """Counter of translated items (the pipeline's progress reporter)."""

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        """Counts more items of a file as done.

        Args:
            by: Number of items.
            filename: Name of the file they belong to.
        """


class _Step(NamedTuple):
    """One recovery request after an unparseable answer.

    Attributes:
        repair: Send the repair prompt instead of the original one. The repair
            prompt is built once, from the answer before the first repair step.
        recovery_budget: Send it with :data:`_RECOVERY_MAX_TOKENS` rather than
            ``TRANSLATION_MAX_TOKENS``; the value is read when the request is
            sent.
        warning: Message logged before the request; ``%s`` is the request label.
    """

    repair: bool
    recovery_budget: bool
    warning: str


#: Recovery steps, keyed by whether the first answer looks cut off mid-string.
_Recovery = Dict[bool, Tuple[_Step, ...]]

_CHUNK_RECOVERY: _Recovery = {
    True: (
        _Step(
            repair=False,
            recovery_budget=True,
            warning="%s: dialog JSON parse failed with truncation-like invalid JSON; "
            "retrying original prompt with higher max_tokens...",
        ),
        _Step(
            repair=True,
            recovery_budget=True,
            warning="%s: high-token original prompt retry still returned invalid JSON; "
            "retrying repair prompt with higher max_tokens as final fallback...",
        ),
    ),
    False: (
        _Step(
            repair=True,
            recovery_budget=False,
            warning="%s: dialog JSON parse failed with non-truncation invalid JSON; "
            "retrying with repair prompt...",
        ),
        _Step(
            repair=True,
            recovery_budget=True,
            warning="%s: repair prompt still returned invalid JSON; "
            "retrying repair prompt with higher max_tokens as final fallback...",
        ),
    ),
}
_GROUP_RECOVERY: _Recovery = {
    True: (
        _Step(
            repair=False,
            recovery_budget=True,
            warning="Dialog group %s: JSON looks truncated; retrying with higher max_tokens...",
        ),
    ),
    False: (
        _Step(
            repair=True,
            recovery_budget=False,
            warning="Dialog group %s: invalid JSON; retrying with repair prompt...",
        ),
    ),
}
_PENDING_RECOVERY: _Recovery = {
    True: (
        _Step(
            repair=False,
            recovery_budget=True,
            warning="%s: pending dialog retry JSON looks truncated; "
            "retrying the same JSON retry prompt with higher max_tokens...",
        ),
    ),
    False: (),
}


class _Rejected(NamedTuple):
    """A model answer for one line that was not accepted.

    Attributes:
        text: The answer as sent back (sanitized form).
        report: How it broke the line's tokens and tags; ``None`` for an
            empty answer.
    """

    text: str
    report: Optional[TokenMismatchReport]


class _FileProgress:
    """Progress of one dialog file, clamped to the file's item budget.

    The pipeline counts a file's extracted items, which need not equal its
    dialog lines, so the lines reported never exceed the budget and
    :meth:`finish` reports whatever is left of it.
    """

    def __init__(self, sink: Optional[ProgressSink], budget: int, filename: str) -> None:
        """Starts the progress of one file at zero.

        Args:
            sink: Progress sink, if any; without one nothing is reported.
            budget: Progress units of the file; ``0`` disables clamping and
                :meth:`finish`.
            filename: File name passed to the sink.
        """
        self._sink = sink
        self._budget = budget
        self._filename = filename
        self._done = 0

    def bump(self, by: int) -> None:
        """Reports more lines, up to what is left of the budget.

        Args:
            by: Number of lines; without a budget it is reported as is.
        """
        if self._sink is None or by <= 0:
            return
        delta = min(by, max(0, self._budget - self._done)) if self._budget else by
        if delta <= 0:
            return
        self._sink.bump(by=delta, filename=self._filename)
        self._done += delta

    def finish(self) -> None:
        """Reports the rest of the budget, whatever was translated."""
        if self._budget:
            self.bump(self._budget - self._done)


@dataclass
class _FileRun:
    """State of one dialog file while its lines are requested.

    Attributes:
        dialog: The prepared dialog.
        progress: Its progress.
        translations: Lines accepted so far.
        speakers: ``DIALOG SPEAKERS`` block of its system prompts.
        rejected: Latest rejected answer of each line not accepted yet.
    """

    dialog: PreparedDialog
    progress: _FileProgress
    translations: Translations
    speakers: str
    rejected: Dict[str, _Rejected] = field(default_factory=dict)

    def take(self, accepted: Translations) -> None:
        """Adds accepted lines and reports their progress.

        Args:
            accepted: Accepted translations by occurrence.
        """
        self.translations.update(accepted)
        self.progress.bump(len(accepted))


def _parse_answer(raw: str, label: str) -> Optional[Dict[str, Any]]:
    """Returns the first JSON object of an answer, logging an error when there is none.

    Args:
        raw: The model's answer.
        label: Name of the request in the log message.

    Returns:
        The object, or ``None`` when the answer holds no valid one.
    """
    parsed = json_extract_first_object(raw)
    if parsed is None:
        logger.error(
            "Failed to parse JSON for %s (no valid object). Raw prefix: %s...",
            label,
            (raw or "").strip()[:400],
        )
    return parsed


def _looks_truncated(raw: str) -> bool:
    """Guesses whether an unparseable answer stopped mid-string at ``max_tokens``.

    Only an unterminated string counts, and the strict decoder is used, so a
    raw control character earlier in the answer hides the truncation.

    Args:
        raw: An answer that did not parse.

    Returns:
        ``True`` when it looks cut off.
    """
    cleaned = strip_json_markdown_fences(raw)
    start = cleaned.find("{")
    if start == -1:
        return False
    try:
        json.JSONDecoder().raw_decode(cleaned, start)
    except json.JSONDecodeError as exc:
        return "unterminated" in str(exc).lower()
    return False


def _group_part(answer: Dict[str, Any], file_path: Path) -> Any:
    """Returns the part of a grouped answer for *file_path*.

    Keys match the file name or its stem, ignoring case and surrounding
    spaces; the first match in answer order wins.

    Args:
        answer: Parsed grouped answer: file name -> line key -> text.
        file_path: Path of one file of the group.

    Returns:
        The file's part, whatever its type, or ``None`` when no key matches.
    """
    targets = {file_path.name.casefold(), file_path.stem.casefold()}
    for key, value in answer.items():
        if str(key).strip().casefold() in targets:
            return value
    return None


class ContextualTranslationManager:
    """Translator of dialog files, with their conversation tree as context.

    Attributes:
        config: Run configuration.
        provider: Model provider.
        world_context: Scanned module objects: speakers and the world block.
        glossary: Canonical translations of proper names, if built.
        failed_items: Lines sent to the model whose translation was never
            accepted.
    """

    def __init__(
        self,
        config: TranslationConfig,
        provider: TranslationProvider,
        world_context: WorldContext,
        glossary: Optional["Glossary"] = None,
        log_writer: Optional[TranslationLogWriter] = None,
    ) -> None:
        """Creates a manager for one run.

        Args:
            config: Run configuration.
            provider: Model provider.
            world_context: Scanned module objects.
            glossary: Canonical translations of proper names, if built.
            log_writer: Log writer of the run; by default the one *config*
                names.
        """
        self.config = config
        self.provider = provider
        self.world_context = world_context
        self.glossary = glossary
        if log_writer is None:
            log_writer = translation_log_writer_for_config(
                config.translation_log, config.translation_log_writer
            )
        self._log_writer = log_writer
        self.failed_items: Set[Occurrence] = set()

    def translate_dialogs(
        self,
        dialog_files: Sequence[Tuple[Path, Dict[str, Any], int]],
        item_progress: Optional[ProgressSink] = None,
    ) -> Tuple[Translations, List[Tuple[Path, Exception]]]:
        """Translates dialog files on a pool of ``max_concurrent_requests`` threads.

        Small dialogs share grouped requests. A file whose part of a grouped
        answer is missing, incomplete or has broken tokens falls back to its
        own requests for the remaining lines. A failed request is logged and
        its lines end up in :attr:`failed_items`; only the failures listed
        under Returns are reported as errors. Each prepared file reports its
        item budget of progress in total, unless an exception escapes its
        job; a file whose preparation failed reports none.

        Args:
            dialog_files: ``(file_path, parsed_data, item_budget)`` per file, in
                pipeline order; *item_budget* is the file's extracted item count.
            item_progress: Progress sink, if any.

        Returns:
            All accepted translations, and ``(file_path, error)`` pairs for:

            - a file whose preparation raised;
            - every file of a group whose request hit a rate or budget limit
              (the files are not requested again one by one; any other
              failure of a group request falls back to single files);
            - a file whose single-file translation raised unexpectedly, or
              every file of a job that an exception escaped.

        Raises:
            TranslationCancelled: If the run is cancelled; queued files are
                dropped.
        """
        translations: Translations = {}
        errors: List[Tuple[Path, Exception]] = []
        dialogs: List[PreparedDialog] = []
        for file_path, parsed_data, item_budget in dialog_files:
            self.config.raise_if_cancelled()
            try:
                dialog = prepare_dialog(
                    file_path,
                    parsed_data,
                    item_budget,
                    preserve_tokens=self.config.preserve_tokens,
                )
            except TranslationCancelled:
                raise
            except Exception as exc:
                errors.append((file_path, exc))
                continue
            if dialog is None or not dialog.texts:
                _FileProgress(item_progress, item_budget, file_path.name).finish()
            else:
                dialogs.append(dialog)

        singles, groups = plan_requests(dialogs, self.config.target_lang, self.glossary)
        if groups:
            logger.info(
                "Dialog grouping: %d small file(s) packed into %d group request(s); "
                "%d file(s) translated individually.",
                sum(len(group) for group in groups),
                len(groups),
                len(singles),
            )

        jobs: List[Tuple[Callable[[], _JobResult], List[PreparedDialog]]] = [
            (partial(self._translate_single, dialog, item_progress), [dialog]) for dialog in singles
        ]
        jobs += [(partial(self._translate_group, group, item_progress), group) for group in groups]
        queue: Deque[Tuple[Callable[[], _JobResult], Future[_JobResult]]] = deque()
        futures: Dict[Future[_JobResult], List[PreparedDialog]] = {}
        for job, files in jobs:
            future: Future[_JobResult] = Future()
            queue.append((job, future))
            futures[future] = files
        workers = [
            threading.Thread(target=self._work, args=(queue,), name=f"dialog-{index}")
            for index in range(min(max(1, self.config.max_concurrent_requests), len(jobs)))
        ]
        for worker in workers:
            worker.start()
        try:
            for future in as_completed(futures):
                try:
                    job_translations, job_errors = future.result()
                except TranslationCancelled:
                    raise
                except Exception as exc:
                    errors.extend((dialog.file_path, exc) for dialog in futures[future])
                else:
                    translations.update(job_translations)
                    errors.extend(job_errors)
        except TranslationCancelled:
            for future in futures:
                future.cancel()
            raise
        finally:
            for worker in workers:
                worker.join()
        return translations, errors

    def _work(self, queue: Deque[Tuple[Callable[[], _JobResult], Future[_JobResult]]]) -> None:
        """Runs queued jobs in order on a worker thread, then releases its resources.

        ``run_async`` keeps one event loop per thread, and the provider one
        HTTP client per loop, so a worker reuses them for all its jobs. They
        are closed when the queue is empty, before the thread ends. A
        cancelled job is skipped.

        Args:
            queue: Jobs shared by all workers, with the future of each; a
                job's result or exception is set on its future.
        """
        try:
            while queue:
                try:
                    job, future = queue.popleft()
                except IndexError:
                    break
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(job())
                    except BaseException as exc:
                        future.set_exception(exc)
        finally:
            close_thread_resources(self.provider)

    def _translate_single(
        self, dialog: PreparedDialog, item_progress: Optional[ProgressSink]
    ) -> _JobResult:
        """Translates one dialog on its own (a pool job).

        Args:
            dialog: The prepared dialog.
            item_progress: Progress sink, if any.

        Returns:
            The file's translations and no errors.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        self.config.raise_if_cancelled()
        return self._translate_file(dialog, item_progress), []

    def _translate_file(
        self,
        dialog: PreparedDialog,
        item_progress: Optional[ProgressSink],
        accepted: Optional[Translations] = None,
    ) -> Translations:
        """Translates the lines of one dialog that *accepted* does not cover yet.

        Errors are logged, not raised: the lines not accepted by then are
        added to :attr:`failed_items`. The file reports its whole item budget
        of progress in the end.

        Args:
            dialog: The prepared dialog.
            item_progress: Progress sink, if any.
            accepted: Translations accepted earlier (from a grouped answer);
                lines of this file found there are kept and not requested
                again.

        Returns:
            The file's accepted translations, including those from *accepted*.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        name = dialog.file_path.name
        progress = _FileProgress(item_progress, dialog.item_budget, name)
        translations = {
            address: text for address, text in (accepted or {}).items() if address[0] == name
        }
        keys = [key for key in dialog.keys if dialog.address(key) not in translations]
        progress.bump(len(dialog.texts) - len(keys))
        if not keys:
            logger.debug(
                "All %d dialog lines for %s already accepted for this dialog",
                len(dialog.texts),
                name,
            )
        else:
            run = _FileRun(
                dialog,
                progress,
                translations,
                speakers_block(
                    speaker_lines(self.world_context, dialog.file_path.stem, dialog.node_map)
                ),
            )
            try:
                self._request_lines(run, keys)
            except TranslationCancelled:
                raise
            except Exception as exc:
                logger.error("Contextual translation failed for %s: %s", name, exc)
            self._mark_failed(dialog, keys, translations)
        progress.finish()
        return translations

    def _request_lines(self, run: _FileRun, keys: List[str]) -> None:
        """Sends the chunks of *keys*, then retries what is still missing or broken.

        Accepted lines are added to *run* as they come.

        Args:
            run: The file's state.
            keys: Keys of the lines to request, in walk order.

        Raises:
            RateLimitError: If a chunk or the pending retry hits a rate or
                budget limit.
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        chunks = plan_chunks(dialog, keys, self.config.target_lang, self.glossary)
        if len(chunks) == 1:
            logger.info(
                "Sending %d/%d dialog lines to AI for %s...", len(keys), len(dialog.texts), name
            )
        else:
            logger.info(
                "Sending %d/%d dialog lines to AI for %s in %d chunk(s)...",
                len(keys),
                len(dialog.texts),
                name,
                len(chunks),
            )
        pending: List[str] = []
        for index, chunk in enumerate(chunks, 1):
            self.config.raise_if_cancelled()
            pending.extend(self._translate_chunk(run, chunk, index, len(chunks)))
        # Sorted as strings (E10 before E2): the retry prompt lists them so.
        pending = sorted(set(pending))
        if not pending:
            return
        self.config.raise_if_cancelled()
        pending = self._retry_pending(run, pending)
        if pending:
            self._retry_lines(run, pending)

    def _translate_chunk(self, run: _FileRun, chunk: Chunk, index: int, total: int) -> List[str]:
        """Requests one chunk and accepts its valid lines.

        A failing request (a provider error after its own retries, a
        timeout) costs only this chunk: its lines stay unaccepted and are
        reported as failed, while the other chunks are still requested. A
        rate or budget limit error stops the file instead, since every
        further request would meet the same limit.

        Args:
            run: The file's state.
            chunk: The lines to request and their script.
            index: 1-based position of the chunk, for log messages.
            total: Number of chunks of the file.

        Returns:
            Keys of the chunk that are missing from the answer or were
            rejected, to be retried; all of them when the answer never
            parsed, none when the request failed.

        Raises:
            RateLimitError: If the provider reports a rate or budget limit.
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        if total > 1:
            logger.info(
                "%s: translating dialog chunk %d/%d (%d node(s), %d chars).",
                name,
                index,
                total,
                len(chunk.keys),
                len(chunk.script),
            )
        try:
            answer = self._request_json(
                _CHUNK_RECOVERY,
                self._system_prompt([chunk.script, dialog.file_path.stem], run.speakers),
                dialog_user_prompt(name, chunk.script),
                lambda raw: repair_prompt(name, chunk.script, chunk.keys, raw),
                trace={"file": name},
                label=name,
            )
        except (TranslationCancelled, RateLimitError):
            raise
        except Exception as exc:
            logger.error("%s: dialog chunk %d/%d request failed: %s", name, index, total, exc)
            return []
        if answer is None:
            logger.error(
                "%s: dialog translation chunk %d/%d failed after retries (invalid JSON).",
                name,
                index,
                total,
            )
            return list(chunk.keys)
        accepted, rejected = self._accept(dialog, answer, chunk.keys, allow_cleanup=False)
        run.take(accepted)
        run.rejected.update(rejected)
        return [key for key in chunk.keys if key not in answer or key in rejected]

    def _retry_pending(self, run: _FileRun, pending: List[str]) -> List[str]:
        """Requests the pending lines again in one token-preserving request.

        A failing request is treated like an unusable answer, so the lines
        still get their single-line retries and cleanup. A rate or budget
        limit error is raised instead: one more request per line would only
        meet the same limit.

        Args:
            run: The file's state.
            pending: Keys still missing or rejected, sorted.

        Returns:
            Keys still pending afterwards, sorted.

        Raises:
            RateLimitError: If the provider reports a rate or budget limit.
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        logger.warning(
            "%s: retrying %d dialog nodes with missing or invalid preserved artifacts...",
            name,
            len(pending),
        )
        script = format_nodes(pending, dialog.node_map, dialog.sanitized)
        prompt = token_retry_prompt(
            name,
            script,
            pending,
            {key: dialog.handlers[key].get_expected_artifact_sequence() for key in pending},
            {key: run.rejected[key].report for key in pending if key in run.rejected},
        )
        answer: Optional[Dict[str, Any]]
        try:
            answer = self._request_json(
                _PENDING_RECOVERY,
                self._system_prompt([script, dialog.file_path.stem], run.speakers),
                prompt,
                None,
                trace={"file": name},
                label=name,
            )
        except (TranslationCancelled, RateLimitError):
            raise
        except Exception as exc:
            logger.error("%s: pending dialog retry request failed: %s", name, exc)
            answer = None
        if not answer:
            return pending
        accepted, rejected = self._accept(dialog, answer, pending, allow_cleanup=False)
        run.take(accepted)
        logger.info("%s: retry recovered %d additional dialog translations.", name, len(accepted))
        for key in pending:
            if key in answer and key not in rejected:
                run.rejected.pop(key, None)
        run.rejected.update(rejected)
        return [key for key in pending if key not in answer or key in rejected]

    def _retry_lines(self, run: _FileRun, keys: List[str]) -> None:
        """Retries lines one by one, accepting a cleaned answer for lines that still fail.

        Args:
            run: The file's state.
            keys: Keys still pending after the pending retry, sorted.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        logger.warning(
            "%s: %d dialog nodes still invalid after JSON retry; retrying individually.",
            dialog.file_path.name,
            len(keys),
        )
        # One block for all the lines retried, matched against all their texts.
        glossary_block = (
            terminology_block(
                [dialog.sanitized[key] for key in keys], self.config.target_lang, self.glossary
            )
            or None
        )
        for key in keys:
            self.config.raise_if_cancelled()
            if self._retry_line(run, key, glossary_block):
                continue
            candidate = run.rejected.get(key)
            cleaned, _ = self._accept(
                dialog, {key: candidate.text if candidate else ""}, [key], allow_cleanup=True
            )
            run.take(cleaned)

    def _retry_line(self, run: _FileRun, key: str, glossary_block: Optional[str]) -> bool:
        """Requests one line alone through ``translate_async``.

        Args:
            run: The file's state.
            key: Key of the line.
            glossary_block: Glossary block shared by the file's retried
                lines, or ``None``.

        Returns:
            ``True`` when the answer was accepted. A rejected answer replaces
            the line's candidate for cleanup.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        previous = run.rejected.get(key)
        context = line_retry_context(
            key,
            name,
            speaker_label(dialog.node_map[key]),
            dialog.handlers[key].get_expected_artifact_sequence(),
            previous.report if previous else None,
        )
        try:
            result = run_async(
                logged_model_call(
                    self._log_writer,
                    self.provider.translate_async,
                    trace_context={"occurrence": dialog.address(key)},
                    text=dialog.sanitized[key],
                    source_lang=self.config.source_lang,
                    target_lang=self.config.target_lang,
                    context=context,
                    glossary_block=glossary_block,
                    content_profile=None,
                )
            )
        except TranslationCancelled:
            raise
        except Exception as exc:
            logger.warning("%s: individual dialog retry failed for %s: %s", name, key, exc)
            return False
        if not result.success:
            return False
        accepted, rejected = self._accept(
            dialog, {key: result.translated}, [key], allow_cleanup=False
        )
        if accepted:
            run.take(accepted)
            run.rejected.pop(key, None)
            return True
        run.rejected[key] = rejected[key]
        return False

    def _translate_group(
        self, group: List[PreparedDialog], item_progress: Optional[ProgressSink]
    ) -> _JobResult:
        """Translates small dialogs in one request and splits the answer (a pool job).

        A file whose part of the answer is missing, incomplete or has a
        rejected line falls back to its own requests; the lines accepted from
        the group are kept. After a rate or budget limit error the files are
        not re-requested: they are reported as errors.

        Args:
            group: Small dialogs packed into one request.
            item_progress: Progress sink, if any.

        Returns:
            The group's translations, and ``(file_path, error)`` for each file
            that failed on a rate or budget limit or whose fallback raised.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        translations: Translations = {}
        errors: List[Tuple[Path, Exception]] = []
        self.config.raise_if_cancelled()
        label = f"{group[0].file_path.name}+{len(group) - 1}"
        answer: Optional[Dict[str, Any]]
        try:
            answer = self._request_group(group, label)
        except TranslationCancelled:
            raise
        except RateLimitError as exc:
            # The request already used up the provider's retries; one request
            # per file would multiply the pressure on the limit.
            logger.warning(
                "Dialog group %s: rate/budget limit (%s); not falling back to single files.",
                label,
                exc,
            )
            for dialog in group:
                errors.append((dialog.file_path, exc))
                self._mark_failed(dialog, dialog.keys, {})
                _FileProgress(item_progress, dialog.item_budget, dialog.file_path.name).finish()
            return translations, errors
        except Exception as exc:
            logger.warning(
                "Dialog group %s: request failed (%s); falling back to single files.",
                label,
                exc,
            )
            answer = None

        for dialog in group:
            self.config.raise_if_cancelled()
            name = dialog.file_path.name
            part = _group_part(answer, dialog.file_path) if answer is not None else None
            applied: Translations = {}
            if isinstance(part, dict):
                applied, rejected = self._accept(dialog, part, dialog.keys, allow_cleanup=False)
                if not rejected and all(key in part for key in dialog.keys):
                    translations.update(applied)
                    _FileProgress(item_progress, dialog.item_budget, name).finish()
                    continue
            logger.warning(
                "Dialog group %s: falling back to single-file translation for %s.", label, name
            )
            try:
                translations.update(applied)
                translations.update(self._translate_file(dialog, item_progress, applied))
            except TranslationCancelled:
                raise
            except Exception as exc:
                errors.append((dialog.file_path, exc))
        return translations, errors

    def _request_group(self, group: List[PreparedDialog], label: str) -> Optional[Dict[str, Any]]:
        """Sends one grouped request.

        Args:
            group: The dialogs of the request.
            label: Name of the group in log messages.

        Returns:
            The parsed answer (file name -> line key -> text), or ``None``.
        """
        names = [dialog.file_path.name for dialog in group]
        combined = group_script([(dialog.file_path.name, dialog.script) for dialog in group])
        lines = [
            line
            for dialog in group
            for line in speaker_lines(
                self.world_context,
                dialog.file_path.stem,
                dialog.node_map,
                file_label=dialog.file_path.name,
            )
        ]
        system = self._system_prompt([combined], speakers_block(lines))
        logger.info(
            "Sending dialog group %s (%d files, %d chars)...", label, len(group), len(combined)
        )
        return self._request_json(
            _GROUP_RECOVERY,
            system,
            group_user_prompt(names, combined),
            lambda raw: group_repair_prompt(names, combined, raw),
            trace={"files": names},
            label=label,
        )

    def _request_json(
        self,
        recovery: _Recovery,
        system: SystemContent,
        user: str,
        repair: Optional[Callable[[str], str]],
        *,
        trace: Dict[str, Any],
        label: str,
    ) -> Optional[Dict[str, Any]]:
        """Sends a JSON request and recovers from an unparseable answer.

        Args:
            recovery: Steps to take when the first answer does not parse.
            system: System message content.
            user: User prompt.
            repair: Builds the repair prompt from the latest answer; required
                when *recovery* has repair steps.
            trace: Context of the requests in the translation log.
            label: Name used in log messages.

        Returns:
            The first answer that parses, or ``None``.
        """
        raw = self._call_json(system, user, TRANSLATION_MAX_TOKENS, trace)
        parsed = _parse_answer(raw, label)
        if parsed is not None:
            return parsed
        repaired: Optional[str] = None
        for step in recovery[_looks_truncated(raw)]:
            logger.warning(step.warning, label)
            prompt = user
            if step.repair:
                assert repair is not None
                if repaired is None:
                    repaired = repair(raw)
                prompt = repaired
            budget = _RECOVERY_MAX_TOKENS if step.recovery_budget else TRANSLATION_MAX_TOKENS
            raw = self._call_json(system, prompt, budget, trace)
            parsed = _parse_answer(raw, label)
            if parsed is not None:
                break
        return parsed

    def _call_json(
        self, system: SystemContent, user: str, max_tokens: int, trace: Dict[str, Any]
    ) -> str:
        """Sends one JSON chat request (metrics phase ``dialog``) and returns the reply.

        Args:
            system: System message content.
            user: User prompt.
            max_tokens: Output budget.
            trace: Context of the request in the translation log.

        Returns:
            The raw answer.
        """

        async def call() -> str:
            """Sends the request tagged with the ``dialog`` metrics phase."""
            with llm_phase("dialog"):
                return await logged_model_call(
                    self._log_writer,
                    self.provider.complete_json_chat_async,
                    trace_context=trace,
                    system_prompt=system,
                    user_prompt=user,
                    max_tokens=max_tokens,
                    temperature=TRANSLATION_TEMPERATURE,
                )

        return run_async(call())

    def _system_prompt(self, corpus: List[str], speakers: str) -> SystemContent:
        """Builds the system message of a dialog request.

        Args:
            corpus: Texts that select the world entities and glossary terms:
                the script, plus the file stem for single-file requests.
            speakers: ``DIALOG SPEAKERS`` block, or ``""``.

        Returns:
            System message content.
        """
        target_lang = self.config.target_lang
        world = self.world_context.to_prompt_block(
            glossary=self.glossary, target_lang=target_lang, source_texts=corpus
        )
        if speakers:
            world = f"{speakers}\n\n{world}" if world else speakers
        stable, variable = build_dialog_system_prompt_parts(
            target_lang,
            self.config.player_gender,
            world,
            terminology_block(corpus, target_lang, self.glossary),
        )
        return self.provider.make_system_message_content(stable, variable)

    def _accept(
        self,
        dialog: PreparedDialog,
        answer: Dict[str, Any],
        requested: Collection[str],
        *,
        allow_cleanup: bool,
    ) -> Tuple[Translations, Dict[str, _Rejected]]:
        """Restores and validates the answered lines, logging each accepted one.

        Only *requested* keys are read: an answer may also carry the IDs of
        context-only nodes or of lines accepted earlier.

        Args:
            dialog: The prepared dialog.
            answer: Line key -> translated (sanitized) text.
            requested: Keys the request asked for.
            allow_cleanup: Accept a text with broken tokens or tags after
                removing them.

        Returns:
            Accepted translations by occurrence, and the rejected answers by key.
        """
        name = dialog.file_path.name
        wanted = set(requested)
        accepted: Translations = {}
        rejected: Dict[str, _Rejected] = {}
        for key, value in answer.items():
            if key not in wanted:
                continue
            text = "" if value is None else str(value)
            if not text.strip():
                rejected[key] = _Rejected(text, None)
                logger.warning("%s: empty translation rejected for dialog node %s", name, key)
                continue
            outcome = dialog.handlers[key].finalize_translation(text, allow_cleanup=allow_cleanup)
            if not outcome.exact_valid and not outcome.used_cleanup:
                report = outcome.mismatch_report
                rejected[key] = _Rejected(text, report)
                logger.warning(
                    "%s: token/tag mismatch for dialog node %s (%s). expected=%s actual=%s",
                    name,
                    key,
                    report.mismatch_type,
                    report.expected_sequence,
                    report.actual_sequence,
                )
                continue
            if outcome.used_cleanup:
                logger.warning(
                    "%s: accepted cleaned dialog translation for node %s after token/tag "
                    "mismatch cleanup.",
                    name,
                    key,
                )
            address = dialog.address(key)
            accepted[address] = outcome.final_text
            write_trace(
                self._log_writer,
                {
                    "original": dialog.texts[key],
                    "translated": outcome.final_text,
                    "context": f"Dialog node {key} in {name}",
                    "model": self.provider.model,
                    "file": name,
                    "item_id": address[1],
                },
            )
        return accepted, rejected

    def _mark_failed(
        self, dialog: PreparedDialog, keys: List[str], translations: Translations
    ) -> None:
        """Records the lines of *keys* that have no accepted translation.

        Args:
            dialog: The prepared dialog.
            keys: Keys of the lines that were requested.
            translations: The file's accepted translations.
        """
        for key in keys:
            address = dialog.address(key)
            if address not in translations:
                self.failed_items.add(address)
