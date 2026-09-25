"""Pipeline stages operating on an explicit :class:`PipelineState`.

The full translation run is the composition :func:`run_pipeline`.  Each stage
function is independently callable on a :class:`PipelineState`, so a single
stage can be executed from a saved artifact (see
:mod:`nwn_translator.pipeline.artifacts`) without running the others.  The
stage bodies are the same logic that previously lived inline in
``ModuleTranslator.translate``; behaviour is unchanged.
"""

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import (
    TranslationCancelled,
    TranslationConfig,
    create_output_path,
    module_string_encoding_for_target_lang,
    source_string_encoding,
)
from ..file_handlers import (
    ERFReader,
    create_mod_from_directory,
    read_gff,
)
from ..extractors import get_extractor_for_file
from ..injectors import get_injector_for_content
from ..injectors.base import InjectedContent
from ..extractors.base import ExtractedContent, TranslatableItem
from ..ai_providers.openrouter_provider import OpenRouterProvider
from ..translators.translation_manager import TranslationManager
from ..extractors.base import Translations
from ..translators.context_translator import ContextualTranslationManager
from ..context.dialog_speakers import dialog_line_speaker
from ..context.world_context import WorldScanner, WorldContext
from ..context.entity_extractor import EntityExtractor
from ..context.entity_candidates import EntityCandidateRegistry
from ..glossary import Glossary, GlossaryBuilder
from ..glossary_curator import GlossaryCurator
from ..telemetry import RunMetricsRecorder
from ..translation_logging import translation_log_writer_for_config, write_trace

logger = logging.getLogger(__name__)

#: ``file_path -> (parsed_data, ExtractedContent, file_ext)``
ExtractedMap = Dict[Path, Tuple[Dict[str, Any], ExtractedContent, str]]


def _new_ncs_diagnostics() -> Dict[str, Any]:
    """Return a fresh NCS diagnostics counter block."""
    return {
        "total": 0,
        "extracted": 0,
        "approved": 0,
        "skipped_hard_veto": 0,
        "skipped_fail_closed": 0,
        "translated": 0,
        "timeout": 0,
        "retry_recovered": 0,
        "failed": 0,
        "patch_failed": 0,
        "samples": [],
    }


def _new_stats() -> Dict[str, Any]:
    """Return a fresh run-statistics dictionary."""
    return {
        "files_processed": 0,
        "items_translated": 0,
        "errors": [],
        "ncs_diagnostics": _new_ncs_diagnostics(),
    }


class _ItemProgress:
    """Shared counter that turns per-item bumps into ``translating_item`` callbacks.

    Thread-safe (translation manager runs asyncio tasks inside ``run_async`` on
    a worker thread; dialog loop is sequential). ``total`` is an estimate;
    the counter is clamped so it never reports > total.
    """

    def __init__(self, total: int, callback) -> None:
        self.total = max(1, int(total))
        self.done = 0
        self.callback = callback
        self._lock = threading.Lock()

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        if by <= 0:
            return
        with self._lock:
            self.done = min(self.total, self.done + by)
            done_snapshot = self.done
            total_snapshot = self.total
        if self.callback is not None:
            self.callback("translating_item", done_snapshot, total_snapshot, filename or "")


def load_parsed_and_extracted(
    file_path: Path,
    file_ext: str,
    gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    source_encoding: Optional[str] = None,
) -> Optional[Tuple[Dict[str, Any], ExtractedContent]]:
    """Parse *file_path* and run the extractor; return data or ``None`` if skipped."""
    if file_ext == ".ncs":
        from ..file_handlers.ncs_parser import parse_ncs, NCSParseError

        try:
            ncs_file = parse_ncs(file_path, source_encoding=source_encoding)
        except NCSParseError as e:
            logger.debug("Skipping unparseable NCS file %s: %s", file_path.name, e)
            return None
        parsed_data: Dict[str, Any] = {"_ncs_file": ncs_file, "_source_encoding": source_encoding}
    else:
        parsed_data = read_gff(file_path, cache=gff_cache, source_encoding=source_encoding)

    extractor = get_extractor_for_file(file_ext)
    if not extractor:
        logger.debug("No extractor for %s: %s", file_ext, file_path.name)
        return None

    extracted = extractor.extract(file_path, parsed_data)
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
    """Run the appropriate injector for *extracted* (shared by Phase C and rebuild).

    *source_encoding* must match the decode used when *extracted* was produced:
    injectors that re-read file bytes (NCS) compare decoded text against the
    extracted originals.
    """
    injector = get_injector_for_content(extracted.content_type)
    if not injector:
        return None
    inject_metadata = {**(extracted.metadata or {}), "type": extracted.content_type}
    inject_metadata["module_text_encoding"] = module_string_encoding_for_target_lang(target_lang)
    inject_metadata["module_source_encoding"] = source_encoding
    inject_metadata["extracted_items"] = extracted.items
    result: InjectedContent = injector.inject(file_path, parsed_data, translations, inject_metadata)
    if log_updates and result.modified:
        logger.info("Updated %s: %s items", file_path.name, result.items_updated)
    return result


@dataclass
class PipelineState:
    """Mutable state shared across pipeline stages for a single run.

    Holds everything the stages read or write so each stage takes the state
    explicitly instead of reaching into ``ModuleTranslator`` internals.
    """

    config: TranslationConfig
    provider: OpenRouterProvider
    metrics_recorder: RunMetricsRecorder = field(default_factory=RunMetricsRecorder)
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    extract_dir: Optional[Path] = None
    world_context: Optional[WorldContext] = None
    glossary: Optional[Glossary] = None
    #: Per-run GFF parse cache: resolved_path -> dict
    _gff_cache: Dict[Path, Dict[str, Any]] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=_new_stats)
    _stats_lock: threading.Lock = field(default_factory=threading.Lock)
    #: Delta-tracking cursors for cumulative manager stats.
    _prev_items: int = 0
    _prev_errors: int = 0

    # ── setup helpers ──────────────────────────────────────────────────
    def _source_encoding(self) -> Optional[str]:
        """Declared code page for reading module strings (``None`` = detect)."""
        return source_string_encoding(self.config.source_lang)

    def _check_cancel(self) -> None:
        """Raise :class:`TranslationCancelled` if the config's cancel check fires."""
        cb = self.config.cancel_check
        if cb is not None and cb():
            raise TranslationCancelled("Translation cancelled by user")

    def _extract_module(self) -> Path:
        """Extract the .mod file to a temporary directory and record it."""
        if self.config.skip_cleanup:
            # Use a persistent directory that won't auto-delete on GC
            parent = (
                self.config.temp_dir
                if self.config.temp_dir.exists()
                else Path(tempfile.gettempdir())
            )
            extract_dir = Path(tempfile.mkdtemp(prefix="nwn_translate_", dir=parent))
            self.temp_dir = None
        else:
            self.temp_dir = tempfile.TemporaryDirectory(
                prefix="nwn_translate_",
                dir=self.config.temp_dir if self.config.temp_dir.exists() else None,
            )
            extract_dir = Path(self.temp_dir.name)

        reader = ERFReader(
            self.config.input_file,
            progress_callback=self.config.progress_callback,
        )
        reader.read_entries()

        # Extract all files
        reader.extract_all(extract_dir)
        self.extract_dir = extract_dir

        return extract_dir

    def _find_translatable_files(self, directory: Path) -> List[Path]:
        """Find all translatable files in *directory*."""
        from ..config import TRANSLATABLE_TYPES

        translatable_files = []
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                ext = file_path.suffix.lower()
                if ext in TRANSLATABLE_TYPES:
                    translatable_files.append(file_path)
        return translatable_files

    def _extract_file(
        self,
        file_path: Path,
    ) -> Optional[Tuple[Dict[str, Any], ExtractedContent, str]]:
        """Extract translatable content from a single file (Phase A)."""
        file_ext = file_path.suffix.lower()
        loaded = load_parsed_and_extracted(
            file_path, file_ext, self._gff_cache, source_encoding=self._source_encoding()
        )
        if loaded is None:
            return None
        parsed_data, extracted = loaded
        return parsed_data, extracted, file_ext

    def _inject_file(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        extracted: ExtractedContent,
        all_translations: Translations,
    ) -> Optional[InjectedContent]:
        """Inject translations into a single file (Phase C)."""
        return inject_translations_into_file(
            file_path,
            parsed_data,
            extracted,
            all_translations,
            log_updates=True,
            target_lang=self.config.target_lang,
            source_encoding=self._source_encoding(),
        )

    # ── stats / logging helpers ────────────────────────────────────────
    def _log_per_file_translations(
        self,
        extracted_map: ExtractedMap,
        all_translations: Translations,
        manager: "TranslationManager",
    ) -> None:
        """Write per-(file, item_id) translation rows for the web editor.

        The TranslationManager logs only unique items (one entry per
        deduplicated text); this method adds an entry for every (file, item_id)
        pair — including dialogs — so the editor groups by source file and each
        item is independently addressable at rebuild time. Dialog rows also
        carry the line's speaker for the editor.
        """
        already_logged: Set[Tuple[str, str]] = set()

        for file_path, (_gff, extracted, file_ext) in extracted_map.items():
            for item in extracted.items:
                if not item.has_text() or not item.item_id:
                    continue
                translated = all_translations.get(item.key)
                failed = item.key in manager.failed_items
                if translated is None and not failed:
                    continue
                if translated is None:
                    translated = item.text
                log_key = (file_path.name, item.item_id)
                if log_key in already_logged:
                    continue
                already_logged.add(log_key)
                speaker = (
                    dialog_line_speaker(
                        self.world_context,
                        file_path.stem,
                        is_entry=item.metadata.get("type") == "entry",
                        speaker_tag=str(item.metadata.get("speaker") or ""),
                    )
                    if file_ext == ".dlg"
                    else None
                )
                manager.log_per_file_item(
                    original=item.text,
                    translated=translated,
                    context=item.context,
                    source_filename=file_path.name,
                    item_id=item.item_id,
                    success=not failed,
                    speaker=speaker,
                )

    def _record_ncs_patch_failure(self, file_path: Path, error: str) -> None:
        sample = {
            "file": file_path.name,
            "reason": "patch_failed",
            "error": error,
        }
        with self._stats_lock:
            ncs_stats = self.stats.setdefault("ncs_diagnostics", _new_ncs_diagnostics())
            ncs_stats["patch_failed"] = int(ncs_stats.get("patch_failed", 0)) + 1
            samples = ncs_stats.setdefault("samples", [])
            if len(samples) < 50:
                samples.append(sample)
        try:
            writer = translation_log_writer_for_config(
                self.config.translation_log,
                self.config.translation_log_writer,
            )
            writer.write({"event": "ncs_diagnostic", **sample})
        except Exception as exc:
            logger.debug("Failed to write NCS patch diagnostic event: %s", exc)

    def _sync_manager_stats(self, manager: "TranslationManager") -> None:
        """Merge delta from the shared TranslationManager stats into run stats."""
        with self._stats_lock:
            items_now = manager.stats["items_translated"]
            errors_now = len(manager.stats["errors"])
            self.stats["items_translated"] += items_now - self._prev_items
            self.stats["errors"].extend(manager.stats["errors"][self._prev_errors :])
            manager_ncs = manager.stats.get("ncs_diagnostics") or {}
            if manager_ncs:
                ncs_stats = self.stats.setdefault("ncs_diagnostics", _new_ncs_diagnostics())
                for key in (
                    "total",
                    "extracted",
                    "approved",
                    "skipped_hard_veto",
                    "skipped_fail_closed",
                    "translated",
                    "timeout",
                    "retry_recovered",
                    "failed",
                ):
                    ncs_stats[key] = int(ncs_stats.get(key, 0)) + int(manager_ncs.get(key, 0))
                samples = ncs_stats.setdefault("samples", [])
                for sample in manager_ncs.get("samples", []):
                    if len(samples) >= 50:
                        break
                    samples.append(sample)
            self._prev_items = items_now
            self._prev_errors = errors_now

    # ── output / teardown helpers ──────────────────────────────────────
    def _resolve_output_path(self, extract_dir: Path) -> Path:
        """Determine the output .mod file path from config or input filename."""
        output_path = self.config.output_file
        if output_path is None:
            output_path = create_output_path(self.config.input_file, self.config.target_lang)
        return output_path

    def _metrics_output_path(self, output_path: Path) -> Path:
        """Return the metrics JSON path for this run."""
        if self.config.metrics_output is not None:
            return self.config.metrics_output
        return output_path.with_suffix(output_path.suffix + ".metrics.json")

    def _write_metrics(self, output_path: Path) -> None:
        """Persist request-level metrics and attach summary to in-memory stats."""
        summary = self.metrics_recorder.summary()
        with self._stats_lock:
            self.stats["metrics"] = summary
        try:
            self.metrics_recorder.write_json(self._metrics_output_path(output_path))
        except Exception as exc:
            logger.debug("Failed to write run metrics: %s", exc)

    def _cleanup(self) -> None:
        """Clean up temporary files."""
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
            self.extract_dir = None

    def _log_summary(self) -> None:
        """Log translation summary."""
        logger.info("=" * 50)
        logger.info("Translation Summary")
        logger.info("=" * 50)
        logger.info(f"Files processed: {self.stats['files_processed']}")
        logger.info(f"Items translated: {self.stats['items_translated']}")

        if self.stats["errors"]:
            logger.warning(f"Errors: {len(self.stats['errors'])}")
            if self.config.verbose:
                for error in self.stats["errors"][:10]:  # Show first 10
                    logger.warning(f"  - {error}")
                if len(self.stats["errors"]) > 10:
                    logger.warning(f"  ... and {len(self.stats['errors']) - 10} more")
        else:
            logger.info("No errors!")
        logger.info("=" * 50)

    def get_statistics(self) -> Dict[str, Any]:
        """Get translation statistics."""
        return {
            **self.stats,
            "metrics": self.metrics_recorder.summary(),
            "total_errors": len(self.stats["errors"]),
        }


# ── stage functions ────────────────────────────────────────────────────


def stage_unpack(state: PipelineState) -> List[Path]:
    """Stage A: extract the archive and list translatable files.

    Returns the list of translatable file paths (empty if none).
    """
    logger.info("Extracting module...")
    extract_dir = state._extract_module()
    if state.config.progress_callback:
        state.config.progress_callback("extracting", 1, 1, "done")
    state._check_cancel()

    logger.info("Finding translatable files...")
    translatable_files = state._find_translatable_files(extract_dir)
    logger.info(f"Found {len(translatable_files)} translatable files")
    return translatable_files


def stage_worldscan(state: PipelineState) -> None:
    """Stage C: static world scan into ``state.world_context`` (when enabled)."""
    assert state.extract_dir is not None
    state.glossary = None
    if state.config.use_context:
        if state.config.progress_callback:
            state.config.progress_callback("scanning", 0, 1, "Building world context...")
        scanner = WorldScanner()
        state.world_context = scanner.scan_directory(
            state.extract_dir,
            gff_cache=state._gff_cache,
            progress_callback=state.config.progress_callback,
            source_encoding=state._source_encoding(),
        )


def stage_extract(state: PipelineState, translatable_files: List[Path]) -> ExtractedMap:
    """Stage B (Phase A): parse and extract translatable content in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info("Phase A: extracting translatable content...")
    total_files = len(translatable_files)
    extracted_map: ExtractedMap = {}

    max_workers = max(1, getattr(state.config, "max_concurrent_requests", 4))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(state._extract_file, file_path): file_path
            for file_path in translatable_files
        }
        completed_count = 0
        try:
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                completed_count += 1
                if state.config.progress_callback is not None:
                    state.config.progress_callback(
                        "extracting_content", completed_count, total_files, file_path.name
                    )
                state._check_cancel()
                try:
                    result = future.result()
                    if result is not None:
                        parsed_data, extracted, file_ext = result
                        extracted_map[file_path] = (parsed_data, extracted, file_ext)
                except Exception as e:
                    error_msg = f"Error extracting {file_path.name}: {e}"
                    with state._stats_lock:
                        state.stats["errors"].append(error_msg)
                    logger.error(error_msg)
        except BaseException:
            # On cancellation (or any error escaping the loop) drop the queued
            # futures; otherwise the executor's __exit__ would run every
            # remaining file to completion before the exception propagates.
            executor.shutdown(wait=False, cancel_futures=True)
            raise

    logger.info("Phase A complete: %d files extracted", len(extracted_map))
    return extracted_map


def stage_collect_entities(state: PipelineState, extracted_map: ExtractedMap) -> None:
    """Stage D1: collect entity candidates from extracted text (feeds glossary)."""
    if state.world_context is None or not extracted_map:
        return

    if state.config.progress_callback:
        state.config.progress_callback("scanning", 0, 1, "Extracting entities from text…")
    all_items: List[TranslatableItem] = []
    extracted_contents: List[ExtractedContent] = []
    for _fp, (_pd, extracted, _ext) in extracted_map.items():
        all_items.extend(extracted.items)
        extracted_contents.append(extracted)

    known_names = {name for name, _cat in state.world_context.get_all_names()}
    extracted_registry = EntityCandidateRegistry.from_extracted_content(extracted_contents)
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
        state.metrics_recorder.increment(
            "entity_candidates.raw",
            len(state.world_context.candidates.values()),
        )
        logger.info(
            "Entity candidate collection produced %d candidate(s)",
            len(state.world_context.candidates.values()),
        )


def stage_build_glossary(state: PipelineState) -> None:
    """Stage D2: curate candidates and build the run-wide glossary."""
    if not (state.config.use_context and state.world_context is not None):
        return

    if state.config.progress_callback:
        state.config.progress_callback("scanning", 0, 1, "Building glossary...")
    try:
        GlossaryCurator().curate(
            state.world_context.candidates,
            state.provider,
            state.config,
            progress_callback=state.config.progress_callback,
        )
        curated_counts: Dict[str, int] = {}
        for candidate in state.world_context.candidates.values():
            key = f"entity_candidates.{candidate.curation_decision}"
            curated_counts[key] = curated_counts.get(key, 0) + 1
        for key, value in curated_counts.items():
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
        translation_log_writer_for_config(
            state.config.translation_log, state.config.translation_log_writer
        ),
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
                for candidate in state.world_context.candidates.values()
            ],
        },
    )
    if state.config.progress_callback:
        state.config.progress_callback("scanning", 1, 1, "done")


def stage_translate(state: PipelineState, extracted_map: ExtractedMap) -> Translations:
    """Stage E (Phase B): translate non-dialog batch + contextual dialogs.

    Returns one resource/item-addressed map for GFF and NCS occurrences.
    """
    assert state.extract_dir is not None
    extract_dir = state.extract_dir

    # Decide which files go to the contextual dialog path.
    use_context_manager = bool(state.config.use_context and state.world_context)
    dialog_files: List[Path] = [
        fp for fp, (_pd, _ex, ext) in extracted_map.items() if ext == ".dlg" and use_context_manager
    ]

    # Collect all unique non-dialog items into a single ExtractedContent
    non_dialog_items: List[TranslatableItem] = []
    for file_path, (_parsed_data, extracted, _file_ext) in extracted_map.items():
        if file_path not in dialog_files:
            if state.world_context is not None:
                for item in extracted.items:
                    state.world_context.enrich_ncs_item_context(item)
            non_dialog_items.extend(extracted.items)

    # Initialize translation managers for Phase B (need glossary).
    manager = TranslationManager(state.config, state.provider, glossary=state.glossary)
    state._prev_items = 0
    state._prev_errors = 0
    context_manager: Optional[ContextualTranslationManager]
    if use_context_manager and state.world_context is not None:
        context_manager = ContextualTranslationManager(
            state.config,
            state.provider,
            state.world_context,
            glossary=state.glossary,
        )
    else:
        context_manager = None

    logger.info("Phase B: translating content...")
    all_translations: Translations = {}

    dialog_item_total = sum(len(extracted_map[fp][1].items) for fp in dialog_files)
    total_items_b = len(non_dialog_items) + dialog_item_total
    item_progress = _ItemProgress(
        total=total_items_b,
        callback=state.config.progress_callback,
    )
    if state.config.progress_callback and total_items_b:
        # Brief sentinel so the UI switches to the translating phase label.
        state.config.progress_callback("translating", 0, total_items_b, "starting")

    # B-1: Translate all non-dialog items in one deduplicated batch
    if non_dialog_items:
        combined = ExtractedContent(
            content_type="combined",
            items=non_dialog_items,
            source_file=extract_dir,
            metadata={"type": "combined"},
        )
        non_dialog_translations = manager.translate_content(combined, item_progress=item_progress)
        if non_dialog_translations:
            all_translations.update(non_dialog_translations)
        state._sync_manager_stats(manager)

    # B-2: Translate dialog files (contextual, concurrent across files)
    if dialog_files:
        state._check_cancel()
        assert context_manager is not None
        dialog_jobs = [
            (file_path, extracted_map[file_path][0], len(extracted_map[file_path][1].items))
            for file_path in dialog_files
        ]
        dialog_translations, dialog_errors = context_manager.translate_dialogs(
            dialog_jobs,
            item_progress=item_progress,
        )
        all_translations.update(dialog_translations)
        for file_path, exc in dialog_errors:
            error_msg = f"Error translating dialog {file_path.name}: {exc}"
            with state._stats_lock:
                state.stats["errors"].append(error_msg)
            logger.error(error_msg)
        manager.failed_items.update(context_manager.failed_items)

    logger.info("Phase B complete: %d translations collected", len(all_translations))

    # Write per-file log entries so the web editor groups by source file.
    state._log_per_file_translations(extracted_map, all_translations, manager)

    return all_translations


def stage_inject(
    state: PipelineState,
    extracted_map: ExtractedMap,
    all_translations: Translations,
) -> None:
    """Stage F (Phase C): byte-patch translations into files and .git areas."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    assert state.extract_dir is not None
    max_workers = max(1, getattr(state.config, "max_concurrent_requests", 4))

    logger.info("Phase C: injecting translations...")
    trace_writer = translation_log_writer_for_config(
        state.config.translation_log, state.config.translation_log_writer
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(
                state._inject_file,
                file_path,
                extracted_map[file_path][0],  # parsed_data
                extracted_map[file_path][1],  # extracted
                all_translations,
            ): file_path
            for file_path in extracted_map
        }
        completed_count = 0
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            completed_count += 1
            if state.config.progress_callback is not None:
                state.config.progress_callback(
                    "injecting", completed_count, len(extracted_map), file_path.name
                )
            try:
                inject_result = future.result()
                write_trace(
                    trace_writer,
                    {
                        "event": "injection_result",
                        "file": file_path.name,
                        "submitted": [
                            {"item_id": item.item_id, "translated": all_translations[item.key]}
                            for item in extracted_map[file_path][1].items
                            if item.key in all_translations
                        ],
                        "modified": inject_result.modified if inject_result else False,
                        "items_updated": inject_result.items_updated if inject_result else 0,
                        "metadata": inject_result.metadata if inject_result else {},
                    },
                )
                if inject_result is not None and (inject_result.metadata or {}).get(
                    "ncs_patch_failed"
                ):
                    state._record_ncs_patch_failure(
                        file_path,
                        str((inject_result.metadata or {}).get("error", "")),
                    )
                with state._stats_lock:
                    state.stats["files_processed"] += 1
            except Exception as e:
                error_msg = f"Error injecting {file_path.name}: {e}"
                write_trace(
                    trace_writer,
                    {"event": "injection_result", "file": file_path.name, "error": str(e)},
                )
                with state._stats_lock:
                    state.stats["errors"].append(error_msg)
                logger.error(error_msg)


def stage_repack(state: PipelineState) -> Path:
    """Stage G: reassemble the .mod and write run metrics."""
    assert state.extract_dir is not None
    extract_dir = state.extract_dir

    if state.config.progress_callback:
        state.config.progress_callback("building", 1, 2, "Repacking module...")
    logger.info("Creating translated module...")

    output_path = state._resolve_output_path(extract_dir)
    create_mod_from_directory(extract_dir, output_path, state.config.input_file)
    state._write_metrics(output_path)

    logger.info(f"Translation complete: {output_path}")
    state._log_summary()
    return output_path


def run_pipeline(state: PipelineState) -> Path:
    """Run the full translation pipeline by composing the stage functions."""
    logger.info(f"Starting translation of {state.config.input_file}")
    logger.info(f"Target language: {state.config.target_lang}")
    logger.info(f"OpenRouter model: {state.config.model}")

    try:
        translatable_files = stage_unpack(state)
        assert state.extract_dir is not None

        if not translatable_files:
            logger.warning("No translatable files found! Copying input archive unchanged.")
            output_path = state._resolve_output_path(state.extract_dir)
            shutil.copyfile(state.config.input_file, output_path)
            state._write_metrics(output_path)
            return output_path

        # Session GFF cache (world scan + translation + .git)
        state._gff_cache = {}

        stage_worldscan(state)
        extracted_map = stage_extract(state, translatable_files)
        stage_collect_entities(state, extracted_map)
        stage_build_glossary(state)
        all_translations = stage_translate(state, extracted_map)
        stage_inject(state, extracted_map, all_translations)
        return stage_repack(state)
    finally:
        _shutdown_async_resources(state)
        if not state.config.skip_cleanup:
            state._cleanup()


def _shutdown_async_resources(state: PipelineState) -> None:
    """Close the provider's HTTP client and this thread's persistent loop.

    The loop (and the loop-bound client) is reused across ``run_async`` calls
    for the whole run; without this the web process would keep one open client
    and loop per finished task thread.
    """
    from ..async_utils import run_async, shutdown_thread_loop

    close = getattr(state.provider, "close_async_client", None)
    try:
        if close is not None:
            run_async(close(), timeout=30.0)
    except Exception:
        pass
    finally:
        shutdown_thread_loop()
