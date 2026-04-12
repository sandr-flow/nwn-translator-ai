"""LLM-based proper-noun extraction from translatable texts.

:class:`EntityExtractor` fills the gap left by
:class:`~nwn_translator.context.world_context.WorldScanner`: names embedded
inside dialog lines, descriptions, sign text, etc. — which never appear as
standalone GFF fields and therefore never reach the glossary.

Pipeline position: runs after Phase A (all TranslatableItems extracted) and
before :class:`~nwn_translator.glossary.GlossaryBuilder` so discovered names
propagate into the glossary prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from ..config import (
    GLOSSARY_LLM_TIMEOUT,
    GLOSSARY_MAX_TOKENS,
    GLOSSARY_RUN_TIMEOUT,
    GLOSSARY_TEMPERATURE,
    ProgressCallback,
)

if TYPE_CHECKING:
    from ..ai_providers.openrouter_provider import OpenRouterProvider
    from ..config import TranslationConfig
    from ..extractors.base import TranslatableItem

logger = logging.getLogger(__name__)

#: Skip strings shorter than this — names/labels rarely contain embedded proper nouns.
_MIN_TEXT_LENGTH = 40

#: Max texts per LLM call (empirically keeps prompt under a few KB).
_BATCH_TEXT_COUNT = 25

#: Max concurrent extraction batches.
_MAX_CONCURRENCY = 3

#: Absolute cap on the whole extraction run (seconds).
_MAX_OVERALL_TIMEOUT = 900.0

#: Accepted entity category values (anything else collapses to ``"unknown"``).
_VALID_CATEGORIES: Set[str] = {
    "character",
    "location",
    "organization",
    "item",
    "unknown",
}


class EntityExtractor:
    """Find proper nouns embedded in TranslatableItem texts via LLM."""

    def extract(
        self,
        items: List["TranslatableItem"],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        known_names: Set[str],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[Tuple[str, str]]:
        """Return (name, category) pairs for proper nouns found in *items*.

        Args:
            items: All translatable items from Phase A (dialog + non-dialog).
            provider: LLM provider for extraction calls.
            config: Translation configuration (source language, concurrency).
            known_names: Names already known to WorldScanner; used to skip
                duplicates case-insensitively.
            progress_callback: Optional progress reporter.

        Returns:
            List of unique (name, category) tuples NOT already in *known_names*.
            Returns an empty list on complete failure (never raises).
        """
        if not hasattr(provider, "complete_json_chat_async"):
            logger.warning(
                "Entity extraction skipped: provider has no complete_json_chat_async"
            )
            return []

        texts = _select_texts(items)
        if not texts:
            logger.info("Entity extraction: no texts above length threshold, skipping")
            return []

        batches = _batch_texts(texts, _BATCH_TEXT_COUNT)
        logger.info(
            "Entity extraction: %d texts in %d batch(es)…",
            len(texts), len(batches),
        )

        overall_timeout = min(
            GLOSSARY_RUN_TIMEOUT * len(batches), _MAX_OVERALL_TIMEOUT
        )

        from ..async_utils import run_async

        known_lower = {n.strip().lower() for n in known_names if n and n.strip()}
        source_lang = getattr(config, "source_lang", "English") or "English"
        if source_lang.lower() == "auto":
            source_lang = "English"

        try:
            results = run_async(
                self._run_all_batches_async(
                    batches,
                    provider,
                    config,
                    source_lang,
                    progress_callback,
                ),
                cleanup=provider.close_async_client,
                timeout=overall_timeout,
            )
        except Exception as exc:
            logger.warning(
                "Entity extraction failed; continuing without extracted names: %s",
                exc,
            )
            return []

        out: List[Tuple[str, str]] = []
        seen_lower: Set[str] = set()
        failed = 0
        for batch_idx, batch_result in enumerate(results, 1):
            if isinstance(batch_result, BaseException):
                failed += 1
                logger.warning(
                    "Entity extraction batch %d/%d failed: %s",
                    batch_idx, len(batches), batch_result,
                )
                continue
            if not batch_result:
                continue
            for name, category in batch_result:
                key = name.strip().lower()
                if not key or key in known_lower or key in seen_lower:
                    continue
                seen_lower.add(key)
                out.append((name.strip(), _coerce_category(category)))

        logger.info(
            "Entity extraction: %d new proper noun(s) found (%d batch failure(s))",
            len(out), failed,
        )
        return out

    async def _run_all_batches_async(
        self,
        batches: List[List[str]],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        source_lang: str,
        progress_callback: Optional[ProgressCallback],
    ) -> List[List[Tuple[str, str]] | BaseException]:
        """Run every batch concurrently under a semaphore."""
        sem = asyncio.Semaphore(
            min(_MAX_CONCURRENCY, max(1, config.max_concurrent_requests))
        )
        total = len(batches)

        async def _one(idx: int, batch: List[str]):
            return await self._extract_batch_async(
                sem, provider, source_lang, batch,
                idx, total, progress_callback,
            )

        return await asyncio.gather(
            *[_one(i + 1, b) for i, b in enumerate(batches)],
            return_exceptions=True,
        )

    async def _extract_batch_async(
        self,
        sem: asyncio.Semaphore,
        provider: "OpenRouterProvider",
        source_lang: str,
        texts: List[str],
        batch_idx: int,
        total_batches: int,
        progress_callback: Optional[ProgressCallback],
    ) -> List[Tuple[str, str]]:
        """Send one batch to the LLM and parse its JSON response."""
        from ..prompts import build_entity_extraction_system_prompt

        system_prompt = build_entity_extraction_system_prompt(source_lang)
        user_prompt = _format_user_prompt(texts)

        if progress_callback:
            progress_callback(
                "scanning", batch_idx - 1, total_batches,
                f"Entity extraction batch {batch_idx}/{total_batches}…",
            )

        t0 = time.monotonic()
        try:
            async with sem:
                raw = await asyncio.wait_for(
                    provider.complete_json_chat_async(
                        system_prompt,
                        user_prompt,
                        max_tokens=GLOSSARY_MAX_TOKENS,
                        temperature=GLOSSARY_TEMPERATURE,
                    ),
                    timeout=GLOSSARY_LLM_TIMEOUT,
                )
        except (TimeoutError, asyncio.TimeoutError, Exception) as exc:
            elapsed = time.monotonic() - t0
            logger.warning(
                "Entity extraction batch %d/%d LLM error after %.1fs: %s",
                batch_idx, total_batches, elapsed, exc,
            )
            return []

        elapsed = time.monotonic() - t0
        entries = _parse_entities_json(raw)
        logger.info(
            "Entity extraction batch %d/%d: %d entities in %.1fs",
            batch_idx, total_batches, len(entries), elapsed,
        )
        return entries


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------

def _select_texts(items: List["TranslatableItem"]) -> List[str]:
    """Pick unique text bodies likely to contain embedded proper nouns."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        text = (item.text or "").strip()
        if len(text) < _MIN_TEXT_LENGTH:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _batch_texts(texts: List[str], batch_size: int) -> List[List[str]]:
    """Split flat list into fixed-size batches."""
    return [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]


def _format_user_prompt(texts: List[str]) -> str:
    """Build a numbered list for the extraction user prompt."""
    lines = ["Extract proper nouns from these texts:", ""]
    for idx, text in enumerate(texts):
        # Escape embedded double-quotes so the LLM sees clean delimiters.
        safe = text.replace("\n", " ").replace('"', "'")
        lines.append(f'[{idx}] "{safe}"')
    return "\n".join(lines)


def _coerce_category(category: object) -> str:
    """Normalise whatever the model returned into a valid category."""
    if not isinstance(category, str):
        return "unknown"
    c = category.strip().lower()
    return c if c in _VALID_CATEGORIES else "unknown"


def _parse_entities_json(raw: str) -> List[Tuple[str, str]]:
    """Extract ``entities`` array from JSON, tolerating common model quirks."""
    if not raw or not raw.strip():
        return []

    try:
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(json_match.group(0) if json_match else raw)
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("Failed to parse entity extraction JSON: %s", exc)
        return []

    if not isinstance(data, dict):
        return []

    entities = data.get("entities")
    if not isinstance(entities, list):
        # Some models return the array under a different wrapper key.
        for v in data.values():
            if isinstance(v, list):
                entities = v
                break
        else:
            return []

    out: List[Tuple[str, str]] = []
    for entry in entities:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        n = name.strip()
        if not n:
            continue
        out.append((n, _coerce_category(entry.get("type"))))
    return out
