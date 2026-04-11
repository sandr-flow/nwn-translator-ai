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
from ..extractors import ExtractedContent
from ..ai_providers import BaseAIProvider, TranslationItem, TranslationResult
from .prefix_translation_cache import PrefixAwareTranslationCache
from .token_handler import TokenHandler, sanitize_text, restore_text

# Minimum length (characters) for a cached key to qualify as a prefix match.
_MIN_PREFIX_LEN = 20

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
        # sanitized_text -> translated_text (with trie for longest-prefix hits)
        self.translation_cache = PrefixAwareTranslationCache()
        self._stats_lock = threading.Lock()
        if glossary:
            glossary.seed_cache(
                self.translation_cache,
                preserve_tokens=config.preserve_tokens,
            )

        #: Final NCS translations keyed by :attr:`TranslatableItem.item_id` (per bytecode offset).
        self.ncs_translations_by_item_id: Dict[str, str] = {}
        #: LLM gate (or deterministic bypass) approval per ``item_id`` for ``ncs_string`` items.
        self._ncs_gate_approval: Dict[str, bool] = {}

    def log_per_file_item(
        self,
        *,
        original: str,
        translated: str,
        context: Optional[str],
        source_filename: str,
    ) -> None:
        """Write one translation log row for web per-file grouping (non-dialog paths)."""
        try:
            self._log_writer.write(
                {
                    "original": original,
                    "translated": translated,
                    "context": context,
                    "model": self.config.model,
                    "file": source_filename,
                }
            )
        except Exception:
            pass

    def _find_cached_prefix(self, sanitized: str) -> Optional[tuple]:
        """Find the longest cached text that is a prefix of *sanitized*.

        This enables incremental translation for journal entries where each
        successive entry appends new text to the previous one.

        Returns:
            ``(cached_key, cached_translation)`` or *None*.
        """
        return self.translation_cache.longest_prefix_match(sanitized, _MIN_PREFIX_LEN)

    def _ncs_item_passes_gate(self, item) -> bool:
        if (item.metadata or {}).get("type") != "ncs_string":
            return True
        return self._ncs_gate_approval.get(item.item_id or "", False)

    def _run_ncs_llm_gate(self, translation_items: List[dict]) -> None:
        """Populate :attr:`_ncs_gate_approval` for all ``ncs_string`` items."""
        self._ncs_gate_approval.clear()
        pending: List[dict] = []
        for itd in translation_items:
            item = itd["item"]
            meta = item.metadata or {}
            if meta.get("type") != "ncs_string":
                continue
            iid = item.item_id or ""
            if self.config.skip_ncs_llm_gate or not meta.get("needs_llm_gate", True):
                self._ncs_gate_approval[iid] = True
            else:
                pending.append(itd)

        if not pending:
            return

        batches = [
            pending[i : i + self._BATCH_SIZE]
            for i in range(0, len(pending), self._BATCH_SIZE)
        ]

        async def run_batches() -> None:
            for batch in batches:
                entries = []
                for j, itd in enumerate(batch):
                    item = itd["item"]
                    meta = item.metadata or {}
                    loc = item.location or ""
                    file_name = Path(loc).name if loc else ""
                    off = meta.get("offset")
                    off_s = hex(off) if isinstance(off, int) else str(off)
                    entries.append({
                        "key": str(j),
                        "text": item.text,
                        "file": file_name,
                        "offset": off_s,
                        "hint": str(meta.get("ncs_hint", "unknown")),
                    })
                partial = await self.provider.classify_ncs_translate_gate_batch_async(
                    entries,
                    source_lang=self.config.source_lang,
                )
                for j, itd in enumerate(batch):
                    iid = itd["item"].item_id or ""
                    self._ncs_gate_approval[iid] = partial.get(str(j), False)

        from ..async_utils import run_async
        run_async(
            run_batches(),
            cleanup=self.provider.close_async_client,
            timeout=self._RUN_ASYNC_TIMEOUT,
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

        self.ncs_translations_by_item_id.clear()

        source_filename = Path(content.source_file).name if content.source_file else None

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

        self._run_ncs_llm_gate(translation_items)

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

            if not self._ncs_item_passes_gate(item):
                continue

            # Cache hit?
            if sanitized in self.translation_cache:
                translated = restore_text(self.translation_cache[sanitized], handler)
                translations[item.text] = translated
                if (item.metadata or {}).get("type") == "ncs_string" and item.item_id:
                    self.ncs_translations_by_item_id[item.item_id] = translated
                with self._stats_lock:
                    self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
                logger.debug("Cache hit for '%s…'", sanitized[:40])
                continue

            # Prefix-cache hit (journal entries that extend earlier text)
            prefix_match = self._find_cached_prefix(sanitized)
            if prefix_match is not None:
                prefix_key, prefix_translation = prefix_match
                # Only the new tail needs translating — queue it
                item_data["_prefix_key"] = prefix_key
                item_data["_prefix_translation"] = prefix_translation
                logger.debug(
                    "Prefix cache match (%d chars) for '%s…'",
                    len(prefix_key), sanitized[:40],
                )

            uncached_items.append(item_data)

        if uncached_items:
            # Deduplicate: only send one API call per unique sanitized text.
            # _process_translation_result writes to translations[item.text]
            # and to self.translation_cache[sanitized], so duplicates with
            # the same original text are covered automatically.
            seen: set = set()
            unique_items: List[dict] = []
            for item_data in uncached_items:
                item = item_data["item"]
                key = item_data.get("_original_sanitized") or item_data["sanitized"]
                if (item.metadata or {}).get("type") == "ncs_string":
                    key = ("ncs", item.item_id or key)
                if key not in seen:
                    seen.add(key)
                    unique_items.append(item_data)

            if len(unique_items) < len(uncached_items):
                logger.info(
                    "Deduplicated %d items down to %d unique texts",
                    len(uncached_items), len(unique_items),
                )

            self._translate_uncached_concurrent(unique_items, translations, source_filename=source_filename)

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
        source_filename: Optional[str] = None,
    ) -> None:
        """Translate items without cache hits (concurrent async API calls).

        Short, single-line items are grouped into batches for fewer API calls.
        Longer items are translated individually with full context.

        Items with a ``_prefix_key`` are prefix-cache hits: only the new tail
        is sent to the API and the result is reassembled in
        :meth:`_process_translation_result`.
        """
        # For prefix-matched items, swap sanitized text to the tail only
        for d in uncached_items:
            prefix_key = d.get("_prefix_key")
            if prefix_key is not None:
                d["_original_sanitized"] = d["sanitized"]
                d["sanitized"] = d["sanitized"][len(prefix_key):]

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
            self._process_translation_result(item_data, result, translations, source_filename=source_filename)

        # Process batch results; collect failures for individual retry
        retry_items: List[dict] = []
        for item_data, result in zip(short_items, batch_results):
            if result.success:
                self._process_translation_result(item_data, result, translations, source_filename=source_filename)
            else:
                retry_items.append(item_data)

        # Fallback: retry failed batch items individually
        if retry_items:
            logger.info(
                "Retrying %d failed batch items individually", len(retry_items),
            )
            self._translate_individual_fallback(retry_items, translations, source_filename=source_filename)

    def _translate_individual_fallback(
        self,
        items: List[dict],
        translations: Dict[str, str],
        source_filename: Optional[str] = None,
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
            self._process_translation_result(item_data, result, translations, source_filename=source_filename)

    def _process_translation_result(
        self,
        item_data: dict,
        result: TranslationResult,
        translations: Dict[str, str],
        source_filename: Optional[str] = None,
    ) -> None:
        """Process a single translation result (shared by individual and batch paths)."""
        item = item_data["item"]
        sanitized = item_data["sanitized"]
        handler = item_data["handler"]

        if result.success:
            # Reassemble prefix translation if this was a prefix-cache hit
            translated_sanitized = result.translated
            prefix_translation = item_data.get("_prefix_translation")
            original_sanitized = item_data.get("_original_sanitized")
            if prefix_translation is not None and original_sanitized is not None:
                translated_sanitized = prefix_translation + translated_sanitized
                # Cache under the full sanitized key
                sanitized = original_sanitized

            with self._stats_lock:
                self.translation_cache[sanitized] = translated_sanitized
                translated = restore_text(translated_sanitized, handler)
                translations[item.text] = translated
                self.stats["items_translated"] += 1

            if (item.metadata or {}).get("type") == "ncs_string" and item.item_id:
                self.ncs_translations_by_item_id[item.item_id] = translated

            # Prefer per-item location for file attribution (handles merged batches)
            item_filename = (
                Path(item.location).name if item.location else source_filename
            )
            log_entry = {
                "original": item.text,
                "translated": translated,
                "context": item.context,
                "model": result.metadata.get("model", self.config.model),
                "file": item_filename,
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


