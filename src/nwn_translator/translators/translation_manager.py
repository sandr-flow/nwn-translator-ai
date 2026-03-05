"""Translation manager for orchestrating the translation process.

This module manages the translation workflow, coordinating between extractors,
AI providers, and injectors.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from tqdm import tqdm

from ..config import TranslationConfig
from ..extractors import get_extractor_for_file, ExtractedContent
from ..injectors import get_injector_for_content
from ..ai_providers import BaseAIProvider, TranslationItem, TranslationResult
from .token_handler import TokenHandler, sanitize_text, restore_text

logger = logging.getLogger(__name__)


class TranslationManager:
    """Manager for the translation process."""

    def __init__(self, config: TranslationConfig, provider: BaseAIProvider):
        """Initialize translation manager.

        Args:
            config: Translation configuration
            provider: AI provider instance
        """
        self.config = config
        self.provider = provider
        self.token_handler = TokenHandler(preserve_standard_tokens=config.preserve_tokens)

        # Statistics
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "cache_hits": 0,
            "errors": [],
        }

        # Global cache for this translation session
        # sanitized_text -> translated_text
        self._translation_cache: Dict[str, str] = {}

    def translate_content(
        self,
        extracted_content: ExtractedContent
    ) -> Dict[str, str]:
        """Translate all items in extracted content.

        Args:
            extracted_content: ExtractedContent with items to translate

        Returns:
            Dictionary mapping original text to translated text
        """
        return self._translate_items(extracted_content)



    def _translate_items(self, content: ExtractedContent) -> Dict[str, str]:
        """Translate multiple items individually, skipping duplicates.

        Items with the same text are translated only once; subsequent
        occurrences reuse the cached result, saving API calls.

        Args:
            content: ExtractedContent with items

        Returns:
            Translation mapping (original text → translated text)
        """
        translations = {}
        items = [item for item in content.items if item.has_text()]

        if not items:
            return translations

        # Prepare translation items (sanitize tokens)
        translation_items = []
        for item in items:
            sanitized, handler = sanitize_text(
                item.text,
                preserve_tokens=self.config.preserve_tokens
            )
            translation_items.append({
                "item": item,
                "sanitized": sanitized,
                "handler": handler,
            })

        # Per-session cache check
        # Avoids duplicate API calls when the same string appears multiple times across files.

        for item_data in tqdm(
            translation_items,
            desc=f"Translating {content.content_type}",
            disable=self.config.quiet,
            leave=False,
        ):
            item = item_data["item"]
            sanitized = item_data["sanitized"]
            handler = item_data["handler"]

            # Cache hit?
            if sanitized in self._translation_cache:
                translated = restore_text(self._translation_cache[sanitized], handler)
                translations[item.text] = translated
                self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
                logger.debug("Cache hit for '%s…'", sanitized[:40])
                continue

            try:
                result = self.provider.translate(
                    text=sanitized,
                    source_lang=self.config.source_lang,
                    target_lang=self.config.target_lang,
                    context=item.context,
                )

                if result.success:
                    self._translation_cache[sanitized] = result.translated
                    translated = restore_text(result.translated, handler)
                    translations[item.text] = translated
                    self.stats["items_translated"] += 1
                    
                    # Log translation to JSONL if enabled
                    if self.config.translation_log:
                        try:
                            # Use dict representation
                            log_entry = {
                                "original": item.text,
                                "translated": translated,
                                "context": item.context,
                                "model": result.metadata.get("model", self.config.model)
                            }
                            with open(self.config.translation_log, "a", encoding="utf-8") as f:
                                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                        except Exception as log_e:
                            logger.debug("Failed to write to translation log: %s", log_e)
                else:
                    error_msg = f"Translation failed for {item.item_id}: {result.error}"
                    self.stats["errors"].append(error_msg)
                    logger.warning(error_msg)

            except Exception as e:
                error_msg = f"Translation error for {item.item_id}: {e}"
                self.stats["errors"].append(error_msg)
                logger.warning(error_msg)

        return translations

    def get_statistics(self) -> Dict[str, Any]:
        """Get translation statistics.

        Returns:
            Dictionary with translation statistics
        """
        return {
            **self.stats,
            "total_errors": len(self.stats["errors"]),
        }

    def get_all_translations(self) -> Dict[str, str]:
        """Get all cached translations from this session.

        Returns:
            Dictionary mapping sanitized original text to translated text.
        """
        return dict(self._translation_cache)

    def clear_statistics(self) -> None:
        """Clear translation statistics."""
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "cache_hits": 0,
            "errors": [],
        }


def translate_file(
    file_path: Path,
    gff_data: Dict[str, Any],
    config: TranslationConfig,
    provider: BaseAIProvider,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Translate a single file.

    Args:
        file_path: Path to the file
        gff_data: Parsed GFF data
        config: Translation configuration
        provider: AI provider instance

    Returns:
        Tuple of (translated_gff_data, statistics)
    """
    # Get file extension
    file_ext = file_path.suffix.lower()

    # Get appropriate extractor
    extractor = get_extractor_for_file(file_ext)
    if not extractor:
        logger.warning(f"No extractor found for {file_ext}")
        return gff_data, {}

    # Extract content
    extracted = extractor.extract(file_path, gff_data)
    if not extracted.items:
        logger.info(f"No translatable content found in {file_path}")
        return gff_data, {}

    # Translate
    manager = TranslationManager(config, provider)
    translations = manager.translate_content(extracted)

    if not translations:
        logger.warning(f"No translations generated for {file_path}")
        return gff_data, manager.get_statistics()

    # Inject translations
    injector = get_injector_for_content(extracted.content_type)
    if injector:
        inject_metadata = {**(extracted.metadata or {}), "type": extracted.content_type}
        result = injector.inject(file_path, gff_data, translations, inject_metadata)

        if result.modified:
            logger.info(f"Updated {result.items_updated} items in {file_path}")

    return gff_data, manager.get_statistics()
