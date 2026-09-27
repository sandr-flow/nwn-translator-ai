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
from .glossary import Glossary, is_quoted, restore_wrapping_quotes
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
    run_timeout_per_batch=GLOSSARY_RUN_TIMEOUT,
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
    """Builder of a :class:`~nwn_translator.glossary.Glossary` via batched model requests."""

    def build(
        self,
        world_context: "WorldContext",
        provider: "TranslationProvider",
        config: "TranslationConfig",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Glossary:
        """Translates the glossary names of *world_context*.

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
            The glossary of the finished batches (a batch still running when the
            overall budget runs out counts as failed); empty, with a warning,
            when no usable entry survives.
        """
        categories: Dict[str, str] = {}  # the first category of a name wins
        for name, category in world_context.get_glossary_names():
            if (name or "").strip():
                categories.setdefault(name.strip(), category)
        if not categories:
            return Glossary()
        registry = world_context.candidates
        aliases = registry.resolved_aliases()
        for candidate in registry.values():
            if candidate.alias_of and candidate.name not in aliases:
                categories.pop(candidate.name, None)
        names = sorted(categories, key=str.lower)
        batches = _pack_alias_families(names, aliases, _STAGE.batch_size)
        logger.info("Building glossary: %d names in %d batch(es)…", len(names), len(batches))

        started = time.monotonic()
        line = _name_line_builder(world_context, categories)
        system_prompt = build_glossary_system_prompt(config.target_lang)

        async def translate_batch(slot: "Slot", number: int, batch: List[str]) -> Dict[str, str]:
            """Translates one batch of names, retrying the names left out."""
            label = f"batch {number}/{len(batches)}" if len(batches) > 1 else "glossary"
            logger.info("Glossary %s: translating %d names…", label, len(batch))

            def report(message: str) -> None:
                """Reports the progress of the batch."""
                if progress_callback:
                    progress_callback("scanning", number - 1, len(batches), message)

            def prepare(
                keys: List[str], accepted: Dict[str, str], attempt: int
            ) -> Callable[[], Awaitable[str]]:
                """Reports the attempt and builds its glossary request."""
                report(f"Glossary {label} (attempt {attempt}/{_STAGE.max_attempts})…")
                return functools.partial(
                    provider.complete_glossary_chat_async,
                    system_prompt,
                    build_glossary_user_prompt([line(name) for name in keys], accepted),
                    glossary_keys=keys,
                    max_tokens=GLOSSARY_MAX_TOKENS,
                    temperature=GLOSSARY_TEMPERATURE,
                )

            # Built exactly like this on purpose: the set's iteration order (it depends
            # on the hash seed, see KI-008) decides the order of the answers, and so of
            # the "Already accepted forms" JSON a retry sends (see LlmStage.fill_keys).
            remaining = set(batch)

            def on_attempt(attempt: int, answered: int) -> None:
                """Reports the progress of the batch after one attempt."""
                if answered:
                    done = len(batch) - len(remaining)
                    report(f"Glossary {label}: {done}/{len(batch)} names done")
                else:
                    report(f"Glossary {label}: attempt {attempt} failed, retrying…")

            entries = await _STAGE.fill_keys(
                slot,
                remaining,
                prepare,
                parse_glossary_json,
                name=f"Glossary {label}",
                on_attempt=on_attempt,
            )
            if not entries:
                logger.error(
                    "Glossary %s returned no usable entries after %d attempts",
                    label,
                    _STAGE.max_attempts,
                )
            return entries

        results = _STAGE.run(batches, translate_batch, concurrency=config.max_concurrent_requests)

        all_entries: Dict[str, str] = {}
        failed_batches = 0
        for number, result in enumerate(results, 1):
            if isinstance(result, BaseException):
                logger.warning(
                    "Glossary batch %d/%d failed with exception: %s", number, len(batches), result
                )
            elif result:
                all_entries.update(result)
                continue
            failed_batches += 1
        logger.info("Glossary build completed in %.1fs", time.monotonic() - started)

        if not all_entries:
            logger.warning(
                "Glossary LLM returned no usable entries after retries; "
                "continuing without a glossary."
            )
            return Glossary()
        missing = len(names) - len(all_entries)
        if missing > 0:
            logger.warning(
                "Glossary incomplete: %d/%d names translated (%d missing, %d batch(es) failed)",
                len(all_entries),
                len(names),
                missing,
                failed_batches,
            )
        else:
            logger.info("Glossary built with %d entries", len(all_entries))
        return Glossary(entries=all_entries, aliases=aliases)


def _pack_alias_families(
    names: Sequence[str], aliases: Dict[str, str], size: int
) -> List[List[str]]:
    """Packs *names* into batches of up to *size*, keeping each alias family in one batch.

    Families keep the order of their first name; a family larger than *size* gets
    a batch of its own.

    Args:
        names: Names in request order.
        aliases: Alias -> canonical name; a name missing here is its own family.
        size: Maximum batch length for families that fit.

    Returns:
        The batches.
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


def _name_line_builder(
    world_context: "WorldContext", categories: Dict[str, str]
) -> Callable[[str], str]:
    """Returns the builder of a name's request line with its candidate and NPC hints.

    Args:
        world_context: World context of the run.
        categories: Glossary category of each requested name.

    Returns:
        A function from a requested name to its line of the glossary user prompt.
    """
    candidates = {c.name: c for c in world_context.candidates.values()}
    npcs: Dict[str, List["NPCInfo"]] = {}
    for npc in world_context.npcs.values():
        for key in dict.fromkeys((npc.first_name, npc.last_name, npc.display_name)):
            npcs.setdefault(key, []).append(npc)
    return lambda name: build_glossary_name_line(
        name, categories[name], candidates.get(name), npcs.get(name, ())
    )


def glossary_key_variants(key: str) -> List[str]:
    """Returns the normalized forms under which a glossary key may be matched.

    The key is NFKC-normalized with zero-width characters removed and whitespace
    collapsed; further variants drop quotation marks wrapping the whole key (a
    model cannot echo them back in a JSON key unescaped) and a trailing
    parenthesized category hint.

    Args:
        key: A requested name or a key of a model reply.

    Returns:
        Distinct non-empty variants, the plain normalized form first.
    """
    normalized = unicodedata.normalize("NFKC", str(key))
    normalized = _SPACE_RUN_RE.sub(" ", _ZERO_WIDTH_RE.sub("", normalized)).strip()
    variants = [normalized, normalized[1:-1].strip()] if is_quoted(normalized) else [normalized]
    variants += [_CATEGORY_SUFFIX_RE.sub("", value).strip() for value in variants]
    return [value for value in dict.fromkeys(variants) if value]


def parse_glossary_json(raw: str, expected_keys: Set[str]) -> Dict[str, str]:
    """Parses a glossary reply, keeping only the requested names.

    Tolerates a single wrapper object (``{"glossary": {…}}``) and the key
    differences of :func:`glossary_key_variants`; for each variant an exact
    match wins over a casefolded one. Values regain the quotation marks their
    key is wrapped in.

    Args:
        raw: Model reply, decoded with :func:`~nwn_translator.json_utils.scan_first_json_object`.
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
        [(wrapper, only)] = data.items()
        if isinstance(only, dict) and str(wrapper).strip().lower() in _WRAPPER_KEYS:
            data = only

    exact: Dict[str, str] = {}
    folded: Dict[str, str] = {}
    for reply_key, value in data.items():
        text = "" if value is None else str(value).strip()
        if text:
            for key in glossary_key_variants(str(reply_key)):
                exact.setdefault(key, text)
                folded.setdefault(key.casefold(), text)

    out: Dict[str, str] = {}
    for expected in expected_keys:
        for key in glossary_key_variants(expected):
            value = exact.get(key) or folded.get(key.casefold())
            if value:
                out[expected] = restore_wrapping_quotes(expected, value)
                break
    return out
