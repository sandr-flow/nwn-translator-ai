"""Contextual Translation Manager.

Translates entire dialog trees in a single batch using world context.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..config import TranslationConfig, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS
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
        #: Shared sanitized_text -> model_output (same as TranslationManager.translation_cache)
        self.translation_cache = translation_cache
        self._log_writer = translation_log_writer_for_config(
            config.translation_log,
            config.translation_log_writer,
        )
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
            script = self.formatter.format_dialog_tree(
                tree, text_overrides=sanitized_by_key
            )
        else:
            script = self.formatter.format_nodes(
                keys_for_api, node_map, original_text_map,
                text_overrides=sanitized_by_key,
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
                    max_tokens=TRANSLATION_MAX_TOKENS,
                    temperature=TRANSLATION_TEMPERATURE,
                )

            async def run_primary() -> str:
                return await call_api(system_prompt, user_prompt)

            from ..async_utils import run_async
            raw_response = run_async(run_primary(), cleanup=self.provider.close_async_client)

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
                    missing_keys, node_map, original_text_map,
                    text_overrides=sanitized_by_key,
                )

                async def run_retry() -> str:
                    return await call_api(
                        self._build_system_prompt(),
                        self._build_user_prompt(file_path.name, retry_script),
                    )

                retry_raw = run_async(run_retry(), cleanup=self.provider.close_async_client)

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
        from ..prompts import build_dialog_system_prompt

        world_block = self.world_context.to_prompt_block(
            glossary=self.glossary,
            target_lang=self.config.target_lang,
        )
        glossary_block = ""
        if self.glossary and self.glossary.entries:
            glossary_block = self.glossary.to_prompt_block()

        return build_dialog_system_prompt(
            self.config.target_lang,
            self.config.player_gender,
            world_block,
            glossary_block,
        )

    def _build_user_prompt(self, filename: str, script: str) -> str:
        """Build the user prompt containing the dialog script."""
        return (
            f"Translate the following dialog script from {filename}:\n\n"
            f"{script}\n\n"
            f"Return ONLY a JSON map of ID -> translation."
        )
