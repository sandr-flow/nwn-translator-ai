"""Translation manager for orchestrating the translation process.

This module manages the translation workflow, coordinating between extractors,
AI providers, and injectors.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from tqdm import tqdm

from ..config import TranslationConfig
from ..translation_logging import translation_log_writer_for_config

if TYPE_CHECKING:
    from ..glossary import Glossary
from ..extractors import get_extractor_for_file, ExtractedContent
from ..injectors import get_injector_for_content
from ..ai_providers import BaseAIProvider, TranslationItem, TranslationResult
from .token_handler import TokenHandler, sanitize_text, restore_text

logger = logging.getLogger(__name__)


class TranslationManager:
    """Manager for the translation process."""

    def __init__(
        self,
        config: TranslationConfig,
        provider: BaseAIProvider,
        glossary: Optional["Glossary"] = None,
    ):
        """Initialize translation manager.

        Args:
            config: Translation configuration
            provider: AI provider instance
            glossary: Optional pre-built proper-name glossary for prompts and cache seeding
        """
        self.config = config
        self.provider = provider
        self.glossary = glossary
        self._glossary_prompt_block = (
            glossary.to_prompt_block()
            if glossary and getattr(glossary, "entries", None)
            else ""
        )
        self._log_writer = translation_log_writer_for_config(
            config.translation_log,
            config.translation_log_writer,
        )
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
        self.translation_cache: Dict[str, str] = {}
        self._stats_lock = threading.Lock()
        if glossary:
            glossary.seed_cache(
                self.translation_cache,
                preserve_tokens=config.preserve_tokens,
            )

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

        use_tqdm = (
            not self.config.quiet
            and self.config.progress_callback is None
        )
        iterable = (
            tqdm(
                translation_items,
                desc=f"Translating {content.content_type}",
                disable=False,
                leave=False,
            )
            if use_tqdm
            else translation_items
        )
        n_items = len(translation_items)
        uncached_items: List[dict] = []

        for idx, item_data in enumerate(iterable):
            if self.config.progress_callback is not None:
                self.config.progress_callback(
                    "translating_item",
                    idx,
                    n_items,
                    content.content_type,
                )
            item = item_data["item"]
            sanitized = item_data["sanitized"]
            handler = item_data["handler"]

            # Cache hit?
            if sanitized in self.translation_cache:
                translated = restore_text(self.translation_cache[sanitized], handler)
                translations[item.text] = translated
                with self._stats_lock:
                    self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
                logger.debug("Cache hit for '%s…'", sanitized[:40])
                continue

            uncached_items.append(item_data)

        if uncached_items:
            self._translate_uncached_concurrent(uncached_items, translations)

        return translations

    def _translate_uncached_concurrent(
        self,
        uncached_items: List[dict],
        translations: Dict[str, str],
    ) -> None:
        """Translate items without cache hits (concurrent async API calls)."""

        async def run_all() -> List[TranslationResult]:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)

            async def one(item_data: dict) -> TranslationResult:
                item = item_data["item"]
                sanitized = item_data["sanitized"]
                async with sem:
                    try:
                        return await self.provider.translate_async(
                            text=sanitized,
                            source_lang=self.config.source_lang,
                            target_lang=self.config.target_lang,
                            context=item.context,
                            glossary_block=self._glossary_prompt_block or None,
                        )
                    except Exception as e:
                        return TranslationResult(
                            translated="",
                            original=sanitized,
                            success=False,
                            error=str(e),
                            metadata={},
                        )

            return await asyncio.gather(*[one(d) for d in uncached_items])

        from ..async_utils import run_async
        results = run_async(run_all(), cleanup=self.provider.close_async_client)

        for item_data, result in zip(uncached_items, results):
            item = item_data["item"]
            sanitized = item_data["sanitized"]
            handler = item_data["handler"]

            if result.success:
                with self._stats_lock:
                    self.translation_cache[sanitized] = result.translated
                    translated = restore_text(result.translated, handler)
                    translations[item.text] = translated
                    self.stats["items_translated"] += 1

                log_entry = {
                    "original": item.text,
                    "translated": translated,
                    "context": item.context,
                    "model": result.metadata.get("model", self.config.model),
                }
                try:
                    self._log_writer.write(log_entry)
                except Exception as log_e:
                    logger.debug("Failed to write to translation log: %s", log_e)
            else:
                error_msg = f"Translation failed for {item.item_id}: {result.error}"
                with self._stats_lock:
                    self.stats["errors"].append(error_msg)
                logger.warning(error_msg)

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
        return dict(self.translation_cache)

    def close(self) -> None:
        """Close the persistent event loop."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def clear_statistics(self) -> None:
        """Clear translation statistics."""
        self.stats = {
            "files_processed": 0,
            "items_translated": 0,
            "cache_hits": 0,
            "errors": [],
        }

