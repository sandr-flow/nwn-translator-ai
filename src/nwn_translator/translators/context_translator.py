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
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
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
from ..async_utils import run_async
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
from ..translation_logging import logged_model_call, translation_log_writer_for_config, write_trace
from .dialog_plan import Chunk, PreparedDialog, plan_chunks, plan_requests, prepare_dialog
from .token_handler import TokenMismatchReport

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

#: Output budget of the recovery requests. It equals ``TRANSLATION_MAX_TOKENS``,
#: so a step that re-sends the prompt repeats the first request unchanged; the
#: step stays because it is one of the run's requests.
_RECOVERY_MAX_TOKENS = 32768

#: Result of one pool job: its translations and ``(file, error)`` pairs.
_JobResult = Tuple[Translations, List[Tuple[Path, Exception]]]


class ProgressSink(Protocol):
    """Counter of translated items (the pipeline's progress reporter)."""

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        """Count *by* more items of *filename* as done."""


class _Step(NamedTuple):
    """One recovery request after an unparseable answer.

    Attributes:
        repair: Send the repair prompt instead of the original one. The repair
            prompt is built once, from the answer before the first repair step.
        max_tokens: Output budget of the request.
        warning: Message logged before the request; ``%s`` is the request label.
    """

    repair: bool
    max_tokens: int
    warning: str


#: Recovery steps, keyed by whether the first answer looks cut off mid-string.
_Recovery = Dict[bool, Tuple[_Step, ...]]

_CHUNK_RECOVERY: _Recovery = {
    True: (
        _Step(
            False,
            _RECOVERY_MAX_TOKENS,
            "%s: dialog JSON parse failed with truncation-like invalid JSON; "
            "retrying original prompt with higher max_tokens...",
        ),
        _Step(
            True,
            _RECOVERY_MAX_TOKENS,
            "%s: high-token original prompt retry still returned invalid JSON; "
            "retrying repair prompt with higher max_tokens as final fallback...",
        ),
    ),
    False: (
        _Step(
            True,
            TRANSLATION_MAX_TOKENS,
            "%s: dialog JSON parse failed with non-truncation invalid JSON; "
            "retrying with repair prompt...",
        ),
        _Step(
            True,
            _RECOVERY_MAX_TOKENS,
            "%s: repair prompt still returned invalid JSON; "
            "retrying repair prompt with higher max_tokens as final fallback...",
        ),
    ),
}
_GROUP_RECOVERY: _Recovery = {
    True: (
        _Step(
            False,
            _RECOVERY_MAX_TOKENS,
            "Dialog group %s: JSON looks truncated; retrying with higher max_tokens...",
        ),
    ),
    False: (
        _Step(
            True,
            TRANSLATION_MAX_TOKENS,
            "Dialog group %s: invalid JSON; retrying with repair prompt...",
        ),
    ),
}
_PENDING_RECOVERY: _Recovery = {
    True: (
        _Step(
            False,
            _RECOVERY_MAX_TOKENS,
            "%s: pending dialog retry JSON looks truncated; "
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
    """Progress of one dialog file, clamped to the file's item budget."""

    def __init__(self, sink: Optional[ProgressSink], budget: int, filename: str) -> None:
        self._sink = sink
        self._budget = budget
        self._filename = filename
        self._done = 0

    def bump(self, by: int) -> None:
        """Report *by* more lines; without a budget nothing is clamped."""
        if self._sink is None or by <= 0:
            return
        delta = min(by, max(0, self._budget - self._done)) if self._budget else by
        if delta <= 0:
            return
        self._sink.bump(by=delta, filename=self._filename)
        self._done += delta

    def finish(self) -> None:
        """Report the rest of the budget, whatever was translated."""
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
        """Add accepted lines and report their progress."""
        self.translations.update(accepted)
        self.progress.bump(len(accepted))


def _parse_answer(raw: str, label: str) -> Optional[Dict[str, Any]]:
    """Return the first JSON object of an answer, logging an error when there is none."""
    parsed = json_extract_first_object(raw)
    if parsed is None:
        logger.error(
            "Failed to parse JSON for %s (no valid object). Raw prefix: %s...",
            label,
            (raw or "").strip()[:400],
        )
    return parsed


def _looks_truncated(raw: str) -> bool:
    """Guess whether an unparseable answer stopped mid-string at ``max_tokens``.

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
    """Return the part of a grouped answer for *file_path*.

    Keys match the file name or its stem, ignoring case and surrounding
    spaces; the first match in answer order wins.
    """
    targets = {file_path.name.casefold(), file_path.stem.casefold()}
    for key, value in answer.items():
        if str(key).strip().casefold() in targets:
            return value
    return None


class ContextualTranslationManager:
    """Translate dialog files with their conversation tree as context.

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
    ) -> None:
        """Create a manager for one run.

        Args:
            config: Run configuration.
            provider: Model provider.
            world_context: Scanned module objects.
            glossary: Canonical translations of proper names, if built.
        """
        self.config = config
        self.provider = provider
        self.world_context = world_context
        self.glossary = glossary
        self._log_writer = translation_log_writer_for_config(
            config.translation_log,
            config.translation_log_writer,
        )
        self.failed_items: Set[Occurrence] = set()

    def translate_dialog(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        item_progress: Optional[ProgressSink] = None,
        item_budget: Optional[int] = None,
        *,
        accepted: Optional[Translations] = None,
    ) -> Translations:
        """Translate one dialog file.

        Errors of the requests are logged, not raised: the lines not accepted
        by then are added to :attr:`failed_items`.

        Args:
            file_path: Path of the ``.dlg`` resource.
            parsed_data: Parsed GFF root struct.
            item_progress: Progress sink, if any.
            item_budget: Progress units of the file; exactly this many are
                reported in total. Without it progress is not clamped.
            accepted: Translations accepted earlier; lines of this file found
                there are kept and not requested again.

        Returns:
            The file's accepted translations, including those from *accepted*.

        Raises:
            TranslationCancelled: When the run is cancelled.
        """
        budget = item_budget or 0
        dialog = prepare_dialog(
            file_path, parsed_data, budget, preserve_tokens=self.config.preserve_tokens
        )
        if dialog is None:
            _FileProgress(item_progress, budget, file_path.name).finish()
            return {}
        return self._translate_file(dialog, item_progress, accepted)

    def translate_dialogs(
        self,
        dialog_files: Sequence[Tuple[Path, Dict[str, Any], int]],
        item_progress: Optional[ProgressSink] = None,
    ) -> Tuple[Translations, List[Tuple[Path, Exception]]]:
        """Translate dialog files on a pool of ``max_concurrent_requests`` threads.

        Small dialogs share grouped requests. A file whose part of a grouped
        answer is missing, incomplete or has broken tokens falls back to its
        own requests for the remaining lines. Each file reports its item
        budget of progress in total.

        Args:
            dialog_files: ``(file_path, parsed_data, item_budget)`` per file, in
                pipeline order; *item_budget* is the file's extracted item count.
            item_progress: Progress sink, if any.

        Returns:
            All accepted translations, and ``(file_path, error)`` for every file
            whose preparation or grouped request failed.

        Raises:
            TranslationCancelled: When the run is cancelled; queued files are
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

        max_workers = max(1, self.config.max_concurrent_requests)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: Dict[Future[_JobResult], List[PreparedDialog]] = {}
            for dialog in singles:
                futures[executor.submit(self._translate_single, dialog, item_progress)] = [dialog]
            for group in groups:
                futures[executor.submit(self._translate_group, group, item_progress)] = group
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
        return translations, errors

    def _translate_single(
        self, dialog: PreparedDialog, item_progress: Optional[ProgressSink]
    ) -> _JobResult:
        """Pool job: translate one dialog on its own."""
        self.config.raise_if_cancelled()
        return self._translate_file(dialog, item_progress), []

    def _translate_file(
        self,
        dialog: PreparedDialog,
        item_progress: Optional[ProgressSink],
        accepted: Optional[Translations] = None,
    ) -> Translations:
        """Request the lines of one dialog that *accepted* does not cover yet.

        See :meth:`translate_dialog` for the arguments and the result.
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
        """Send the chunks of *keys*, then retry what is still missing or broken."""
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
        """Request one chunk and accept its valid lines.

        Returns:
            Keys of the chunk that are missing from the answer or were
            rejected; all of them when the answer never parsed.
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
        answer = self._request_json(
            _CHUNK_RECOVERY,
            self._system_prompt([chunk.script, dialog.file_path.stem], run.speakers),
            dialog_user_prompt(name, chunk.script),
            lambda raw: repair_prompt(name, chunk.script, chunk.keys, raw),
            trace={"file": name},
            label=name,
        )
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
        """Request the pending lines again in one token-preserving request.

        Args:
            run: The file's state.
            pending: Keys still missing or rejected, sorted.

        Returns:
            Keys still pending afterwards, sorted.
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
        answer = self._request_json(
            _PENDING_RECOVERY,
            self._system_prompt([script, dialog.file_path.stem], run.speakers),
            prompt,
            None,
            trace={"file": name},
            label=name,
        )
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
        """Retry lines one by one; accept a cleaned answer for lines that still fail."""
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
        """Request one line alone through ``translate_async``.

        Returns:
            Whether the answer was accepted. A rejected answer replaces the
            line's candidate for cleanup.
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
        """Pool job: translate small dialogs in one request, then split the answer.

        A file whose part of the answer is missing, incomplete or has a
        rejected line falls back to its own requests; the lines accepted from
        the group are kept. After a rate or budget limit error the files are
        not re-requested: they are reported as errors.
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
        """Send one grouped request.

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
        """Send a JSON request and recover from an unparseable answer.

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
            raw = self._call_json(system, prompt, step.max_tokens, trace)
            parsed = _parse_answer(raw, label)
            if parsed is not None:
                break
        return parsed

    def _call_json(
        self, system: SystemContent, user: str, max_tokens: int, trace: Dict[str, Any]
    ) -> str:
        """Send one JSON chat request (metrics phase ``dialog``) and return the reply."""

        async def call() -> str:
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
        """Build the system message of a dialog request.

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
        """Restore and validate the answered lines; log each accepted one.

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
        """Record the lines of *keys* that have no accepted translation."""
        for key in keys:
            address = dialog.address(key)
            if address not in translations:
                self.failed_items.add(address)
