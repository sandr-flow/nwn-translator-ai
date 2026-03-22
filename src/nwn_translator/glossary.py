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
from typing import TYPE_CHECKING, Dict, List, Set

from .config import (
    GLOSSARY_TEMPERATURE,
    GLOSSARY_FALLBACK_TEMPERATURE,
    GLOSSARY_MAX_TOKENS,
)
from .translators.token_handler import sanitize_text

if TYPE_CHECKING:
    from .ai_providers.openrouter_provider import OpenRouterProvider
    from .config import TranslationConfig
    from .context.world_context import WorldContext as WorldContextType

logger = logging.getLogger(__name__)

# Max names per single LLM request to stay within context/token limits.
_BATCH_SIZE = 80

# How many times to retry the entire glossary build (per batch) on parse failure.
_MAX_RETRIES = 2

# Timeout (seconds) for a single LLM glossary call.
_LLM_CALL_TIMEOUT: float = 180.0

# Overall timeout (seconds) for run_async when calling the LLM.
_RUN_ASYNC_TIMEOUT: float = 210.0


@dataclass
class Glossary:
    """Canonical English → target-language mappings for world proper names."""

    entries: Dict[str, str] = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """Format glossary for system prompt injection."""
        if not self.entries:
            return ""
        lines = [
            "GLOSSARY (canonical proper names — use these consistently in every line; "
            "decline or conjugate as required by grammar in the target language):",
        ]
        for en in sorted(self.entries.keys(), key=str.lower):
            lines.append(f'  * "{en}" → {self.entries[en]}')
        return "\n".join(lines)

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

        Large name lists are split into batches of ~80 to stay within token
        limits.  Each batch is retried up to ``_MAX_RETRIES`` times on parse
        failure.  If any batch ultimately fails the method raises
        ``RuntimeError`` so the caller can abort early.

        Raises:
            RuntimeError: If the glossary cannot be built after retries.
        """
        if not hasattr(provider, "complete_glossary_chat_async") and not hasattr(
            provider, "complete_json_chat_async"
        ):
            logger.warning(
                "Glossary building skipped: provider has no glossary/JSON chat API"
            )
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

        all_entries: Dict[str, str] = {}
        failed_batches: int = 0
        for batch_idx, batch_names in enumerate(batches, 1):
            if progress_callback:
                progress_callback(
                    "scanning", batch_idx - 1, len(batches),
                    f"Glossary batch {batch_idx}/{len(batches)}…",
                )
            batch_seen = {n: seen[n] for n in batch_names}
            t0 = time.monotonic()
            entries = self._translate_batch(
                batch_seen, provider, config, batch_idx, len(batches)
            )
            elapsed = time.monotonic() - t0
            if entries:
                all_entries.update(entries)
                logger.info(
                    "Glossary batch %d/%d: %d entries in %.1fs",
                    batch_idx, len(batches), len(entries), elapsed,
                )
            else:
                failed_batches += 1
                logger.warning(
                    "Glossary batch %d/%d failed after %.1fs (0 entries)",
                    batch_idx, len(batches), elapsed,
                )

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

    def _translate_batch(
        self,
        seen: Dict[str, str],
        provider: "OpenRouterProvider",
        config: "TranslationConfig",
        batch_idx: int,
        total_batches: int,
    ) -> Dict[str, str]:
        """Translate one batch of names, retrying on parse failure.

        Returns an empty dict on total failure instead of raising, so that
        other batches can still proceed.

        Args:
            seen: Mapping of name → category for this batch.
            provider: OpenRouter provider instance.
            config: Translation config.
            batch_idx: 1-based batch index (for logging).
            total_batches: Total number of batches (for logging).

        Returns:
            Dict of name → translation for successfully parsed entries.
        """
        names_lines = [
            f"- {name} ({seen[name]})" for name in sorted(seen.keys(), key=str.lower)
        ]
        expected_keys: Set[str] = set(seen.keys())

        system_prompt = self._build_system_prompt(config.target_lang)
        user_prompt = (
            "Translate every name below. "
            "Keys in your JSON must be the English name only, "
            "without the parenthesized category hint:\n\n"
            + "\n".join(names_lines)
        )
        keys_for_schema = sorted(seen.keys(), key=str.lower)

        last_raw = ""
        batch_label = (
            f"batch {batch_idx}/{total_batches}" if total_batches > 1 else "glossary"
        )

        for attempt in range(1, _MAX_RETRIES + 2):  # +2 because range is exclusive
            if attempt > 1:
                logger.info(
                    "Retrying %s (attempt %d/%d)…",
                    batch_label,
                    attempt,
                    _MAX_RETRIES + 1,
                )

            try:
                raw = self._run_llm(provider, system_prompt, user_prompt, keys_for_schema)
            except (TimeoutError, Exception) as exc:
                logger.warning(
                    "%s attempt %d: LLM call failed: %s",
                    batch_label.capitalize(), attempt, exc,
                )
                last_raw = f"[LLM error: {exc}]"
                continue

            last_raw = raw

            entries = self._parse_glossary_json(raw, expected_keys)
            if entries:
                coverage = len(entries) / len(expected_keys) * 100
                logger.info(
                    "%s: %d/%d entries (%.0f%% coverage)",
                    batch_label.capitalize(),
                    len(entries),
                    len(expected_keys),
                    coverage,
                )
                return entries

            logger.warning(
                "%s attempt %d: no usable entries parsed. Raw (truncated): %s",
                batch_label.capitalize(),
                attempt,
                (last_raw[:600] + "…") if len(last_raw) > 600 else last_raw,
            )

        # All retries exhausted — return empty dict instead of crashing
        logger.error(
            "Glossary %s returned no usable entries after %d attempts. "
            "Raw (truncated): %s",
            batch_label,
            _MAX_RETRIES + 1,
            (last_raw[:400] + "…") if len(last_raw) > 400 else last_raw,
        )
        return {}

    @staticmethod
    def _build_system_prompt(target_lang: str) -> str:
        """Build the system prompt for glossary translation."""
        from .prompts import build_glossary_system_prompt
        return build_glossary_system_prompt(target_lang)

    @staticmethod
    def _run_llm(
        provider: "OpenRouterProvider",
        system_prompt: str,
        user_prompt: str,
        keys_for_schema: List[str],
    ) -> str:
        """Call the LLM for glossary translation (handles event loop).

        Wraps the inner coroutine with ``asyncio.wait_for`` so a stalled
        provider call is cancelled after :data:`_LLM_CALL_TIMEOUT` seconds.

        Raises:
            TimeoutError: If the LLM call does not complete in time.
        """

        async def _call() -> str:
            coro: asyncio.coroutines
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
                )
            return await asyncio.wait_for(coro, timeout=_LLM_CALL_TIMEOUT)

        from .async_utils import run_async
        return run_async(
            _call(),
            cleanup=provider.close_async_client,
            timeout=_RUN_ASYNC_TIMEOUT,
        )

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
