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

from .config import TranslationConfig, TRANSLATABLE_TYPES
from .file_handlers import (
    ERFReader,
    create_mod_from_directory,
    read_gff,
)
from .file_handlers.tlk_reader import parse_tlk, find_dialog_tlk, TLKFile
from .extractors import get_extractor_for_file
from .injectors import get_injector_for_content
from .extractors.base import ExtractedContent, TranslatableItem
from .injectors.git_injector import (
    collect_git_strings_missing_from_translations,
    patch_git_file,
)
from .ai_providers import create_provider
from .translators.translation_manager import TranslationManager
from .translators.context_translator import ContextualTranslationManager
from .context.world_context import WorldScanner, WorldContext

logger = logging.getLogger(__name__)


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
        )

        # Statistics
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "errors": [],
        }
        
        # TLK file for resolving StrRef names
        self.tlk: Optional[TLKFile] = None

        # World context cache
        self.world_context: Optional[WorldContext] = None
        #: Per-run GFF parse cache: (resolved_path, tlk_id) -> dict
        self._gff_cache: Dict[Tuple[Path, int], Dict[str, Any]] = {}

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

        # Step 2: Find translatable files
        logger.info("Finding translatable files...")
        translatable_files = self._find_translatable_files(extract_dir)
        logger.info(f"Found {len(translatable_files)} translatable files")

        if not translatable_files:
            logger.warning("No translatable files found!")
            return self._cleanup_and_return(extract_dir)

        # Session GFF cache (world scan + translation + .git)
        self._gff_cache = {}

        # Step 2.5: Load TLK file for resolving StrRef names
        self._load_tlk(extract_dir)

        # Step 2.6: Build World Context (if enabled)
        if self.config.use_context:
            scanner = WorldScanner()
            self.world_context = scanner.scan_directory(
                extract_dir, tlk=self.tlk, gff_cache=self._gff_cache
            )

        # Step 3: Process each file
        logger.info("Translating files...")

        # Initialize single translation managers for the whole session
        manager = TranslationManager(self.config, self.provider)
        context_manager = (
            ContextualTranslationManager(
                self.config,
                self.provider,
                self.world_context,
                translation_cache=manager._translation_cache,
            )
            if self.config.use_context
            else None
        )

        # Accumulate all translations (original_text -> translated_text)
        all_translations: Dict[str, str] = {}

        total_files = len(translatable_files)
        file_iterator = (
            tqdm(translatable_files, desc="Translating")
            if self.config.progress_callback is None
            else translatable_files
        )
        for idx, file_path in enumerate(file_iterator):
            if self.config.progress_callback is not None:
                self.config.progress_callback(
                    "translating", idx, total_files, file_path.name
                )
            try:
                file_translations = self._translate_file(file_path, manager, context_manager)
                if file_translations:
                    all_translations.update(file_translations)
                self.stats["files_processed"] += 1
            except Exception as e:
                error_msg = f"Error processing {file_path.name}: {e}"
                self.stats["errors"].append(error_msg)
                logger.error(error_msg)

        # Step 3.5: Extract + translate strings that exist only in .git instances
        git_translations = self._translate_git_instances(
            extract_dir, all_translations, manager
        )
        if git_translations:
            all_translations.update(git_translations)

        # Step 3.6: Patch .git area instance files
        if all_translations:
            self._patch_git_files(extract_dir, all_translations)

        # Step 4: Create new module
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

    def _translate_file(
        self, 
        file_path: Path, 
        manager: TranslationManager, 
        context_manager: Optional[ContextualTranslationManager] = None
    ) -> Optional[Dict[str, str]]:
        """Translate a single file.

        Args:
            file_path: Path to the file
            manager: Standard translation manager instance
            context_manager: Contextual translation manager instance

        Returns:
            Dictionary mapping original text to translated text, or None.
        """
        # Read GFF data (pass TLK to resolve StrRef-only names)
        gff_data = read_gff(file_path, tlk=self.tlk, cache=self._gff_cache)

        # Get file extension
        file_ext = file_path.suffix.lower()

        # Get appropriate extractor
        extractor = get_extractor_for_file(file_ext)
        if not extractor:
            logger.debug(f"No extractor for {file_ext}: {file_path.name}")
            return None

        # Extract content
        extracted = extractor.extract(file_path, gff_data)
        if not extracted.items:
            logger.debug(f"No translatable content in: {file_path.name}")
            return None

        # Translate
        if file_ext == ".dlg" and self.config.use_context and context_manager:
            # Use contextual translation for full dialog trees
            translations = context_manager.translate_dialog(file_path, gff_data)
        else:
            # Use standard line-by-line translation for everything else
            translations = manager.translate_content(extracted)

        if not translations:
            logger.debug(f"No translations generated for: {file_path.name}")
            return None

        # Update statistics
        stats = manager.get_statistics()
        self.stats["items_translated"] += stats.get("items_translated", 0)
        self.stats["errors"].extend(stats.get("errors", []))

        # Inject translations
        injector = get_injector_for_content(extracted.content_type)
        if injector:
            inject_metadata = {**(extracted.metadata or {}), "type": extracted.content_type}
            result = injector.inject(file_path, gff_data, translations, inject_metadata)

            if result.modified:
                logger.info(f"Updated {file_path.name}: {result.items_updated} items")

        return translations

    def _translate_git_instances(
        self,
        extract_dir: Path,
        all_translations: Dict[str, str],
        manager: TranslationManager,
    ) -> Dict[str, str]:
        """Collect locstrings from .git files missing from *all_translations*, translate them."""
        git_files = list(extract_dir.glob("*.git"))
        if not git_files:
            return {}

        pending: Set[str] = set()
        for git_path in git_files:
            try:
                gff_data = read_gff(git_path, tlk=self.tlk, cache=self._gff_cache)
            except Exception as e:
                logger.error("Failed to read %s for git string collection: %s", git_path.name, e)
                continue
            pending |= collect_git_strings_missing_from_translations(
                gff_data, all_translations
            )

        if not pending:
            return {}

        logger.info(
            "Translating %d unique strings from .git instance files...",
            len(pending),
        )

        items = [
            TranslatableItem(
                text=text,
                context="Area instance (.git)",
                item_id=f"git_instance:{text[:48]}",
                location=".git",
                metadata={"type": "git_instance_string"},
            )
            for text in sorted(pending)
        ]
        extracted = ExtractedContent(
            content_type="git_instance",
            items=items,
            source_file=extract_dir,
            metadata={"type": "git_instance"},
        )

        items_before = manager.stats["items_translated"]
        errors_before = len(manager.stats["errors"])
        new_map = manager.translate_content(extracted)
        self.stats["items_translated"] += manager.stats["items_translated"] - items_before
        self.stats["errors"].extend(manager.stats["errors"][errors_before:])

        return new_map

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
                    gff_data=gff_cached,
                )
                total_patched += patched
            except Exception as e:
                logger.error(f"Failed to patch {git_path.name}: {e}")

        if total_patched:
            logger.info(f"Patched {total_patched} instance fields across {len(git_files)} .git files")

    def _cleanup_and_return(self, extract_dir: Path) -> Path:
        """Handle cleanup and return output path.

        Args:
            extract_dir: Extraction directory
        """
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
