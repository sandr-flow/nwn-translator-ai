"""Module extractor for NWN module info files.

This module handles extraction of module name and description from .ifo GFF files.
"""

from pathlib import Path
from typing import Any, Dict

from .base import BaseExtractor, ExtractedContent, TranslatableItem


class ModuleExtractor(BaseExtractor):
    """Extractor for module info (.ifo) files."""

    SUPPORTED_TYPES = [".ifo"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract module info content from a .ifo file.

        Args:
            file_path: Path to the .ifo file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with module data
        """
        items = []

        tag = parsed_data.get("Mod_Tag", file_path.stem)

        # Extract module name (Mod_Name is CExoLocString)
        name_obj = parsed_data.get("Mod_Name", {})
        name = self._extract_text_from_local_string(name_obj)
        if name:
            items.append(
                TranslatableItem(
                    text=name,
                    context=f"Module name: {tag}",
                    item_id=f"{tag}_mod_name",
                    location=str(file_path),
                    metadata={
                        "type": "module_name",
                        "tag": tag,
                    },
                )
            )

        # Extract module description (Mod_Description is CExoLocString)
        desc_obj = parsed_data.get("Mod_Description", {})
        description = self._extract_text_from_local_string(desc_obj)
        if description:
            items.append(
                TranslatableItem(
                    text=description,
                    context=f"Module description: {tag}",
                    item_id=f"{tag}_mod_description",
                    location=str(file_path),
                    metadata={
                        "type": "module_description",
                        "tag": tag,
                    },
                )
            )

        return ExtractedContent(
            content_type="module",
            items=items,
            source_file=file_path,
            metadata={
                "tag": tag,
                "item_count": len(items),
            },
        )
