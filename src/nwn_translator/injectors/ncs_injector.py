"""NCS script injector for writing translated strings back into bytecode.

Delegates the actual binary patching to :mod:`~nwn_translator.file_handlers.ncs_patcher`.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .base import BaseInjector, InjectedContent
from ..file_handlers.ncs_patcher import NCSPatchError, patch_ncs_string_replacements

logger = logging.getLogger(__name__)


class NcsInjector(BaseInjector):
    """Injector for compiled NWScript (``.ncs``) files."""

    SUPPORTED_TYPES = ["ncs_script"]

    def can_inject(self, content_type: str) -> bool:
        return content_type in self.SUPPORTED_TYPES

    def inject(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        translations: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InjectedContent:
        """Inject translated strings into an NCS bytecode file.

        Args:
            file_path: Path to the ``.ncs`` file.
            parsed_data: Dict with ``_ncs_file`` key (not used for injection,
                      the patcher re-reads the file).
            translations: Mapping of original text to translated text.
            metadata: Optional metadata (unused).

        Returns:
            InjectedContent with results.
        """
        ncs_by_item_id = (metadata or {}).get("ncs_translations_by_item_id") or {}
        ncs_items = (metadata or {}).get("ncs_extracted_items")

        if not ncs_by_item_id and not translations:
            return InjectedContent(
                source_file=file_path,
                modified=False,
                items_updated=0,
                metadata={"type": "ncs_script"},
            )

        replacements = []
        if ncs_items and ncs_by_item_id:
            for item in ncs_items:
                tid = item.item_id
                if not tid or tid not in ncs_by_item_id:
                    continue
                translated = ncs_by_item_id[tid]
                if translated == item.text:
                    continue
                off = (item.metadata or {}).get("offset")
                if off is None:
                    continue
                replacements.append((int(off), item.text, translated))
        else:
            logger.warning(
                "NCS inject missing ncs_translations_by_item_id/ncs_extracted_items; "
                "skipping patch for %s",
                file_path.name,
            )
            return InjectedContent(
                source_file=file_path,
                modified=False,
                items_updated=0,
                metadata={"type": "ncs_script", "error": "missing_ncs_inject_metadata"},
            )

        if not replacements:
            return InjectedContent(
                source_file=file_path,
                modified=False,
                items_updated=0,
                metadata={"type": "ncs_script"},
            )

        enc = (metadata or {}).get("module_text_encoding") or "cp1251"
        try:
            patched_count = patch_ncs_string_replacements(
                file_path, replacements, text_encoding=enc
            )
        except NCSPatchError as e:
            logger.error("Failed to patch NCS file %s: %s", file_path.name, e)
            return InjectedContent(
                source_file=file_path,
                modified=False,
                items_updated=0,
                metadata={"error": str(e)},
            )

        return InjectedContent(
            source_file=file_path,
            modified=patched_count > 0,
            items_updated=patched_count,
            metadata={"type": "ncs_script"},
        )
