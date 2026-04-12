"""Item extractor for NWN item files.

This module handles extraction of item names and descriptions from .uti GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List

from ..nwn_constants import base_item_label
from .base import BaseExtractor, ExtractedContent, TranslatableItem


class ItemExtractor(BaseExtractor):
    """Extractor for item (.uti) files."""

    SUPPORTED_TYPES = [".uti"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract item content from a .uti file.

        Args:
            file_path: Path to the .uti file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with item data
        """
        items = []

        # Extract item name
        name_obj = parsed_data.get("LocalizedName", {})
        name = self._extract_text_from_local_string(name_obj)

        # Extract item description (flavor text)
        desc_obj = parsed_data.get("Description", {})
        description = self._extract_text_from_local_string(desc_obj)

        # Extract identified description (when item is identified)
        identified_desc_obj = parsed_data.get("DescIdentified", {})
        identified_description = self._extract_text_from_local_string(identified_desc_obj)

        # Get tag and base item type for context
        tag = parsed_data.get("Tag", file_path.stem)
        base_item = base_item_label(parsed_data.get("BaseItem", -1))

        # Create item for name
        if name:
            name_ctx = (
                f"Game item name ({base_item}). Translate the name naturally."
                if base_item
                else "Game item name. Translate the name naturally."
            )
            items.append(TranslatableItem(
                text=name,
                context=name_ctx,
                item_id=f"{tag}_name",
                location=str(file_path),
                metadata={
                    "type": "item_name",
                    "tag": tag,
                }
            ))

        # Create item for description
        if description:
            if base_item and name:
                desc_ctx = f"Description of {base_item} '{name}'"
            elif name:
                desc_ctx = f"Item description for '{name}'"
            else:
                desc_ctx = "Item description"
            items.append(TranslatableItem(
                text=description,
                context=desc_ctx,
                item_id=f"{tag}_description",
                location=str(file_path),
                metadata={
                    "type": "item_description",
                    "tag": tag,
                }
            ))

        # Create item for identified description
        if identified_description and identified_description != description:
            if base_item and name:
                idesc_ctx = f"Identified description of {base_item} '{name}'"
            elif name:
                idesc_ctx = f"Item identified description for '{name}'"
            else:
                idesc_ctx = "Item identified description"
            items.append(TranslatableItem(
                text=identified_description,
                context=idesc_ctx,
                item_id=f"{tag}_identified_description",
                location=str(file_path),
                metadata={
                    "type": "item_identified_description",
                    "tag": tag,
                }
            ))

        return ExtractedContent(
            content_type="item",
            items=items,
            source_file=file_path,
            metadata={
                "tag": tag,
                "item_count": len(items),
            }
        )
