"""Patch only the concrete GFF fields emitted by extraction."""

from pathlib import Path
from typing import Any, Dict, Optional

from ..extractors.base import Translations
from ..file_handlers.gff_patcher import GFFPatcher
from .base import BaseInjector, InjectedContent


class GffInjector(BaseInjector):
    """All GFF resources share the same field-record patch contract."""

    SUPPORTED_TYPES = [
        "dialog",
        "journal",
        "item",
        "creature",
        "area",
        "trigger",
        "placeable",
        "door",
        "encounter",
        "store",
        "module",
        "git_instance",
    ]

    def inject(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        translations: Translations,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InjectedContent:
        metadata = metadata or {}
        patches = []
        for item in metadata["extracted_items"]:
            translated = translations.get(item.key)
            if translated is None or translated == item.text:
                continue
            offset = item.metadata.get("record_offset")
            if not offset:
                raise ValueError(f"Missing field record for {item.key}")
            patches.append((offset, translated))
        if patches:
            GFFPatcher(
                file_path, text_encoding=metadata.get("module_text_encoding", "cp1251")
            ).patch_multiple(patches)
        return InjectedContent(
            source_file=file_path,
            modified=bool(patches),
            items_updated=len(patches),
            metadata={"type": metadata.get("type", "gff")},
        )
