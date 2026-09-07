"""Glossary of canonical translations for proper names (NPCs, locations, items, quests).

Built once per translation run from :class:`~nwn_translator.context.world_context.WorldContext`
and included in translation prompts as canonical forms with explicit aliases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Iterable, List, Optional, Set

from .config import (
    GLOSSARY_LLM_TIMEOUT,
    GLOSSARY_RUN_TIMEOUT,
    GLOSSARY_TEMPERATURE,
    GLOSSARY_FALLBACK_TEMPERATURE,
    GLOSSARY_MAX_TOKENS,
    ProgressCallback,
)
from .telemetry import llm_phase

if TYPE_CHECKING:
    from .ai_providers.openrouter_provider import OpenRouterProvider
    from .config import TranslationConfig
    from .context.world_context import WorldContext as WorldContextType

logger = logging.getLogger(__name__)

GLOSSARY_MAX_CHARS = 6000

# Max names per single LLM request to stay within context/token limits.
_BATCH_SIZE = 80

# How many times to retry the entire glossary build (per batch) on parse failure.
_MAX_RETRIES = 2

# Ceiling for overall glossary build timeout (seconds).
_MAX_OVERALL_TIMEOUT = 900.0

# Quotation marks a game string may be wrapped in; stripped when matching a
# model's JSON keys, which never carry them.
_QUOTE_CHARS = '"“”«»'


class _TermMatcher:
    """Word-bounded, case-insensitive search for complete source forms, memoized per text.

    A form matches a batch iff it matches one of the batch's texts, so callers
    take the union of per-text results instead of rescanning a joined corpus.
    The ``str.lower`` substring prefilter skips the regex for absent forms; the
    exotic equivalences of ``re.IGNORECASE`` (long s, Kelvin sign) are not
    matched, which is acceptable for game text.
    """

    def __init__(self, keys: Iterable[str]) -> None:
        self._patterns = {
            key: (key.lower(), re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)", re.IGNORECASE))
            for key in keys
        }
        self._memo: Dict[str, FrozenSet[str]] = {}

    def keys_in(self, text: str) -> FrozenSet[str]:
        found = self._memo.get(text)
        if found is None:
            lowered = text.lower()
            found = frozenset(
                key
                for key, (needle, pattern) in self._patterns.items()
                if needle in lowered and pattern.search(text)
            )
            self._memo[text] = found
        return found


@dataclass
class Glossary:
    """Canonical English -> target-language mappings for world proper names.

    ``entries`` and ``aliases`` are not modified after matching starts: the
    matcher and the per-language merged glossaries below are derived from them.
    """

    entries: Dict[str, str] = field(default_factory=dict)

    aliases: Dict[str, str] = field(default_factory=dict)

    _matcher: Optional[_TermMatcher] = field(default=None, init=False, repr=False, compare=False)
    _with_terms: Dict[str, "Glossary"] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def matching_entries(self, texts: Iterable[str]) -> Dict[str, str]:
        """Match complete source forms; only explicit aliases share an entity."""
        if self._matcher is None:
            self._matcher = _TermMatcher(self.entries)
        matches: Set[str] = set()
        for text in texts:
            if text:
                matches |= self._matcher.keys_in(str(text))
        roots = {self.aliases.get(key, key) for key in matches}
        return {
            key: value
            for key, value in self.entries.items()
            if key in matches or self.aliases.get(key, key) in roots
        }

    def to_prompt_block(self, texts: Optional[Iterable[str]] = None) -> str:
        """Render canonical forms without silently dropping aliases or terms."""
        entries = self.entries if texts is None else self.matching_entries(texts)
        if not entries:
            return ""
        lines = [
            "GLOSSARY (distinct entities; use these canonical forms consistently. "
            "Inflect as required by the current context. An alias may have its own "
            "abbreviated or deliberately distorted form; do not expand it automatically):"
        ]
        for source, target in sorted(entries.items()):
            alias = self.aliases.get(source)
            relation = f" (alias of {alias})" if alias else ""
            lines.append(f'  * "{source}" → {target}{relation}')
        return "\n".join(lines)


_NO_GLOSSARY = Glossary()


def terminology_block(texts: Iterable[str], target_lang: str, glossary: Optional[Glossary]) -> str:
    """Resolve project terminology once, before rendering any translation prompt.

    The glossary merged with the project terms of *target_lang* is built once
    per glossary and language, so its match memo serves every later prompt.
    """
    from .race_dictionary import RACE_TERMS

    source = glossary if glossary is not None else _NO_GLOSSARY
    lang = target_lang.lower()
    merged = source._with_terms.get(lang)
    if merged is None:
        static = RACE_TERMS.get(lang, {})
        entries = {
            key: value for key, value in source.entries.items() if key.casefold() not in static
        }
        entries.update(static)
        merged = source._with_terms[lang] = Glossary(entries, source.aliases)
    return merged.to_prompt_block(texts)


class GlossaryBuilder:
    """Builds a :class:`Glossary` via batched LLM calls."""

    def build(
        self,
        world_context: "WorldContextType",
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        progress_callback=None,
    ) -> Glossary:
        """Collect names from *world_context* and ask the model for translations.

        Large name lists are split into batches of ~40 to stay within token
        limits.  Batches run concurrently (up to ``config.max_concurrent_requests``).
        Each batch is retried up to ``_MAX_RETRIES`` times on parse failure,
        with partial results merged across attempts.  When no usable entries
        survive at all, the run degrades to an empty glossary with a warning.
        """
        if not hasattr(provider, "complete_glossary_chat_async") and not hasattr(
            provider, "complete_json_chat_async"
        ):
            logger.warning("Glossary building skipped: provider has no glossary/JSON chat API")
            return Glossary()

        if hasattr(world_context, "get_glossary_names"):
            pairs = world_context.get_glossary_names()
        else:
            pairs = world_context.get_all_names()
        if not pairs:
            return Glossary()

        # Deduplicate by name (first category wins for prompt listing)
        seen: Dict[str, str] = {}
        for name, category in pairs:
            n = (name or "").strip()
            if not n or n in seen:
                continue
            seen[n] = category

        if not seen:
            return Glossary()

        registry = getattr(world_context, "candidates", None)
        aliases = registry.resolved_aliases() if registry else {}
        if registry:
            for candidate in registry.values():
                if candidate.alias_of and candidate.name not in aliases:
                    seen.pop(candidate.name, None)
        sorted_names = sorted(seen, key=str.lower)
        groups: Dict[str, List[str]] = {}
        for name in sorted_names:
            groups.setdefault(aliases.get(name, name), []).append(name)
        batches: List[List[str]] = []
        for group in groups.values():
            if not batches or len(batches[-1]) + len(group) > _BATCH_SIZE:
                batches.append([])
            batches[-1].extend(group)

        logger.info(
            "Building glossary: %d names in %d batch(es)…",
            len(sorted_names),
            len(batches),
        )

        t0_total = time.monotonic()

        # Overall timeout scales with batch count but has a ceiling.
        overall_timeout = min(GLOSSARY_RUN_TIMEOUT * len(batches), _MAX_OVERALL_TIMEOUT)

        from .async_utils import run_async

        results = run_async(
            self._build_all_batches_async(
                batches,
                seen,
                provider,
                config,
                progress_callback,
                world_context,
            ),
            timeout=overall_timeout,
        )

        # Merge results from all batches.
        all_entries: Dict[str, str] = {}
        failed_batches = 0
        for batch_idx, result in enumerate(results, 1):
            if isinstance(result, BaseException):
                failed_batches += 1
                logger.warning(
                    "Glossary batch %d/%d failed with exception: %s",
                    batch_idx,
                    len(batches),
                    result,
                )
            elif result:
                all_entries.update(result)
            else:
                failed_batches += 1

        total_elapsed = time.monotonic() - t0_total
        logger.info("Glossary build completed in %.1fs", total_elapsed)

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

    async def _build_all_batches_async(
        self,
        batches: List[List[str]],
        seen: Dict[str, str],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        progress_callback: Optional[ProgressCallback],
        world_context: Optional["WorldContextType"] = None,
    ) -> List[Dict[str, str] | BaseException]:
        """Run all glossary batches concurrently with a semaphore."""
        sem = asyncio.Semaphore(max(1, config.max_concurrent_requests))
        total = len(batches)

        async def process_batch(batch_idx: int, batch_names: List[str]):
            batch_seen = {n: seen[n] for n in batch_names}
            return await self._translate_batch_async(
                sem,
                batch_seen,
                provider,
                config,
                batch_idx,
                total,
                progress_callback,
                world_context,
            )

        results = await asyncio.gather(
            *[process_batch(i + 1, b) for i, b in enumerate(batches)],
            return_exceptions=True,
        )
        return results

    async def _translate_batch_async(
        self,
        sem: asyncio.Semaphore,
        seen: Dict[str, str],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        batch_idx: int,
        total_batches: int,
        progress_callback: Optional[ProgressCallback],
        world_context: Optional["WorldContextType"] = None,
    ) -> Dict[str, str]:
        """Translate one batch of names with retries, merging partial results.

        Acquires the semaphore before each LLM call.  On each retry, only
        the keys still missing are requested.

        Returns:
            Dict of name -> translation for successfully parsed entries.
        """
        batch_label = f"batch {batch_idx}/{total_batches}" if total_batches > 1 else "glossary"
        system_prompt = self._build_system_prompt(config.target_lang)

        all_batch_entries: Dict[str, str] = {}
        remaining_keys: Set[str] = set(seen.keys())
        last_raw = ""

        logger.info(
            "Glossary %s: translating %d names…",
            batch_label,
            len(remaining_keys),
        )

        for attempt in range(1, _MAX_RETRIES + 2):  # +2 because range is exclusive
            if not remaining_keys:
                break

            attempt_seen = {k: seen[k] for k in remaining_keys}

            if progress_callback:
                progress_callback(
                    "scanning",
                    batch_idx - 1,
                    total_batches,
                    f"Glossary {batch_label} (attempt {attempt}/{_MAX_RETRIES + 1})…",
                )

            if attempt > 1:
                logger.info(
                    "Retrying %s (attempt %d/%d, %d keys remaining)…",
                    batch_label,
                    attempt,
                    _MAX_RETRIES + 1,
                    len(remaining_keys),
                )

            names_lines = [
                GlossaryBuilder._format_glossary_name_line(name, attempt_seen[name], world_context)
                for name in sorted(attempt_seen.keys(), key=str.lower)
            ]
            user_prompt = (
                "Translate every name below. "
                "Keys in your JSON must be the English name only, "
                "without the parenthesized category hint:\n\n" + "\n".join(names_lines)
            )
            if all_batch_entries:
                user_prompt += "\n\nAlready accepted forms in this family/batch: " + json.dumps(
                    all_batch_entries, ensure_ascii=False
                )
            keys_for_schema = sorted(attempt_seen.keys(), key=str.lower)

            t0 = time.monotonic()
            try:
                async with sem:
                    raw = await self._call_llm_async(
                        provider,
                        system_prompt,
                        user_prompt,
                        keys_for_schema,
                    )
            except (TimeoutError, asyncio.TimeoutError, Exception) as exc:
                elapsed = time.monotonic() - t0
                logger.warning(
                    "Glossary %s attempt %d: LLM timed out after %.1fs: %s",
                    batch_label,
                    attempt,
                    elapsed,
                    exc,
                )
                last_raw = f"[LLM error: {exc}]"
                if progress_callback:
                    progress_callback(
                        "scanning",
                        batch_idx - 1,
                        total_batches,
                        f"Glossary {batch_label}: attempt {attempt} failed, retrying…",
                    )
                continue

            elapsed = time.monotonic() - t0
            last_raw = raw

            entries = self._parse_glossary_json(raw, remaining_keys)
            if entries:
                all_batch_entries.update(entries)
                remaining_keys -= set(entries.keys())
                coverage = len(all_batch_entries) / len(seen) * 100
                logger.info(
                    "Glossary %s attempt %d: %d entries in %.1fs (%.0f%% cumulative coverage, %d remaining)",
                    batch_label,
                    attempt,
                    len(entries),
                    elapsed,
                    coverage,
                    len(remaining_keys),
                )
                if progress_callback:
                    progress_callback(
                        "scanning",
                        batch_idx - 1,
                        total_batches,
                        f"Glossary {batch_label}: {len(all_batch_entries)}/{len(seen)} names done",
                    )
            else:
                logger.warning(
                    "%s attempt %d: no usable entries parsed in %.1fs. Raw (truncated): %s",
                    batch_label.capitalize(),
                    attempt,
                    elapsed,
                    (last_raw[:600] + "…") if len(last_raw) > 600 else last_raw,
                )
                if progress_callback:
                    progress_callback(
                        "scanning",
                        batch_idx - 1,
                        total_batches,
                        f"Glossary {batch_label}: attempt {attempt} failed, retrying…",
                    )

        if remaining_keys:
            logger.warning(
                "Glossary %s: %d/%d keys still missing after all attempts: %s",
                batch_label,
                len(remaining_keys),
                len(seen),
                ", ".join(sorted(remaining_keys)[:15]) + ("…" if len(remaining_keys) > 15 else ""),
            )

        if not all_batch_entries:
            logger.error(
                "Glossary %s returned no usable entries after %d attempts. " "Raw (truncated): %s",
                batch_label,
                _MAX_RETRIES + 1,
                (last_raw[:400] + "…") if len(last_raw) > 400 else last_raw,
            )

        return all_batch_entries

    @staticmethod
    def _format_glossary_name_line(
        name: str,
        category: str,
        world_context: Optional["WorldContextType"] = None,
    ) -> str:
        """One glossary user-prompt line with category / gender / field hints."""
        hints: List[str] = [category or "unknown"]
        cat = (category or "").strip().lower()
        if cat == "nickname":
            hints.append("vocative epithet; translate meaning, not a name")
        if world_context is not None:
            registry = getattr(world_context, "candidates", None)
            for candidate in registry.values() if registry else []:
                if candidate.name == name:
                    if candidate.alias_of:
                        hints.append(
                            f"alias of {candidate.alias_of}; preserve abbreviation or wordplay"
                        )
                    hints.extend(candidate.contexts)
            for npc in getattr(world_context, "npcs", {}).values():
                if name in {npc.first_name, npc.last_name, npc.display_name}:
                    hints.append(
                        f"NPC fields: FirstName={npc.first_name!r}, LastName={npc.last_name!r}, gender={npc.gender}"
                    )
        return f"- {name} ({', '.join(hints)})"

    @staticmethod
    async def _call_llm_async(
        provider: "OpenRouterProvider",
        system_prompt: str,
        user_prompt: str,
        keys_for_schema: List[str],
    ) -> str:
        """Call the LLM for glossary translation (async coroutine).

        Wraps the provider call with ``asyncio.wait_for`` so a stalled
        call is cancelled after :data:`GLOSSARY_LLM_TIMEOUT` seconds.

        Raises:
            TimeoutError: If the LLM call does not complete in time.
        """
        if hasattr(provider, "complete_glossary_chat_async"):
            with llm_phase("glossary"):
                return await asyncio.wait_for(
                    provider.complete_glossary_chat_async(
                        system_prompt,
                        user_prompt,
                        glossary_keys=keys_for_schema,
                        max_tokens=GLOSSARY_MAX_TOKENS,
                        temperature=GLOSSARY_TEMPERATURE,
                    ),
                    timeout=GLOSSARY_LLM_TIMEOUT,
                )
        with llm_phase("glossary"):
            return await asyncio.wait_for(
                provider.complete_json_chat_async(
                    system_prompt,
                    user_prompt,
                    max_tokens=GLOSSARY_MAX_TOKENS,
                    temperature=GLOSSARY_FALLBACK_TEMPERATURE,
                    use_reasoning=False,
                ),
                timeout=GLOSSARY_LLM_TIMEOUT,
            )

    @staticmethod
    def _build_system_prompt(target_lang: str) -> str:
        """Build the system prompt for glossary translation."""
        from .prompts import build_glossary_system_prompt

        return build_glossary_system_prompt(target_lang)

    @staticmethod
    def _load_first_json_object(raw: str) -> Optional[Dict[str, Any]]:
        """Load the first valid JSON object from a model response."""
        decoder = json.JSONDecoder(strict=False)
        last_error: Optional[json.JSONDecodeError] = None

        for match in re.finditer(r"\{", raw):
            candidate = raw[match.start() :]
            try:
                data, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(data, dict):
                return data

        try:
            data = json.loads(raw, strict=False)
        except json.JSONDecodeError as exc:
            last_error = exc
        else:
            if isinstance(data, dict):
                return data

        if last_error is not None:
            logger.error("Failed to parse glossary JSON: %s", last_error)
        return None

    @staticmethod
    def _glossary_key_variants(key: str) -> List[str]:
        """Return normalized variants for matching model JSON keys."""
        normalized = unicodedata.normalize("NFKC", str(key))
        normalized = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        variants: List[str] = []

        def add(value: str) -> None:
            if value and value not in variants:
                variants.append(value)

        add(normalized)

        # Some modules put quotation marks inside the game string itself, e.g. an
        # area literally named ``"Thesis Paper Room"``. A model cannot echo that
        # back as a JSON key without escaping, so it answers with the bare name.
        if (
            len(normalized) >= 2
            and normalized[0] in _QUOTE_CHARS
            and normalized[-1] in _QUOTE_CHARS
        ):
            add(normalized[1:-1].strip())

        for value in list(variants):
            add(re.sub(r"\s*\([^)]*\)\s*$", "", value).strip())

        return variants

    @staticmethod
    def _parse_glossary_json(raw: str, expected_keys: Set[str]) -> Dict[str, str]:
        """Parse model JSON; keep only keys that were requested.

        Handles common model quirks:
        - Keys wrapped in a single top-level object (``{"glossary": {…}}``)
        - Keys that include category suffixes (``"name (character)"``)
        - Stray whitespace in keys
        """
        data = GlossaryBuilder._load_first_json_object(raw)
        if data is None:
            return {}

        if not isinstance(data, dict):
            return {}

        # Unwrap common wrapper keys from models ignoring instructions
        if len(data) == 1:
            only = next(iter(data.values()))
            if isinstance(only, dict):
                inner_key = next(iter(data.keys()))
                if str(inner_key).strip().lower() in (
                    "glossary",
                    "translations",
                    "entries",
                    "names",
                    "result",
                    "data",
                ):
                    data = only

        # Build a normalised lookup: strip whitespace and optional category
        # suffixes like " (character)", " (location)" that models may include
        # despite instructions to omit them.
        normalised_to_val: Dict[str, str] = {}
        casefolded_to_val: Dict[str, str] = {}
        for k, v in data.items():
            if v is None:
                continue
            sv = str(v).strip()
            if not sv:
                continue
            for key in GlossaryBuilder._glossary_key_variants(str(k)):
                normalised_to_val.setdefault(key, sv)
                casefolded_to_val.setdefault(key.casefold(), sv)

        out: Dict[str, str] = {}
        for ek in expected_keys:
            v = None
            for key in GlossaryBuilder._glossary_key_variants(ek):
                v = normalised_to_val.get(key)
                if v is None:
                    v = casefolded_to_val.get(key.casefold())
                if v is not None:
                    break
            if v is None:
                continue
            out[ek] = GlossaryBuilder._restore_wrapping_quotes(ek, v)
        return out

    @staticmethod
    def _restore_wrapping_quotes(key: str, value: str) -> str:
        """Give *value* back the quotation marks *key* is wrapped in.

        The glossary seeds the exact-match translation cache, so its value
        replaces the whole game string. A name the module author wrote as
        ``"Thesis Paper Room"`` must keep its quotes in the patched module,
        even though the model answers without them.
        """
        if len(key) < 2 or key[0] not in _QUOTE_CHARS or key[-1] not in _QUOTE_CHARS:
            return value
        if len(value) >= 2 and value[0] in _QUOTE_CHARS and value[-1] in _QUOTE_CHARS:
            return value
        return f"{key[0]}{value}{key[-1]}"
