"""Contextual Translation Manager.

Translates entire dialog trees in a single batch using world context.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..ai_providers import BaseAIProvider
from ..ai_providers.openrouter_provider import OpenRouterProvider
from ..config import (
    TranslationCancelled,
    TranslationConfig,
    TRANSLATION_MAX_TOKENS,
    TRANSLATION_TEMPERATURE,
)
from ..context.dialog_formatter import DialogFormatter
from ..context.world_context import NPCInfo, WorldContext
from ..extractors.dialog_extractor import DialogExtractor, DialogNode
from ..json_utils import json_extract_first_object, strip_json_markdown_fences
from ..telemetry import llm_phase
from ..translation_logging import translation_log_writer_for_config
from .token_handler import TokenHandler, sanitize_text

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

# Upper bound for dialog responses when retrying after likely truncation.
# Kept local to the dialog path so future tuning does not affect the rest of
# the translation pipeline.
_DIALOG_TRUNCATION_MAX_TOKENS = 32768

# Chunking keeps large DLG files away from one huge JSON response. The limits
# are intentionally conservative and based on prompt characters, not exact
# tokenizer counts, because providers and models vary.
_DIALOG_CHUNK_TARGET_CHARS = 24000
_DIALOG_CHUNK_MAX_KEYS = 120


@dataclass
class _PreparedDialog:
    """Parsed dialog state shared by the cache pass and the API pass."""

    tree: List[DialogNode]
    node_map: Dict[str, DialogNode]
    original_text_map: Dict[str, str]
    sanitized_by_key: Dict[str, str]
    handlers: Dict[str, TokenHandler]
    speakers_block: str
    #: original_text -> final translation, served from the shared translation cache
    translations: Dict[str, str]
    keys_for_api: List[str]
    all_keys: List[str]


class ContextualTranslationManager:
    """Manager for full-graph contextual translation."""

    _TOKEN_RETRY_BUDGET = 2

    def __init__(
        self,
        config: TranslationConfig,
        provider: BaseAIProvider,
        world_context: WorldContext,
        translation_cache: Any = None,
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

    def _raise_if_cancelled(self) -> None:
        """Raise :class:`TranslationCancelled` when the config's cancel check fires."""
        cb = self.config.cancel_check
        if cb is not None and cb():
            raise TranslationCancelled("Translation cancelled by user")

    def translate_dialog(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        item_progress: Optional[Any] = None,
        item_budget: Optional[int] = None,
    ) -> Dict[str, str]:
        """Translate a complete dialog tree."""
        budget = int(item_budget) if item_budget else 0
        bumped = 0

        def _bump(by: int) -> None:
            nonlocal bumped
            if item_progress is None or by <= 0:
                return
            remaining = max(0, budget - bumped)
            delta = min(by, remaining) if budget else by
            if delta <= 0:
                return
            item_progress.bump(by=delta, filename=file_path.name)
            bumped += delta

        def _finish() -> None:
            if item_progress is not None and budget:
                _bump(budget - bumped)

        if not isinstance(self.provider, OpenRouterProvider):
            logger.error(
                "Contextual dialog translation requires OpenRouterProvider (got %s)",
                type(self.provider).__name__,
            )
            _finish()
            return {}

        provider: OpenRouterProvider = self.provider

        prepared = self._prepare_dialog(file_path, parsed_data)
        if prepared is None:
            _finish()
            return {}

        tree = prepared.tree
        node_map = prepared.node_map
        speakers_block = prepared.speakers_block
        original_text_map = prepared.original_text_map
        sanitized_by_key = prepared.sanitized_by_key
        handlers = prepared.handlers
        translations = prepared.translations
        keys_for_api = prepared.keys_for_api
        all_keys = prepared.all_keys

        cached_served = len(all_keys) - len(keys_for_api)
        if cached_served > 0:
            _bump(cached_served)
        if not keys_for_api:
            logger.debug(
                "All %d dialog lines for %s served from translation cache",
                len(all_keys),
                file_path.name,
            )
            _finish()
            return translations

        try:

            async def call_api(
                sp: str, up: str, *, max_tokens: int = TRANSLATION_MAX_TOKENS
            ) -> str:
                with llm_phase("dialog"):
                    return await provider.complete_json_chat_async(
                        sp,
                        up,
                        max_tokens=max_tokens,
                        temperature=TRANSLATION_TEMPERATURE,
                    )

            from ..async_utils import run_async

            dialog_chunks = self._build_dialog_chunks(
                keys_for_api,
                all_keys,
                tree,
                node_map,
                original_text_map,
                sanitized_by_key,
            )
            if not dialog_chunks:
                _finish()
                return translations

            if len(dialog_chunks) == 1:
                logger.info(
                    "Sending %d/%d dialog lines to AI for %s...",
                    len(keys_for_api),
                    len(original_text_map),
                    file_path.name,
                )
            else:
                logger.info(
                    "Sending %d/%d dialog lines to AI for %s in %d chunk(s)...",
                    len(keys_for_api),
                    len(original_text_map),
                    file_path.name,
                    len(dialog_chunks),
                )

            pending_keys: List[str] = []
            latest_invalid: Dict[str, Dict[str, Any]] = {}

            for chunk_index, (chunk_keys, script) in enumerate(dialog_chunks, 1):
                self._raise_if_cancelled()
                chunk_translations, chunk_pending, chunk_invalid = self._translate_dialog_chunk(
                    file_path,
                    file_path.stem,
                    script,
                    chunk_keys,
                    original_text_map,
                    handlers,
                    sanitized_by_key,
                    call_api,
                    run_async,
                    chunk_index=chunk_index,
                    total_chunks=len(dialog_chunks),
                    speakers_block=speakers_block,
                )
                translations.update(chunk_translations)
                _bump(len(chunk_translations))
                pending_keys.extend(chunk_pending)
                latest_invalid.update(chunk_invalid)

            pending_keys = sorted(set(pending_keys))

            if pending_keys:
                self._raise_if_cancelled()
                logger.warning(
                    "%s: retrying %d dialog nodes with missing or invalid preserved artifacts...",
                    file_path.name,
                    len(pending_keys),
                )
                retry_script = self.formatter.format_nodes(
                    pending_keys,
                    node_map,
                    original_text_map,
                    text_overrides=sanitized_by_key,
                )
                retry_prompt = self._build_token_retry_user_prompt(
                    file_path.name,
                    retry_script,
                    pending_keys,
                    handlers,
                    {key: latest_invalid.get(key, {}).get("report") for key in pending_keys},
                )
                retry_raw = run_async(
                    call_api(
                        self._build_system_prompt(
                            source_text=retry_script,
                            filename_stem=file_path.stem,
                            speakers_block=speakers_block,
                        ),
                        retry_prompt,
                        max_tokens=TRANSLATION_MAX_TOKENS,
                    ),
                )
                retry_json = self._parse_json_response(retry_raw, file_path.name)
                if retry_json is None and self._dialog_response_likely_truncated(retry_raw):
                    logger.warning(
                        "%s: pending dialog retry JSON looks truncated; "
                        "retrying the same JSON retry prompt with higher max_tokens...",
                        file_path.name,
                    )
                    retry_raw = run_async(
                        call_api(
                            self._build_system_prompt(
                                source_text=retry_script,
                                filename_stem=file_path.stem,
                                speakers_block=speakers_block,
                            ),
                            retry_prompt,
                            max_tokens=_DIALOG_TRUNCATION_MAX_TOKENS,
                        ),
                    )
                    retry_json = self._parse_json_response(retry_raw, file_path.name)

                pending_after_json = list(pending_keys)
                if retry_json:
                    retry_translations, retry_invalid = self._apply_translations(
                        retry_json,
                        original_text_map,
                        handlers,
                        file_path,
                        sanitized_by_key=sanitized_by_key,
                        session_cache=self.translation_cache,
                        allow_cleanup=False,
                    )
                    translations.update(retry_translations)
                    _bump(len(retry_translations))
                    logger.info(
                        "%s: retry recovered %d additional dialog translations.",
                        file_path.name,
                        len(retry_translations),
                    )
                    for key in pending_keys:
                        if key in retry_json and key not in retry_invalid:
                            latest_invalid.pop(key, None)
                    latest_invalid.update(retry_invalid)
                    pending_after_json = sorted(
                        set(
                            [key for key in pending_keys if key not in retry_json]
                            + list(retry_invalid.keys())
                        )
                    )

                if pending_after_json:
                    logger.warning(
                        "%s: %d dialog nodes still invalid after JSON retry; retrying individually.",
                        file_path.name,
                        len(pending_after_json),
                    )
                    glossary_block = self._glossary_block_for_texts(
                        [sanitized_by_key[key] for key in pending_after_json]
                    )
                    for key in pending_after_json:
                        self._raise_if_cancelled()
                        context = self._build_dialog_retry_context(
                            key,
                            node_map,
                            handlers,
                            latest_invalid.get(key, {}).get("report"),
                            file_path.name,
                            self._TOKEN_RETRY_BUDGET,
                        )

                        async def run_single_retry() -> Any:
                            return await provider.translate_async(
                                text=sanitized_by_key[key],
                                source_lang=self.config.source_lang,
                                target_lang=self.config.target_lang,
                                context=context,
                                glossary_block=glossary_block,
                                content_profile=None,
                            )

                        try:
                            line_result = run_async(run_single_retry())
                        except TranslationCancelled:
                            raise
                        except Exception as exc:
                            logger.warning(
                                "%s: individual dialog retry failed for %s: %s",
                                file_path.name,
                                key,
                                exc,
                            )
                            line_result = None

                        if line_result is not None and getattr(line_result, "success", False):
                            single_translations, single_invalid = self._apply_translations(
                                {key: line_result.translated},
                                original_text_map,
                                handlers,
                                file_path,
                                sanitized_by_key=sanitized_by_key,
                                session_cache=self.translation_cache,
                                allow_cleanup=False,
                            )
                            if single_translations:
                                translations.update(single_translations)
                                _bump(len(single_translations))
                                latest_invalid.pop(key, None)
                                continue
                            latest_invalid[key] = single_invalid.get(
                                key,
                                {
                                    "translated_sanitized": line_result.translated,
                                    "report": None,
                                },
                            )

                        cleanup_payload = latest_invalid.get(
                            key,
                            {"translated_sanitized": "", "report": None},
                        )
                        cleaned_translations, _ = self._apply_translations(
                            {key: cleanup_payload.get("translated_sanitized", "")},
                            original_text_map,
                            handlers,
                            file_path,
                            sanitized_by_key=sanitized_by_key,
                            session_cache=self.translation_cache,
                            allow_cleanup=True,
                        )
                        if cleaned_translations:
                            translations.update(cleaned_translations)
                            _bump(len(cleaned_translations))

            _finish()
            return translations

        except TranslationCancelled:
            raise
        except Exception as exc:
            logger.error("Contextual translation failed for %s: %s", file_path.name, exc)
            _finish()
            return translations

    def translate_dialogs(
        self,
        dialog_files: List[tuple[Path, Dict[str, Any], int]],
        item_progress: Optional[Any] = None,
    ) -> tuple[Dict[str, str], List[tuple[Path, Exception]]]:
        """Translate multiple dialog files concurrently on a thread pool.

        Each *dialog_files* entry is ``(file_path, parsed_data, item_budget)``.
        Failures are isolated per file and returned alongside the aggregated
        translations; :class:`TranslationCancelled` aborts the whole pool.

        The shared ``translation_cache`` dict is read and written from worker
        threads without a lock: single dict operations are atomic under the
        GIL, and a lost race only costs one duplicate API request.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_translations: Dict[str, str] = {}
        errors: List[tuple[Path, Exception]] = []
        if not dialog_files:
            return all_translations, errors

        max_workers = max(1, int(getattr(self.config, "max_concurrent_requests", 4) or 1))

        def translate_one(
            file_path: Path, parsed_data: Dict[str, Any], item_budget: int
        ) -> Dict[str, str]:
            self._raise_if_cancelled()
            return self.translate_dialog(
                file_path,
                parsed_data,
                item_progress=item_progress,
                item_budget=item_budget,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(translate_one, file_path, parsed_data, item_budget): file_path
                for file_path, parsed_data, item_budget in dialog_files
            }
            try:
                for future in as_completed(future_to_path):
                    file_path = future_to_path[future]
                    try:
                        translations = future.result()
                    except TranslationCancelled:
                        raise
                    except Exception as exc:
                        errors.append((file_path, exc))
                    else:
                        if translations:
                            all_translations.update(translations)
            except TranslationCancelled:
                for pending in future_to_path:
                    pending.cancel()
                raise
        return all_translations, errors

    def _prepare_dialog(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
    ) -> Optional[_PreparedDialog]:
        """Parse the dialog tree, sanitize node texts, and serve cache hits.

        Returns ``None`` when the dialog has no tree at all.
        """
        extractor = DialogExtractor()

        tree = extractor.build_dialog_tree(parsed_data)
        if not tree:
            return None

        node_map: Dict[str, DialogNode] = {}

        def collect_nodes(nodes: List[DialogNode]) -> None:
            for node in nodes:
                key = f"{'E' if node.is_entry else 'R'}{node.node_id}"
                if key not in node_map:
                    node_map[key] = node
                    collect_nodes(node.replies)

        collect_nodes(tree)

        speakers_block = self._build_speakers_block(file_path.stem, node_map)

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
                outcome = handlers[key].finalize_translation(
                    self.translation_cache[san],
                    allow_cleanup=True,
                )
                translations[original_text] = outcome.final_text
            else:
                keys_for_api.append(key)

        return _PreparedDialog(
            tree=tree,
            node_map=node_map,
            original_text_map=original_text_map,
            sanitized_by_key=sanitized_by_key,
            handlers=handlers,
            speakers_block=speakers_block,
            translations=translations,
            keys_for_api=keys_for_api,
            all_keys=list(original_text_map.keys()),
        )

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
        except json.JSONDecodeError as exc:
            msg = str(exc).lower()
            return "unterminated" in msg
        return False

    def _build_dialog_chunks(
        self,
        keys_for_api: List[str],
        all_keys: List[str],
        tree: List[DialogNode],
        node_map: Dict[str, DialogNode],
        original_text_map: Dict[str, str],
        sanitized_by_key: Dict[str, str],
    ) -> List[tuple[List[str], str]]:
        """Return one full-dialog script or smaller node chunks for large dialogs."""
        if set(keys_for_api) == set(all_keys):
            full_script = self.formatter.format_dialog_tree(
                tree,
                text_overrides=sanitized_by_key,
            )
        else:
            full_script = self.formatter.format_nodes(
                keys_for_api,
                node_map,
                original_text_map,
                text_overrides=sanitized_by_key,
            )

        if not full_script:
            return []

        if (
            len(full_script) <= _DIALOG_CHUNK_TARGET_CHARS
            and len(keys_for_api) <= _DIALOG_CHUNK_MAX_KEYS
        ):
            return [(list(keys_for_api), full_script)]

        chunks: List[tuple[List[str], str]] = []
        current_keys: List[str] = []
        current_chars = 0

        for key in keys_for_api:
            node_script = self.formatter.format_nodes(
                [key],
                node_map,
                original_text_map,
                text_overrides=sanitized_by_key,
            )
            if not node_script:
                continue

            would_exceed_chars = (
                current_keys and current_chars + len(node_script) > _DIALOG_CHUNK_TARGET_CHARS
            )
            would_exceed_keys = current_keys and len(current_keys) >= _DIALOG_CHUNK_MAX_KEYS
            if would_exceed_chars or would_exceed_keys:
                script = self.formatter.format_nodes(
                    current_keys,
                    node_map,
                    original_text_map,
                    text_overrides=sanitized_by_key,
                )
                if script:
                    chunks.append((list(current_keys), script))
                current_keys = []
                current_chars = 0

            current_keys.append(key)
            current_chars += len(node_script)

        if current_keys:
            script = self.formatter.format_nodes(
                current_keys,
                node_map,
                original_text_map,
                text_overrides=sanitized_by_key,
            )
            if script:
                chunks.append((list(current_keys), script))

        return chunks

    def _translate_dialog_chunk(
        self,
        file_path: Path,
        filename_stem: str,
        script: str,
        keys_for_api: List[str],
        original_text_map: Dict[str, str],
        handlers: Dict[str, Any],
        sanitized_by_key: Dict[str, str],
        call_api: Any,
        run_async: Any,
        *,
        chunk_index: int,
        total_chunks: int,
        speakers_block: str = "",
    ) -> tuple[Dict[str, str], List[str], Dict[str, Dict[str, Any]]]:
        """Translate one dialog script chunk and return accepted plus pending keys."""
        system_prompt = self._build_system_prompt(
            source_text=script,
            filename_stem=filename_stem,
            speakers_block=speakers_block,
        )
        user_prompt = self._build_user_prompt(file_path.name, script)
        if total_chunks > 1:
            logger.info(
                "%s: translating dialog chunk %d/%d (%d node(s), %d chars).",
                file_path.name,
                chunk_index,
                total_chunks,
                len(keys_for_api),
                len(script),
            )

        raw_response = run_async(call_api(system_prompt, user_prompt))
        parsed_json = self._parse_json_response(raw_response, file_path.name)

        if parsed_json is None:
            truncation_like_invalid_json = self._dialog_response_likely_truncated(raw_response)
            if truncation_like_invalid_json:
                logger.warning(
                    "%s: dialog JSON parse failed with truncation-like invalid JSON; "
                    "retrying original prompt with higher max_tokens...",
                    file_path.name,
                )
                raw_response = run_async(
                    call_api(
                        system_prompt,
                        user_prompt,
                        max_tokens=_DIALOG_TRUNCATION_MAX_TOKENS,
                    ),
                )
                parsed_json = self._parse_json_response(raw_response, file_path.name)

                if parsed_json is None:
                    logger.warning(
                        "%s: high-token original prompt retry still returned invalid JSON; "
                        "retrying repair prompt with higher max_tokens as final fallback...",
                        file_path.name,
                    )
                    repair_prompt = self._build_repair_user_prompt(
                        file_path.name,
                        script,
                        keys_for_api,
                        raw_response,
                    )
                    raw_response = run_async(
                        call_api(
                            system_prompt,
                            repair_prompt,
                            max_tokens=_DIALOG_TRUNCATION_MAX_TOKENS,
                        ),
                    )
                    parsed_json = self._parse_json_response(raw_response, file_path.name)
            else:
                logger.warning(
                    "%s: dialog JSON parse failed with non-truncation invalid JSON; "
                    "retrying with repair prompt...",
                    file_path.name,
                )
                repair_prompt = self._build_repair_user_prompt(
                    file_path.name,
                    script,
                    keys_for_api,
                    raw_response,
                )
                raw_response = run_async(call_api(system_prompt, repair_prompt))
                parsed_json = self._parse_json_response(raw_response, file_path.name)

                if parsed_json is None:
                    logger.warning(
                        "%s: repair prompt still returned invalid JSON; "
                        "retrying repair prompt with higher max_tokens as final fallback...",
                        file_path.name,
                    )
                    raw_response = run_async(
                        call_api(
                            system_prompt,
                            repair_prompt,
                            max_tokens=_DIALOG_TRUNCATION_MAX_TOKENS,
                        ),
                    )
                    parsed_json = self._parse_json_response(raw_response, file_path.name)

        if parsed_json is None:
            logger.error(
                "%s: dialog translation chunk %d/%d failed after retries (invalid JSON).",
                file_path.name,
                chunk_index,
                total_chunks,
            )
            return {}, list(keys_for_api), {}

        api_translations, invalid_nodes = self._apply_translations(
            parsed_json,
            original_text_map,
            handlers,
            file_path,
            sanitized_by_key=sanitized_by_key,
            session_cache=self.translation_cache,
            allow_cleanup=False,
        )
        missing_keys = [key for key in keys_for_api if key not in parsed_json]
        pending_keys = sorted(set(missing_keys + list(invalid_nodes.keys())))
        return api_translations, pending_keys, dict(invalid_nodes)

    def _build_dialog_retry_context(
        self,
        key: str,
        node_map: Dict[str, DialogNode],
        handlers: Dict[str, TokenHandler],
        mismatch_report: Optional[Any],
        filename: str,
        attempt: int,
    ) -> str:
        """Build a stricter line-level retry context for one dialog node."""
        node = node_map.get(key)
        parts: List[str] = [f"Dialog node {key} in {filename}."]
        if node is not None:
            speaker = node.speaker or ("NPC" if node.is_entry else "Player")
            parts.append(f"Speaker: {speaker}.")
        parts.append(
            "TOKEN/TAG PRESERVATION RETRY: preserve every placeholder and helper token "
            "surrogate exactly as it appears in the source text. Do not rename, reorder, "
            "delete, duplicate, or replace any placeholder."
        )
        parts.append(
            "If the line contains NWN inline markup such as StartAction, StartCheck, "
            "StartHighlight, or </Start>, preserve that markup exactly after restoration. "
            "Translate only normal prose and text inside square brackets."
        )
        expected = handlers[key].get_expected_artifact_sequence()
        if expected:
            parts.append("Expected preserved artifacts after restoration: " + " | ".join(expected))
        if mismatch_report is not None and not mismatch_report.is_exact_match:
            parts.append(f"Previous mismatch type: {mismatch_report.mismatch_type}.")
            if mismatch_report.actual_sequence:
                parts.append(
                    "Previous restored artifact sequence: "
                    + " | ".join(mismatch_report.actual_sequence)
                )
        parts.append(f"Retry attempt {attempt} of {self._TOKEN_RETRY_BUDGET}.")
        return "\n".join(parts)

    def _build_token_retry_user_prompt(
        self,
        filename: str,
        script: str,
        keys_required: List[str],
        handlers: Dict[str, TokenHandler],
        mismatch_reports: Dict[str, Any],
    ) -> str:
        """Ask the model to regenerate only invalid/missing nodes with strict preservation."""
        keys_csv = ", ".join(sorted(keys_required))
        lines = [
            f"The previous answer for {filename} changed, dropped, or omitted preserved NWN tags/tokens for keys: {keys_csv}.",
            f"Return ONLY one JSON object: keys exactly {keys_csv} (same IDs as in the script), each value a string translation.",
            "Preserve every placeholder and helper token surrogate EXACTLY as it appears in the script.",
            "Do not rename, reorder, delete, duplicate, or replace any placeholder.",
            "Translate only the normal prose and the text inside square brackets.",
            "",
            "Expected preserved artifacts after restoration:",
        ]
        for key in sorted(keys_required):
            expected = handlers[key].get_expected_artifact_sequence()
            if expected:
                lines.append(f"- {key}: " + " | ".join(expected))
            report = mismatch_reports.get(key)
            if report is not None and not report.is_exact_match and report.actual_sequence:
                lines.append("  previous restored sequence: " + " | ".join(report.actual_sequence))
        lines.extend(["", "Dialog script:", "", script])
        return "\n".join(lines)

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
        *,
        allow_cleanup: bool = False,
    ) -> tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
        """Restore, validate, and return accepted plus invalid dialog nodes."""
        translations: Dict[str, str] = {}
        invalid: Dict[str, Dict[str, Any]] = {}
        for key, translated_sanitized in parsed_json.items():
            if key not in original_text_map:
                continue
            original_text = original_text_map[key]
            if translated_sanitized is None:
                translated_sanitized = ""
            elif not isinstance(translated_sanitized, str):
                translated_sanitized = str(translated_sanitized)

            outcome = handlers[key].finalize_translation(
                translated_sanitized,
                allow_cleanup=allow_cleanup,
            )
            if not outcome.exact_valid and not outcome.used_cleanup:
                invalid[key] = {
                    "translated_sanitized": translated_sanitized,
                    "report": outcome.mismatch_report,
                }
                logger.warning(
                    "%s: token/tag mismatch for dialog node %s (%s). expected=%s actual=%s",
                    file_path.name,
                    key,
                    outcome.mismatch_report.mismatch_type,
                    outcome.mismatch_report.expected_sequence,
                    outcome.mismatch_report.actual_sequence,
                )
                continue

            final_translated = outcome.final_text
            translations[original_text] = final_translated

            if outcome.exact_valid and session_cache is not None and sanitized_by_key is not None:
                san = sanitized_by_key.get(key)
                if san:
                    session_cache[san] = translated_sanitized

            if outcome.used_cleanup:
                logger.warning(
                    "%s: accepted cleaned dialog translation for node %s after token/tag mismatch cleanup.",
                    file_path.name,
                    key,
                )

            log_entry = {
                "original": original_text,
                "translated": final_translated,
                "context": f"Dialog node {key} in {file_path.name}",
                "model": self.provider.model,
                "file": file_path.name,
            }
            try:
                self._log_writer.write(log_entry)
            except Exception as log_exc:
                logger.debug("Failed to write to translation log: %s", log_exc)
        return translations, invalid

    def _build_speakers_block(
        self,
        file_stem: str,
        node_map: Dict[str, DialogNode],
    ) -> str:
        """Describe known dialog speakers so the model uses correct gender forms.

        The dialog's owner NPC almost never mentions their own name in their
        lines, so the relevance-filtered WORLD CONTEXT block usually omits them
        and the model has to guess the speaker's gender. This block resolves
        speakers deterministically: creatures whose ``Conversation`` resref
        matches the .dlg stem own the unmarked ``[NPC]`` lines, and per-entry
        ``Speaker`` tags are looked up directly.
        """
        if self.world_context is None or not self.world_context.npcs:
            return ""

        def describe(npc: NPCInfo) -> str:
            name = " ".join(
                str(p).strip() for p in (npc.first_name, npc.last_name) if p and str(p).strip()
            )
            name = name or npc.tag
            traits = ", ".join(t for t in (npc.race, npc.gender) if t)
            return f"{name} ({traits})" if traits else name

        lines: List[str] = []
        stem_key = file_stem.casefold()
        owner_descs = sorted(
            {
                describe(npc)
                for npc in self.world_context.npcs.values()
                if str(npc.conversation).casefold() == stem_key
            }
        )
        if owner_descs:
            lines.append("- Lines marked [NPC]: spoken by " + "; or ".join(owner_descs))

        tags = sorted(
            {node.speaker for node in node_map.values() if node.is_entry and node.speaker}
        )
        for tag in tags:
            npc = self.world_context.npcs.get(tag)
            if npc is not None:
                lines.append(f"- Lines marked [{tag}]: spoken by {describe(npc)}")

        if not lines:
            return ""
        lines.append(
            "Use each speaker's gender for their grammatical forms (verb endings, "
            "adjectives, self-references) in the lines they speak."
        )
        return "DIALOG SPEAKERS:\n" + "\n".join(lines)

    def _build_system_prompt(
        self,
        source_text: str = "",
        filename_stem: str = "",
        speakers_block: str = "",
    ) -> Any:
        """Build the system ``content`` payload for a dialog translation call."""
        from ..prompts import build_dialog_system_prompt_parts
        from ..race_dictionary import match_race_terms

        corpus = [text for text in (source_text, filename_stem) if text]

        if self.world_context is not None and corpus:
            world_block = self.world_context.to_prompt_block(
                glossary=self.glossary,
                target_lang=self.config.target_lang,
                source_texts=corpus,
            )
        elif self.world_context is not None:
            world_block = self.world_context.to_prompt_block(
                glossary=self.glossary,
                target_lang=self.config.target_lang,
            )
        else:
            world_block = ""

        if self.glossary and getattr(self.glossary, "entries", None):
            glossary_block = (
                self.glossary.to_prompt_block(texts=corpus)
                if corpus
                else self.glossary.to_prompt_block()
            )
        else:
            glossary_block = ""

        race_block = match_race_terms(source_text, self.config.target_lang)
        if race_block:
            glossary_block = glossary_block + "\n\n" + race_block if glossary_block else race_block

        if speakers_block:
            world_block = f"{speakers_block}\n\n{world_block}" if world_block else speakers_block

        stable, variable = build_dialog_system_prompt_parts(
            self.config.target_lang,
            self.config.player_gender,
            world_block,
            glossary_block,
        )
        return self.provider.make_system_message_content(stable, variable)

    def _build_user_prompt(self, filename: str, script: str) -> str:
        """Build the user prompt containing the dialog script."""
        return (
            f"Translate the following dialog script from {filename}:\n\n"
            f"{script}\n\n"
            f"Return ONLY a JSON object: map each line ID (e.g. E0, R1) to the "
            f"translated string. No markdown fences, no extra keys, no text outside JSON."
        )

    def _glossary_block_for_texts(self, texts: List[str]) -> Optional[str]:
        """Return a glossary block narrowed to entries present in *texts*."""
        if not self.glossary or not getattr(self.glossary, "entries", None):
            return None
        block = self.glossary.to_prompt_block(texts=texts)
        return block or None
