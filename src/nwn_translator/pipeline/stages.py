"""Stages of a translation run, each operating on one explicit :class:`PipelineState`.

:func:`run_pipeline` runs them in order: unpack the archive, scan the world, extract
the strings, collect entity candidates, build the glossary, translate, inject and
repack. Every stage can also run on its own from saved artifacts (see
:mod:`nwn_translator.pipeline.artifacts` and ``scripts/stage.py``).
"""

import logging
import shutil
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from ..ai_providers import TranslationProvider, create_provider
from ..async_utils import close_thread_resources
from ..config import (
    TranslationConfig,
    create_output_path,
    module_string_encoding_for_target_lang,
    source_string_encoding,
)
from ..context.dialog_speakers import dialog_line_speaker
from ..context.entity_candidates import EntityCandidateRegistry
from ..context.entity_extractor import EntityExtractor
from ..context.world_context import WorldContext, WorldScanner
from ..extractors.base import ExtractedContent, Occurrence, TranslatableItem, Translations
from ..formats.erf import ERFReader, create_mod_from_directory
from ..glossary import Glossary
from ..glossary_builder import GlossaryBuilder
from ..glossary_curator import GlossaryCurator
from ..injectors.base import InjectedContent
from ..resources import RESOURCE_KINDS, TRANSLATABLE_TYPES
from ..telemetry import RunMetricsRecorder
from ..translation_logging import (
    FileTranslationLogWriter,
    NullTranslationLogWriter,
    TranslationLogWriter,
    write_trace,
)
from ..translators.context_translator import ContextualTranslationManager
from ..translators.ncs_diagnostics import NCS_COUNTERS, add_sample, new_ncs_diagnostics
from ..translators.translation_manager import TranslationManager

logger = logging.getLogger(__name__)

#: ``file_path -> (parsed_data, ExtractedContent, file_ext)``, in file order.
ExtractedMap = Dict[Path, Tuple[Dict[str, Any], ExtractedContent, str]]

_Result = TypeVar("_Result")


def _new_run_stats() -> Dict[str, Any]:
    """Return empty run statistics, in the key order of the stats dict."""
    return {
        "files_processed": 0,
        "items_translated": 0,
        "errors": [],
        "ncs_diagnostics": new_ncs_diagnostics(),
    }


def find_translatable_files(directory: Path) -> List[Path]:
    """Return the files under *directory* whose kind can be translated.

    Args:
        directory: Unpacked module.

    Returns:
        Translatable files in ``rglob`` order.
    """
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in TRANSLATABLE_TYPES
    ]


def load_parsed_and_extracted(
    file_path: Path,
    file_ext: str,
    gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    source_encoding: Optional[str] = None,
) -> Optional[Tuple[Dict[str, Any], ExtractedContent]]:
    """Load *file_path* and extract its translatable items.

    Args:
        file_path: Resource file.
        file_ext: Extension selecting the resource kind (any case).
        gff_cache: Parse cache shared by the run, if any.
        source_encoding: Code page of the strings (None to detect).

    Returns:
        ``(parsed data, extracted content)``, or None when the kind is not
        translatable, the file cannot be loaded or it has nothing to translate.
    """
    kind = RESOURCE_KINDS.get(file_ext.lower())
    if kind is None:
        logger.debug("No extractor for %s: %s", file_ext, file_path.name)
        return None
    parsed_data = kind.load(file_path, gff_cache, source_encoding)
    if parsed_data is None:
        return None
    extracted = kind.extractor.extract(file_path, parsed_data)
    if not extracted.items:
        logger.debug("No translatable content in: %s", file_path.name)
        return None
    return parsed_data, extracted


def inject_translations_into_file(
    file_path: Path,
    parsed_data: Dict[str, Any],
    extracted: ExtractedContent,
    translations: Translations,
    *,
    log_updates: bool = False,
    target_lang: Optional[str] = None,
    source_encoding: Optional[str] = None,
) -> Optional[InjectedContent]:
    """Write the translations of *extracted* into *file_path* (injection and rebuild).

    Args:
        file_path: Resource file to patch in place.
        parsed_data: Loaded resource; unused, injectors patch the file by the
            offsets recorded in *extracted*.
        extracted: Items extracted from the file.
        translations: Translated text by occurrence.
        log_updates: Log the number of patched items.
        target_lang: Target language; selects the code page of written text.
        source_encoding: Decode used when *extracted* was produced. Script
            injection re-reads the file and compares against the originals.

    Returns:
        The injection result, or None when the file kind is not translatable.
    """
    kind = RESOURCE_KINDS.get(file_path.suffix.lower())
    if kind is None:
        return None
    result = kind.inject(
        file_path,
        extracted.items,
        translations,
        content_type=extracted.content_type,
        text_encoding=module_string_encoding_for_target_lang(target_lang),
        source_encoding=source_encoding,
    )
    if log_updates and result.modified:
        logger.info("Updated %s: %s items", file_path.name, result.items_updated)
    return result


@dataclass
class PipelineState:
    """Everything the stages of one run read and write.

    The statistics are only changed from the thread that runs the stages.

    Attributes:
        config: Run settings.
        provider: Model provider.
        metrics_recorder: Request metrics of the run.
        temp_dir: Temporary directory holding :attr:`extract_dir`; None when the
            directory is kept after the run (``skip_cleanup``) or not created.
        extract_dir: Unpacked module.
        world_context: Scanned module objects (context mode).
        glossary: Proper-name glossary (context mode).
        gff_cache: Parsed GFF resources by path, shared by the stages.
        stats: Run statistics; see :meth:`get_statistics`.
        trace: Translation log of the run.
    """

    config: TranslationConfig
    provider: TranslationProvider
    metrics_recorder: RunMetricsRecorder = field(default_factory=RunMetricsRecorder)
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    extract_dir: Optional[Path] = None
    world_context: Optional[WorldContext] = None
    glossary: Optional[Glossary] = None
    gff_cache: Dict[Path, Dict[str, Any]] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=_new_run_stats)
    trace: TranslationLogWriter = field(init=False)
    _log_file: Optional[FileTranslationLogWriter] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Choose the translation log writer the stages share.

        An injected writer (``config.translation_log_writer``) belongs to the
        caller; the file named by ``config.translation_log`` is opened by the
        run and closed by :func:`run_pipeline`.
        """
        if self.config.translation_log_writer is not None:
            self.trace = self.config.translation_log_writer
        elif self.config.translation_log is not None:
            self.trace = self._log_file = FileTranslationLogWriter(self.config.translation_log)
        else:
            self.trace = NullTranslationLogWriter()

    @classmethod
    def create(cls, config: TranslationConfig) -> "PipelineState":
        """Build the state of a run with the provider that matches its API key.

        Args:
            config: Run settings.

        Returns:
            A state whose provider reports to the state's metrics recorder.
        """
        recorder = RunMetricsRecorder()
        provider = create_provider(
            config.api_key,
            config.model,
            player_gender=config.player_gender,
            reasoning_effort=config.reasoning_effort,
            metrics_recorder=recorder,
        )
        return cls(config=config, provider=provider, metrics_recorder=recorder)

    @property
    def source_encoding(self) -> Optional[str]:
        """Code page of the module's strings; None lets the readers detect it."""
        return source_string_encoding(self.config.source_lang)

    def progress(self, phase: str, current: int, total: int, message: str) -> None:
        """Report progress to the run's callback, if there is one.

        Args:
            phase: Progress phase (the web maps it to a task status).
            current: Finished units.
            total: Units of the phase.
            message: File name or step description.
        """
        if self.config.progress_callback is not None:
            self.config.progress_callback(phase, current, total, message)

    def add_error(self, message: str) -> None:
        """Record one error of the run and log it.

        Args:
            message: Error description.
        """
        self.stats["errors"].append(message)
        logger.error(message)

    def merge_manager_stats(self, manager_stats: Dict[str, Any]) -> None:
        """Add the statistics of a finished translation manager to the run.

        Every counter is added in full, so each manager is merged exactly once.

        Args:
            manager_stats: ``TranslationManager.stats`` after its last request.
        """
        self.stats["items_translated"] += manager_stats["items_translated"]
        self.stats["errors"].extend(manager_stats["errors"])
        run_ncs = self.stats["ncs_diagnostics"]
        manager_ncs = manager_stats["ncs_diagnostics"]
        for name in NCS_COUNTERS:
            run_ncs[name] += manager_ncs[name]
        for sample in manager_ncs["samples"]:
            add_sample(run_ncs, sample)

    def record_ncs_patch_failure(self, file_path: Path, error: str) -> None:
        """Count a script whose translations could not be patched in, and log it.

        Args:
            file_path: The script.
            error: Why the patch failed.
        """
        sample = {"file": file_path.name, "reason": "patch_failed", "error": error}
        add_sample(self.stats["ncs_diagnostics"], sample, "patch_failed")
        write_trace(self.trace, {"event": "ncs_diagnostic", **sample})

    def output_path(self) -> Path:
        """Return the translated module's path: the configured one or one next to the input."""
        if self.config.output_file is not None:
            return self.config.output_file
        return create_output_path(self.config.input_file, self.config.target_lang)

    def write_metrics(self, output_path: Path) -> None:
        """Store the metrics summary in :attr:`stats` and write the metrics file.

        The file is ``config.metrics_output``, or *output_path* with a
        ``.metrics.json`` suffix appended. A failed write is only logged.

        Args:
            output_path: The translated module.
        """
        self.stats["metrics"] = self.metrics_recorder.summary()
        metrics_path = self.config.metrics_output
        if metrics_path is None:
            metrics_path = output_path.with_suffix(output_path.suffix + ".metrics.json")
        try:
            self.metrics_recorder.write_json(metrics_path)
        except Exception as exc:
            logger.debug("Failed to write run metrics: %s", exc)

    def get_statistics(self) -> Dict[str, Any]:
        """Return the run statistics.

        Returns:
            ``files_processed`` (files injected without an error),
            ``items_translated`` (accepted non-dialog requests), ``errors``,
            ``ncs_diagnostics``, ``metrics`` (the current request summary) and
            ``total_errors``, in this order.
        """
        return {
            **self.stats,
            "metrics": self.metrics_recorder.summary(),
            "total_errors": len(self.stats["errors"]),
        }


class _ItemProgress:
    """Turns the per-item bumps of the translation managers into progress events.

    Dialog files are translated on a thread pool, so the counter is locked.
    The total is an estimate; the count never goes past it.
    """

    def __init__(self, state: PipelineState, total: int) -> None:
        """Count up to *total* items of the run *state*.

        Args:
            state: Run whose progress callback is called.
            total: Items expected (at least one is assumed).
        """
        self._state = state
        self.total = max(1, total)
        self.done = 0
        self._lock = threading.Lock()

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        """Count *by* finished items and report a ``translating_item`` event.

        Args:
            by: Items finished; nothing is reported for zero or less.
            filename: Resource the items belong to.
        """
        if by <= 0:
            return
        with self._lock:
            self.done = min(self.total, self.done + by)
            done = self.done
        self._state.progress("translating_item", done, self.total, filename or "")


def _run_pool(
    state: PipelineState,
    work: Callable[[Path], _Result],
    paths: List[Path],
    phase: str,
    *,
    cancellable: bool,
) -> List[Tuple[Path, Optional[_Result], Optional[Exception]]]:
    """Run *work* on every path on a thread pool; return the outcomes in input order.

    Progress is reported as files finish, but the outcomes are handed back in the
    order of *paths*, so what the caller does with them never depends on thread
    timing.

    Args:
        state: Run state (worker count, progress callback, cancellation).
        work: Called with one path per task.
        paths: Files to process.
        phase: Progress phase reported once per finished file.
        cancellable: Check for cancellation after every finished file.

    Returns:
        ``(path, result, error)`` per path in input order; *error* is the
        exception *work* raised, and *result* is then None.

    Raises:
        TranslationCancelled: If *cancellable* and the run is cancelled; queued
            files are dropped.
    """
    outcomes: List[Tuple[Optional[_Result], Optional[Exception]]] = [(None, None)] * len(paths)
    with ThreadPoolExecutor(max_workers=max(1, state.config.max_concurrent_requests)) as pool:
        index = {pool.submit(work, path): i for i, path in enumerate(paths)}
        try:
            for done, future in enumerate(as_completed(index), 1):
                i = index[future]
                state.progress(phase, done, len(paths), paths[i].name)
                if cancellable:
                    state.config.raise_if_cancelled()
                error = future.exception()
                if error is None:
                    outcomes[i] = (future.result(), None)
                elif isinstance(error, Exception):
                    outcomes[i] = (None, error)
                else:
                    raise error
        except BaseException:
            # Otherwise the executor's exit would first run every queued file.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return [(path, result, error) for path, (result, error) in zip(paths, outcomes)]


def _unpack(state: PipelineState) -> Path:
    """Unpack the input archive into a new directory under ``config.temp_dir``.

    The system temporary directory is used when ``config.temp_dir`` does not
    exist. Without ``skip_cleanup`` the directory is a :attr:`PipelineState.temp_dir`
    that :func:`run_pipeline` removes.
    """
    config = state.config
    parent = config.temp_dir if config.temp_dir.exists() else None
    if config.skip_cleanup:
        extract_dir = Path(tempfile.mkdtemp(prefix="nwn_translate_", dir=parent))
    else:
        state.temp_dir = tempfile.TemporaryDirectory(prefix="nwn_translate_", dir=parent)
        extract_dir = Path(state.temp_dir.name)
    ERFReader(config.input_file, progress_callback=config.progress_callback).extract_all(
        extract_dir
    )
    return extract_dir


def stage_unpack(state: PipelineState) -> List[Path]:
    """Unpack the input archive into :attr:`PipelineState.extract_dir`.

    Args:
        state: Run state.

    Returns:
        The translatable files (empty when there are none).

    Raises:
        TranslationCancelled: If the run was cancelled.
    """
    logger.info("Extracting module...")
    state.extract_dir = _unpack(state)
    state.progress("extracting", 1, 1, "done")
    state.config.raise_if_cancelled()

    logger.info("Finding translatable files...")
    translatable_files = find_translatable_files(state.extract_dir)
    logger.info("Found %d translatable files", len(translatable_files))
    return translatable_files


def stage_worldscan(state: PipelineState) -> None:
    """Scan the unpacked module into :attr:`PipelineState.world_context` (context mode).

    Args:
        state: Run state with an unpacked module.
    """
    assert state.extract_dir is not None
    if not state.config.use_context:
        return
    state.progress("scanning", 0, 1, "Building world context...")
    state.world_context = WorldScanner().scan_directory(
        state.extract_dir,
        gff_cache=state.gff_cache,
        progress_callback=state.config.progress_callback,
        source_encoding=state.source_encoding,
    )


def _extract_file(
    state: PipelineState, file_path: Path
) -> Optional[Tuple[Dict[str, Any], ExtractedContent, str]]:
    """Load one file and extract its items, for :func:`stage_extract`."""
    file_ext = file_path.suffix.lower()
    loaded = load_parsed_and_extracted(
        file_path, file_ext, state.gff_cache, source_encoding=state.source_encoding
    )
    if loaded is None:
        return None
    parsed_data, extracted = loaded
    return parsed_data, extracted, file_ext


def stage_extract(state: PipelineState, translatable_files: List[Path]) -> ExtractedMap:
    """Parse the files and extract their translatable items on a thread pool.

    A file that fails is recorded as an error and left out.

    Args:
        state: Run state.
        translatable_files: Files to extract.

    Returns:
        The files with translatable items, in the order of *translatable_files*
        (item order drives batch composition).

    Raises:
        TranslationCancelled: If the run is cancelled; queued files are dropped.
    """
    logger.info("Extracting translatable content...")
    extracted_map: ExtractedMap = {}
    for file_path, result, error in _run_pool(
        state,
        partial(_extract_file, state),
        translatable_files,
        "extracting_content",
        cancellable=True,
    ):
        if error is not None:
            state.add_error(f"Error extracting {file_path.name}: {error}")
        elif result is not None:
            extracted_map[file_path] = result
    logger.info("Extraction complete: %d files extracted", len(extracted_map))
    return extracted_map


def stage_collect_entities(state: PipelineState, extracted_map: ExtractedMap) -> None:
    """Add entity candidates from the extracted text to the world context.

    Candidates come from the extracted fields themselves and from model
    requests over all items; they feed :func:`stage_build_glossary`.

    Args:
        state: Run state; nothing happens without a world context.
        extracted_map: Extracted files.
    """
    if state.world_context is None or not extracted_map:
        return

    state.progress("scanning", 0, 1, "Extracting entities from text…")
    contents = [extracted for _parsed, extracted, _ext in extracted_map.values()]
    all_items: List[TranslatableItem] = [item for content in contents for item in content.items]

    known_names = {name for name, _cat in state.world_context.get_all_names()}
    extracted_registry = EntityCandidateRegistry.from_extracted_content(contents)
    state.world_context.candidates.extend(extracted_registry.values())

    llm_registry = EntityExtractor().extract_candidates(
        all_items,
        state.provider,
        state.config,
        known_names,
        progress_callback=state.config.progress_callback,
    )
    state.world_context.candidates.extend(llm_registry.values())
    state.world_context.extracted_names = llm_registry.glossary_pairs()
    if state.world_context.candidates:
        candidate_count = len(state.world_context.candidates.values())
        state.metrics_recorder.increment("entity_candidates.raw", candidate_count)
        logger.info("Entity candidate collection produced %d candidate(s)", candidate_count)


def stage_build_glossary(state: PipelineState) -> None:
    """Curate the entity candidates and build :attr:`PipelineState.glossary`.

    A failed build leaves an empty glossary. The outcome is logged as a
    ``terminology_resolved`` event.

    Args:
        state: Run state; nothing happens outside context mode or without a
            world context.
    """
    if not (state.config.use_context and state.world_context is not None):
        return

    state.progress("scanning", 0, 1, "Building glossary...")
    candidates = state.world_context.candidates
    try:
        GlossaryCurator().curate(
            candidates,
            state.provider,
            state.config,
            progress_callback=state.config.progress_callback,
        )
        decisions = Counter(
            f"entity_candidates.{candidate.curation_decision}" for candidate in candidates.values()
        )
        for key, value in decisions.items():
            state.metrics_recorder.increment(key, value)
        state.glossary = GlossaryBuilder().build(
            state.world_context,
            state.provider,
            state.config,
            progress_callback=state.config.progress_callback,
        )
    except RuntimeError as e:
        logger.warning("Glossary build failed, continuing without it: %s", e)
        state.glossary = Glossary()
    write_trace(
        state.trace,
        {
            "event": "terminology_resolved",
            "entries": state.glossary.entries,
            "aliases": state.glossary.aliases,
            "candidates": [
                {
                    "name": candidate.name,
                    "decision": candidate.curation_decision,
                    "reason": candidate.curation_reason,
                    "alias_of": candidate.alias_of,
                }
                for candidate in candidates.values()
            ],
        },
    )
    state.progress("scanning", 1, 1, "done")


def stage_translate(state: PipelineState, extracted_map: ExtractedMap) -> Translations:
    """Translate every extracted item.

    Non-dialog items of all files go to one deduplicated batch pass; in context
    mode the dialog files are translated as whole conversations afterwards.
    Rejected requests become errors and editor rows with ``success: False``.

    Args:
        state: Run state.
        extracted_map: Extracted files.

    Returns:
        Accepted translation per occurrence.

    Raises:
        TranslationCancelled: If the run is cancelled.
    """
    assert state.extract_dir is not None
    use_dialog_manager = state.config.use_context and state.world_context is not None
    dialog_files = [
        path
        for path, (_parsed, _extracted, ext) in extracted_map.items()
        if use_dialog_manager and ext == ".dlg"
    ]
    dialog_set = set(dialog_files)

    non_dialog_items: List[TranslatableItem] = []
    for file_path, (_parsed, extracted, _ext) in extracted_map.items():
        if file_path in dialog_set:
            continue
        if state.world_context is not None:
            for item in extracted.items:
                state.world_context.enrich_ncs_item_context(item)
        non_dialog_items.extend(extracted.items)

    logger.info("Translating content...")
    item_total = len(non_dialog_items) + sum(len(extracted_map[fp][1].items) for fp in dialog_files)
    item_progress = _ItemProgress(state, item_total)
    if item_total:
        # Switches the UI to the translating phase before the first item finishes.
        state.progress("translating", 0, item_total, "starting")

    translations: Translations = {}
    failed: Set[Occurrence] = set()
    if non_dialog_items:
        manager = TranslationManager(state.config, state.provider, glossary=state.glossary)
        combined = ExtractedContent(
            content_type="combined",
            items=non_dialog_items,
            source_file=state.extract_dir,
            metadata={"type": "combined"},
        )
        translations.update(manager.translate_content(combined, item_progress=item_progress))
        state.merge_manager_stats(manager.stats)
        failed |= manager.failed_items

    if dialog_files:
        state.config.raise_if_cancelled()
        assert state.world_context is not None
        dialog_manager = ContextualTranslationManager(
            state.config, state.provider, state.world_context, glossary=state.glossary
        )
        dialog_translations, dialog_errors = dialog_manager.translate_dialogs(
            [(fp, extracted_map[fp][0], len(extracted_map[fp][1].items)) for fp in dialog_files],
            item_progress=item_progress,
        )
        translations.update(dialog_translations)
        for file_path, exc in dialog_errors:
            state.add_error(f"Error translating dialog {file_path.name}: {exc}")
        failed |= dialog_manager.failed_items

    logger.info("Translation complete: %d translations collected", len(translations))
    _log_editor_rows(state, extracted_map, translations, failed)
    return translations


def _log_editor_rows(
    state: PipelineState,
    extracted_map: ExtractedMap,
    translations: Translations,
    failed: Set[Occurrence],
) -> None:
    """Write one translation log row per file and item id for the web editor.

    The managers log each distinct request once; the editor needs every
    occurrence, grouped by file and addressable at rebuild. A rejected
    occurrence keeps its source text with ``success: False``; dialog rows name
    the speaker of the line. The rows are written after both managers have
    finished, so a store that keeps the last row per file and item (the web
    database) shows these.

    Args:
        state: Run state.
        extracted_map: Extracted files.
        translations: Accepted translation per occurrence.
        failed: Occurrences whose translation was rejected.
    """
    logged: Set[Tuple[str, str]] = set()
    for file_path, (_parsed, extracted, file_ext) in extracted_map.items():
        for item in extracted.items:
            if not item.has_text() or not item.item_id:
                continue
            translated = translations.get(item.key)
            rejected = item.key in failed
            if translated is None and not rejected:
                continue
            row_key = (file_path.name, item.item_id)
            if row_key in logged:
                continue
            logged.add(row_key)
            row: Dict[str, Any] = {
                "original": item.text,
                "translated": item.text if translated is None else translated,
                "context": item.context,
                "model": state.config.model,
                "file": file_path.name,
                "item_id": item.item_id,
                "success": not rejected,
            }
            if file_ext == ".dlg":
                row["speaker"] = dialog_line_speaker(
                    state.world_context,
                    file_path.stem,
                    is_entry=item.metadata.get("type") == "entry",
                    speaker_tag=str(item.metadata.get("speaker") or ""),
                )
            write_trace(state.trace, row)


def _inject_file(
    state: PipelineState,
    extracted_map: ExtractedMap,
    translations: Translations,
    file_path: Path,
) -> Optional[InjectedContent]:
    """Patch the translations of one extracted file, for :func:`stage_inject`."""
    parsed_data, extracted, _ext = extracted_map[file_path]
    return inject_translations_into_file(
        file_path,
        parsed_data,
        extracted,
        translations,
        log_updates=True,
        target_lang=state.config.target_lang,
        source_encoding=state.source_encoding,
    )


def stage_inject(
    state: PipelineState,
    extracted_map: ExtractedMap,
    translations: Translations,
) -> None:
    """Patch the translations into the unpacked files on a thread pool.

    Each file's outcome is logged as an ``injection_result`` event, in file
    order. A failed file is recorded as an error; the others count as processed.

    Args:
        state: Run state.
        extracted_map: Extracted files.
        translations: Translation per occurrence.
    """
    assert state.extract_dir is not None
    logger.info("Injecting translations...")
    for file_path, result, error in _run_pool(
        state,
        partial(_inject_file, state, extracted_map, translations),
        list(extracted_map),
        "injecting",
        cancellable=False,
    ):
        if error is not None:
            write_trace(
                state.trace,
                {"event": "injection_result", "file": file_path.name, "error": str(error)},
            )
            state.add_error(f"Error injecting {file_path.name}: {error}")
            continue
        metadata = result.metadata if result else {}
        write_trace(
            state.trace,
            {
                "event": "injection_result",
                "file": file_path.name,
                "submitted": [
                    {"item_id": item.item_id, "translated": translations[item.key]}
                    for item in extracted_map[file_path][1].items
                    if item.key in translations
                ],
                "modified": result.modified if result else False,
                "items_updated": result.items_updated if result else 0,
                "metadata": metadata,
            },
        )
        if metadata.get("ncs_patch_failed"):
            state.record_ncs_patch_failure(file_path, str(metadata.get("error", "")))
        state.stats["files_processed"] += 1


def stage_repack(state: PipelineState) -> Path:
    """Build the translated module from the unpacked files and write the metrics.

    Args:
        state: Run state.

    Returns:
        The translated module.
    """
    assert state.extract_dir is not None
    state.progress("building", 1, 2, "Repacking module...")
    logger.info("Creating translated module...")
    output_path = state.output_path()
    create_mod_from_directory(state.extract_dir, output_path, state.config.input_file)
    state.write_metrics(output_path)
    logger.info("Translation complete: %s", output_path)
    _log_summary(state)
    return output_path


def _log_summary(state: PipelineState) -> None:
    """Log the processed files, translated items and errors of the run."""
    errors = state.stats["errors"]
    logger.info("=" * 50)
    logger.info("Translation Summary")
    logger.info("=" * 50)
    logger.info("Files processed: %s", state.stats["files_processed"])
    logger.info("Items translated: %s", state.stats["items_translated"])
    if errors:
        logger.warning("Errors: %d", len(errors))
        if state.config.verbose:
            for error in errors[:10]:
                logger.warning("  - %s", error)
            if len(errors) > 10:
                logger.warning("  ... and %d more", len(errors) - 10)
    else:
        logger.info("No errors!")
    logger.info("=" * 50)


def run_pipeline(state: PipelineState) -> Path:
    """Run every stage on *state* and return the translated module.

    An archive without translatable files is copied unchanged. However the run
    ends, the provider's HTTP client and this thread's event loop are closed,
    the log file opened by the run is closed, and the temporary directory is
    removed unless ``config.skip_cleanup`` is set.

    Args:
        state: Fresh run state.

    Returns:
        The translated module.

    Raises:
        TranslationCancelled: If the run is cancelled.
    """
    logger.info("Starting translation of %s", state.config.input_file)
    logger.info("Target language: %s", state.config.target_lang)
    logger.info("Model: %s", state.config.model)
    try:
        translatable_files = stage_unpack(state)
        if not translatable_files:
            logger.warning("No translatable files found! Copying input archive unchanged.")
            output_path = state.output_path()
            shutil.copyfile(state.config.input_file, output_path)
            state.write_metrics(output_path)
            return output_path

        stage_worldscan(state)
        extracted_map = stage_extract(state, translatable_files)
        stage_collect_entities(state, extracted_map)
        stage_build_glossary(state)
        translations = stage_translate(state, extracted_map)
        stage_inject(state, extracted_map, translations)
        return stage_repack(state)
    finally:
        # The loop and its client serve the whole run; without closing them
        # the web process would keep one open client and loop per task thread.
        close_thread_resources(state.provider)
        if state._log_file is not None:
            state._log_file.close()
        if not state.config.skip_cleanup and state.temp_dir is not None:
            state.temp_dir.cleanup()
            state.temp_dir = None
            state.extract_dir = None
