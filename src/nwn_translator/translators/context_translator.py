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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

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
from ..glossary import Glossary, terminology_block
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
from .model_calls import SingleRequest, send_single
from .token_handler import TokenMismatchReport
from .translation_manager import ItemProgress

logger = logging.getLogger(__name__)

#: Output budget of the recovery requests. It equals ``TRANSLATION_MAX_TOKENS``
#: today, so a step that re-sends the original prompt repeats the first request;
#: it is kept because requests are sampled, and a lower first budget would make
#: the recovery budget higher again.
_RECOVERY_MAX_TOKENS = 32768

#: Result of one pool job: its translations and ``(file, error)`` pairs.
_JobResult = Tuple[Translations, List[Tuple[Path, Exception]]]


#: Recovery requests after an unparseable answer: one per warning (``%s`` is the
#: request label), keyed by whether the answer looks cut off mid-string. The first
#: request re-sends a cut-off prompt with :data:`_RECOVERY_MAX_TOKENS`, or asks to
#: repair other invalid JSON within the first budget; a later one repairs with
#: :data:`_RECOVERY_MAX_TOKENS`. The repair prompt is built once, from the answer
#: before the first repair request.
_Recovery = Dict[bool, Tuple[str, ...]]

_CHUNK_RECOVERY: _Recovery = {
    True: (
        "%s: dialog JSON parse failed with truncation-like invalid JSON; "
        "retrying original prompt with higher max_tokens...",
        "%s: high-token original prompt retry still returned invalid JSON; "
        "retrying repair prompt with higher max_tokens as final fallback...",
    ),
    False: (
        "%s: dialog JSON parse failed with non-truncation invalid JSON; "
        "retrying with repair prompt...",
        "%s: repair prompt still returned invalid JSON; "
        "retrying repair prompt with higher max_tokens as final fallback...",
    ),
}
_GROUP_RECOVERY: _Recovery = {
    True: ("Dialog group %s: JSON looks truncated; retrying with higher max_tokens...",),
    False: ("Dialog group %s: invalid JSON; retrying with repair prompt...",),
}
_PENDING_RECOVERY: _Recovery = {
    True: (
        "%s: pending dialog retry JSON looks truncated; "
        "retrying the same JSON retry prompt with higher max_tokens...",
    ),
    False: (),
}


class _Rejected(NamedTuple):
    """A rejected answer of one line and how it broke the tokens (``None`` if empty)."""

    text: str
    report: Optional[TokenMismatchReport]


@dataclass
class _FileRun:
    """State of one dialog file while its lines are requested.

    Attributes:
        dialog: The prepared dialog.
        progress: Progress sink, if any.
        translations: Lines accepted so far.
        rejected: Latest rejected answer of each line not accepted yet.
        speakers: ``DIALOG SPEAKERS`` block of its system prompts.
        reported: Progress units reported so far.
    """

    dialog: PreparedDialog
    progress: Optional[ItemProgress]
    translations: Translations = field(default_factory=dict)
    rejected: Dict[str, _Rejected] = field(default_factory=dict)
    speakers: str = ""
    reported: int = 0

    def report(self, lines: int) -> None:
        """Reports *lines* more progress, clamped to the file's item budget.

        The pipeline counts a file's extracted items, which need not equal its
        dialog lines; a budget of ``0`` disables the clamp.
        """
        budget = self.dialog.item_budget
        if budget:
            lines = min(lines, budget - self.reported)
        if self.progress is not None and lines > 0:
            self.progress.bump(by=lines, filename=self.dialog.file_path.name)
            self.reported += lines

    def finish(self) -> None:
        """Reports the rest of the item budget, whatever was translated."""
        self.report(self.dialog.item_budget - self.reported)


def _parse_answer(raw: str, label: str) -> Optional[Dict[str, Any]]:
    """Returns the first JSON object of an answer; logs an error when there is none."""
    parsed = json_extract_first_object(raw)
    if parsed is None:
        logger.error(
            "Failed to parse JSON for %s (no valid object). Raw prefix: %s...",
            label,
            (raw or "").strip()[:400],
        )
    return parsed


def _looks_truncated(raw: str) -> bool:
    """Tells whether an unparseable answer stopped mid-string at ``max_tokens``.

    Only an unterminated string counts, and the strict decoder is used, so a
    raw control character earlier in the answer hides the truncation.
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
    """Returns the part of a grouped answer for *file_path*, or ``None``.

    Keys match the file name or its stem, ignoring case and surrounding spaces;
    the first match in answer order wins.
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
        failed_items: Lines sent to the model whose translation was never accepted.
    """

    def __init__(
        self,
        config: TranslationConfig,
        provider: TranslationProvider,
        world_context: WorldContext,
        glossary: Optional[Glossary] = None,
        log_writer: Optional[TranslationLogWriter] = None,
    ) -> None:
        """Creates a manager for one run.

        Args:
            config: Run configuration.
            provider: Model provider.
            world_context: Scanned module objects.
            glossary: Canonical translations of proper names, if built.
            log_writer: Log writer of the run; by default the one *config* names.
        """
        self.config = config
        self.provider = provider
        self.world_context = world_context
        self.glossary = glossary
        self._log_writer = log_writer or translation_log_writer_for_config(
            config.translation_log, config.translation_log_writer
        )
        self.failed_items: Set[Occurrence] = set()

    def translate_dialogs(
        self,
        dialog_files: Sequence[Tuple[Path, Dict[str, Any], int]],
        item_progress: Optional[ItemProgress] = None,
    ) -> _JobResult:
        """Translates dialog files on a pool of ``max_concurrent_requests`` threads.

        Small dialogs share grouped requests; a file whose part of a grouped
        answer is missing, incomplete or broken falls back to its own requests.
        A failed request is logged and its lines end up in :attr:`failed_items`.
        Each prepared file reports its whole item budget of progress, unless an
        exception escapes its job.

        Args:
            dialog_files: ``(file_path, parsed_data, item_budget)`` per file, in
                pipeline order; *item_budget* is the file's extracted item count.
            item_progress: Progress sink, if any.

        Returns:
            All accepted translations, and ``(file_path, error)`` for a file whose
            preparation or fallback raised, for every file of a group that hit a
            rate or budget limit, and for every file of a job an exception escaped.

        Raises:
            TranslationCancelled: If the run is cancelled; queued files are dropped.
        """
        errors: List[Tuple[Path, Exception]] = []
        dialogs: List[PreparedDialog] = []
        for file_path, parsed_data, item_budget in dialog_files:
            self.config.raise_if_cancelled()
            try:
                dialog = prepare_dialog(
                    file_path, parsed_data, item_budget, preserve_tokens=self.config.preserve_tokens
                )
            except Exception as exc:
                errors.append((file_path, exc))
                continue
            if dialog is not None and dialog.texts:
                dialogs.append(dialog)
            elif item_progress is not None and item_budget > 0:
                item_progress.bump(by=item_budget, filename=file_path.name)

        singles, groups = plan_requests(dialogs, self.config.target_lang, self.glossary)
        if groups:
            logger.info(
                "Dialog grouping: %d small file(s) packed into %d group request(s); "
                "%d file(s) translated individually.",
                sum(len(group) for group in groups),
                len(groups),
                len(singles),
            )
        jobs = [[dialog] for dialog in singles] + groups
        translations, job_errors = self._run_pool(jobs, item_progress)
        return translations, errors + job_errors

    def _run_pool(
        self, jobs: List[List[PreparedDialog]], progress: Optional[ItemProgress]
    ) -> _JobResult:
        """Translates *jobs* in queue order on up to ``max_concurrent_requests`` threads.

        A job is one dialog on its own or a group of two or more small ones.
        ``run_async`` keeps one event loop per thread, and the provider one HTTP
        client per loop, so a worker reuses them for all its jobs and closes them
        before it ends. An exception escaping a job fails all of its files; a
        cancelled job stops the workers from starting queued ones.

        Raises:
            TranslationCancelled: If a job was cancelled.
        """
        queue = deque(enumerate(jobs))
        outcomes: List[Any] = [None] * len(jobs)
        cancelled = threading.Event()

        def work() -> None:
            """Runs queued jobs until none is left or one was cancelled."""
            try:
                while not cancelled.is_set():
                    try:
                        index, files = queue.popleft()
                    except IndexError:
                        return
                    try:
                        if len(files) > 1:
                            outcomes[index] = self._translate_group(files, progress)
                        else:
                            outcomes[index] = self._translate_file(_FileRun(files[0], progress)), []
                    except BaseException as exc:
                        outcomes[index] = exc
                        if isinstance(exc, TranslationCancelled):
                            cancelled.set()
            finally:
                close_thread_resources(self.provider)

        workers = [
            threading.Thread(target=work, name=f"dialog-{index}")
            for index in range(min(max(1, self.config.max_concurrent_requests), len(jobs)))
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        translations: Translations = {}
        errors: List[Tuple[Path, Exception]] = []
        for files, outcome in zip(jobs, outcomes):
            if isinstance(outcome, tuple):
                translations.update(outcome[0])
                errors.extend(outcome[1])
            elif isinstance(outcome, Exception) and not isinstance(outcome, TranslationCancelled):
                errors.extend((dialog.file_path, outcome) for dialog in files)
            elif outcome is not None:
                raise outcome
        return translations, errors

    def _translate_file(self, run: _FileRun) -> Translations:
        """Requests the lines of a file not accepted yet, then reports its whole budget.

        Errors are logged, not raised: the lines not accepted by then are added
        to :attr:`failed_items`.

        Returns:
            The file's accepted translations.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        self.config.raise_if_cancelled()
        dialog = run.dialog
        keys = [key for key in dialog.keys if dialog.address(key) not in run.translations]
        run.speakers = speakers_block(
            speaker_lines(self.world_context, dialog.file_path.stem, dialog.node_map)
        )
        try:
            self._request_lines(run, keys)
        except TranslationCancelled:
            raise
        except Exception as exc:
            logger.error("Contextual translation failed for %s: %s", dialog.file_path.name, exc)
        self.failed_items.update(
            address for address in map(dialog.address, keys) if address not in run.translations
        )
        run.finish()
        return run.translations

    def _request_lines(self, run: _FileRun, keys: List[str]) -> None:
        """Sends the chunks of *keys*, then retries what is still missing or broken.

        Raises:
            RateLimitError: If a chunk or the pending retry hits a rate or budget limit.
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        chunks = plan_chunks(dialog, keys, self.config.target_lang, self.glossary)
        logger.info(
            "Sending %d/%d dialog lines to AI for %s%s...",
            len(keys),
            len(dialog.texts),
            dialog.file_path.name,
            f" in {len(chunks)} chunk(s)" if len(chunks) > 1 else "",
        )
        pending: Set[str] = set()
        for index, chunk in enumerate(chunks, 1):
            self.config.raise_if_cancelled()
            pending.update(self._translate_chunk(run, chunk, index, len(chunks)))
        if not pending:
            return
        self.config.raise_if_cancelled()
        # Sorted as strings (E10 before E2): the retry prompt lists them so.
        still_pending = self._retry_pending(run, sorted(pending))
        if still_pending:
            self._retry_lines(run, still_pending)

    def _translate_chunk(self, run: _FileRun, chunk: Chunk, index: int, total: int) -> List[str]:
        """Requests one chunk and accepts its valid lines.

        A failing request costs only this chunk: its lines are reported as
        failed, while the other chunks are still requested. A rate or budget
        limit stops the file instead, as every further request would meet it.

        Returns:
            Keys to retry: those missing or rejected; all of them when the answer
            never parsed, none when the request failed.

        Raises:
            RateLimitError: If the provider reports a rate or budget limit.
        """
        name = run.dialog.file_path.name
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
                self._system_prompt([chunk.script, run.dialog.file_path.stem], run.speakers),
                dialog_user_prompt(name, chunk.script),
                lambda raw: repair_prompt(name, chunk.script, chunk.keys, raw),
                trace={"file": name},
                label=name,
            )
        except RateLimitError:
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
        return self._accept(run, answer, chunk.keys)

    def _retry_pending(self, run: _FileRun, pending: List[str]) -> List[str]:
        """Requests the pending lines again in one token-preserving request.

        A failing request counts as an unusable answer, so the lines still get
        their single-line retries; a rate or budget limit is raised instead.

        Returns:
            Keys still pending afterwards, sorted.

        Raises:
            RateLimitError: If the provider reports a rate or budget limit.
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
        try:
            answer = self._request_json(
                _PENDING_RECOVERY,
                self._system_prompt([script, dialog.file_path.stem], run.speakers),
                prompt,
                None,
                trace={"file": name},
                label=name,
            )
        except RateLimitError:
            raise
        except Exception as exc:
            logger.error("%s: pending dialog retry request failed: %s", name, exc)
            return pending
        if not answer:
            return pending
        still_pending = self._accept(run, answer, pending)
        logger.info(
            "%s: retry recovered %d additional dialog translations.",
            name,
            len(pending) - len(still_pending),
        )
        return still_pending

    def _retry_lines(self, run: _FileRun, keys: List[str]) -> None:
        """Requests lines one by one; a line that still fails is accepted cleaned if valid.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        logger.warning(
            "%s: %d dialog nodes still invalid after JSON retry; retrying individually.",
            name,
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
            previous = run.rejected.get(key)
            context = line_retry_context(
                key,
                name,
                speaker_label(dialog.node_map[key]),
                dialog.handlers[key].get_expected_artifact_sequence(),
                previous.report if previous else None,
            )
            request = SingleRequest(context, glossary_block, None)
            try:
                result = run_async(
                    send_single(
                        self._log_writer,
                        self.provider,
                        self.config,
                        dialog.address(key),
                        dialog.sanitized[key],
                        request,
                    )
                )
            except Exception as exc:
                logger.warning("%s: individual dialog retry failed for %s: %s", name, key, exc)
            else:
                if result.success and not self._accept(run, {key: result.translated}, [key]):
                    continue
            candidate = run.rejected.get(key)
            self._accept(run, {key: candidate.text if candidate else ""}, [key], allow_cleanup=True)

    def _translate_group(
        self, group: List[PreparedDialog], progress: Optional[ItemProgress]
    ) -> _JobResult:
        """Translates small dialogs in one request and splits the answer.

        A file whose part is missing, incomplete or has a rejected line falls
        back to its own requests, keeping the lines accepted from the group.
        After a rate or budget limit the files are reported as errors instead:
        the request already used up the provider's retries, and one request per
        file would multiply the pressure on the limit.

        Returns:
            The group's translations, and ``(file_path, error)`` for each file
            that failed on a rate or budget limit or whose fallback raised.

        Raises:
            TranslationCancelled: If the run is cancelled.
        """
        self.config.raise_if_cancelled()
        label = f"{group[0].file_path.name}+{len(group) - 1}"
        translations: Translations = {}
        errors: List[Tuple[Path, Exception]] = []
        answer: Optional[Dict[str, Any]] = None
        try:
            answer = self._request_group(group, label)
        except RateLimitError as exc:
            logger.warning(
                "Dialog group %s: rate/budget limit (%s); not falling back to single files.",
                label,
                exc,
            )
            for dialog in group:
                errors.append((dialog.file_path, exc))
                self.failed_items.update(map(dialog.address, dialog.keys))
                _FileRun(dialog, progress).finish()
            return translations, errors
        except Exception as exc:
            logger.warning(
                "Dialog group %s: request failed (%s); falling back to single files.", label, exc
            )

        for dialog in group:
            self.config.raise_if_cancelled()
            run = _FileRun(dialog, progress)
            part = _group_part(answer, dialog.file_path) if answer is not None else None
            missing = (
                self._accept(run, part, dialog.keys, report=False)
                if isinstance(part, dict)
                else dialog.keys
            )
            translations.update(run.translations)
            if not missing:
                run.finish()
                continue
            logger.warning(
                "Dialog group %s: falling back to single-file translation for %s.",
                label,
                dialog.file_path.name,
            )
            # The file's own requests start without the group's rejected answers.
            run.rejected.clear()
            run.report(len(run.translations))
            try:
                translations.update(self._translate_file(run))
            except TranslationCancelled:
                raise
            except Exception as exc:
                errors.append((dialog.file_path, exc))
        return translations, errors

    def _request_group(self, group: List[PreparedDialog], label: str) -> Optional[Dict[str, Any]]:
        """Sends one grouped request; returns its answer (file -> line key -> text) or ``None``."""
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
        logger.info(
            "Sending dialog group %s (%d files, %d chars)...", label, len(group), len(combined)
        )
        return self._request_json(
            _GROUP_RECOVERY,
            self._system_prompt([combined], speakers_block(lines)),
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
            recovery: Warnings of the recovery requests (see :data:`_Recovery`).
            system: System message content.
            user: User prompt.
            repair: Builds the repair prompt from an answer; required when
                *recovery* has repair requests.
            trace: Context of the requests in the translation log.
            label: Name of the request in log messages.

        Returns:
            The first answer that parses, or ``None``.
        """
        raw = self._call_json(system, user, TRANSLATION_MAX_TOKENS, trace)
        parsed = _parse_answer(raw, label)
        truncated = parsed is None and _looks_truncated(raw)
        repaired: Optional[str] = None
        for index, warning in enumerate(recovery[truncated] if parsed is None else ()):
            logger.warning(warning, label)
            prompt = user
            if index or not truncated:
                assert repair is not None
                if repaired is None:
                    repaired = repair(raw)
                prompt = repaired
            budget = _RECOVERY_MAX_TOKENS if index or truncated else TRANSLATION_MAX_TOKENS
            raw = self._call_json(system, prompt, budget, trace)
            parsed = _parse_answer(raw, label)
            if parsed is not None:
                break
        return parsed

    def _call_json(
        self, system: SystemContent, user: str, max_tokens: int, trace: Dict[str, Any]
    ) -> str:
        """Sends one JSON chat request (metrics phase ``dialog``) and returns the reply."""
        with llm_phase("dialog"):
            return run_async(
                logged_model_call(
                    self._log_writer,
                    self.provider.complete_json_chat_async,
                    trace_context=trace,
                    system_prompt=system,
                    user_prompt=user,
                    max_tokens=max_tokens,
                    temperature=TRANSLATION_TEMPERATURE,
                )
            )

    def _system_prompt(self, corpus: List[str], speakers: str) -> SystemContent:
        """Builds the system message of a dialog request.

        Args:
            corpus: Texts that select the world entities and glossary terms: the
                script, plus the file stem for single-file requests.
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
        run: _FileRun,
        answer: Dict[str, Any],
        keys: List[str],
        *,
        allow_cleanup: bool = False,
        report: bool = True,
    ) -> List[str]:
        """Restores and validates the answers for *keys*, logging each accepted line.

        Accepted lines go into *run*; a rejected answer becomes the line's latest
        rejection. Other keys of *answer* (context-only nodes, lines accepted
        earlier) are ignored.

        Args:
            run: The file's state.
            answer: Line key -> translated (sanitized) text.
            keys: Keys the request asked for.
            allow_cleanup: Accept a text with broken tokens or tags after removing them.
            report: Report the accepted lines as progress.

        Returns:
            The keys of *keys* still not accepted, in order.
        """
        dialog = run.dialog
        name = dialog.file_path.name
        wanted = set(keys)
        accepted = 0
        for key, value in answer.items():
            if key not in wanted:
                continue
            text = "" if value is None else str(value)
            if not text.strip():
                run.rejected[key] = _Rejected(text, None)
                logger.warning("%s: empty translation rejected for dialog node %s", name, key)
                continue
            outcome = dialog.handlers[key].finalize_translation(text, allow_cleanup=allow_cleanup)
            if not outcome.exact_valid and not outcome.used_cleanup:
                mismatch = outcome.mismatch_report
                run.rejected[key] = _Rejected(text, mismatch)
                logger.warning(
                    "%s: token/tag mismatch for dialog node %s (%s). expected=%s actual=%s",
                    name,
                    key,
                    mismatch.mismatch_type,
                    mismatch.expected_sequence,
                    mismatch.actual_sequence,
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
            run.translations[address] = outcome.final_text
            run.rejected.pop(key, None)
            accepted += 1
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
        if report:
            run.report(accepted)
        return [key for key in keys if dialog.address(key) not in run.translations]
