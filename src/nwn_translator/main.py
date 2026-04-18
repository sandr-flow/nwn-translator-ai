"""Main orchestration for NWN module translation.

This module handles the complete workflow of translating a Neverwinter Nights module:
1. Extract .mod file
2. Parse all translatable resources
3. Translate content using AI
4. Inject translations back into GFF files
5. Create new .mod file
"""

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tqdm import tqdm

from .config import (
    TranslationConfig,
    TRANSLATABLE_TYPES,
    module_string_encoding_for_target_lang,
)
from .file_handlers import (
    ERFReader,
    create_mod_from_directory,
    read_gff,
)
from .file_handlers.tlk_reader import parse_tlk, find_dialog_tlk, TLKFile
from .extractors import get_extractor_for_file
from .injectors import get_injector_for_content
from .extractors.base import ExtractedContent, TranslatableItem
from .injectors.git_injector import patch_git_file
from .ai_providers import create_provider
from .translators.translation_manager import TranslationManager
from .translators.context_translator import ContextualTranslationManager
from .context.world_context import WorldScanner, WorldContext
from .context.entity_extractor import EntityExtractor
from .glossary import Glossary, GlossaryBuilder

import threading
import concurrent.futures

logger = logging.getLogger(__name__)


def load_parsed_and_extracted(
    file_path: Path,
    file_ext: str,
    tlk: Optional[TLKFile],
    gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
) -> Optional[Tuple[Dict[str, Any], ExtractedContent]]:
    """Parse *file_path* and run the extractor; return data or ``None`` if skipped."""
    if file_ext == ".ncs":
        from .file_handlers.ncs_parser import parse_ncs, NCSParseError

        try:
            ncs_file = parse_ncs(file_path)
        except NCSParseError as e:
            logger.debug("Skipping unparseable NCS file %s: %s", file_path.name, e)
            return None
        parsed_data: Dict[str, Any] = {"_ncs_file": ncs_file}
    else:
        parsed_data = read_gff(file_path, tlk=tlk, cache=gff_cache)

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
    translations: Dict[str, str],
    *,
    ncs_translations_by_item_id: Optional[Dict[str, str]] = None,
    log_updates: bool = False,
    target_lang: Optional[str] = None,
) -> None:
    """Run the appropriate injector for *extracted* (shared by Phase C and rebuild)."""
    injector = get_injector_for_content(extracted.content_type)
    if not injector:
        return
    inject_metadata = {**(extracted.metadata or {}), "type": extracted.content_type}
    inject_metadata["module_text_encoding"] = module_string_encoding_for_target_lang(
        target_lang
    )
    if extracted.content_type == "ncs_script":
        by_id: Dict[str, str] = {}
        if ncs_translations_by_item_id is not None:
            by_id.update(ncs_translations_by_item_id)
        for item in extracted.items:
            tid = item.item_id or ""
            if not tid or tid in by_id:
                continue
            new_t = translations.get(item.text)
            if new_t is None or new_t == item.text:
                continue
            by_id[tid] = new_t
        inject_metadata["ncs_translations_by_item_id"] = by_id
        inject_metadata["ncs_extracted_items"] = extracted.items
    result = injector.inject(file_path, parsed_data, translations, inject_metadata)
    if log_updates and result.modified:
        logger.info("Updated %s: %s items", file_path.name, result.items_updated)


class ModuleTranslator:
    """Main translator for NWN modules."""

    def __init__(self, config: TranslationConfig):
        """Initialize the translator.

        Args:
            config: Translation configuration
        """
        self.config = config
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.extract_dir: Optional[Path] = None

        # Create AI provider
        self.provider = create_provider(
            config.api_key,
            config.model,
            player_gender=config.player_gender,
            reasoning_effort=config.reasoning_effort,
        )

        # Statistics
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "errors": [],
        }
        self._stats_lock = threading.Lock()

        # TLK file for resolving StrRef names
        self.tlk: Optional[TLKFile] = None

        # World context cache
        self.world_context: Optional[WorldContext] = None
        self.glossary: Optional[Glossary] = None
        #: Per-run GFF parse cache: (resolved_path, tlk_id) -> dict
        self._gff_cache: Dict[Tuple[Path, int], Dict[str, Any]] = {}
        #: Latest NCS per-``item_id`` translations from :class:`TranslationManager` (Phase B).
        self._ncs_translations_by_item_id: Dict[str, str] = {}

    def translate(self) -> Path:
        """Translate the module.

        Returns:
            Path to translated .mod file

        Raises:
            Exception: If translation fails
        """
        logger.info(f"Starting translation of {self.config.input_file}")
        logger.info(f"Target language: {self.config.target_lang}")
        logger.info(f"OpenRouter model: {self.config.model}")

        # Step 1: Extract module
        logger.info("Extracting module...")
        extract_dir = self._extract_module()
        if self.config.progress_callback:
            self.config.progress_callback("extracting", 1, 1, "done")

        # Step 2: Find translatable files
        logger.info("Finding translatable files...")
        translatable_files = self._find_translatable_files(extract_dir)
        logger.info(f"Found {len(translatable_files)} translatable files")

        if not translatable_files:
            logger.warning("No translatable files found!")
            return self._resolve_output_path(extract_dir)

        # Session GFF cache (world scan + translation + .git)
        self._gff_cache = {}

        # Step 2.5: Load TLK file for resolving StrRef names
        self._load_tlk(extract_dir)

        # Step 2.6: World scan (glossary build deferred until after Phase A)
        self.glossary = None
        if self.config.use_context:
            if self.config.progress_callback:
                self.config.progress_callback("scanning", 0, 1, "Building world context...")
            scanner = WorldScanner()
            self.world_context = scanner.scan_directory(
                extract_dir, tlk=self.tlk, gff_cache=self._gff_cache,
                progress_callback=self.config.progress_callback,
            )

        # ── Phase A: parallel extract ──────────────────────────────────
        # Phase A runs before glossary build so entity extraction can feed
        # text-embedded proper nouns into the glossary.
        logger.info("Phase A: extracting translatable content...")
        total_files = len(translatable_files)

        # file_path -> (parsed_data, ExtractedContent, file_ext)
        extracted_map: Dict[Path, Tuple[Dict[str, Any], ExtractedContent, str]] = {}

        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = max(1, getattr(self.config, 'max_concurrent_requests', 4))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(self._extract_file, file_path): file_path
                for file_path in translatable_files
            }
            completed_count = 0
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                if self.config.progress_callback is not None:
                    self.config.progress_callback(
                        "extracting_content", completed_count, total_files, file_path.name
                    )
                completed_count += 1
                try:
                    result = future.result()
                    if result is not None:
                        parsed_data, extracted, file_ext = result
                        extracted_map[file_path] = (parsed_data, extracted, file_ext)
                except Exception as e:
                    error_msg = f"Error extracting {file_path.name}: {e}"
                    with self._stats_lock:
                        self.stats["errors"].append(error_msg)
                    logger.error(error_msg)

        # Decide which files go to the contextual dialog path.
        use_context_manager = bool(self.config.use_context and self.world_context)
        dialog_files: List[Path] = [
            fp for fp, (_pd, _ex, ext) in extracted_map.items()
            if ext == ".dlg" and use_context_manager
        ]

        # Collect all unique non-dialog items into a single ExtractedContent
        non_dialog_items: List[TranslatableItem] = []
        for file_path, (parsed_data, extracted, file_ext) in extracted_map.items():
            if file_path not in dialog_files:
                non_dialog_items.extend(extracted.items)

        logger.info(
            "Phase A complete: %d files extracted, %d non-dialog items, %d dialog files",
            len(extracted_map), len(non_dialog_items), len(dialog_files),
        )

        # Step 2.7: Entity extraction from text bodies (feeds glossary).
        if self.world_context is not None and extracted_map:
            if self.config.progress_callback:
                self.config.progress_callback("scanning", 0, 1, "Extracting entities from text…")
            all_items: List[TranslatableItem] = []
            for _fp, (_pd, extracted, _ext) in extracted_map.items():
                all_items.extend(extracted.items)

            known_names = {
                name for name, _cat in self.world_context.get_all_names()
            }
            extracted_entities = EntityExtractor().extract(
                all_items,
                self.provider,
                self.config,
                known_names,
                progress_callback=self.config.progress_callback,
            )
            if extracted_entities:
                self.world_context.extracted_names = extracted_entities
                logger.info(
                    "Entity extraction added %d proper noun(s) to glossary input",
                    len(extracted_entities),
                )

        # Step 2.8: Build glossary (now includes text-extracted names).
        if self.config.use_context and self.world_context is not None:
            if self.config.progress_callback:
                self.config.progress_callback("scanning", 0, 1, "Building glossary...")
            try:
                self.glossary = GlossaryBuilder().build(
                    self.world_context, self.provider, self.config,
                    progress_callback=self.config.progress_callback,
                )
            except RuntimeError as e:
                logger.warning("Glossary build failed, continuing without it: %s", e)
                self.glossary = Glossary()
            if self.config.progress_callback:
                self.config.progress_callback("scanning", 1, 1, "done")

        # Initialize translation managers for Phase B (need glossary).
        manager = TranslationManager(
            self.config, self.provider, glossary=self.glossary
        )
        # Delta-tracking cursors for cumulative manager stats
        self._prev_items = 0
        self._prev_errors = 0
        context_manager = (
            ContextualTranslationManager(
                self.config,
                self.provider,
                self.world_context,
                translation_cache=manager.translation_cache,
                glossary=self.glossary,
            )
            if use_context_manager
            else None
        )

        # ── Phase B: deduplicated translate ────────────────────────────
        logger.info("Phase B: translating content...")
        all_translations: Dict[str, str] = {}

        # B-1: Translate all non-dialog items in one deduplicated batch
        if non_dialog_items:
            if self.config.progress_callback:
                self.config.progress_callback(
                    "translating", 0, total_files, "non-dialog items"
                )
            combined = ExtractedContent(
                content_type="combined",
                items=non_dialog_items,
                source_file=extract_dir,
                metadata={"type": "combined"},
            )
            non_dialog_translations = manager.translate_content(combined)
            if non_dialog_translations:
                all_translations.update(non_dialog_translations)
            self._ncs_translations_by_item_id = manager.ncs_translations_by_item_id
            self._sync_manager_stats(manager)

        # B-2: Translate dialog files (contextual, sequential to benefit from cache)
        for idx, file_path in enumerate(dialog_files):
            if self.config.progress_callback:
                self.config.progress_callback(
                    "translating", idx, len(dialog_files), file_path.name
                )
            parsed_data, extracted, file_ext = extracted_map[file_path]
            try:
                translations = context_manager.translate_dialog(file_path, parsed_data)
                if translations:
                    all_translations.update(translations)
            except Exception as e:
                error_msg = f"Error translating dialog {file_path.name}: {e}"
                with self._stats_lock:
                    self.stats["errors"].append(error_msg)
                logger.error(error_msg)

        logger.info("Phase B complete: %d translations collected", len(all_translations))

        # Write per-file log entries so the web editor groups by source file.
        # The TranslationManager already logged unique items; here we add
        # entries for every (file, item) pair so duplicates across files
        # appear under each file in the editor.
        self._log_per_file_translations(extracted_map, dialog_files, all_translations, manager)

        # ── Phase C: parallel inject ───────────────────────────────────
        logger.info("Phase C: injecting translations...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(
                    self._inject_file, file_path,
                    extracted_map[file_path][0],  # parsed_data
                    extracted_map[file_path][1],  # extracted
                    all_translations,
                ): file_path
                for file_path in extracted_map
            }
            completed_count = 0
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                if self.config.progress_callback is not None:
                    self.config.progress_callback(
                        "injecting", completed_count, len(extracted_map), file_path.name
                    )
                completed_count += 1
                try:
                    future.result()
                    with self._stats_lock:
                        self.stats["files_processed"] += 1
                except Exception as e:
                    error_msg = f"Error injecting {file_path.name}: {e}"
                    with self._stats_lock:
                        self.stats["errors"].append(error_msg)
                    logger.error(error_msg)

        # Step 3.5: Patch .git area instance files (strings come from Phase A GitExtractor)
        if self.config.progress_callback:
            self.config.progress_callback("building", 0, 2, "Patching area files...")
        if all_translations:
            self._patch_git_files(extract_dir, all_translations)

        # Step 4: Create new module
        if self.config.progress_callback:
            self.config.progress_callback("building", 1, 2, "Repacking module...")
        logger.info("Creating translated module...")

        output_path = self.config.output_file
        if output_path is None:
            from .config import create_output_path
            output_path = create_output_path(self.config.input_file, self.config.target_lang)

        create_mod_from_directory(extract_dir, output_path, self.config.input_file)

        logger.info(f"Translation complete: {output_path}")
        self._log_summary()

        # Step 5: Cleanup
        if not self.config.skip_cleanup:
            self._cleanup()

        return output_path

    def _extract_module(self) -> Path:
        """Extract the .mod file to temporary directory.

        Returns:
            Path to extraction directory
        """
        if self.config.skip_cleanup:
            # Use a persistent directory that won't auto-delete on GC
            parent = self.config.temp_dir if self.config.temp_dir.exists() else Path(tempfile.gettempdir())
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
        """Find all translatable files in directory.

        Args:
            directory: Directory to search

        Returns:
            List of file paths
        """
        translatable_files = []

        for file_path in directory.rglob("*"):
            if file_path.is_file():
                ext = file_path.suffix.lower()
                if ext in TRANSLATABLE_TYPES:
                    translatable_files.append(file_path)

        return translatable_files

    def _load_tlk(self, extract_dir: Path) -> None:
        """Load TLK file for resolving StrRef-based names.

        Args:
            extract_dir: Module extraction directory
        """
        tlk_path = self.config.tlk_file
        if not tlk_path:
            tlk_path = find_dialog_tlk(extract_dir)

        if tlk_path and tlk_path.exists():
            try:
                self.tlk = parse_tlk(tlk_path)
                logger.info(f"Loaded TLK file: {tlk_path} ({len(self.tlk)} entries)")
            except Exception as e:
                logger.warning(f"Failed to load TLK file {tlk_path}: {e}")
        else:
            logger.debug("No TLK file found, StrRef-only names will not be resolved")

    def _extract_file(
        self,
        file_path: Path,
    ) -> Optional[Tuple[Dict[str, Any], ExtractedContent, str]]:
        """Extract translatable content from a single file (Phase A).

        Returns:
            (parsed_data, ExtractedContent, file_ext) or None if nothing to extract.
        """
        file_ext = file_path.suffix.lower()
        loaded = load_parsed_and_extracted(
            file_path, file_ext, self.tlk, self._gff_cache
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
        all_translations: Dict[str, str],
    ) -> None:
        """Inject translations into a single file (Phase C)."""
        inject_translations_into_file(
            file_path,
            parsed_data,
            extracted,
            all_translations,
            ncs_translations_by_item_id=self._ncs_translations_by_item_id,
            log_updates=True,
            target_lang=self.config.target_lang,
        )

    def _log_per_file_translations(
        self,
        extracted_map: Dict[Path, Tuple[Dict[str, Any], "ExtractedContent", str]],
        dialog_files: List[Path],
        all_translations: Dict[str, str],
        manager: "TranslationManager",
    ) -> None:
        """Write per-file JSONL entries for non-dialog items.

        Dialog files are already logged inside ContextualTranslationManager.
        For non-dialog files the TranslationManager logs only unique items
        (one entry per deduplicated text).  This method adds entries for
        every (file, item) pair so the web editor can group by source file.
        """
        already_logged: Set[Tuple[str, str]] = set()

        for file_path, (_gff, extracted, file_ext) in extracted_map.items():
            if file_path in dialog_files:
                continue
            for item in extracted.items:
                if not item.has_text():
                    continue
                translated = all_translations.get(item.text)
                if translated is None and (item.metadata or {}).get("type") == "ncs_string":
                    translated = manager.ncs_translations_by_item_id.get(
                        item.item_id or ""
                    )
                if translated is None:
                    continue
                if (item.metadata or {}).get("type") == "ncs_string" and item.item_id:
                    log_key: Tuple[str, str] = (file_path.name, item.item_id)
                else:
                    log_key = (file_path.name, item.text)
                if log_key in already_logged:
                    continue
                already_logged.add(log_key)
                manager.log_per_file_item(
                    original=item.text,
                    translated=translated,
                    context=item.context,
                    source_filename=file_path.name,
                    item_id=item.item_id
                    if (item.metadata or {}).get("type") == "ncs_string"
                    else None,
                )

    def _patch_git_files(
        self,
        extract_dir: Path,
        translations: Dict[str, str],
    ) -> None:
        """Patch .git area instance files with accumulated translations.

        Args:
            extract_dir: Directory containing extracted module files.
            translations: Session-wide original-text to translated-text mapping.
        """
        git_files = list(extract_dir.glob("*.git"))
        if not git_files:
            logger.debug("No .git files found in extraction directory")
            return

        logger.info(f"Patching {len(git_files)} area instance (.git) files...")
        total_patched = 0
        for git_path in git_files:
            try:
                gff_cached = read_gff(
                    git_path, tlk=self.tlk, cache=self._gff_cache
                )
                patched = patch_git_file(
                    git_path,
                    translations,
                    tlk=self.tlk,
                    parsed_data=gff_cached,
                    text_encoding=module_string_encoding_for_target_lang(
                        self.config.target_lang
                    ),
                )
                total_patched += patched
            except Exception as e:
                logger.error(f"Failed to patch {git_path.name}: {e}")

        if total_patched:
            logger.info(f"Patched {total_patched} instance fields across {len(git_files)} .git files")

    def _sync_manager_stats(self, manager: "TranslationManager") -> None:
        """Merge delta from the shared TranslationManager stats into orchestrator stats.

        Thread-safe: uses ``_stats_lock`` to guard both the read of manager
        cursors and the write to ``self.stats``.
        """
        with self._stats_lock:
            items_now = manager.stats["items_translated"]
            errors_now = len(manager.stats["errors"])
            self.stats["items_translated"] += items_now - self._prev_items
            self.stats["errors"].extend(manager.stats["errors"][self._prev_errors:])
            self._prev_items = items_now
            self._prev_errors = errors_now

    def _resolve_output_path(self, extract_dir: Path) -> Path:
        """Determine the output .mod file path from config or input filename."""
        output_path = self.config.output_file
        if output_path is None:
            from .config import create_output_path
            output_path = create_output_path(self.config.input_file, self.config.target_lang)

        return output_path

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
        """Get translation statistics.

        Returns:
            Statistics dictionary
        """
        return {
            **self.stats,
            "total_errors": len(self.stats["errors"]),
        }


def rebuild_module(
    extract_dir: Path,
    translations: Dict[str, str],
    output_path: Path,
    original_mod_path: Path,
    target_lang: Optional[str] = None,
    ncs_translations_by_item_id: Optional[Dict[str, str]] = None,
) -> Path:
    """Re-inject translations and reassemble a .mod without LLM calls.

    Args:
        extract_dir: Directory with previously extracted GFF files.
        translations: Mapping of original text to (possibly edited) translated text.
        output_path: Where to write the rebuilt .mod file.
        original_mod_path: Path to the original .mod (needed for ERF header info).
        ncs_translations_by_item_id: Optional per-``item_id`` NCS strings (required when
            bytecode already contains translated text so *translations* cannot be
            resolved by original substring).

    Returns:
        Path to the rebuilt .mod file.
    """
    from .file_handlers.tlk_reader import parse_tlk, find_dialog_tlk

    # Load TLK if available
    tlk = None
    tlk_path = find_dialog_tlk(extract_dir)
    if tlk_path and tlk_path.exists():
        try:
            tlk = parse_tlk(tlk_path)
        except Exception:
            pass

    gff_cache: Dict[Tuple[Path, int], Dict[str, Any]] = {}
    text_enc = module_string_encoding_for_target_lang(target_lang)

    # Inject translations into each translatable file
    for file_path in extract_dir.rglob("*"):
        if not file_path.is_file():
            continue
        ext = file_path.suffix.lower()
        if ext not in TRANSLATABLE_TYPES:
            continue

        try:
            loaded = load_parsed_and_extracted(file_path, ext, tlk, gff_cache)
        except Exception as e:
            logger.warning("Failed to read %s during rebuild: %s", file_path.name, e)
            continue
        if loaded is None:
            continue
        parsed_data, extracted = loaded
        inject_translations_into_file(
            file_path,
            parsed_data,
            extracted,
            translations,
            ncs_translations_by_item_id=ncs_translations_by_item_id,
            log_updates=False,
            target_lang=target_lang,
        )

    # Patch .git area instance files
    from .injectors.git_injector import patch_git_file
    for git_path in extract_dir.glob("*.git"):
        try:
            parsed_data = read_gff(git_path, tlk=tlk, cache=gff_cache)
            patch_git_file(
                git_path,
                translations,
                tlk=tlk,
                parsed_data=parsed_data,
                text_encoding=text_enc,
            )
        except Exception as e:
            logger.warning("Failed to patch %s during rebuild: %s", git_path.name, e)

    # Reassemble .mod
    create_mod_from_directory(extract_dir, output_path, original_mod_path)
    logger.info("Rebuild complete: %s", output_path)
    return output_path


def translate_module(config: TranslationConfig) -> Path:
    """Translate a NWN module.

    Args:
        config: Translation configuration

    Returns:
        Path to translated module file

    Raises:
        ValueError: If configuration is invalid
        Exception: If translation fails
    """
    # Validate configuration
    if not config.input_file.exists():
        raise ValueError(f"Input file not found: {config.input_file}")

    if config.input_file.suffix.lower() not in [".mod", ".erf", ".hak"]:
        raise ValueError(f"Input file must be a .mod, .erf, or .hak file")

    # Get API key
    config.api_key = config.get_api_key()

    # Create translator and translate
    translator = ModuleTranslator(config)
    return translator.translate()
