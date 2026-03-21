"""Contextual Translation Manager.

Translates entire dialog trees in a single batch using world context.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import TranslationConfig
from ..ai_providers import BaseAIProvider
from ..translation_logging import translation_log_writer_for_config
from ..extractors.dialog_extractor import DialogExtractor, DialogNode
from ..context.world_context import WorldContext
from ..context.dialog_formatter import DialogFormatter
from .token_handler import TokenHandler, sanitize_text, restore_text

logger = logging.getLogger(__name__)

class ContextualTranslationManager:
    """Manager for full-graph contextual translation."""

    def __init__(
        self,
        config: TranslationConfig,
        provider: BaseAIProvider,
        world_context: WorldContext
    ):
        self.config = config
        self.provider = provider
        self.world_context = world_context
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
        handlers: Dict[str, TokenHandler] = {}
        
        for key, node in node_map.items():
            original_text = node.text
            if original_text is None or not str(original_text).strip():
                continue
                
            original_text_map[key] = original_text
            
            sanitized, handler = sanitize_text(
                original_text,
                preserve_tokens=self.config.preserve_tokens
            )
            handlers[key] = handler
            # Temporarily replace node text with sanitized text for the script formatting
            node.text = sanitized

        # Generate full script using sanitized text
        script = self.formatter.format_dialog_tree(tree)
        if not script:
            return {}

        # Build prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(file_path.name, script)

        logger.info(f"Sending {len(original_text_map)} dialog lines to AI for {file_path.name}...")
        
        try:
            # Send to provider
            response = self.provider.client.chat.completions.create(
                model=self.provider.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=16384,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            raw_response = (content or "").strip()
            parsed_json = self._parse_json_response(raw_response, file_path.name)
            if parsed_json is None:
                return {}

            translations = self._apply_translations(
                parsed_json, original_text_map, handlers, file_path
            )

            # Retry for any nodes the model missed
            missing_keys = [k for k in original_text_map if k not in parsed_json]
            if missing_keys:
                logger.warning(
                    "%s: %d/%d nodes were not translated, retrying missing nodes...",
                    file_path.name, len(missing_keys), len(original_text_map),
                )
                retry_script = self.formatter.format_nodes(
                    missing_keys, node_map, original_text_map
                )
                retry_response = self.provider.client.chat.completions.create(
                    model=self.provider.model,
                    messages=[
                        {"role": "system", "content": self._build_system_prompt()},
                        {"role": "user", "content": self._build_user_prompt(
                            file_path.name, retry_script
                        )},
                    ],
                    temperature=0.3,
                    max_tokens=16384,
                    response_format={"type": "json_object"},
                )
                retry_content = retry_response.choices[0].message.content
                retry_raw = (retry_content or "").strip()
                retry_json = self._parse_json_response(retry_raw, file_path.name)
                if retry_json:
                    retry_translations = self._apply_translations(
                        retry_json, original_text_map, handlers, file_path
                    )
                    translations.update(retry_translations)
                    logger.info(
                        "%s: retry recovered %d additional translations.",
                        file_path.name, len(retry_translations),
                    )

            return translations

        except Exception as e:
            logger.error(f"Contextual translation failed for {file_path.name}: {e}")
            return {}

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
        world_block = self.world_context.to_prompt_block()
        
        return (
            f"You are an elite translator for the game Neverwinter Nights.\n"
            f"Your task is to translate entire dialogue scripts to {self.config.target_lang} "
            f"according to Nora Gal's Golden School of Translation.\n\n"
            f"{world_block}\n\n"
            f"RULES:\n"
            f"1. You will receive a dialogue script. Each line to translate is marked with an ID like [E0] or [R1], inside <<< >>>.\n"
            f"2. Translate ONLY the text inside <<< >>>. Do NOT translate the routing hints (like '-> Player Reply').\n"
            f"3. Use the WORLD CONTEXT to understand who is speaking to whom, ensuring gender and rank appropriate phrasing.\n"
            f"4. Preserve all special tokens exactly as they are (e.g., <<TOKEN_0>>).\n"
            f"5. Maintain natural phrasing, emotion, and tone.\n\n"
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
