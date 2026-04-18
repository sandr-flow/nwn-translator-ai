"""Glossary of canonical translations for proper names (NPCs, locations, items, quests).

Built once per translation run from :class:`~nwn_translator.context.world_context.WorldContext`
and injected into prompts plus the session translation cache for consistency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Set

from .config import (
    GLOSSARY_LLM_TIMEOUT,
    GLOSSARY_RUN_TIMEOUT,
    GLOSSARY_TEMPERATURE,
    GLOSSARY_FALLBACK_TEMPERATURE,
    GLOSSARY_MAX_TOKENS,
    ProgressCallback,
)
from .translators.token_handler import sanitize_text

if TYPE_CHECKING:
    from .ai_providers.openrouter_provider import OpenRouterProvider
    from .config import TranslationConfig
    from .context.world_context import WorldContext as WorldContextType

logger = logging.getLogger(__name__)

# Max names per single LLM request to stay within context/token limits.
_BATCH_SIZE = 40

# How many times to retry the entire glossary build (per batch) on parse failure.
_MAX_RETRIES = 2

# Max concurrent glossary batches (limits parallel LLM calls).
_MAX_GLOSSARY_CONCURRENCY = 3

# Ceiling for overall glossary build timeout (seconds).
_MAX_OVERALL_TIMEOUT = 900.0


@dataclass
class Glossary:
    """Canonical English -> target-language mappings for world proper names."""

    entries: Dict[str, str] = field(default_factory=dict)

    def to_prompt_block(self, texts: Optional[Iterable[str]] = None) -> str:
        """Format glossary for system prompt injection.

        When *texts* is provided, only entries whose English name occurs
        (case-insensitive, whole-word) in at least one of the texts are
        included.  This keeps the variable half of the prompt small for
        short batches (Phase 2 — glossary-by-batch filtering).  Dialog
        translation should pass *texts=None* to get the full block.
        """
        if not self.entries:
            return ""
        if texts is None:
            entries = self.entries
        else:
            entries = self._filter_entries_by_texts(texts)
            if not entries:
                return ""
        lines = [
            "GLOSSARY (canonical proper names — use these consistently in every line; "
            "decline or conjugate as required by grammar in the target language, "
            "but only if the name is declinable; each entry is a DISTINCT entity — "
            "never substitute one name for another):",
        ]
        for en in sorted(entries.keys(), key=str.lower):
            lines.append(f'  * "{en}" → {entries[en]}')
        return "\n".join(lines)

    def _filter_entries_by_texts(self, texts: Iterable[str]) -> Dict[str, str]:
        """Return glossary entries whose keys appear in *texts* (whole-word, ci)."""
        blob_parts: List[str] = []
        for t in texts:
            if t:
                blob_parts.append(str(t))
        if not blob_parts:
            return {}
        blob = "\n".join(blob_parts).lower()
        out: Dict[str, str] = {}
        for en, tr in self.entries.items():
            name = en.strip().lower()
            if not name:
                continue
            pattern = r"\b" + re.escape(name) + r"\b"
            if re.search(pattern, blob):
                out[en] = tr
        return out

    def seed_cache(self, cache: Dict[str, str], *, preserve_tokens: bool) -> None:
        """Populate session translation cache so exact-match strings skip the API.

        Keys must match :func:`~nwn_translator.translators.token_handler.sanitize_text`
        output, same as :class:`~nwn_translator.translators.translation_manager.TranslationManager`.
        """
        for original_en, translated in self.entries.items():
            if not original_en or not str(original_en).strip():
                continue
            sanitized, _ = sanitize_text(
                str(original_en).strip(),
                preserve_tokens=preserve_tokens,
            )
            cache[sanitized] = translated


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
        limits.  Batches run concurrently (up to ``_MAX_GLOSSARY_CONCURRENCY``).
        Each batch is retried up to ``_MAX_RETRIES`` times on parse failure,
        with partial results merged across attempts.

        Raises:
            RuntimeError: If the glossary cannot be built after retries.
        """
        if not hasattr(provider, "complete_glossary_chat_async") and not hasattr(
            provider, "complete_json_chat_async"
        ):
            logger.warning("Glossary building skipped: provider has no glossary/JSON chat API")
            return Glossary()

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

        sorted_names = sorted(seen.keys(), key=str.lower)

        # Split into batches
        batches: List[List[str]] = []
        for i in range(0, len(sorted_names), _BATCH_SIZE):
            batches.append(sorted_names[i : i + _BATCH_SIZE])

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
            ),
            cleanup=provider.close_async_client,
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
            raise RuntimeError(
                "Glossary LLM returned no usable entries after retries. "
                "Translation cannot proceed without a glossary."
            )

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

        return Glossary(entries=all_entries)

    async def _build_all_batches_async(
        self,
        batches: List[List[str]],
        seen: Dict[str, str],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        progress_callback: Optional[ProgressCallback],
    ) -> List[Dict[str, str] | BaseException]:
        """Run all glossary batches concurrently with a semaphore."""
        sem = asyncio.Semaphore(min(_MAX_GLOSSARY_CONCURRENCY, config.max_concurrent_requests))
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
                f"- {name} ({attempt_seen[name]})"
                for name in sorted(attempt_seen.keys(), key=str.lower)
            ]
            user_prompt = (
                "Translate every name below. "
                "Keys in your JSON must be the English name only, "
                "without the parenthesized category hint:\n\n" + "\n".join(names_lines)
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
                # Detect echo-backs: model returned the English name unchanged.
                # These are likely untranslated; exclude and retry them.
                echobacks = {k for k, v in entries.items() if v == k}
                if echobacks:
                    logger.warning(
                        "Glossary %s attempt %d: %d echo-back(s) (value == key), will retry: %s",
                        batch_label,
                        attempt,
                        len(echobacks),
                        ", ".join(sorted(echobacks)[:10]),
                    )
                    for k in echobacks:
                        del entries[k]

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
            coro = provider.complete_glossary_chat_async(
                system_prompt,
                user_prompt,
                glossary_keys=keys_for_schema,
                max_tokens=GLOSSARY_MAX_TOKENS,
                temperature=GLOSSARY_TEMPERATURE,
            )
        else:
            coro = provider.complete_json_chat_async(
                system_prompt,
                user_prompt,
                max_tokens=GLOSSARY_MAX_TOKENS,
                temperature=GLOSSARY_FALLBACK_TEMPERATURE,
                use_reasoning=False,
            )
        return await asyncio.wait_for(coro, timeout=GLOSSARY_LLM_TIMEOUT)

    @staticmethod
    def _build_system_prompt(target_lang: str) -> str:
        """Build the system prompt for glossary translation."""
        from .prompts import build_glossary_system_prompt

        return build_glossary_system_prompt(target_lang)

    @staticmethod
    def _parse_glossary_json(raw: str, expected_keys: Set[str]) -> Dict[str, str]:
        """Parse model JSON; keep only keys that were requested.

        Handles common model quirks:
        - Keys wrapped in a single top-level object (``{"glossary": {…}}``)
        - Keys that include category suffixes (``"name (character)"``)
        - Stray whitespace in keys
        """
        try:
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            json_str = json_match.group(0) if json_match else raw
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse glossary JSON: %s", e)
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
        for k, v in data.items():
            if v is None:
                continue
            sv = str(v).strip()
            if not sv:
                continue
            key = str(k).strip()
            normalised_to_val[key] = sv
            # Also store without trailing " (category)" so we can match bare names
            bare = re.sub(r"\s*\([^)]*\)\s*$", "", key).strip()
            if bare and bare != key:
                normalised_to_val.setdefault(bare, sv)

        out: Dict[str, str] = {}
        for ek in expected_keys:
            v = normalised_to_val.get(ek)
            if v is None:
                v = normalised_to_val.get(ek.strip())
            if v is None:
                continue
            out[ek] = v
        return out
