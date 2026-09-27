"""Library entry points of the NWN module translator.

:func:`translate_module` translates a module end to end; :func:`run_translation_pipeline`
also returns the :class:`ModuleTranslator` with the statistics of the run.
:func:`rebuild_module` writes edited translations into the unpacked files of a
finished run without model requests. The stages live in :mod:`nwn_translator.pipeline.stages`.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import TranslationConfig, module_string_encoding_for_target_lang
from .context.world_context import WorldContext
from .extractors.base import occurrence_key
from .formats.erf import create_mod_from_directory
from .glossary import Glossary
from .pipeline.stages import (
    PipelineState,
    find_translatable_files,
    inject_translations_into_file,
    load_parsed_and_extracted,
    run_pipeline,
)

__all__ = ["ModuleTranslator", "rebuild_module", "translate_module", "run_translation_pipeline"]

logger = logging.getLogger(__name__)

#: Archive kinds a run accepts.
_ARCHIVE_SUFFIXES = (".mod", ".erf", ".hak")


class ModuleTranslator:
    """One translation run: a pipeline state with the provider for its API key.

    Attributes:
        config: Run settings.
        state: Pipeline state of the run.
        provider: Model provider of the run.
        metrics_recorder: Request metrics of the run.
    """

    def __init__(self, config: TranslationConfig):
        """Creates the provider and the pipeline state of a run.

        Args:
            config: Run settings.
        """
        self.config = config
        self.state = PipelineState.create(config)
        self.provider = self.state.provider
        self.metrics_recorder = self.state.metrics_recorder

    def translate(self) -> Path:
        """Runs every stage and returns the translated module."""
        return run_pipeline(self.state)

    @property
    def extract_dir(self) -> Optional[Path]:
        """Unpacked module; kept after the run only with ``config.skip_cleanup``."""
        return self.state.extract_dir

    @property
    def stats(self) -> Dict[str, Any]:
        """Run statistics as the stages fill them in."""
        return self.state.stats

    @property
    def world_context(self) -> Optional[WorldContext]:
        """Scanned module objects (context mode)."""
        return self.state.world_context

    @property
    def glossary(self) -> Optional[Glossary]:
        """Proper-name glossary of the run (context mode)."""
        return self.state.glossary

    def get_statistics(self) -> Dict[str, Any]:
        """Returns the run statistics (see :meth:`PipelineState.get_statistics`)."""
        return self.state.get_statistics()


def rebuild_module(
    extract_dir: Path,
    translations_by_item_id: Dict[str, Dict[str, str]],
    output_path: Path,
    original_mod_path: Path,
    target_lang: Optional[str] = None,
) -> Path:
    """Re-injects translations and reassembles a .mod without model requests.

    Translations are addressed by ``item_id``, not by original text: the files on
    disk already hold the first-pass translation. Only files with an addressed
    translation are re-extracted (for their current field offsets) and patched;
    the others are packed as they are.

    Args:
        extract_dir: Directory with previously extracted files.
        translations_by_item_id: ``{filename: {item_id: translated}}``.
        output_path: Where to write the rebuilt .mod file.
        original_mod_path: The original .mod (its ERF header is copied).
        target_lang: Target language (drives GFF/NCS string encoding).

    Returns:
        Path to the rebuilt .mod file.
    """
    # The files hold first-pass translations, so re-extraction and the
    # injectors' re-reads both decode with the target code page.
    encoding = module_string_encoding_for_target_lang(target_lang)
    translations = {
        occurrence_key(filename, item_id): text
        for filename, per_file in translations_by_item_id.items()
        for item_id, text in per_file.items()
    }
    addressed_files = {resource for resource, _item_id in translations}
    gff_cache: Dict[Path, Dict[str, Any]] = {}
    for file_path in find_translatable_files(extract_dir):
        if file_path.name not in addressed_files:
            continue
        try:
            loaded = load_parsed_and_extracted(
                file_path, file_path.suffix.lower(), gff_cache, source_encoding=encoding
            )
        except Exception as e:
            logger.warning("Failed to read %s during rebuild: %s", file_path.name, e)
            continue
        if loaded is not None:
            parsed_data, extracted = loaded
            inject_translations_into_file(
                file_path,
                parsed_data,
                extracted,
                translations,
                target_lang=target_lang,
                source_encoding=encoding,
            )

    create_mod_from_directory(extract_dir, output_path, original_mod_path)
    logger.info("Rebuild complete: %s", output_path)
    return output_path


def translate_module(config: TranslationConfig) -> Path:
    """Translates a NWN module.

    Args:
        config: Run settings.

    Returns:
        The translated module.

    Raises:
        ValueError: If the input is missing or not an archive, or no API key is set.
        TranslationCancelled: If the run is cancelled.
    """
    return run_translation_pipeline(config)[0]


def run_translation_pipeline(config: TranslationConfig) -> Tuple[Path, ModuleTranslator]:
    """Validates *config*, translates the module and returns the translator too.

    Args:
        config: Run settings.

    Returns:
        The translated module and the :class:`ModuleTranslator` of the run.

    Raises:
        ValueError: If the input is missing or not an archive, or no API key is set.
        TranslationCancelled: If the run is cancelled.
    """
    if not config.input_file.exists():
        raise ValueError(f"Input file not found: {config.input_file}")
    if config.input_file.suffix.lower() not in _ARCHIVE_SUFFIXES:
        raise ValueError("Input file must be a .mod, .erf, or .hak file")
    config.get_api_key()  # raises ValueError without a key, before any work

    translator = ModuleTranslator(config)
    return translator.translate(), translator
