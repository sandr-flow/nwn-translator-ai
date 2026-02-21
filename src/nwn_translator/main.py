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
from typing import Dict, List, Optional, Any

from tqdm import tqdm

from .config import TranslationConfig, TRANSLATABLE_TYPES
from .file_handlers import (
    ERFReader,
    ERFWriter,
    create_mod_from_directory,
    read_gff,
    write_gff,
)
from .extractors import get_extractor_for_file
from .injectors import get_injector_for_content
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
            config.provider,
            config.api_key,
            config.model,
        )

        # Statistics
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "errors": [],
        }
        
        # World context cache
        self.world_context: Optional[WorldContext] = None

    def translate(self) -> Path:
        """Translate the module.

        Returns:
            Path to translated .mod file

        Raises:
            Exception: If translation fails
        """
        logger.info(f"Starting translation of {self.config.input_file}")
        logger.info(f"Target language: {self.config.target_lang}")
        logger.info(f"Provider: {self.config.provider} ({self.config.model})")

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

        # Step 2.5: Build World Context (if enabled)
        if self.config.use_context:
            scanner = WorldScanner()
            self.world_context = scanner.scan_directory(extract_dir)

        # Step 3: Process each file
        logger.info("Translating files...")

        # Initialize single translation managers for the whole session
        manager = TranslationManager(self.config, self.provider)
        context_manager = ContextualTranslationManager(self.config, self.provider, self.world_context) if self.config.use_context else None

        for file_path in tqdm(translatable_files, desc="Translating"):
            try:
                self._translate_file(file_path, manager, context_manager)
                self.stats["files_processed"] += 1
            except Exception as e:
                error_msg = f"Error processing {file_path.name}: {e}"
                self.stats["errors"].append(error_msg)
                logger.error(error_msg)

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

        reader = ERFReader(self.config.input_file)
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

    def _translate_file(
        self, 
        file_path: Path, 
        manager: TranslationManager, 
        context_manager: Optional[ContextualTranslationManager] = None
    ) -> None:
        """Translate a single file.

        Args:
            file_path: Path to the file
            manager: Standard translation manager instance
            context_manager: Contextual translation manager instance
        """
        # Read GFF data
        gff_data = read_gff(file_path)

        # Get file extension
        file_ext = file_path.suffix.lower()

        # Get appropriate extractor
        extractor = get_extractor_for_file(file_ext)
        if not extractor:
            logger.debug(f"No extractor for {file_ext}: {file_path.name}")
            return

        # Extract content
        extracted = extractor.extract(file_path, gff_data)
        if not extracted.items:
            logger.debug(f"No translatable content in: {file_path.name}")
            return

        # Translate
        if file_ext == ".dlg" and self.config.use_context and context_manager:
            # Use contextual translation for full dialog trees
            translations = context_manager.translate_dialog(file_path, gff_data)
        else:
            # Use standard line-by-line translation for everything else
            translations = manager.translate_content(extracted)

        if not translations:
            logger.debug(f"No translations generated for: {file_path.name}")
            return

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
