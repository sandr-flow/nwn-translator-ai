"""LLM extraction of proper nouns embedded in translatable texts.

The world scan only sees names stored in their own GFF fields. :class:`EntityExtractor`
finds the names inside dialog lines, descriptions and sign text, so they reach the
glossary too. It runs after extraction, before glossary curation.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from ..config import GLOSSARY_RUN_TIMEOUT, ProgressCallback
from ..json_utils import load_brace_span
from ..llm_batches import RUN_TIMEOUT_CAP, LlmStage, chunks, json_request
from ..prompts.terminology import (
    build_entity_extraction_system_prompt,
    build_entity_extraction_user_prompt,
)
from .entity_candidates import EntityCandidateRegistry
from .string_filters import (
    describe_rejection,
    is_valid_entity_name,
    should_skip_entity_source_text,
)

if TYPE_CHECKING:
    from ..ai_providers.base import TranslationProvider
    from ..config import TranslationConfig
    from ..extractors.base import TranslatableItem
    from ..llm_batches import Slot

logger = logging.getLogger(__name__)

#: Texts shorter than this rarely embed a proper noun and are skipped.
_MIN_TEXT_LENGTH = 40

#: Accepted entity categories; anything else becomes ``"unknown"``.
_VALID_CATEGORIES = frozenset(
    {"character", "location", "organization", "item", "nickname", "term", "unknown"}
)

#: One request per batch of texts, no retry.
_STAGE = LlmStage(
    phase="entity_extraction",
    label="Entity extraction",
    batch_size=50,
    run_timeout_per_batch=GLOSSARY_RUN_TIMEOUT,
    max_run_timeout=RUN_TIMEOUT_CAP,
)


class EntityExtractor:
    """Find proper nouns embedded in item texts via the model."""

    def extract_candidates(
        self,
        items: List["TranslatableItem"],
        provider: "TranslationProvider",
        config: "TranslationConfig",
        known_names: Set[str],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> EntityCandidateRegistry:
        """Return evidence-backed candidates for the names found in *items*.

        Args:
            items: All extracted items (dialog and non-dialog).
            provider: Model provider.
            config: Run configuration (source language, concurrency).
            known_names: Names the world scan already knows; skipped case-insensitively.
            progress_callback: Optional progress reporter.

        Returns:
            One ``entity_extractor`` evidence per new name, with the first item
            text that mentions it as context.
        """
        registry = EntityCandidateRegistry()
        found = self.extract(items, provider, config, known_names, progress_callback)
        if not found:
            return registry
        folded_texts = [(item.text or "").casefold() for item in items]
        for name, category in found:
            registry.add(
                name,
                category=category,
                source="entity_extractor",
                resource="",
                field="text",
                context=_first_context_for_name(items, folded_texts, name),
            )
        return registry

    def extract(
        self,
        items: List["TranslatableItem"],
        provider: "TranslationProvider",
        config: "TranslationConfig",
        known_names: Set[str],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[Tuple[str, str]]:
        """Return ``(name, category)`` pairs for the proper nouns found in *items*.

        Args:
            items: All extracted items (dialog and non-dialog).
            provider: Model provider.
            config: Run configuration (source language, concurrency).
            known_names: Names the world scan already knows; skipped case-insensitively.
            progress_callback: Optional progress reporter.

        Returns:
            New names in reply order, deduplicated case-insensitively and
            filtered by :func:`is_valid_entity_name`. Failed batches, and
            batches unfinished when the overall budget runs out, contribute
            nothing; the method never raises for model errors.
        """
        texts = _select_texts(items)
        if not texts:
            logger.info("Entity extraction: no texts above length threshold, skipping")
            return []

        batches = chunks(texts, _STAGE.batch_size)
        logger.info("Entity extraction: %d texts in %d batch(es)…", len(texts), len(batches))
        source_lang = config.source_lang or "English"
        if source_lang.lower() == "auto":
            source_lang = "English"
        system_prompt = build_entity_extraction_system_prompt(source_lang)

        async def extract_batch(
            slot: "Slot", number: int, batch: List[str]
        ) -> Optional[List[Tuple[str, str]]]:
            if progress_callback:
                progress_callback(
                    "scanning",
                    number - 1,
                    len(batches),
                    f"Entity extraction batch {number}/{len(batches)}…",
                )
            user_prompt = build_entity_extraction_user_prompt(batch)
            started = time.monotonic()
            try:
                raw = await _STAGE.request(
                    slot, functools.partial(json_request, provider, system_prompt, user_prompt)
                )
            except Exception as exc:
                logger.warning(
                    "Entity extraction batch %d/%d LLM error after %.1fs: %s",
                    number,
                    len(batches),
                    time.monotonic() - started,
                    exc,
                )
                return None
            entries = _parse_entities_json(raw)
            logger.info(
                "Entity extraction batch %d/%d: %d entities in %.1fs",
                number,
                len(batches),
                len(entries),
                time.monotonic() - started,
            )
            return entries

        results = _STAGE.run(batches, extract_batch, concurrency=config.max_concurrent_requests)

        known_lower = {n.strip().lower() for n in known_names if n and n.strip()}
        out: List[Tuple[str, str]] = []
        seen_lower: Set[str] = set()
        failed = 0
        rejected: List[Tuple[str, str]] = []
        for number, result in enumerate(results, 1):
            if not isinstance(result, list):
                failed += 1
                if result is not None:
                    logger.warning(
                        "Entity extraction batch %d/%d failed: %s", number, len(batches), result
                    )
                continue
            for name, category in result:
                key = name.lower()
                if key in known_lower or key in seen_lower:
                    continue
                if not is_valid_entity_name(name, category):
                    rejected.append((name, describe_rejection(name, category)))
                    continue
                seen_lower.add(key)
                out.append((name, category))

        logger.info(
            "Entity extraction: %d accepted, %d rejected (%d batch failure(s))",
            len(out),
            len(rejected),
            failed,
        )
        if rejected:
            logger.info(
                "Entity extraction rejected examples: %s",
                ", ".join(f"{name} [{reason}]" for name, reason in rejected[:12]),
            )
        return out


def _select_texts(items: List["TranslatableItem"]) -> List[str]:
    """Pick the unique item texts likely to embed proper nouns, in item order."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        text = (item.text or "").strip()
        if not text:
            continue
        meta = item.metadata or {}
        # Proven player-facing NCS barks are often short ("Stay back, sword-one!")
        # but still carry nicknames the glossary must lock.
        short_ncs_ok = meta.get("type") == "ncs_string" and bool(meta.get("proven_player"))
        if len(text) < _MIN_TEXT_LENGTH and not short_ncs_ok:
            continue
        if should_skip_entity_source_text(text, meta):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _first_context_for_name(
    items: List["TranslatableItem"], folded_texts: List[str], name: str
) -> str:
    """Return the first item text containing *name* (casefolded substring), clipped to 240.

    Args:
        items: Extracted items, in order.
        folded_texts: Casefolded text of each item, parallel to *items*.
        name: Name to look for.

    Returns:
        The item text with newlines flattened, or ``""`` when no text contains *name*.
    """
    needle = (name or "").casefold()
    if not needle:
        return ""
    for item, folded in zip(items, folded_texts):
        if needle in folded:
            return (item.text or "").replace("\n", " ")[:240]
    return ""


def _coerce_category(category: object) -> str:
    """Normalize a model-supplied category to one of :data:`_VALID_CATEGORIES`."""
    if not isinstance(category, str):
        return "unknown"
    c = category.strip().lower()
    return c if c in _VALID_CATEGORIES else "unknown"


def _parse_entities_json(raw: str) -> List[Tuple[str, str]]:
    """Parse an entity-extraction reply into ``(name, category)`` pairs.

    The pairs come from the ``entities`` list or, failing that, the first list
    value of the object; entries without a non-empty string name are skipped.

    Args:
        raw: Model reply, decoded with
            :func:`~nwn_translator.json_utils.load_brace_span`.

    Returns:
        Stripped names with their normalized category, in reply order; empty
        when the reply does not decode to an object holding such a list.
    """
    if not raw or not raw.strip():
        return []

    try:
        data = load_brace_span(raw)
    except json.JSONDecodeError as exc:
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
