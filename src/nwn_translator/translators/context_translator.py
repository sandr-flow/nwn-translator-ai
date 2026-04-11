"""Contextual Translation Manager.

Translates entire dialog trees in a single batch using world context.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..config import TranslationConfig, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS
from ..json_utils import json_extract_first_object, strip_json_markdown_fences
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

# Upper bound for dialog responses when retrying after likely truncation.
_DIALOG_MAX_TOKENS_BOOST = min(32768, TRANSLATION_MAX_TOKENS * 2)


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
        parsed_data: Dict[str, Any]
    ) -> Dict[str, str]:
        """Translate a complete dialog tree.

        Args:
            file_path: Path to the .dlg file
            parsed_data: Parsed GFF data

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
        tree = extractor.build_dialog_tree(parsed_data)
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

            async def call_api(
                sp: str, up: str, *, max_tokens: int = TRANSLATION_MAX_TOKENS
            ) -> str:
                return await self.provider.complete_json_chat_async(
                    sp,
                    up,
                    max_tokens=max_tokens,
                    temperature=TRANSLATION_TEMPERATURE,
                )

            from ..async_utils import run_async

            raw_response = run_async(
                call_api(system_prompt, user_prompt),
                cleanup=self.provider.close_async_client,
            )
            parsed_json = self._parse_json_response(raw_response, file_path.name)

            if parsed_json is None:
                logger.warning(
                    "%s: dialog JSON parse failed, retrying with repair prompt...",
                    file_path.name,
                )
                repair_prompt = self._build_repair_user_prompt(
                    file_path.name, script, keys_for_api, raw_response
                )
                raw_response = run_async(
                    call_api(system_prompt, repair_prompt),
                    cleanup=self.provider.close_async_client,
                )
                parsed_json = self._parse_json_response(raw_response, file_path.name)

            if parsed_json is None and self._dialog_response_likely_truncated(raw_response):
                logger.warning(
                    "%s: dialog JSON still invalid; retry with higher max_tokens...",
                    file_path.name,
                )
                repair_prompt = self._build_repair_user_prompt(
                    file_path.name, script, keys_for_api, raw_response
                )
                raw_response = run_async(
                    call_api(
                        system_prompt,
                        repair_prompt,
                        max_tokens=_DIALOG_MAX_TOKENS_BOOST,
                    ),
                    cleanup=self.provider.close_async_client,
                )
                parsed_json = self._parse_json_response(raw_response, file_path.name)

            if parsed_json is None:
                logger.error(
                    "%s: dialog translation failed after retries (invalid JSON).",
                    file_path.name,
                )
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
                        max_tokens=TRANSLATION_MAX_TOKENS,
                    )

                retry_raw = run_async(run_retry(), cleanup=self.provider.close_async_client)

                retry_json = self._parse_json_response(retry_raw, file_path.name)
                if retry_json is None and self._dialog_response_likely_truncated(
                    retry_raw
                ):
                    repair = self._build_repair_user_prompt(
                        file_path.name,
                        retry_script,
                        missing_keys,
                        retry_raw,
                    )
                    retry_raw = run_async(
                        call_api(
                            self._build_system_prompt(),
                            repair,
                            max_tokens=_DIALOG_MAX_TOKENS_BOOST,
                        ),
                        cleanup=self.provider.close_async_client,
                    )
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
        parsed = json_extract_first_object(raw)
        if parsed is not None:
            return parsed
        snippet = (raw or "").strip()[:400]
        logger.error(
            "Failed to parse JSON for %s (no valid object). Raw prefix: %s...",
            filename,
            snippet,
        )
        return None

    @staticmethod
    def _dialog_response_likely_truncated(raw: str) -> bool:
        """Heuristic: model hit max_tokens mid-string."""
        cleaned = strip_json_markdown_fences(raw)
        idx = cleaned.find("{")
        if idx == -1:
            return False
        try:
            json.JSONDecoder().raw_decode(cleaned, idx)
        except json.JSONDecodeError as e:
            msg = str(e).lower()
            return "unterminated" in msg
        return False

    def _build_repair_user_prompt(
        self,
        filename: str,
        script: str,
        keys_required: List[str],
        bad_response: str,
    ) -> str:
        """Ask the model to return a single valid JSON object after a failed parse."""
        keys_csv = ", ".join(sorted(keys_required))
        bad_snip = (bad_response or "").strip()[:1200]
        return (
            f"The previous answer for {filename} was not valid JSON or was truncated.\n"
            f"Return ONLY one JSON object: keys exactly {keys_csv} "
            f"(same IDs as in the script), each value a string translation.\n"
            f"No markdown, no comments, no text before or after the object.\n\n"
            f"Dialog script:\n\n{script}\n\n"
            f"Invalid previous output (truncated for context):\n{bad_snip}"
        )

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
                "file": file_path.name,
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
            f"Return ONLY a JSON object: map each line ID (e.g. E0, R1) to the "
            f"translated string. No markdown fences, no extra keys, no text outside JSON."
        )
