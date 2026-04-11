"""Area extractor for NWN area files.

This module handles extraction of area names and descriptions from .are GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List

from .base import BaseExtractor, ExtractedContent, TranslatableItem


class AreaExtractor(BaseExtractor):
    """Extractor for area (.are) files."""

    SUPPORTED_TYPES = [".are"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract area content from an .are file.

        Args:
            file_path: Path to the .are file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with area data
        """
        items = []

        # Extract area name
        name_obj = parsed_data.get("Name", {})
        name = self._extract_text_from_local_string(name_obj)

        # Extract area description (if present)
        # Note: Not all areas have descriptions
        desc_obj = parsed_data.get("Description", {})
        description = self._extract_text_from_local_string(desc_obj)

        # Get tag for reference
        tag = parsed_data.get("Tag", file_path.stem)

        # Create item for name
        if name:
            items.append(TranslatableItem(
                text=name,
                context="Location/area name in game world",
                item_id=f"{tag}_name",
                location=str(file_path),
                metadata={
                    "type": "area_name",
                    "tag": tag,
                }
            ))

        # Create item for description
        if description:
            items.append(TranslatableItem(
                text=description,
                context=f"Area description: {tag}",
                item_id=f"{tag}_description",
                location=str(file_path),
                metadata={
                    "type": "area_description",
                    "tag": tag,
                }
            ))

        return ExtractedContent(
            content_type="area",
            items=items,
            source_file=file_path,
            metadata={
                "tag": tag,
                "item_count": len(items),
            }
        )


class TriggerExtractor(BaseExtractor):
    """Extractor for trigger (.utt) files.

    In NWN:EE, .utt (Usable Trigger Template) stores triggers with a localized
    name.  This is distinct from .utp (Placeable) files.
    """

    SUPPORTED_TYPES = [".utt"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract trigger content from a .utt file.

        Args:
            file_path: Path to the .utt file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with trigger data
        """
        tag = parsed_data.get("Tag", file_path.stem)
        items: List[TranslatableItem] = []
        name_item = self._make_name_item(
            parsed_data, file_path, "LocalizedName", "Trigger", "trigger_name"
        )
        if name_item:
            items.append(name_item)
        desc = self._extract_text_from_local_string(parsed_data.get("Description", {}))
        if desc:
            items.append(
                TranslatableItem(
                    text=desc,
                    context=f"Trigger description: {tag}",
                    item_id=f"{tag}_description",
                    location=str(file_path),
                    metadata={"type": "trigger_description", "tag": tag},
                )
            )
        return ExtractedContent(
            content_type="trigger",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )


class PlaceableExtractor(BaseExtractor):
    """Extractor for placeable (.utp) files.

    In NWN:EE, .utp (Usable Placeable Template) stores placeables with a
    localized Name field.
    """

    SUPPORTED_TYPES = [".utp"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract placeable content from a .utp file.

        Args:
            file_path: Path to the .utp file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with placeable data
        """
        tag = parsed_data.get("Tag", file_path.stem)
        # .utp uses 'Name' (CExoLocString), not 'LocalizedName'
        items: List[TranslatableItem] = []
        name_item = self._make_name_item(
            parsed_data, file_path, "Name", "Placeable", "placeable_name"
        )
        if name_item:
            items.append(name_item)
        desc = self._extract_text_from_local_string(parsed_data.get("Description", {}))
        if desc:
            items.append(
                TranslatableItem(
                    text=desc,
                    context=f"Placeable description: {tag}",
                    item_id=f"{tag}_description",
                    location=str(file_path),
                    metadata={"type": "placeable_description", "tag": tag},
                )
            )
        idesc = self._extract_text_from_local_string(
            parsed_data.get("DescIdentified", {})
        )
        if idesc and idesc != desc:
            items.append(
                TranslatableItem(
                    text=idesc,
                    context=f"Placeable identified description: {tag}",
                    item_id=f"{tag}_desc_identified",
                    location=str(file_path),
                    metadata={"type": "placeable_desc_identified", "tag": tag},
                )
            )
        return ExtractedContent(
            content_type="placeable",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )


class DoorExtractor(BaseExtractor):
    """Extractor for door (.utd) files."""

    SUPPORTED_TYPES = [".utd"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract door content from a .utd file.

        Args:
            file_path: Path to the .utd file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with door data
        """
        tag = parsed_data.get("Tag", file_path.stem)
        items: List[TranslatableItem] = []
        name_item = self._make_name_item(
            parsed_data, file_path, "LocalizedName", "Door", "door_name"
        )
        if name_item:
            items.append(name_item)
        desc = self._extract_text_from_local_string(parsed_data.get("Description", {}))
        if desc:
            items.append(
                TranslatableItem(
                    text=desc,
                    context=f"Door description: {tag}",
                    item_id=f"{tag}_description",
                    location=str(file_path),
                    metadata={"type": "door_description", "tag": tag},
                )
            )
        return ExtractedContent(
            content_type="door",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )


class StoreExtractor(BaseExtractor):
    """Extractor for store (.utm) files."""

    SUPPORTED_TYPES = [".utm"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract store content from a .utm file.

        Args:
            file_path: Path to the .utm file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with store data
        """
        tag = parsed_data.get("Tag", file_path.stem)
        items: List[TranslatableItem] = []
        name_item = self._make_name_item(
            parsed_data, file_path, "LocalizedName", "Store", "store_name"
        )
        if name_item:
            items.append(name_item)
        desc = self._extract_text_from_local_string(parsed_data.get("Description", {}))
        if desc:
            items.append(
                TranslatableItem(
                    text=desc,
                    context=f"Store description: {tag}",
                    item_id=f"{tag}_description",
                    location=str(file_path),
                    metadata={"type": "store_description", "tag": tag},
                )
            )
        return ExtractedContent(
            content_type="store",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )
