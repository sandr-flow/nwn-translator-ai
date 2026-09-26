"""Patch the GFF field records of extracted items."""

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..extractors.base import TranslatableItem, Translations
from ..formats.gff import GFFPatcher
from .base import InjectedContent, changed_translations


def inject_gff(
    file_path: Path,
    items: Sequence[TranslatableItem],
    translations: Translations,
    *,
    content_type: str,
    text_encoding: str,
    source_encoding: Optional[str] = None,
) -> InjectedContent:
    """Rewrites the CExoLocString of every translated item in one pass.

    Every GFF resource kind shares this contract: extraction records the
    field record offset of each item, and only those fields are patched.

    Args:
        file_path: GFF resource to patch.
        items: Items extracted from the file; their order is the patch order.
        translations: Translated text by occurrence.
        content_type: Content type of the extraction, reported back.
        text_encoding: Code page of the written strings.
        source_encoding: Unused: fields are addressed by offset, not by text.

    Returns:
        The injection result, with metadata ``{"type": content_type}``.

    Raises:
        ValueError: If a translated item has no field record offset.
    """
    patches: List[Tuple[int, str]] = []
    for item, translated in changed_translations(items, translations):
        offset = item.metadata.get("record_offset")
        if not offset:
            raise ValueError(f"Missing field record for {item.key}")
        patches.append((offset, translated))
    if patches:
        GFFPatcher(file_path, text_encoding=text_encoding).patch_multiple(patches)
    return InjectedContent(
        source_file=file_path,
        modified=bool(patches),
        items_updated=len(patches),
        metadata={"type": content_type},
    )
