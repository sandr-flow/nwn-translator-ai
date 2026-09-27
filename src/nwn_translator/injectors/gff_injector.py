"""GFF injector: patches the field records of extracted items."""

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..extractors.base import TranslatableItem, Translations
from ..formats.gff import patch_locstrings
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

    Every GFF resource kind shares this contract: extraction records the field record
    offset of each item, and only those fields are patched, in item order. Fields are
    addressed by offset, not by text, so *source_encoding* is unused.

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
        patch_locstrings(file_path, patches, text_encoding=text_encoding)
    return InjectedContent(
        file_path,
        modified=bool(patches),
        items_updated=len(patches),
        metadata={"type": content_type},
    )
