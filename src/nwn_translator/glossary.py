"""Glossary of canonical translations for proper names (NPCs, locations, items, quests).

Built once per translation run from :class:`~nwn_translator.context.world_context.WorldContext`
and injected into prompts plus the session translation cache for consistency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Set

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
        for batch_idx, batch_names in enumerate(batches, 1):
            batch_seen = {n: seen[n] for n in batch_names}
            entries = self._translate_batch(
                batch_seen, provider, config, batch_idx, len(batches)
            )
            all_entries.update(entries)

        if not all_entries:
            raise RuntimeError(
                "Glossary LLM returned no usable entries after retries. "
                "Translation cannot proceed without a glossary."
            )

        missing = len(sorted_names) - len(all_entries)
        if missing > 0:
            logger.warning(
                "Glossary incomplete: %d/%d names translated (%d missing)",
                len(all_entries),
                len(sorted_names),
                missing,
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

        Args:
            seen: Mapping of name → category for this batch.
            provider: OpenRouter provider instance.
            config: Translation config.
            batch_idx: 1-based batch index (for logging).
            total_batches: Total number of batches (for logging).

        Returns:
            Dict of name → translation for successfully parsed entries.

        Raises:
            RuntimeError: If all retries fail for this batch.
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
        for attempt in range(1, _MAX_RETRIES + 2):  # +2 because range is exclusive
            batch_label = (
                f"batch {batch_idx}/{total_batches}" if total_batches > 1 else "glossary"
            )
            if attempt > 1:
                logger.info(
                    "Retrying %s (attempt %d/%d)…",
                    batch_label,
                    attempt,
                    _MAX_RETRIES + 1,
                )

            raw = self._run_llm(provider, system_prompt, user_prompt, keys_for_schema)
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

        raise RuntimeError(
            f"Glossary batch {batch_idx}/{total_batches} returned no usable entries "
            f"after {_MAX_RETRIES + 1} attempts. "
            f"Raw (truncated): {(last_raw[:400] + '…') if len(last_raw) > 400 else last_raw}"
        )

    @staticmethod
    def _build_system_prompt(target_lang: str) -> str:
        """Build the system prompt for glossary translation."""
        return (
            f"You are preparing a translation glossary for the game Neverwinter Nights.\n"
            f"Target language: {target_lang}.\n\n"
            "Translate each proper name below into the target language.\n\n"
            "KEY RULES — translating vs transliterating:\n"
            "- Personal names (character first/last names, unique fantasy names): "
            "TRANSLITERATE into target-language script.\n"
            '  Examples: "Perin Izrick" -> "Перин Изрик", "Drixie" -> "Дрикси"\n'
            "- Descriptive/meaningful names (locations, items, quests, titles composed of "
            "real English words with clear meaning): TRANSLATE the meaning. "
            "NEVER produce phonetic transliteration of English words.\n"
            '  Examples: "Inn of the Lance" -> "Таверна Копья" (NOT "Инн оф зэ Ланс"), '
            '"Deadman\'s Marsh" -> "Болото Мертвецов" (NOT "Дэдмэнз Марш"), '
            '"Dark Ranger" -> "Тёмный Рейнджер" (NOT "Дарк Рейнджер"), '
            '"Horde Raven" -> "Стайный Ворон" (NOT "ХордРейвен"), '
            '"Fearling" -> "Страхолик" (NOT "Фирлинг")\n'
            "- When in doubt: if the name consists of ordinary English words, translate the meaning. "
            "If it is a made-up fantasy word, transliterate.\n\n"
            "Return each value in nominative (dictionary) form only; "
            "the game will inflect in context later.\n\n"
            "OUTPUT: A single JSON object whose keys are the EXACT English name "
            "(WITHOUT the category hint in parentheses) and values are the translations.\n"
            'Example: the list entry "- Perin Izrick (character)" '
            'must produce key "Perin Izrick", NOT "Perin Izrick (character)".\n'
            "Do not omit keys. Do not add keys not in the list.\n"
            "Do not use markdown code fences."
        )

    @staticmethod
    def _run_llm(
        provider: "OpenRouterProvider",
        system_prompt: str,
        user_prompt: str,
        keys_for_schema: List[str],
    ) -> str:
        """Call the LLM for glossary translation (handles event loop)."""

        async def _call() -> str:
            if hasattr(provider, "complete_glossary_chat_async"):
                return await provider.complete_glossary_chat_async(
                    system_prompt,
                    user_prompt,
                    glossary_keys=keys_for_schema,
                    max_tokens=8192,
                    temperature=0.2,
                )
            return await provider.complete_json_chat_async(
                system_prompt,
                user_prompt,
                max_tokens=8192,
                temperature=0.2,
            )

        try:
            return asyncio.run(_call())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(_call())
            finally:
                loop.close()
                asyncio.set_event_loop(None)

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
