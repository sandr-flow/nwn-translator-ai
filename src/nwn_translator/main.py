"""Main orchestration for NWN module translation.

This module handles the complete workflow of translating a Neverwinter Nights module:
1. Extract .mod file
2. Parse all translatable resources
3. Translate content using AI
4. Inject translations back into GFF files
5. Create new .mod file

The per-stage logic lives in :mod:`nwn_translator.pipeline.stages`; this module
keeps the public entrypoints (:class:`ModuleTranslator`, :func:`rebuild_module`,
:func:`translate_module`, :func:`run_translation_pipeline`) and the offline
rebuild path.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tqdm import tqdm  # noqa: F401  (kept: pre-existing import)

from .config import (
    TranslationConfig,
    TRANSLATABLE_TYPES,
    create_output_path,
    module_string_encoding_for_target_lang,
)
from .file_handlers import (
    create_mod_from_directory,
)
from .extractors.base import ExtractedContent
from .context.world_context import WorldContext
from .glossary import Glossary
from .ai_providers import create_provider
from .telemetry import RunMetricsRecorder
from .pipeline.stages import (
    PipelineState,
    inject_translations_into_file,
    load_parsed_and_extracted,
    run_pipeline,
)

__all__ = [
    "ModuleTranslator",
    "rebuild_module",
    "translate_module",
    "run_translation_pipeline",
    "load_parsed_and_extracted",
    "inject_translations_into_file",
]

logger = logging.getLogger(__name__)


class ModuleTranslator:
    """Main translator for NWN modules.

    Thin facade over :class:`~nwn_translator.pipeline.stages.PipelineState`:
    builds the AI provider and run state, then runs the full pipeline via
    :func:`~nwn_translator.pipeline.stages.run_pipeline`.
    """

    def __init__(self, config: TranslationConfig):
        """Initialize the translator.

        Args:
            config: Translation configuration
        """
        self.config = config
        self.metrics_recorder = RunMetricsRecorder()

        # Create AI provider
        self.provider = create_provider(
            config.api_key,
            config.model,
            player_gender=config.player_gender,
            reasoning_effort=config.reasoning_effort,
            metrics_recorder=self.metrics_recorder,
        )

        self.state = PipelineState(
            config=config,
            provider=self.provider,
            metrics_recorder=self.metrics_recorder,
        )

    def translate(self) -> Path:
        """Translate the module.

        Returns:
            Path to translated .mod file

        Raises:
            Exception: If translation fails
        """
        return run_pipeline(self.state)

    # ── delegations preserved for external callers (web, tests) ─────────
    @property
    def extract_dir(self) -> Optional[Path]:
        return self.state.extract_dir

    @property
    def stats(self) -> Dict[str, Any]:
        return self.state.stats

    @property
    def world_context(self) -> Optional[WorldContext]:
        return self.state.world_context

    @property
    def glossary(self) -> Optional[Glossary]:
        return self.state.glossary

    def get_statistics(self) -> Dict[str, Any]:
        """Get translation statistics."""
        return self.state.get_statistics()

    def _record_ncs_patch_failure(self, file_path: Path, error: str) -> None:
        self.state._record_ncs_patch_failure(file_path, error)


def rebuild_module(
    extract_dir: Path,
    translations_by_item_id: Dict[str, Dict[str, str]],
    output_path: Path,
    original_mod_path: Path,
    target_lang: Optional[str] = None,
) -> Path:
    """Re-inject translations and reassemble a .mod without LLM calls.

    Translations are addressed by ``item_id``, not by original text: the
    extracted files on disk already hold the first-pass translation, so a
    text-keyed map would never match. For each file we re-extract its items
    (each carries a stable ``item_id`` and its current on-disk text) and build a
    ``{current_text: desired_text}`` map from *translations_by_item_id*, which
    the existing text-based injectors then apply. Unedited items map to
    themselves, so rebuild is idempotent.

    Args:
        extract_dir: Directory with previously extracted files.
        translations_by_item_id: ``{filename: {item_id: translated}}``.
        output_path: Where to write the rebuilt .mod file.
        original_mod_path: Path to the original .mod (needed for ERF header info).
        target_lang: Target language (drives GFF/NCS string encoding).

    Returns:
        Path to the rebuilt .mod file.
    """
    gff_cache: Dict[Path, Dict[str, Any]] = {}
    text_enc = module_string_encoding_for_target_lang(target_lang)
    # The extracted files on disk already hold first-pass translations, so both
    # re-extraction and injector-side re-reads decode with the target code page.
    read_enc = text_enc

    # NCS item_ids are globally unique (file stem + offset), so the per-file
    # maps can be flattened for the bytecode injector's by-item_id channel.
    ncs_by_item_id: Dict[str, str] = {}
    for fname, per_file in translations_by_item_id.items():
        if fname.lower().endswith(".ncs"):
            ncs_by_item_id.update(per_file)

    def _text_map(extracted: ExtractedContent, per_file: Dict[str, str]) -> Dict[str, str]:
        """Map each item's current on-disk text to its desired translation."""
        text_map: Dict[str, str] = {}
        for item in extracted.items:
            if item.item_id and item.item_id in per_file:
                text_map[item.text] = per_file[item.item_id]
        return text_map

    # Inject translations into each translatable file
    for file_path in extract_dir.rglob("*"):
        if not file_path.is_file():
            continue
        ext = file_path.suffix.lower()
        if ext not in TRANSLATABLE_TYPES:
            continue

        try:
            loaded = load_parsed_and_extracted(file_path, ext, gff_cache, source_encoding=read_enc)
        except Exception as e:
            logger.warning("Failed to read %s during rebuild: %s", file_path.name, e)
            continue
        if loaded is None:
            continue
        parsed_data, extracted = loaded
        per_file = translations_by_item_id.get(file_path.name, {})
        inject_translations_into_file(
            file_path,
            parsed_data,
            extracted,
            _text_map(extracted, per_file),
            ncs_translations_by_item_id=ncs_by_item_id,
            log_updates=False,
            target_lang=target_lang,
            source_encoding=read_enc,
        )

    # Patch .git area instance files (separate injector, also text-addressed)
    from .injectors.git_injector import patch_git_file

    for git_path in extract_dir.glob("*.git"):
        try:
            loaded = load_parsed_and_extracted(
                git_path, ".git", gff_cache, source_encoding=read_enc
            )
            if loaded is None:
                continue
            parsed_data, extracted = loaded
            per_file = translations_by_item_id.get(git_path.name, {})
            patch_git_file(
                git_path,
                _text_map(extracted, per_file),
                parsed_data=parsed_data,
                text_encoding=text_enc,
            )
        except Exception as e:
            logger.warning("Failed to patch %s during rebuild: %s", git_path.name, e)

    # Reassemble .mod
    create_mod_from_directory(extract_dir, output_path, original_mod_path)
    logger.info("Rebuild complete: %s", output_path)
    return output_path


def translate_module(config: TranslationConfig) -> Path:
    """Translate a NWN module.

    Args:
        config: Translation configuration

    Returns:
        Path to translated module file

    Raises:
        ValueError: If configuration is invalid
        Exception: If translation fails
    """
    result_path, _translator = run_translation_pipeline(config)
    return result_path


def run_translation_pipeline(config: TranslationConfig) -> Tuple[Path, ModuleTranslator]:
    """Validate config, run the translation pipeline, and return the translator.

    Shared by the public library entrypoint and the web task runner so the
    validation / startup path stays in one place.
    """
    # Validate configuration
    if not config.input_file.exists():
        raise ValueError(f"Input file not found: {config.input_file}")

    if config.input_file.suffix.lower() not in [".mod", ".erf", ".hak"]:
        raise ValueError(f"Input file must be a .mod, .erf, or .hak file")

    # Get API key
    config.api_key = config.get_api_key()

    # Create translator and translate
    translator = ModuleTranslator(config)
    result_path = translator.translate()
    return result_path, translator
