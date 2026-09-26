"""LLM build step of the run-wide glossary.

:class:`GlossaryBuilder` takes the eligible names of the world context, packs
them into batches that keep alias families together and asks the model for
their canonical translations, retrying the names a reply left out.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
import unicodedata
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Sequence, Set

from .config import (
    GLOSSARY_MAX_TOKENS,
    GLOSSARY_RUN_TIMEOUT,
    GLOSSARY_TEMPERATURE,
    ProgressCallback,
)
from .glossary import QUOTE_CHARS, Glossary, restore_wrapping_quotes
from .json_utils import scan_first_json_object
from .llm_batches import RUN_TIMEOUT_CAP, LlmStage
from .prompts.terminology import (
    build_glossary_name_line,
    build_glossary_system_prompt,
    build_glossary_user_prompt,
)

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider
    from .config import TranslationConfig
    from .context.world_context import NPCInfo, WorldContext
    from .llm_batches import Slot

logger = logging.getLogger(__name__)

#: Up to three requests per batch; a failed request is retried too.
_STAGE = LlmStage(
    phase="glossary",
    label="Glossary",
    batch_size=80,
    batch_timeout=GLOSSARY_RUN_TIMEOUT,
    max_attempts=3,
    retry_on_error=True,
    max_run_timeout=RUN_TIMEOUT_CAP,
)

#: Keys a model may nest the whole name map under.
_WRAPPER_KEYS = ("glossary", "translations", "entries", "names", "result", "data")

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_SPACE_RUN_RE = re.compile(r"\s+")
_CATEGORY_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


class GlossaryBuilder:
    """Builds a :class:`~nwn_translator.glossary.Glossary` via batched model requests."""

    def build(
        self,
        world_context: "WorldContext",
        provider: "TranslationProvider",
        config: "TranslationConfig",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Glossary:
        """Translate the glossary names of *world_context*.

        Names are sorted case-insensitively and packed into batches of up to 80
        that keep each alias family together. Batches run concurrently (up to
        ``config.max_concurrent_requests`` requests); each retries the names its
        replies left out, merging partial results.

        Args:
            world_context: Source of the names (curated candidates when available).
            provider: Model provider.
            config: Run configuration (target language, concurrency).
            progress_callback: Optional progress reporter.

        Returns:
            The glossary of the finished batches (a batch still running when
            the overall budget runs out counts as failed); empty, with a
            warning, when no usable entry survives.
        """
        pairs = world_context.get_glossary_names()
        if not pairs:
            return Glossary()

        # The first category of a name wins.
        seen: Dict[str, str] = {}
        for name, category in pairs:
            n = (name or "").strip()
            if not n or n in seen:
                continue
            seen[n] = category

        if not seen:
            return Glossary()

        registry = world_context.candidates
        aliases = registry.resolved_aliases() if registry else {}
        if registry:
            for candidate in registry.values():
                if candidate.alias_of and candidate.name not in aliases:
                    seen.pop(candidate.name, None)
        sorted_names = sorted(seen, key=str.lower)
        batches = _pack_alias_families(sorted_names, aliases, _STAGE.batch_size)
        logger.info(
            "Building glossary: %d names in %d batch(es)…",
            len(sorted_names),
            len(batches),
        )

        started = time.monotonic()
        hints = _NameHints(world_context)
        system_prompt = build_glossary_system_prompt(config.target_lang)

        async def translate_batch(
            slot: "Slot", number: int, batch_names: List[str]
        ) -> Dict[str, str]:
            label = f"batch {number}/{len(batches)}" if len(batches) > 1 else "glossary"
            batch = {name: seen[name] for name in batch_names}
            logger.info("Glossary %s: translating %d names…", label, len(batch))

            def prepare(
                keys: List[str], accepted: Dict[str, str], attempt: int
            ) -> Callable[[], Awaitable[str]]:
                if progress_callback:
                    progress_callback(
                        "scanning",
                        number - 1,
                        len(batches),
                        f"Glossary {label} (attempt {attempt}/{_STAGE.max_attempts})…",
                    )
                lines = [hints.line(name, batch[name]) for name in keys]
                return functools.partial(
                    provider.complete_glossary_chat_async,
                    system_prompt,
                    build_glossary_user_prompt(lines, accepted),
                    glossary_keys=keys,
                    max_tokens=GLOSSARY_MAX_TOKENS,
                    temperature=GLOSSARY_TEMPERATURE,
                )

            # Built from the batch dict exactly like this: the set's iteration
            # order decides the order of the accepted forms a retry repeats (KI-008).
            remaining = set(batch.keys())
            return await _STAGE.fill_keys(
                slot, remaining, prepare, parse_glossary_json, name=f"Glossary {label}"
            )

        results = _STAGE.run(batches, translate_batch, concurrency=config.max_concurrent_requests)

        all_entries: Dict[str, str] = {}
        failed_batches = 0
        for number, result in enumerate(results, 1):
            if isinstance(result, BaseException):
                failed_batches += 1
                logger.warning(
                    "Glossary batch %d/%d failed with exception: %s", number, len(batches), result
                )
            elif result:
                all_entries.update(result)
            else:
                failed_batches += 1

        logger.info("Glossary build completed in %.1fs", time.monotonic() - started)

        if not all_entries:
            logger.warning(
                "Glossary LLM returned no usable entries after retries; "
                "continuing without a glossary."
            )
            return Glossary()

        missing = len(sorted_names) - len(all_entries)
        if missing > 0:
            logger.warning(
                "Glossary incomplete: %d/%d names translated (%d missing, %d batch(es) failed)",
                len(all_entries),
                len(sorted_names),
                missing,
                failed_batches,
            )
        else:
            logger.info("Glossary built with %d entries", len(all_entries))

        return Glossary(entries=all_entries, aliases=aliases)


def _pack_alias_families(
    names: Sequence[str], aliases: Dict[str, str], size: int
) -> List[List[str]]:
    """Pack *names* into batches of up to *size*, keeping each alias family in one batch.

    Families keep the order of their first name; a family larger than *size*
    gets a batch of its own.
    """
    families: Dict[str, List[str]] = {}
    for name in names:
        families.setdefault(aliases.get(name, name), []).append(name)
    batches: List[List[str]] = []
    for family in families.values():
        if not batches or len(batches[-1]) + len(family) > size:
            batches.append([])
        batches[-1].extend(family)
    return batches


class _NameHints:
    """World-context facts that annotate the names of a glossary request."""

    def __init__(self, world_context: "WorldContext") -> None:
        self._candidates = {c.name: c for c in world_context.candidates.values()}
        self._npcs: Dict[str, List["NPCInfo"]] = {}
        for npc in world_context.npcs.values():
            for key in dict.fromkeys((npc.first_name, npc.last_name, npc.display_name)):
                self._npcs.setdefault(key, []).append(npc)

    def line(self, name: str, category: str) -> str:
        """Render the request line of *name* with its candidate and NPC hints."""
        return build_glossary_name_line(
            name, category, self._candidates.get(name), self._npcs.get(name, ())
        )


def glossary_key_variants(key: str) -> List[str]:
    """Return the normalized forms under which a glossary key may be matched.

    The key is NFKC-normalized with zero-width characters removed and
    whitespace collapsed; further variants drop quotation marks wrapping the
    whole key and a trailing parenthesized category hint.

    Args:
        key: A requested name or a key of a model reply.

    Returns:
        Distinct variants, the plain normalized form first.
    """
    normalized = unicodedata.normalize("NFKC", str(key))
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = _SPACE_RUN_RE.sub(" ", normalized).strip()

    variants: List[str] = []

    def add(value: str) -> None:
        if value and value not in variants:
            variants.append(value)

    add(normalized)

    # Some modules put quotation marks inside the game string itself, e.g. an
    # area literally named ``"Thesis Paper Room"``. A model cannot echo that
    # back as a JSON key without escaping, so it answers with the bare name.
    if len(normalized) >= 2 and normalized[0] in QUOTE_CHARS and normalized[-1] in QUOTE_CHARS:
        add(normalized[1:-1].strip())

    for value in list(variants):
        add(_CATEGORY_SUFFIX_RE.sub("", value).strip())

    return variants


def parse_glossary_json(raw: str, expected_keys: Set[str]) -> Dict[str, str]:
    """Parse a glossary reply; keep only the requested names.

    Tolerates a single wrapper object (``{"glossary": {…}}``), category suffixes
    and quotation marks in the keys, stray whitespace and case differences
    (exact variants win over casefolded ones). Values regain the quotation marks
    their key is wrapped in.

    Args:
        raw: Model reply, decoded with
            :func:`~nwn_translator.json_utils.scan_first_json_object`.
        expected_keys: Requested names; iterated to build the result.

    Returns:
        Requested name -> translation, in *expected_keys* iteration order.
    """
    try:
        data = scan_first_json_object(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse glossary JSON: %s", exc)
        return {}
    if data is None:
        return {}

    if len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, dict) and str(next(iter(data))).strip().lower() in _WRAPPER_KEYS:
            data = only

    normalised_to_val: Dict[str, str] = {}
    casefolded_to_val: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
        for key in glossary_key_variants(str(k)):
            normalised_to_val.setdefault(key, sv)
            casefolded_to_val.setdefault(key.casefold(), sv)

    out: Dict[str, str] = {}
    for ek in expected_keys:
        value = None
        for key in glossary_key_variants(ek):
            value = normalised_to_val.get(key)
            if value is None:
                value = casefolded_to_val.get(key.casefold())
            if value is not None:
                break
        if value is None:
            continue
        out[ek] = restore_wrapping_quotes(ek, value)
    return out
