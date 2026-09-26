"""Patch translated string constants into compiled scripts (``.ncs``).

The binary patching itself is done by
:mod:`~nwn_translator.file_handlers.ncs_patcher`.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..extractors.base import TranslatableItem, Translations
from ..file_handlers.ncs_concat import parts_from_metadata, split_concat_translation
from ..file_handlers.ncs_patcher import NCSPatchError, patch_ncs_string_replacements
from .base import InjectedContent, changed_translations

logger = logging.getLogger(__name__)


def inject_ncs(
    file_path: Path,
    items: Sequence[TranslatableItem],
    translations: Translations,
    *,
    content_type: str,
    text_encoding: str,
    source_encoding: Optional[str],
) -> InjectedContent:
    """Replace the translated literals of a script in one patch.

    A concat chain is split back into its literals; a chain whose translation
    cannot be split keeps its original text and is reported as a failure.

    Args:
        file_path: Script to patch.
        items: Items extracted from the script (``offset`` or ``concat_parts``
            in their metadata).
        translations: Translated text by occurrence.
        content_type: Content type of the extraction, reported back.
        text_encoding: Code page of the written strings.
        source_encoding: Code page used to decode the literals at extraction;
            the patcher re-reads the file and compares with it.

    Returns:
        The injection result. Its metadata carries ``error`` and
        ``ncs_patch_failed`` when the patch or a concat split failed, and
        ``concat_split_failed`` (item ids) for failed splits.
    """
    replacements: List[Tuple[int, str, str]] = []
    split_failed: List[str] = []
    for item, translated in changed_translations(items, translations):
        concat_parts = item.metadata.get("concat_parts")
        if not concat_parts:
            replacements.append((int(item.metadata["offset"]), item.text, translated))
            continue
        split = split_concat_translation(parts_from_metadata(concat_parts), translated)
        if split is None:
            logger.warning(
                "NCS concat split failed for %s in %s; leaving original",
                item.item_id,
                file_path.name,
            )
            split_failed.append(str(item.item_id))
            continue
        replacements.extend(split)

    metadata: Dict[str, Any] = {"type": content_type}
    if split_failed:
        metadata.update(
            error="concat_split_failed: " + ", ".join(split_failed),
            ncs_patch_failed=True,
            concat_split_failed=split_failed,
        )
    if not replacements:
        return InjectedContent(
            source_file=file_path, modified=False, items_updated=0, metadata=metadata
        )

    try:
        patched_count = patch_ncs_string_replacements(
            file_path, replacements, text_encoding=text_encoding, source_encoding=source_encoding
        )
    except NCSPatchError as e:
        logger.error("Failed to patch NCS file %s: %s", file_path.name, e)
        return InjectedContent(
            source_file=file_path,
            modified=False,
            items_updated=0,
            metadata={"type": content_type, "error": str(e), "ncs_patch_failed": True},
        )
    return InjectedContent(
        source_file=file_path,
        modified=patched_count > 0,
        items_updated=patched_count,
        metadata=metadata,
    )
