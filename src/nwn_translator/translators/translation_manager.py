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

    def translate_content(self, content: ExtractedContent) -> Dict[str, str]:
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

    # Maximum characters for a string to be considered "short" (eligible for batching)
    _BATCH_SHORT_THRESHOLD = 50
    # Maximum items per batch API call
    _BATCH_SIZE = 15

    # Timeout (seconds) for a single async translation call.
    _ITEM_TIMEOUT: float = 120.0
    # Timeout (seconds) for a batch translation call.
    _BATCH_CALL_TIMEOUT: float = 180.0
    # Overall timeout (seconds) for async.gather of all items in one file.
    _GATHER_TIMEOUT: float = 600.0
    # run_async outer timeout (slightly above _GATHER_TIMEOUT).
    _RUN_ASYNC_TIMEOUT: float = 660.0

    # Item types that are safe for batching (short names/labels only)
    _BATCHABLE_TYPES = frozenset({
        "creature_first_name", "creature_last_name",
        "item_name", "area_name",
        "trigger_name", "placeable_name", "door_name", "store_name",
    })

    @staticmethod
    def _is_short_item(item_data: dict) -> bool:
        """Check if an item is short and batchable (names/labels only)."""
        sanitized = item_data["sanitized"]
        item = item_data["item"]
        if "\n" in sanitized or len(sanitized) > TranslationManager._BATCH_SHORT_THRESHOLD:
            return False
        item_type = (item.metadata or {}).get("type", "")
        return item_type in TranslationManager._BATCHABLE_TYPES

    async def _translate_one_async(
        self,
        sem: asyncio.Semaphore,
        item_data: dict,
    ) -> TranslationResult:
        """Translate a single item with semaphore and timeout."""
        item = item_data["item"]
        sanitized = item_data["sanitized"]
        async with sem:
            try:
                return await asyncio.wait_for(
                    self.provider.translate_async(
                        text=sanitized,
                        source_lang=self.config.source_lang,
                        target_lang=self.config.target_lang,
                        context=item.context,
                        glossary_block=self._glossary_prompt_block or None,
                    ),
                    timeout=self._ITEM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Translate timeout (%.0fs) for '%s…'",
                    self._ITEM_TIMEOUT,
                    sanitized[:40],
                )
                return TranslationResult(
                    translated="",
                    original=sanitized,
                    success=False,
                    error=f"Timeout after {self._ITEM_TIMEOUT}s",
                    metadata={},
                )
            except Exception as e:
                return TranslationResult(
                    translated="",
                    original=sanitized,
                    success=False,
                    error=str(e),
                    metadata={},
                )

    def _translate_uncached_concurrent(
        self,
        uncached_items: List[dict],
        translations: Dict[str, str],
    ) -> None:
        """Translate items without cache hits (concurrent async API calls).

        Short, single-line items are grouped into batches for fewer API calls.
        Longer items are translated individually with full context.
        """
        short_items = [d for d in uncached_items if self._is_short_item(d)]
        long_items = [d for d in uncached_items if not self._is_short_item(d)]

        async def run_all() -> tuple:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)

            long_coros = [self._translate_one_async(sem, d) for d in long_items]

            # --- Batch translation for short items ---
            batch_size = self._BATCH_SIZE
            batches = [
                short_items[i:i + batch_size]
                for i in range(0, len(short_items), batch_size)
            ]

            async def batch_one(batch: List[dict]) -> List[TranslationResult]:
                batch_items = [
                    TranslationItem(
                        original=d["sanitized"],
                        context=d["item"].context,
                        metadata=d["item"].metadata or {},
                    )
                    for d in batch
                ]
                async with sem:
                    try:
                        return await asyncio.wait_for(
                            self.provider.translate_batch_async(
                                items=batch_items,
                                source_lang=self.config.source_lang,
                                target_lang=self.config.target_lang,
                                glossary_block=self._glossary_prompt_block or None,
                            ),
                            timeout=self._BATCH_CALL_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Batch translate timeout (%.0fs) for %d items",
                            self._BATCH_CALL_TIMEOUT,
                            len(batch_items),
                        )
                        return [
                            TranslationResult(
                                translated="",
                                original=bi.original,
                                success=False,
                                error=f"Batch timeout after {self._BATCH_CALL_TIMEOUT}s",
                                metadata={},
                            )
                            for bi in batch_items
                        ]
                    except Exception as e:
                        return [
                            TranslationResult(
                                translated="",
                                original=bi.original,
                                success=False,
                                error=str(e),
                                metadata={},
                            )
                            for bi in batch_items
                        ]

            batch_coros = [batch_one(b) for b in batches]

            try:
                long_results = (
                    await asyncio.wait_for(
                        asyncio.gather(*long_coros),
                        timeout=self._GATHER_TIMEOUT,
                    )
                    if long_coros
                    else []
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Overall gather timeout (%.0fs) for %d long items",
                    self._GATHER_TIMEOUT,
                    len(long_coros),
                )
                long_results = [
                    TranslationResult(
                        translated="", original=d["sanitized"],
                        success=False, error="Gather timeout",
                    )
                    for d in long_items
                ]

            try:
                batch_results_nested = (
                    await asyncio.wait_for(
                        asyncio.gather(*batch_coros),
                        timeout=self._GATHER_TIMEOUT,
                    )
                    if batch_coros
                    else []
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Overall gather timeout (%.0fs) for %d batch coros",
                    self._GATHER_TIMEOUT,
                    len(batch_coros),
                )
                batch_results_nested = [
                    [
                        TranslationResult(
                            translated="", original=d["sanitized"],
                            success=False, error="Gather timeout",
                        )
                        for d in batch
                    ]
                    for batch in batches
                ]

            # Flatten batch results
            batch_results = [r for group in batch_results_nested for r in group]

            return long_results, batch_results

        from ..async_utils import run_async
        long_results, batch_results = run_async(
            run_all(),
            cleanup=self.provider.close_async_client,
            timeout=self._RUN_ASYNC_TIMEOUT,
        )

        if short_items:
            logger.info(
                "Batch-translated %d short items in %d batch(es), "
                "%d long items individually",
                len(short_items),
                (len(short_items) + self._BATCH_SIZE - 1) // self._BATCH_SIZE,
                len(long_items),
            )

        # Process long results
        for item_data, result in zip(long_items, long_results):
            self._process_translation_result(item_data, result, translations)

        # Process batch results; collect failures for individual retry
        retry_items: List[dict] = []
        for item_data, result in zip(short_items, batch_results):
            if result.success:
                self._process_translation_result(item_data, result, translations)
            else:
                retry_items.append(item_data)

        # Fallback: retry failed batch items individually
        if retry_items:
            logger.info(
                "Retrying %d failed batch items individually", len(retry_items),
            )
            self._translate_individual_fallback(retry_items, translations)

    def _translate_individual_fallback(
        self,
        items: List[dict],
        translations: Dict[str, str],
    ) -> None:
        """Translate items individually as fallback for failed batch items."""

        async def run_fallback() -> List[TranslationResult]:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)

            try:
                return await asyncio.wait_for(
                    asyncio.gather(*[self._translate_one_async(sem, d) for d in items]),
                    timeout=self._GATHER_TIMEOUT / 2,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Fallback gather timeout (%.0fs) for %d items",
                    self._GATHER_TIMEOUT / 2,
                    len(items),
                )
                return [
                    TranslationResult(
                        translated="", original=d["sanitized"],
                        success=False, error="Fallback gather timeout",
                    )
                    for d in items
                ]

        from ..async_utils import run_async
        results = run_async(
            run_fallback(),
            cleanup=self.provider.close_async_client,
            timeout=self._RUN_ASYNC_TIMEOUT / 2,
        )

        for item_data, result in zip(items, results):
            self._process_translation_result(item_data, result, translations)

    def _process_translation_result(
        self,
        item_data: dict,
        result: TranslationResult,
        translations: Dict[str, str],
    ) -> None:
        """Process a single translation result (shared by individual and batch paths)."""
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


