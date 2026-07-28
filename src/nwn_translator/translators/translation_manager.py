"""Translation manager for orchestrating the translation process.

This module manages the translation workflow, coordinating between extractors,
AI providers, and injectors.
"""

import asyncio
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, cast

from tqdm import tqdm

from ..config import TranslationCancelled, TranslationConfig
from ..prompts._builder import (
    CONTENT_PROFILE_DEFAULT,
    CONTENT_PROFILE_SCRIPT_MESSAGE,
    CONTENT_PROFILE_SHORT_LABEL,
)
from ..translation_logging import translation_log_writer_for_config
from ..extractors.ncs_extractor import ncs_hard_veto_reason

if TYPE_CHECKING:
    from ..glossary import Glossary
from ..extractors import ExtractedContent
from ..ai_providers import BaseAIProvider, TranslationItem, TranslationResult
from .prefix_translation_cache import PrefixAwareTranslationCache
from .token_handler import TokenHandler, sanitize_text

# Minimum length (characters) for a cached key to qualify as a prefix match.
_MIN_PREFIX_LEN = 20

# Placeholders produced by TokenHandler.sanitize() that carry no translatable
# content and must be stripped before checking if a sanitized string is empty.
_NON_TRANSLATABLE_RE = re.compile(
    r"__(?:NWN_INLINE|NWN_TOKEN)_[A-Za-z0-9_]+__"
    r"|\[\[(?:NWN_INLINE|NWN_TOKEN)_[A-Za-z0-9_]+\]\]"
    r"|<<\[(?:NWN_INLINE|NWN_TOKEN)_[A-Za-z0-9_]+\]>>"
    r"|<\[(?:NWN_INLINE|NWN_TOKEN)_[A-Za-z0-9_]+\]>"
)
_NUMBERED_LABEL_FAMILY_RE = re.compile(
    r"^(?P<base>[A-Z][A-Za-z']+(?: [A-Z][A-Za-z']+){0,4}) (?P<number>\d{1,4})$"
)
_NUMBER_PLACEHOLDER = "__NWN_NUMBER__"
_NUMBERED_TEMPLATE_DENY_BASES = frozenset(
    {
        "Act",
        "Chapter",
        "Day",
        "Level",
        "Part",
        "Quest",
        "Rank",
        "Year",
    }
)
_NUMBERED_TEMPLATE_GENERIC_WORDS = frozenset(
    {
        "Awning",
        "Banner",
        "Barrel",
        "Bed",
        "Bench",
        "Boarded",
        "Boat",
        "Book",
        "Box",
        "Candle",
        "Chair",
        "Chest",
        "Container",
        "Crate",
        "Display",
        "Door",
        "Food",
        "Flower",
        "Guard",
        "Human",
        "Lamp",
        "Lantern",
        "Oak",
        "Patron",
        "Patch",
        "Pipe",
        "Plant",
        "Qube",
        "Rat",
        "Resident",
        "Shelf",
        "Sign",
        "Static",
        "Table",
        "Tree",
        "Underdark",
    }
)


def _is_empty_after_sanitize(sanitized: str) -> bool:
    """True when *sanitized* has no letter/digit once placeholders are removed.

    NWN token placeholders (``<<TOKEN_n>>``) and internal NWN-tag helper
    placeholders are stripped; the remainder is scanned for any Unicode
    letter or digit.  Whitespace, punctuation, and lone underscores do not
    count as translatable content.
    """
    if not sanitized:
        return True
    stripped = _NON_TRANSLATABLE_RE.sub("", sanitized)
    return not re.search(r"[^\W_]", stripped, flags=re.UNICODE)


logger = logging.getLogger(__name__)

_NCS_DIAGNOSTIC_SAMPLE_LIMIT = 50


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
        #: Cached full glossary block (unfiltered); kept as a fallback and for
        #: callers that do not benefit from per-batch filtering.
        self._glossary_prompt_block = (
            glossary.to_prompt_block() if glossary and getattr(glossary, "entries", None) else ""
        )
        self._log_writer = translation_log_writer_for_config(
            config.translation_log,
            config.translation_log_writer,
        )
        self.token_handler = TokenHandler(preserve_standard_tokens=config.preserve_tokens)

        # Statistics
        self.stats: Dict[str, Any] = {
            "files_processed": 0,
            "items_translated": 0,
            "cache_hits": 0,
            "errors": [],
            "ncs_diagnostics": {
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
            },
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
        #: Shared per-item progress counter (set during ``translate_content``).
        self._active_item_progress: Optional[Any] = None

    def _ncs_stats(self) -> Dict[str, Any]:
        return cast(
            Dict[str, Any],
            self.stats.setdefault(
                "ncs_diagnostics",
                {
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
                },
            ),
        )

    @staticmethod
    def _is_ncs_item(item: Any) -> bool:
        return (getattr(item, "metadata", None) or {}).get("type") == "ncs_string"

    def _record_ncs_diagnostic(
        self,
        item: Optional[Any],
        *,
        reason: str,
        count_field: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record one structured NCS diagnostic sample and optional counter."""
        if item is None:
            sample: Dict[str, Any] = {"reason": reason}
        else:
            meta = item.metadata or {}
            loc = item.location or ""
            sample = {
                "file": Path(loc).name if loc else None,
                "item_id": item.item_id,
                "offset": meta.get("offset"),
                "confidence": meta.get("confidence"),
                "ncs_hint": meta.get("ncs_hint"),
                "reason": reason,
                "text_prefix": (item.text or "")[:120],
            }
        if error:
            sample["error"] = error

        with self._stats_lock:
            ncs_stats = self._ncs_stats()
            if count_field:
                ncs_stats[count_field] = int(ncs_stats.get(count_field, 0)) + 1
            samples = ncs_stats.setdefault("samples", [])
            if len(samples) < _NCS_DIAGNOSTIC_SAMPLE_LIMIT:
                samples.append(sample)

        event = {"event": "ncs_diagnostic", **sample}
        try:
            self._log_writer.write(event)
        except Exception as log_exc:
            logger.debug("Failed to write NCS diagnostic event: %s", log_exc)

    def _increment_ncs_count(self, field: str, by: int = 1) -> None:
        with self._stats_lock:
            ncs_stats = self._ncs_stats()
            ncs_stats[field] = int(ncs_stats.get(field, 0)) + by

    def log_per_file_item(
        self,
        *,
        original: str,
        translated: str,
        context: Optional[str],
        source_filename: str,
        item_id: Optional[str] = None,
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
                    "item_id": item_id,
                }
            )
        except Exception:
            pass

    def _glossary_block_for_texts(self, texts: Iterable[str]) -> Optional[str]:
        """Return a glossary block narrowed to entries present in *texts*.

        Returns *None* when no glossary is configured or no entries match
        the batch — callers should omit the field from the prompt in that
        case so the variable half of the system message stays empty.
        """
        if not self.glossary or not getattr(self.glossary, "entries", None):
            return None
        block = self.glossary.to_prompt_block(texts=texts)
        return block or None

    def _find_cached_prefix(self, sanitized: str) -> Optional[tuple]:
        """Find the longest cached text that is a prefix of *sanitized*.

        This enables incremental translation for journal entries where each
        successive entry appends new text to the previous one.

        Returns:
            ``(cached_key, cached_translation)`` or *None*.
        """
        return self.translation_cache.longest_prefix_match(sanitized, _MIN_PREFIX_LEN)

    def _translation_cache_key_for_item_data(self, item_data: dict) -> str:
        """Return the session-cache key for one translation item."""
        item = item_data["item"]
        sanitized = str(item_data.get("_original_sanitized") or item_data["sanitized"])
        if self._is_ncs_item(item):
            meta = item.metadata or {}
            return f"ncs:{meta.get('ncs_hint') or ''}:{sanitized}"
        return sanitized

    def _ncs_item_passes_gate(self, item) -> bool:
        if not self._is_ncs_item(item):
            return True
        return self._ncs_gate_approval.get(item.item_id or "", False)

    # Max entries per LLM gate batch. Each entry ships a bounded nss_snippet
    # (~2000 chars cap), so 20 entries stays well under the chat context window
    # and keeps latency manageable; the provider splits further on parse fail.
    _NCS_GATE_BATCH_SIZE = 20

    def _run_ncs_llm_gate(self, translation_items: List[dict]) -> None:
        """Populate :attr:`_ncs_gate_approval` for all ``ncs_string`` items.

        Routing:
        1. Hard-veto (see :func:`ncs_hard_veto_reason`) → reject deterministically.
        2. ``config.skip_ncs_llm_gate`` → auto-approve (escape hatch for offline
           runs and tests). Every other item — including high-confidence
           ``SendMessageToPC`` dialogue — goes through the LLM gate so we catch
           debug concatenations and engine traps the heuristics can miss.
        """
        self._ncs_gate_approval.clear()
        pending: List[dict] = []  # items awaiting LLM verdict
        for itd in translation_items:
            item = itd["item"]
            meta = item.metadata or {}
            if meta.get("type") != "ncs_string":
                continue
            iid = item.item_id or ""
            hard_veto = ncs_hard_veto_reason(
                item.text, proven_player=bool(meta.get("proven_player"))
            )
            if hard_veto:
                self._ncs_gate_approval[iid] = False
                self._record_ncs_diagnostic(
                    item,
                    reason=hard_veto,
                    count_field="skipped_hard_veto",
                )
                continue
            if self.config.skip_ncs_llm_gate:
                self._ncs_gate_approval[iid] = True
                with self._stats_lock:
                    ncs_stats = self._ncs_stats()
                    ncs_stats["approved"] = int(ncs_stats.get("approved", 0)) + 1
                continue
            pending.append(itd)

        if not pending:
            return

        entries: List[Dict[str, Any]] = []
        for i, itd in enumerate(pending):
            item = itd["item"]
            meta = item.metadata or {}
            loc = item.location or ""
            entries.append(
                {
                    "key": str(i),
                    "text": item.text,
                    "file": Path(loc).name if loc else "",
                    "offset": meta.get("offset"),
                    "hint": meta.get("ncs_hint", ""),
                    "nss_snippet": meta.get("nss_snippet"),
                    "bytecode_context": meta.get("bytecode_context"),
                    "confidence": meta.get("confidence"),
                    "source_class": meta.get("source_class"),
                }
            )

        verdicts: Dict[str, Dict[str, Any]] = {}
        from ..async_utils import run_async

        async def gate_all() -> Dict[str, Dict[str, Any]]:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)
            chunks: List[List[Dict[str, Any]]] = [
                entries[start : start + self._NCS_GATE_BATCH_SIZE]
                for start in range(0, len(entries), self._NCS_GATE_BATCH_SIZE)
            ]

            async def run_chunk(chunk: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
                rekeyed = [{**e, "key": str(j)} for j, e in enumerate(chunk)]
                async with sem:
                    result = await self.provider.classify_ncs_translate_gate_batch_async(
                        rekeyed, source_lang=self.config.source_lang
                    )
                return {
                    e["key"]: result.get(str(j), {"translate": False, "reason": "gate_missing_key"})
                    for j, e in enumerate(chunk)
                }

            chunk_results = await asyncio.gather(*(run_chunk(c) for c in chunks))
            merged: Dict[str, Dict[str, Any]] = {}
            for cr in chunk_results:
                merged.update(cr)
            return merged

        try:
            # No overall timeout: large modules may need many minutes to gate.
            # Per-call timeouts and retries live in the provider layer.
            verdicts = run_async(gate_all(), timeout=None)
        except Exception as exc:
            logger.warning(
                "NCS LLM gate failed for %d item(s): %s — defaulting to reject.",
                len(pending),
                exc,
            )

        for i, itd in enumerate(pending):
            item = itd["item"]
            iid = item.item_id or ""
            cell = verdicts.get(str(i), {"translate": False, "reason": "gate_unavailable"})
            if cell.get("translate"):
                self._ncs_gate_approval[iid] = True
                self._record_ncs_diagnostic(
                    item,
                    reason=f"gate_approved:{cell.get('reason', 'unspecified')}",
                    count_field="approved",
                )
            else:
                self._ncs_gate_approval[iid] = False
                self._record_ncs_diagnostic(
                    item,
                    reason=f"gate_rejected:{cell.get('reason', 'unspecified')}",
                    count_field="skipped_fail_closed",
                )

    def translate_content(
        self,
        content: ExtractedContent,
        item_progress: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Translate multiple items individually, skipping duplicates.

        Items with the same text are translated only once; subsequent
        occurrences reuse the cached result, saving API calls.

        Args:
            content: ExtractedContent with items

        Returns:
            Translation mapping (original text → translated text)
        """
        translations: Dict[str, str] = {}
        items = [item for item in content.items if item.has_text()]

        if not items:
            return translations

        self._active_item_progress = item_progress
        self.ncs_translations_by_item_id.clear()

        source_filename = Path(content.source_file).name if content.source_file else None

        # Prepare translation items (sanitize tokens)
        translation_items = []
        for item in items:
            sanitized, handler = sanitize_text(
                item.text, preserve_tokens=self.config.preserve_tokens
            )
            translation_items.append(
                {
                    "item": item,
                    "sanitized": sanitized,
                    "full_sanitized": sanitized,
                    "handler": handler,
                }
            )

        ncs_item_count = sum(1 for itd in translation_items if self._is_ncs_item(itd["item"]))
        if ncs_item_count:
            with self._stats_lock:
                ncs_stats = self._ncs_stats()
                ncs_stats["total"] = int(ncs_stats.get("total", 0)) + ncs_item_count
                ncs_stats["extracted"] = int(ncs_stats.get("extracted", 0)) + ncs_item_count

        self._run_ncs_llm_gate(translation_items)

        # Per-session cache check
        # Avoids duplicate API calls when the same string appears multiple times across files.

        use_tqdm = not self.config.quiet and self.config.progress_callback is None
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
            if self.config.progress_callback is not None and item_progress is None:
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
                if item_progress is not None:
                    item_progress.bump(filename=content.content_type)
                continue

            # Cache hit?
            cache_key = self._translation_cache_key_for_item_data(item_data)
            if cache_key in self.translation_cache:
                outcome = handler.finalize_translation(
                    self.translation_cache[cache_key],
                    allow_cleanup=True,
                )
                translations[item.text] = outcome.final_text
                if (item.metadata or {}).get("type") == "ncs_string" and item.item_id:
                    self.ncs_translations_by_item_id[item.item_id] = outcome.final_text
                    self._increment_ncs_count("translated")
                with self._stats_lock:
                    self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
                logger.debug("Cache hit for '%s…'", sanitized[:40])
                if item_progress is not None:
                    item_progress.bump(filename=content.content_type)
                continue

            # Prefix-cache hit (journal entries that extend earlier text)
            prefix_match = None if self._is_ncs_item(item) else self._find_cached_prefix(sanitized)
            if prefix_match is not None:
                prefix_key, prefix_translation = prefix_match
                # Only the new tail needs translating — queue it
                item_data["_prefix_key"] = prefix_key
                item_data["_prefix_translation"] = prefix_translation
                logger.debug(
                    "Prefix cache match (%d chars) for '%s…'",
                    len(prefix_key),
                    sanitized[:40],
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
                    len(uncached_items),
                    len(unique_items),
                )

            # Pre-bump for duplicates that will be resolved transparently via
            # the session cache once the canonical unique item completes.
            duplicates = len(uncached_items) - len(unique_items)
            if duplicates > 0 and item_progress is not None:
                item_progress.bump(by=duplicates, filename=content.content_type)

            self._translate_uncached_concurrent(
                unique_items, translations, source_filename=source_filename
            )

        self._active_item_progress = None
        return translations

    # Maximum characters for a string to be considered "short" (eligible for batching)
    _BATCH_SHORT_THRESHOLD = 50
    # Items at or below this length use the larger batch size (Phase 3.5).
    _BATCH_VERY_SHORT_THRESHOLD = 20
    # Maximum items per batch API call (regular short items, 20 < len <= 50).
    _BATCH_SIZE = 15
    # Larger batch size for very short items (<= 20 chars) — mostly tag names
    # and one-word labels where per-item prompt overhead dominates cost.
    _BATCH_SIZE_VERY_SHORT = 30
    # NCS batch sizes by sanitized string length. Single-line strings at or
    # above 100 chars keep the existing individual translation path.
    _NCS_BATCH_SHORT_THRESHOLD = 50
    _NCS_BATCH_MAX_LENGTH = 100
    _NCS_BATCH_SIZE_SHORT = 20
    _NCS_BATCH_SIZE_MEDIUM = 10

    # Timeout (seconds) for a single async translation call.
    _ITEM_TIMEOUT: float = 120.0
    # Timeout (seconds) for a batch translation call.
    _BATCH_CALL_TIMEOUT: float = 180.0
    # Overall timeout (seconds) for async.gather of all items in one file.
    _GATHER_TIMEOUT: float = 600.0
    # Minimum run_async outer timeout. Large queues scale this up dynamically.
    _RUN_ASYNC_TIMEOUT: float = 660.0
    # Additional retries for token/tag mismatches after the first model answer.
    _TOKEN_RETRY_BUDGET: int = 2

    # Item types that are safe for batching (short names/labels only)
    _BATCHABLE_TYPES = frozenset(
        {
            "creature_first_name",
            "creature_last_name",
            "item_name",
            "area_name",
            "trigger_name",
            "placeable_name",
            "door_name",
            "store_name",
            "waypoint_name",
            "waypoint_map_note",
            "journal_category_name",
        }
    )

    @staticmethod
    def _is_short_item(item_data: dict) -> bool:
        """Check if an item is short and batchable (names/labels only)."""
        sanitized = item_data["sanitized"]
        item = item_data["item"]
        if "\n" in sanitized or len(sanitized) > TranslationManager._BATCH_SHORT_THRESHOLD:
            return False
        item_type = (item.metadata or {}).get("type", "")
        return item_type in TranslationManager._BATCHABLE_TYPES

    @staticmethod
    def _is_ncs_batchable(item_data: dict) -> bool:
        """Return whether an approved NCS item can use the NCS batch path."""
        item = item_data["item"]
        if not TranslationManager._is_ncs_item(item):
            return False
        sanitized = item_data["sanitized"]
        return "\n" not in sanitized and len(sanitized) < TranslationManager._NCS_BATCH_MAX_LENGTH

    @staticmethod
    def _ncs_batch_size_for(item_data: dict) -> int:
        """Return the configured NCS batch size for one item."""
        if len(item_data["sanitized"]) < TranslationManager._NCS_BATCH_SHORT_THRESHOLD:
            return TranslationManager._NCS_BATCH_SIZE_SHORT
        return TranslationManager._NCS_BATCH_SIZE_MEDIUM

    @staticmethod
    def _ncs_dedup_key(item_data: dict) -> tuple[str, str]:
        """Deduplicate NCS batch payloads by text plus script role hint."""
        meta = item_data["item"].metadata or {}
        return item_data["sanitized"], str(meta.get("ncs_hint") or "")

    @staticmethod
    def _content_profile_for_item(item_data: dict) -> str:
        """Deterministic profile for a single item (Phase 3.4).

        Items whose metadata type is in :data:`_BATCHABLE_TYPES` are
        labels/names — they use the compact ``short_label`` profile.
        Everything else falls back to the full ``default`` profile.
        """
        item = item_data["item"]
        item_type = (item.metadata or {}).get("type", "")
        if item_type == "ncs_string":
            return CONTENT_PROFILE_SCRIPT_MESSAGE
        if item_type in TranslationManager._BATCHABLE_TYPES:
            return CONTENT_PROFILE_SHORT_LABEL
        return CONTENT_PROFILE_DEFAULT

    @staticmethod
    def _content_profile_for_batch(batch: List[dict]) -> str:
        """Profile for a batch; uses ``short_label`` only if EVERY item qualifies.

        The selection is a pure function of the content-type mix of the batch
        (Phase 3.4 determinism rule) so each profile hits its own stable
        prefix in provider caches.
        """
        if not batch:
            return CONTENT_PROFILE_DEFAULT
        if all(TranslationManager._is_ncs_item(d["item"]) for d in batch):
            return CONTENT_PROFILE_SCRIPT_MESSAGE
        for d in batch:
            item_type = (d["item"].metadata or {}).get("type", "")
            if item_type not in TranslationManager._BATCHABLE_TYPES:
                return CONTENT_PROFILE_DEFAULT
        return CONTENT_PROFILE_SHORT_LABEL

    @staticmethod
    def _numbered_template_for_item(item_data: dict) -> Optional[tuple[str, str]]:
        """Return ``(template, number)`` for safe numbered short labels."""
        item = item_data["item"]
        item_type = (item.metadata or {}).get("type", "")
        if item_type not in TranslationManager._BATCHABLE_TYPES:
            return None
        text = item_data["sanitized"]
        if any(ch in text for ch in ".!?:;,\n\r"):
            return None
        match = _NUMBERED_LABEL_FAMILY_RE.fullmatch(text)
        if not match:
            return None
        base = match.group("base")
        if base in _NUMBERED_TEMPLATE_DENY_BASES:
            return None
        if not any(word in _NUMBERED_TEMPLATE_GENERIC_WORDS for word in base.split()):
            return None
        return f"{base} {_NUMBER_PLACEHOLDER}", match.group("number")

    @staticmethod
    def _restore_numbered_template(translated: str, number: str) -> Optional[str]:
        """Restore the original number into a translated template result."""
        if _NUMBER_PLACEHOLDER not in translated:
            return None
        return translated.replace(_NUMBER_PLACEHOLDER, number)

    def _async_bump(self, item_data: dict) -> None:
        """Per-item progress bump fired inside asyncio worker on completion."""
        progress = self._active_item_progress
        if progress is None:
            return
        item = item_data["item"]
        loc = item.location or ""
        fn = Path(loc).name if loc else ""
        progress.bump(filename=fn)

    @staticmethod
    def _queued_call_timeout(
        work_units: int,
        per_call_timeout: float,
        concurrency: int,
    ) -> float:
        """Return a queue-level timeout that accounts for semaphore waves."""
        if work_units <= 0:
            return 0.0
        limit = max(1, int(concurrency))
        waves = (work_units + limit - 1) // limit
        slack = max(5.0, min(60.0, per_call_timeout * 0.5))
        return waves * per_call_timeout + slack

    def _raise_if_cancelled(self) -> None:
        cb = self.config.cancel_check
        if cb is not None and cb():
            raise TranslationCancelled("Translation cancelled by user")

    async def _translate_one_async(
        self,
        sem: asyncio.Semaphore,
        item_data: dict,
        bump: bool = True,
    ) -> TranslationResult:
        """Translate a single item with semaphore and timeout."""
        item = item_data["item"]
        sanitized = item_data["sanitized"]
        glossary_block = self._glossary_block_for_texts([sanitized])
        content_profile = self._content_profile_for_item(item_data)
        async with sem:
            self._raise_if_cancelled()
            try:
                result = await asyncio.wait_for(
                    self.provider.translate_async(
                        text=sanitized,
                        source_lang=self.config.source_lang,
                        target_lang=self.config.target_lang,
                        context=item.context,
                        glossary_block=glossary_block,
                        content_profile=content_profile,
                    ),
                    timeout=self._ITEM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Translate timeout (%.0fs) for '%s…'",
                    self._ITEM_TIMEOUT,
                    sanitized[:40],
                )
                if self._is_ncs_item(item):
                    self._record_ncs_diagnostic(
                        item,
                        reason="translation_timeout",
                        count_field="timeout",
                    )
                    result = await self._retry_ncs_timeout_minimal(item_data)
                else:
                    result = TranslationResult(
                        translated="",
                        original=sanitized,
                        success=False,
                        error=f"Timeout after {self._ITEM_TIMEOUT}s",
                        metadata={},
                    )
            except Exception as e:
                result = TranslationResult(
                    translated="",
                    original=sanitized,
                    success=False,
                    error=str(e),
                    metadata={},
                )
        if bump:
            self._async_bump(item_data)
        return result

    def _build_ncs_minimal_retry_context(self, item: Any) -> str:
        meta = item.metadata or {}
        loc = item.location or ""
        filename = Path(loc).name if loc else "<unknown>"
        return (
            "NCS timeout fallback. Translate only if this is player-visible script text. "
            "Do not translate identifiers, tags, resrefs, variables, debug logs, or code. "
            f"file={filename}; item_id={item.item_id}; offset={meta.get('offset')}; "
            f"confidence={meta.get('confidence')}; hint={meta.get('ncs_hint')}."
        )

    async def _retry_ncs_timeout_minimal(self, item_data: dict) -> TranslationResult:
        """Retry an approved NCS item once with minimal context and no glossary."""
        item = item_data["item"]
        sanitized = item_data["sanitized"]
        content_profile = self._content_profile_for_item(item_data)
        try:
            retry_result = await asyncio.wait_for(
                self.provider.translate_async(
                    text=sanitized,
                    source_lang=self.config.source_lang,
                    target_lang=self.config.target_lang,
                    context=self._build_ncs_minimal_retry_context(item),
                    glossary_block=None,
                    content_profile=content_profile,
                ),
                timeout=self._ITEM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._record_ncs_diagnostic(
                item,
                reason="translation_timeout_retry_failed",
                error=f"Timeout after {self._ITEM_TIMEOUT}s",
            )
            return TranslationResult(
                translated="",
                original=sanitized,
                success=False,
                error=f"Timeout after {self._ITEM_TIMEOUT}s",
                metadata={},
            )
        except Exception as exc:
            self._record_ncs_diagnostic(
                item,
                reason="translation_timeout_retry_failed",
                error=str(exc),
            )
            return TranslationResult(
                translated="",
                original=sanitized,
                success=False,
                error=str(exc),
                metadata={},
            )

        if retry_result.success:
            self._record_ncs_diagnostic(
                item,
                reason="translation_timeout_retry_recovered",
                count_field="retry_recovered",
            )
        return retry_result

    def _compose_translated_sanitized(
        self,
        item_data: dict,
        translated_sanitized: str,
        *,
        use_full_output: bool = False,
    ) -> tuple[str, str]:
        """Return ``(full_translated_sanitized, cache_key)`` for one item output."""
        full_sanitized = item_data.get("full_sanitized") or item_data["sanitized"]
        if use_full_output:
            return translated_sanitized, full_sanitized

        prefix_translation = item_data.get("_prefix_translation")
        original_sanitized = item_data.get("_original_sanitized")
        if prefix_translation is not None and original_sanitized is not None:
            return prefix_translation + translated_sanitized, original_sanitized

        return translated_sanitized, self._translation_cache_key_for_item_data(item_data)

    def _build_token_retry_context(
        self,
        item_data: dict,
        attempt: int,
        mismatch_report: Optional[Any],
    ) -> str:
        """Build a stricter context hint for token/tag preservation retries."""
        item = item_data["item"]
        handler = item_data["handler"]
        expected = handler.get_expected_artifact_sequence()
        parts: List[str] = []
        if item.context:
            parts.append(item.context)
        parts.append(
            "TOKEN/TAG PRESERVATION RETRY: preserve every placeholder and helper token "
            "surrogate exactly as it appears in the source text. Do not rename, reorder, "
            "delete, duplicate, or replace any placeholder."
        )
        parts.append(
            "If the line contains NWN inline markup such as StartAction, StartCheck, "
            "StartHighlight, or </Start>, preserve that markup exactly after restoration. "
            "Translate only normal prose and text inside square brackets."
        )
        parts.append(
            "If the line contains dialog action markers like <<...>> or -...-, preserve "
            "the surrounding markers exactly and translate only the inner text. Do not "
            "invent new angle-bracket pseudo-tags such as <sir/madam>."
        )
        if expected:
            parts.append("Expected preserved artifacts after restoration: " + " | ".join(expected))
        if mismatch_report is not None and not mismatch_report.is_exact_match:
            parts.append(f"Previous mismatch type: {mismatch_report.mismatch_type}.")
            if mismatch_report.actual_sequence:
                parts.append(
                    "Previous restored artifact sequence: "
                    + " | ".join(mismatch_report.actual_sequence)
                )
        parts.append(f"Retry attempt {attempt} of {self._TOKEN_RETRY_BUDGET}.")
        return "\n".join(parts)

    def _accept_translation_candidate(
        self,
        item_data: dict,
        translated_sanitized: str,
        translations: Dict[str, str],
        *,
        source_filename: Optional[str] = None,
        model: Optional[str] = None,
        allow_cleanup: bool = False,
        use_full_output: bool = False,
    ) -> bool:
        """Finalize, validate, and store one model output."""
        item = item_data["item"]
        handler = item_data["handler"]
        full_translated_sanitized, cache_key = self._compose_translated_sanitized(
            item_data,
            translated_sanitized,
            use_full_output=use_full_output,
        )
        outcome = handler.finalize_translation(
            full_translated_sanitized,
            allow_cleanup=allow_cleanup,
        )

        if not outcome.exact_valid and not outcome.used_cleanup:
            item_data["_token_mismatch_report"] = outcome.mismatch_report
            item_data["_last_invalid_sanitized"] = full_translated_sanitized
            item_filename = Path(item.location).name if item.location else source_filename
            logger.warning(
                "%s: token/tag mismatch for %s (%s). expected=%s actual=%s",
                item_filename or "<unknown>",
                item.item_id or item.text[:40],
                outcome.mismatch_report.mismatch_type,
                outcome.mismatch_report.expected_sequence,
                outcome.mismatch_report.actual_sequence,
            )
            return False

        translated = outcome.final_text
        with self._stats_lock:
            if outcome.exact_valid:
                self.translation_cache[cache_key] = full_translated_sanitized
            translations[item.text] = translated
            self.stats["items_translated"] += 1

        if outcome.used_cleanup:
            item_filename = Path(item.location).name if item.location else source_filename
            logger.warning(
                "%s: accepted cleaned translation for %s after token/tag mismatch cleanup.",
                item_filename or "<unknown>",
                item.item_id or item.text[:40],
            )

        if (item.metadata or {}).get("type") == "ncs_string" and item.item_id:
            self.ncs_translations_by_item_id[item.item_id] = translated
            self._increment_ncs_count("translated")

        item_filename = Path(item.location).name if item.location else source_filename
        log_entry = {
            "original": item.text,
            "translated": translated,
            "context": item.context,
            "model": model or self.config.model,
            "file": item_filename,
            "item_id": item.item_id,
        }
        try:
            self._log_writer.write(log_entry)
        except Exception as log_e:
            logger.debug("Failed to write to translation log: %s", log_e)

        return True

    def _retry_token_mismatch(
        self,
        item_data: dict,
        initial_translated_sanitized: str,
        translations: Dict[str, str],
        *,
        source_filename: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bool:
        """Retry token/tag-mismatched outputs individually, then cleanup."""
        last_candidate, _ = self._compose_translated_sanitized(
            item_data,
            initial_translated_sanitized,
            use_full_output=False,
        )
        last_model = model or self.config.model
        mismatch_report = item_data.get("_token_mismatch_report")
        full_sanitized = item_data.get("full_sanitized") or item_data["sanitized"]
        glossary_block = self._glossary_block_for_texts([full_sanitized])
        content_profile = self._content_profile_for_item(item_data)
        from ..async_utils import run_async

        for attempt in range(1, self._TOKEN_RETRY_BUDGET + 1):

            async def run_retry() -> TranslationResult:
                return await self.provider.translate_async(
                    text=full_sanitized,
                    source_lang=self.config.source_lang,
                    target_lang=self.config.target_lang,
                    context=self._build_token_retry_context(item_data, attempt, mismatch_report),
                    glossary_block=glossary_block,
                    content_profile=content_profile,
                )

            try:
                retry_result = run_async(
                    run_retry(),
                    timeout=self._ITEM_TIMEOUT,
                )
            except Exception as exc:
                retry_result = TranslationResult(
                    translated="",
                    original=full_sanitized,
                    success=False,
                    error=str(exc),
                    metadata={},
                )

            if not retry_result.success:
                logger.warning(
                    "Token retry failed for %s on attempt %d: %s",
                    item_data["item"].item_id or item_data["item"].text[:40],
                    attempt,
                    retry_result.error,
                )
                continue

            last_candidate = retry_result.translated
            last_model = retry_result.metadata.get("model", last_model)
            if self._accept_translation_candidate(
                item_data,
                retry_result.translated,
                translations,
                source_filename=source_filename,
                model=last_model,
                allow_cleanup=False,
                use_full_output=True,
            ):
                return True
            new_report = item_data.get("_token_mismatch_report", mismatch_report)
            # Short-circuit: if the model deterministically reproduces the same
            # mismatch (e.g. hallucinated <StartCheck> or dropped placeholder),
            # further retries waste API calls. Skip straight to cleanup.
            if (
                mismatch_report is not None
                and new_report is not None
                and getattr(mismatch_report, "actual_sequence", None)
                == getattr(new_report, "actual_sequence", None)
            ):
                logger.debug(
                    "Token retry produced identical mismatch for %s; skipping remaining retries.",
                    item_data["item"].item_id or item_data["item"].text[:40],
                )
                mismatch_report = new_report
                break
            mismatch_report = new_report

        return self._accept_translation_candidate(
            item_data,
            last_candidate,
            translations,
            source_filename=source_filename,
            model=last_model,
            allow_cleanup=True,
            use_full_output=True,
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
                d["sanitized"] = d["sanitized"][len(prefix_key) :]

        # Passthrough: strings reduced to tokens/punctuation/whitespace only
        # need no API call — the sanitized form IS the "translation".
        real_items: List[dict] = []
        passthrough_items: List[dict] = []
        for d in uncached_items:
            if _is_empty_after_sanitize(d["sanitized"]):
                passthrough_items.append(d)
            else:
                real_items.append(d)

        if passthrough_items:
            logger.info(
                "Skipping API for %d item(s) with no translatable content "
                "(tokens/punctuation only)",
                len(passthrough_items),
            )
            for d in passthrough_items:
                self._apply_passthrough(d, translations)

        ncs_batch_items = [d for d in real_items if self._is_ncs_batchable(d)]
        regular_items = [d for d in real_items if not self._is_ncs_batchable(d)]
        short_items = [d for d in regular_items if self._is_short_item(d)]
        long_items = [d for d in regular_items if not self._is_short_item(d)]

        # Phase 3.5 — adaptive batch size: very short (<=20 chars) items carry
        # so little payload that per-batch prompt overhead dominates; group
        # them into larger batches to raise payload/overhead ratio.
        very_short_items: List[dict] = []
        regular_short_items: List[dict] = []
        for d in short_items:
            if len(d["sanitized"]) <= self._BATCH_VERY_SHORT_THRESHOLD:
                very_short_items.append(d)
            else:
                regular_short_items.append(d)

        batches: List[List[dict]] = []
        for i in range(0, len(very_short_items), self._BATCH_SIZE_VERY_SHORT):
            batches.append(very_short_items[i : i + self._BATCH_SIZE_VERY_SHORT])
        for i in range(0, len(regular_short_items), self._BATCH_SIZE):
            batches.append(regular_short_items[i : i + self._BATCH_SIZE])

        async def run_all() -> tuple:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)

            long_coros = [self._translate_one_async(sem, d) for d in long_items]

            # --- Batch translation for short items ---
            async def batch_one(batch: List[dict]) -> List[TranslationResult]:
                # Deduplicate by sanitized text within the batch so repeated
                # short strings (e.g. many identical tag-names in .git files)
                # cost exactly one slot in the API payload. Results are
                # replicated back to every duplicate in batch-order.
                sanitized_to_unique_idx: Dict[str, int] = {}
                numbered_template_by_sanitized: Dict[str, tuple[str, str]] = {}
                unique_batch: List[dict] = []
                for d in batch:
                    template_info = self._numbered_template_for_item(d)
                    if template_info is not None:
                        template, number = template_info
                        san = template
                        numbered_template_by_sanitized[d["sanitized"]] = (template, number)
                    else:
                        san = d["sanitized"]
                    if san not in sanitized_to_unique_idx:
                        sanitized_to_unique_idx[san] = len(unique_batch)
                        if template_info is not None:
                            proxy = dict(d)
                            proxy["sanitized"] = san
                            unique_batch.append(proxy)
                        else:
                            unique_batch.append(d)

                batch_items = [
                    TranslationItem(
                        original=d["sanitized"],
                        context=d["item"].context,
                        metadata=d["item"].metadata or {},
                    )
                    for d in unique_batch
                ]
                glossary_block = self._glossary_block_for_texts(
                    d["sanitized"] for d in unique_batch
                )
                content_profile = self._content_profile_for_batch(unique_batch)
                async with sem:
                    self._raise_if_cancelled()
                    try:
                        unique_results = await asyncio.wait_for(
                            self.provider.translate_batch_async(
                                items=batch_items,
                                source_lang=self.config.source_lang,
                                target_lang=self.config.target_lang,
                                glossary_block=glossary_block,
                                content_profile=content_profile,
                            ),
                            timeout=self._BATCH_CALL_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Batch translate timeout (%.0fs) for %d items",
                            self._BATCH_CALL_TIMEOUT,
                            len(batch_items),
                        )
                        unique_results = [
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
                        unique_results = [
                            TranslationResult(
                                translated="",
                                original=bi.original,
                                success=False,
                                error=str(e),
                                metadata={},
                            )
                            for bi in batch_items
                        ]
                # Replicate unique results back to every duplicate occurrence
                # in the original batch order. Callers iterate zip(batch, results).
                results: List[TranslationResult] = []
                for d in batch:
                    template_info = numbered_template_by_sanitized.get(d["sanitized"])
                    lookup_key = template_info[0] if template_info is not None else d["sanitized"]
                    source_result = unique_results[sanitized_to_unique_idx[lookup_key]]
                    if template_info is None:
                        results.append(source_result)
                        continue
                    _template, number = template_info
                    if not source_result.success:
                        results.append(
                            TranslationResult(
                                translated="",
                                original=d["sanitized"],
                                success=False,
                                error=source_result.error,
                                metadata=source_result.metadata,
                            )
                        )
                        continue
                    restored = self._restore_numbered_template(source_result.translated, number)
                    if restored is None:
                        results.append(
                            TranslationResult(
                                translated="",
                                original=d["sanitized"],
                                success=False,
                                error="Numbered template placeholder missing in batch response",
                                metadata=source_result.metadata,
                            )
                        )
                    else:
                        results.append(
                            TranslationResult(
                                translated=restored,
                                original=d["sanitized"],
                                success=True,
                                metadata=source_result.metadata,
                            )
                        )
                if len(unique_batch) < len(batch):
                    logger.debug(
                        "Batch dedup: %d items → %d unique sanitized",
                        len(batch),
                        len(unique_batch),
                    )
                # Per-item progress bump — one per batch member, regardless
                # of success; retry path below must not re-bump.
                for d in batch:
                    self._async_bump(d)
                return results

            batch_coros = [batch_one(b) for b in batches]

            try:
                long_results = await asyncio.gather(*long_coros) if long_coros else []
            except asyncio.TimeoutError:
                logger.error(
                    "Overall gather timeout (%.0fs) for %d long items",
                    self._GATHER_TIMEOUT,
                    len(long_coros),
                )
                long_results = [
                    TranslationResult(
                        translated="",
                        original=d["sanitized"],
                        success=False,
                        error="Gather timeout",
                    )
                    for d in long_items
                ]

            try:
                batch_results_nested = await asyncio.gather(*batch_coros) if batch_coros else []
            except asyncio.TimeoutError:
                logger.error(
                    "Overall gather timeout (%.0fs) for %d batch coros",
                    self._GATHER_TIMEOUT,
                    len(batch_coros),
                )
                batch_results_nested = [
                    [
                        TranslationResult(
                            translated="",
                            original=d["sanitized"],
                            success=False,
                            error="Gather timeout",
                        )
                        for d in batch
                    ]
                    for batch in batches
                ]

            # Flatten batch results
            batch_results = [r for group in batch_results_nested for r in group]

            return long_results, batch_results

        from ..async_utils import run_async

        limit = max(1, int(self.config.max_concurrent_requests))
        queue_timeout = (
            self._queued_call_timeout(len(long_items), self._ITEM_TIMEOUT, limit)
            + self._queued_call_timeout(len(batches), self._BATCH_CALL_TIMEOUT, limit)
            + 60.0
        )
        run_timeout = max(self._RUN_ASYNC_TIMEOUT, queue_timeout)

        long_results, batch_results = run_async(
            run_all(),
            timeout=run_timeout,
        )

        if short_items:
            n_batches = (
                (len(very_short_items) + self._BATCH_SIZE_VERY_SHORT - 1)
                // self._BATCH_SIZE_VERY_SHORT
            ) + ((len(regular_short_items) + self._BATCH_SIZE - 1) // self._BATCH_SIZE)
            logger.info(
                "Batch-translated %d short items (%d very-short + %d regular) "
                "in %d batch(es), %d long items individually",
                len(short_items),
                len(very_short_items),
                len(regular_short_items),
                n_batches,
                len(long_items),
            )

        if ncs_batch_items:
            self._translate_ncs_batches(
                ncs_batch_items,
                translations,
                source_filename=source_filename,
            )

        # Reorder the item list to match the flattened batch_results ordering
        # (very-short batches first, then regular-short batches).
        ordered_short_items = very_short_items + regular_short_items

        # Process long results
        for item_data, result in zip(long_items, long_results):
            self._process_translation_result(
                item_data, result, translations, source_filename=source_filename
            )

        # Process batch results; collect failures for individual retry
        retry_items: List[dict] = []
        for item_data, result in zip(ordered_short_items, batch_results):
            if result.success:
                self._process_translation_result(
                    item_data, result, translations, source_filename=source_filename
                )
            else:
                retry_items.append(item_data)

        # Fallback: retry failed batch items individually
        if retry_items:
            logger.info(
                "Retrying %d failed batch items individually",
                len(retry_items),
            )
            self._translate_individual_fallback(
                retry_items, translations, source_filename=source_filename
            )

    async def _translate_ncs_batch_with_recovery(
        self,
        sem: asyncio.Semaphore,
        batch: List[dict],
    ) -> List[TranslationResult]:
        """Translate one NCS batch, splitting all-failed batches recursively."""
        if not batch:
            return []

        dedup_index: Dict[tuple[str, str], int] = {}
        unique_batch: List[dict] = []
        for d in batch:
            key = self._ncs_dedup_key(d)
            if key not in dedup_index:
                dedup_index[key] = len(unique_batch)
                unique_batch.append(d)

        batch_items = []
        for d in unique_batch:
            item = d["item"]
            meta = item.metadata or {}
            ncs_hint = str(meta.get("ncs_hint") or "")
            batch_items.append(
                TranslationItem(
                    original=d["sanitized"],
                    context=item.context,
                    metadata={
                        **meta,
                        "type": "ncs_string",
                        "hint": ncs_hint,
                        "ncs_hint": ncs_hint,
                    },
                )
            )

        glossary_block = self._glossary_block_for_texts(d["sanitized"] for d in unique_batch)

        async with sem:
            self._raise_if_cancelled()
            try:
                unique_results = await asyncio.wait_for(
                    self.provider.translate_batch_async(
                        items=batch_items,
                        source_lang=self.config.source_lang,
                        target_lang=self.config.target_lang,
                        glossary_block=glossary_block,
                        content_profile=CONTENT_PROFILE_SCRIPT_MESSAGE,
                    ),
                    timeout=self._BATCH_CALL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                unique_results = [
                    TranslationResult(
                        translated="",
                        original=bi.original,
                        success=False,
                        error=f"NCS batch timeout after {self._BATCH_CALL_TIMEOUT}s",
                        metadata={},
                    )
                    for bi in batch_items
                ]
            except Exception as exc:
                unique_results = [
                    TranslationResult(
                        translated="",
                        original=bi.original,
                        success=False,
                        error=str(exc),
                        metadata={},
                    )
                    for bi in batch_items
                ]

        if len(unique_results) < len(unique_batch):
            unique_results = [
                *unique_results,
                *[
                    TranslationResult(
                        translated="",
                        original=batch_items[i].original,
                        success=False,
                        error="Missing translation result in NCS batch response",
                        metadata={},
                    )
                    for i in range(len(unique_results), len(unique_batch))
                ],
            ]
        elif len(unique_results) > len(unique_batch):
            unique_results = unique_results[: len(unique_batch)]

        if (
            unique_results
            and all(not result.success for result in unique_results)
            and len(batch) > 1
        ):
            mid = len(batch) // 2
            left = await self._translate_ncs_batch_with_recovery(sem, batch[:mid])
            right = await self._translate_ncs_batch_with_recovery(sem, batch[mid:])
            return left + right

        results: List[TranslationResult] = []
        for d in batch:
            source_result = unique_results[dedup_index[self._ncs_dedup_key(d)]]
            results.append(
                TranslationResult(
                    translated=source_result.translated,
                    original=d["sanitized"],
                    success=source_result.success,
                    error=source_result.error,
                    metadata=source_result.metadata,
                )
            )

        if len(unique_batch) < len(batch):
            logger.debug(
                "NCS batch dedup: %d items -> %d unique sanitized/hint pairs",
                len(batch),
                len(unique_batch),
            )
        return results

    async def _translate_ncs_minimal_async(
        self,
        sem: asyncio.Semaphore,
        item_data: dict,
    ) -> TranslationResult:
        """Retry one failed NCS batch item with minimal single-item context."""
        item = item_data["item"]
        sanitized = item_data["sanitized"]
        async with sem:
            self._raise_if_cancelled()
            try:
                return await asyncio.wait_for(
                    self.provider.translate_async(
                        text=sanitized,
                        source_lang=self.config.source_lang,
                        target_lang=self.config.target_lang,
                        context=self._build_ncs_minimal_retry_context(item),
                        glossary_block=None,
                        content_profile=CONTENT_PROFILE_SCRIPT_MESSAGE,
                    ),
                    timeout=self._ITEM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return TranslationResult(
                    translated="",
                    original=sanitized,
                    success=False,
                    error=f"NCS minimal fallback timeout after {self._ITEM_TIMEOUT}s",
                    metadata={},
                )
            except Exception as exc:
                return TranslationResult(
                    translated="",
                    original=sanitized,
                    success=False,
                    error=str(exc),
                    metadata={},
                )

    def _translate_ncs_batches(
        self,
        items: List[dict],
        translations: Dict[str, str],
        source_filename: Optional[str] = None,
    ) -> None:
        """Translate approved short NCS strings in dynamic JSON batches."""
        short_items = [
            d for d in items if self._ncs_batch_size_for(d) == self._NCS_BATCH_SIZE_SHORT
        ]
        medium_items = [
            d for d in items if self._ncs_batch_size_for(d) == self._NCS_BATCH_SIZE_MEDIUM
        ]

        batches: List[List[dict]] = []
        for i in range(0, len(short_items), self._NCS_BATCH_SIZE_SHORT):
            batches.append(short_items[i : i + self._NCS_BATCH_SIZE_SHORT])
        for i in range(0, len(medium_items), self._NCS_BATCH_SIZE_MEDIUM):
            batches.append(medium_items[i : i + self._NCS_BATCH_SIZE_MEDIUM])

        async def run_batches() -> List[TranslationResult]:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)
            nested = await asyncio.gather(
                *[self._translate_ncs_batch_with_recovery(sem, batch) for batch in batches]
            )
            return [result for group in nested for result in group]

        from ..async_utils import run_async

        limit = max(1, int(self.config.max_concurrent_requests))
        run_timeout = max(
            self._RUN_ASYNC_TIMEOUT,
            self._queued_call_timeout(len(batches), self._BATCH_CALL_TIMEOUT, limit) + 60.0,
        )
        batch_results = run_async(
            run_batches(),
            timeout=run_timeout,
        )

        ordered_items = short_items + medium_items
        failed_items: List[dict] = []
        failed_results: List[TranslationResult] = []
        for item_data, result in zip(ordered_items, batch_results):
            if result.success:
                self._process_translation_result(
                    item_data,
                    result,
                    translations,
                    source_filename=source_filename,
                )
                self._async_bump(item_data)
                continue
            failed_items.append(item_data)
            failed_results.append(result)

        if failed_items:
            fallback_results = self._translate_ncs_batch_failures_minimal(
                failed_items,
                failed_results,
            )
            for item_data, result in zip(failed_items, fallback_results):
                self._process_translation_result(
                    item_data,
                    result,
                    translations,
                    source_filename=source_filename,
                )
                self._async_bump(item_data)

        logger.info(
            "Batch-translated %d NCS items (%d <50 chars + %d 50-99 chars) in %d batch(es)",
            len(items),
            len(short_items),
            len(medium_items),
            len(batches),
        )

    def _translate_ncs_batch_failures_minimal(
        self,
        items: List[dict],
        failed_results: List[TranslationResult],
    ) -> List[TranslationResult]:
        """Retry failed NCS batch items individually with minimal context."""
        retry_by_key: Dict[tuple[str, str], dict] = {}
        timeout_keys: set[tuple[str, str]] = set()
        for item_data, result in zip(items, failed_results):
            key = self._ncs_dedup_key(item_data)
            retry_by_key.setdefault(key, item_data)
            if result.error and "timeout" in result.error.lower():
                timeout_keys.add(key)

        retry_items = list(retry_by_key.values())
        for item_data in retry_items:
            key = self._ncs_dedup_key(item_data)
            if key in timeout_keys:
                self._record_ncs_diagnostic(
                    item_data["item"],
                    reason="translation_timeout",
                    count_field="timeout",
                )

        async def run_fallback() -> List[TranslationResult]:
            limit = max(1, int(self.config.max_concurrent_requests))
            sem = asyncio.Semaphore(limit)
            return await asyncio.gather(
                *[self._translate_ncs_minimal_async(sem, item_data) for item_data in retry_items]
            )

        from ..async_utils import run_async

        limit = max(1, int(self.config.max_concurrent_requests))
        retry_results = run_async(
            run_fallback(),
            timeout=max(
                self._RUN_ASYNC_TIMEOUT / 2,
                self._queued_call_timeout(len(retry_items), self._ITEM_TIMEOUT, limit) + 30.0,
            ),
        )

        results_by_key = dict(zip(retry_by_key.keys(), retry_results))
        out: List[TranslationResult] = []
        for item_data in items:
            result = results_by_key[self._ncs_dedup_key(item_data)]
            key = self._ncs_dedup_key(item_data)
            if key in timeout_keys:
                if result.success:
                    self._record_ncs_diagnostic(
                        item_data["item"],
                        reason="translation_timeout_retry_recovered",
                        count_field="retry_recovered",
                    )
                else:
                    self._record_ncs_diagnostic(
                        item_data["item"],
                        reason="translation_timeout_retry_failed",
                        error=result.error,
                    )
            out.append(
                TranslationResult(
                    translated=result.translated,
                    original=item_data["sanitized"],
                    success=result.success,
                    error=result.error,
                    metadata=result.metadata,
                )
            )
        return out

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
                return await asyncio.gather(
                    *[self._translate_one_async(sem, d, bump=False) for d in items]
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Fallback gather timeout (%.0fs) for %d items",
                    self._GATHER_TIMEOUT / 2,
                    len(items),
                )
                return [
                    TranslationResult(
                        translated="",
                        original=d["sanitized"],
                        success=False,
                        error="Fallback gather timeout",
                    )
                    for d in items
                ]

        from ..async_utils import run_async

        limit = max(1, int(self.config.max_concurrent_requests))
        results = run_async(
            run_fallback(),
            timeout=max(
                self._RUN_ASYNC_TIMEOUT / 2,
                self._queued_call_timeout(len(items), self._ITEM_TIMEOUT, limit) + 30.0,
            ),
        )

        for item_data, result in zip(items, results):
            self._process_translation_result(
                item_data, result, translations, source_filename=source_filename
            )

    def _apply_passthrough(
        self,
        item_data: dict,
        translations: Dict[str, str],
    ) -> None:
        """Record a no-API translation for items that carry no text to translate.

        After :func:`sanitize_text` the string holds only token placeholders,
        NWN-tag helpers, whitespace, and punctuation; the round-trip back to
        the original text is the identity, so we reuse the sanitized form as
        the "translated" value and restore tokens via the handler.
        """
        sanitized = item_data["sanitized"]

        translated_sanitized = sanitized
        prefix_translation = item_data.get("_prefix_translation")
        if prefix_translation is not None:
            translated_sanitized = prefix_translation + translated_sanitized

        self._accept_translation_candidate(
            item_data,
            translated_sanitized,
            translations,
            source_filename=(
                Path(item_data["item"].location).name if item_data["item"].location else None
            ),
            model=self.config.model,
            use_full_output=True,
        )
        self._async_bump(item_data)

    def _process_translation_result(
        self,
        item_data: dict,
        result: TranslationResult,
        translations: Dict[str, str],
        source_filename: Optional[str] = None,
    ) -> None:
        """Process a single translation result (shared by individual and batch paths)."""
        if result.success:
            if self._accept_translation_candidate(
                item_data,
                result.translated,
                translations,
                source_filename=source_filename,
                model=result.metadata.get("model", self.config.model),
            ):
                return
            self._retry_token_mismatch(
                item_data,
                result.translated,
                translations,
                source_filename=source_filename,
                model=result.metadata.get("model", self.config.model),
            )
        else:
            item = item_data["item"]
            error_msg = f"Translation failed for {item.item_id}: {result.error}"
            if self._is_ncs_item(item):
                self._record_ncs_diagnostic(
                    item,
                    reason="translation_failed",
                    count_field="failed",
                    error=result.error,
                )
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
