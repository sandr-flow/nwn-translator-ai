"""Creature extractor for NWN creature files.

This module handles extraction of creature names and descriptions from .utc GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List

from .base import BaseExtractor, ExtractedContent, TranslatableItem


class CreatureExtractor(BaseExtractor):
    """Extractor for creature (.utc) files."""

    SUPPORTED_TYPES = [".utc"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        gff_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract creature content from a .utc file.

        Args:
            file_path: Path to the .utc file
            gff_data: Parsed GFF data

        Returns:
            ExtractedContent with creature data
        """
        items = []

        # Extract creature name
        name_obj = gff_data.get("FirstName", {})
        first_name = self._extract_text_from_local_string(name_obj)

        # Extract last name
        last_name_obj = gff_data.get("LastName", {})
        last_name = self._extract_text_from_local_string(last_name_obj)

        # Combine names
        full_name = ""
        if first_name and last_name:
            full_name = f"{first_name} {last_name}"
        elif first_name:
            full_name = first_name
        elif last_name:
            full_name = last_name

        # Extract description
        desc_obj = gff_data.get("Description", {})
        description = self._extract_text_from_local_string(desc_obj)

        # Get tag for reference
        tag = gff_data.get("Tag", file_path.stem)

        # Create item for full name
        if full_name:
            items.append(TranslatableItem(
                text=full_name,
                context=f"Creature name: {tag}",
                item_id=f"{tag}_name",
                location=str(file_path),
                metadata={
                    "type": "creature_name",
                    "tag": tag,
                }
            ))

        # Create item for description
        if description:
            items.append(TranslatableItem(
                text=description,
                context=f"Creature description: {tag}",
                item_id=f"{tag}_description",
                location=str(file_path),
                metadata={
                    "type": "creature_description",
                    "tag": tag,
                }
            ))

        return ExtractedContent(
            content_type="creature",
            items=items,
            source_file=file_path,
            metadata={
                "tag": tag,
                "item_count": len(items),
            }
        )
