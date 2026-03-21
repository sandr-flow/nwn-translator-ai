"""Contextual Translation Manager.

Translates entire dialog trees in a single batch using world context.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..config import TranslationConfig
from ..ai_providers import BaseAIProvider
from ..ai_providers.openrouter_provider import OpenRouterProvider
from ..translation_logging import translation_log_writer_for_config
from ..extractors.dialog_extractor import DialogExtractor, DialogNode
from ..context.world_context import WorldContext
from ..context.dialog_formatter import DialogFormatter
from .token_handler import TokenHandler, sanitize_text, restore_text

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

class ContextualTranslationManager:
    """Manager for full-graph contextual translation."""

    def __init__(
        self,
        config: TranslationConfig,
        provider: BaseAIProvider,
        world_context: WorldContext,
        translation_cache: Optional[Dict[str, str]] = None,
        glossary: Optional["Glossary"] = None,
    ):
        self.config = config
        self.provider = provider
        self.world_context = world_context
        self.glossary = glossary
        #: Shared sanitized_text -> model_output (same as TranslationManager._translation_cache)
        self.translation_cache = translation_cache
        self._log_writer = translation_log_writer_for_config(
            config.translation_log,
            config.translation_log_writer,
        )
        self.token_handler = TokenHandler(preserve_standard_tokens=config.preserve_tokens)
        self.formatter = DialogFormatter()

    def translate_dialog(
        self,
        file_path: Path,
        gff_data: Dict[str, Any]
    ) -> Dict[str, str]:
        """Translate a complete dialog tree.

        Args:
            file_path: Path to the .dlg file
            gff_data: Parsed GFF data

        Returns:
            Dictionary mapping original text to translated text
        """
        if not isinstance(self.provider, OpenRouterProvider):
            logger.error(
                "Contextual dialog translation requires OpenRouterProvider (got %s)",
                type(self.provider).__name__,
            )
            return {}

        extractor = DialogExtractor()

        # Build hierarchical tree for context
        tree = extractor.build_dialog_tree(gff_data)
        if not tree:
            return {}

        # We also need a flat list of nodes to map back to original text
        node_map: Dict[str, DialogNode] = {}

        def collect_nodes(nodes: List[DialogNode]):
            for node in nodes:
                key = f"{'E' if node.is_entry else 'R'}{node.node_id}"
                if key not in node_map:
                    node_map[key] = node
                    collect_nodes(node.replies)

        collect_nodes(tree)

        # Save original texts and sanitize
        original_text_map: Dict[str, str] = {}
        sanitized_by_key: Dict[str, str] = {}
        handlers: Dict[str, TokenHandler] = {}

        for key, node in node_map.items():
            original_text = node.text
            if original_text is None or not str(original_text).strip():
                continue

            original_text_map[key] = original_text

            sanitized, handler = sanitize_text(
                original_text,
                preserve_tokens=self.config.preserve_tokens,
            )
            handlers[key] = handler
            sanitized_by_key[key] = sanitized
            # Temporarily replace node text with sanitized text for the script formatting
            node.text = sanitized

        translations: Dict[str, str] = {}
        keys_for_api: List[str] = []

        for key, original_text in original_text_map.items():
            san = sanitized_by_key[key]
            if self.translation_cache is not None and san in self.translation_cache:
                translations[original_text] = restore_text(
                    self.translation_cache[san], handlers[key]
                )
            else:
                keys_for_api.append(key)

        all_keys = list(original_text_map.keys())
        if not keys_for_api:
            logger.debug(
                "All %d dialog lines for %s served from translation cache",
                len(all_keys),
                file_path.name,
            )
            return translations

        if set(keys_for_api) == set(all_keys):
            script = self.formatter.format_dialog_tree(tree)
        else:
            script = self.formatter.format_nodes(
                keys_for_api, node_map, original_text_map
            )

        if not script:
            return translations

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(file_path.name, script)

        logger.info(
            "Sending %d/%d dialog lines to AI for %s...",
            len(keys_for_api),
            len(original_text_map),
            file_path.name,
        )

        try:

            async def call_api(sp: str, up: str) -> str:
                return await self.provider.complete_json_chat_async(
                    sp,
                    up,
                    max_tokens=16384,
                    temperature=0.3,
                )

            async def run_primary() -> str:
                return await call_api(system_prompt, user_prompt)

            from ..async_utils import run_async
            raw_response = run_async(run_primary())

            parsed_json = self._parse_json_response(raw_response, file_path.name)
            if parsed_json is None:
                return translations

            api_translations = self._apply_translations(
                parsed_json,
                original_text_map,
                handlers,
                file_path,
                sanitized_by_key=sanitized_by_key,
                session_cache=self.translation_cache,
            )
            translations.update(api_translations)

            # Retry for any nodes the model missed (among keys we asked to translate)
            missing_keys = [k for k in keys_for_api if k not in parsed_json]
            if missing_keys:
                logger.warning(
                    "%s: %d/%d nodes were not translated, retrying missing nodes...",
                    file_path.name,
                    len(missing_keys),
                    len(keys_for_api),
                )
                retry_script = self.formatter.format_nodes(
                    missing_keys, node_map, original_text_map
                )

                async def run_retry() -> str:
                    return await call_api(
                        self._build_system_prompt(),
                        self._build_user_prompt(file_path.name, retry_script),
                    )

                retry_raw = run_async(run_retry())

                retry_json = self._parse_json_response(retry_raw, file_path.name)
                if retry_json:
                    retry_translations = self._apply_translations(
                        retry_json,
                        original_text_map,
                        handlers,
                        file_path,
                        sanitized_by_key=sanitized_by_key,
                        session_cache=self.translation_cache,
                    )
                    translations.update(retry_translations)
                    logger.info(
                        "%s: retry recovered %d additional translations.",
                        file_path.name,
                        len(retry_translations),
                    )

            return translations

        except Exception as e:
            logger.error("Contextual translation failed for %s: %s", file_path.name, e)
            return translations

    def _parse_json_response(self, raw: str, filename: str) -> Optional[dict]:
        """Parse a JSON object from a raw AI response string."""
        try:
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            json_str = json_match.group(0) if json_match else raw
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse JSON for %s: %s\nRaw: %s...",
                filename, e, raw[:200],
            )
            return None

    def _apply_translations(
        self,
        parsed_json: dict,
        original_text_map: Dict[str, str],
        handlers: Dict[str, Any],
        file_path: Path,
        sanitized_by_key: Optional[Dict[str, str]] = None,
        session_cache: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Restore tokens and build the original→translated mapping."""
        translations = {}
        for key, translated_sanitized in parsed_json.items():
            if key not in original_text_map:
                continue
            original_text = original_text_map[key]
            if translated_sanitized is None:
                translated_sanitized = ""
            elif not isinstance(translated_sanitized, str):
                translated_sanitized = str(translated_sanitized)
            final_translated = restore_text(translated_sanitized, handlers[key])
            translations[original_text] = final_translated

            if session_cache is not None and sanitized_by_key is not None:
                san = sanitized_by_key.get(key)
                if san:
                    session_cache[san] = translated_sanitized

            log_entry = {
                "original": original_text,
                "translated": final_translated,
                "context": f"Dialog node {key} in {file_path.name}",
                "model": self.provider.model,
            }
            try:
                self._log_writer.write(log_entry)
            except Exception as log_e:
                logger.debug("Failed to write to translation log: %s", log_e)
        return translations

    def _build_system_prompt(self) -> str:
        """Build the system prompt containing world context and instructions."""
        world_block = self.world_context.to_prompt_block(
            glossary=self.glossary,
            target_lang=self.config.target_lang,
        )
        glossary_block = ""
        if self.glossary and self.glossary.entries:
            glossary_block = self.glossary.to_prompt_block() + "\n\n"

        target = self.config.target_lang
        return (
            f"You are an elite translator for the game Neverwinter Nights.\n"
            f"Your task is to translate entire dialogue scripts to {target} "
            f"according to Nora Gal's Golden School of Translation.\n\n"
            f"{world_block}\n\n"
            f"{glossary_block}"
            f"RULES:\n"
            f"1. You will receive a dialogue script. Each line to translate is marked with an ID "
            f"like [E0] or [R1], inside <<< >>>.\n"
            f"2. Translate ONLY the text inside <<< >>>. Do NOT translate the routing hints "
            f"(like '-> Player Reply').\n"
            f"3. Use the WORLD CONTEXT to understand who is speaking to whom, ensuring gender "
            f"and rank appropriate phrasing.\n"
            f"4. For every name listed in the GLOSSARY (if present), use that translation "
            f"consistently; only adjust grammar (case, number) for the sentence.\n"
            f"5. Preserve all special tokens exactly as they are (e.g., <<TOKEN_0>>).\n"
            f"6. Maintain natural phrasing, emotion, and tone.\n"
            f"7. PROPER NAMES — translating vs. transliterating:\n"
            f"   a) Descriptive/meaningful names: TRANSLATE the meaning. "
            f"NEVER produce phonetic transliterations of English words.\n"
            f'      - "Inn of the Lance" -> "Таверна Копья" (GOOD) — NOT "Инн оф зэ Ланс" (BAD)\n'
            f'      - "Deadman\'s Marsh" -> "Болото Мертвецов" (GOOD) — NOT "Дэдмэнз Марш" (BAD)\n'
            f'      - "Dark Ranger" -> "Тёмный Рейнджер" (GOOD) — NOT "Дарк Рейнджер" (BAD)\n'
            f"   b) Personal names (first/last names): transliterate.\n"
            f'      - "Perin Izrick" -> "Перин Изрик", "Talias" -> "Талиас"\n'
            f"8. PRESERVE SPEECH STYLE AND REGISTER. This RPG has characters of different "
            f"intelligence and background. If the original text uses broken grammar, primitive "
            f"syntax, or childlike speech (low-INT characters, barbarians, goblins), you MUST "
            f"reproduce an equally broken, primitive style in {target}. "
            f"DO NOT \"fix\" or \"correct\" their speech — that destroys the character.\n"
            f"   In English, low-INT speech uses \"me\" instead of \"I\", drops articles/verbs. "
            f"In Russian, the equivalent is \"моя\" instead of \"я\", infinitives instead of "
            f"conjugated verbs, dropping prepositions, childlike structure.\n"
            f"   Examples:\n"
            f'   - "Me no want you here no more" -> "Моя тебя тут не хотеть больше" (GOOD, broken) '
            f'— NOT "Мне не нужен ты тут" (BAD, normalized)\n'
            f'   - "Me <FullName>. Me big adventurer too." -> "Моя <FullName>. Моя тоже большой путешественник." (GOOD)\n'
            f'   - "Ha ha! Me no crawl. Me here to point and laugh!" -> '
            f'"Ха-ха! Моя не ползать. Моя тут — пальцем тыкать и ржать!" (GOOD)\n'
            f"   Normal-INT dialog lines in the SAME script must stay grammatically correct.\n\n"
            f"OUTPUT FORMAT:\n"
            f"You MUST return a perfectly valid JSON object mapping the node ID to its translation.\n"
            f"Example:\n"
            f"{{\n"
            f'  "E0": "Приветствую, путник.",\n'
            f'  "R1": "Здравствуй.",\n'
            f'  "E2": "Что тебе нужно?"\n'
            f"}}\n\n"
            f"Do NOT include any markdown code blocks outside the JSON."
        )

    def _build_user_prompt(self, filename: str, script: str) -> str:
        """Build the user prompt containing the dialog script."""
        return (
            f"Translate the following dialog script from {filename}:\n\n"
            f"{script}\n\n"
            f"Return ONLY a JSON map of ID -> translation."
        )
