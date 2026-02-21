"""Journal extractor for NWN journal files.

This module handles extraction of journal entries and categories from .jrl GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List

from .base import BaseExtractor, ExtractedContent, TranslatableItem


class JournalExtractor(BaseExtractor):
    """Extractor for journal (.jrl) files."""

    SUPPORTED_TYPES = [".jrl"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        gff_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract journal content from a .jrl file.

        Args:
            file_path: Path to the .jrl file
            gff_data: Parsed GFF data

        Returns:
            ExtractedContent with journal items
        """
        items = []

        # Extract journal categories
        categories = self._get_list_value(gff_data, "CategoriesList")
        for i, category in enumerate(categories):
            item = self._extract_category(category, i, file_path)
            if item and item.has_text():
                items.append(item)

        # Extract journal entries
        entries = self._get_list_value(gff_data, "EntriesList")
        for i, entry in enumerate(entries):
            item = self._extract_entry(entry, i, file_path)
            if item and item.has_text():
                items.append(item)

        return ExtractedContent(
            content_type="journal",
            items=items,
            source_file=file_path,
            metadata={
                "category_count": len(categories),
                "entry_count": len(entries),
            }
        )

    def _extract_category(
        self,
        category_data: Dict[str, Any],
        index: int,
        file_path: Path
    ) -> TranslatableItem:
        """Extract a journal category.

        Args:
            category_data: Category data dictionary
            index: Category index
            file_path: Source file path

        Returns:
            TranslatableItem for the category
        """
        # Extract name
        name_obj = category_data.get("Name", {})
        name = self._extract_text_from_local_string(name_obj) or ""

        # Extract description (if present)
        description_obj = category_data.get("Description", {})
        description = self._extract_text_from_local_string(description_obj) or ""

        # Combine name and description
        text = name
        if description:
            text = f"{name}\n\n{description}"

        # Get priority for sorting
        priority = category_data.get("Priority", 0)

        # Get tag
        tag = category_data.get("Tag", "")

        return TranslatableItem(
            text=text,
            context=f"Journal category: {tag}" if tag else "Journal category",
            item_id=f"category_{index}",
            location=str(file_path),
            metadata={
                "type": "journal_category",
                "tag": tag,
                "priority": priority,
                "name_only": not bool(description),
            }
        )

    def _extract_entry(
        self,
        entry_data: Dict[str, Any],
        index: int,
        file_path: Path
    ) -> TranslatableItem:
        """Extract a journal entry.

        Args:
            entry_data: Entry data dictionary
            index: Entry index
            file_path: Source file path

        Returns:
            TranslatableItem for the entry
        """
        # Extract text
        text_obj = entry_data.get("Text", {})
        text = self._extract_text_from_local_string(text_obj) or ""

        # Get category index
        category_index = entry_data.get("Category", 0)

        # Get priority
        priority = entry_data.get("Priority", 0)

        # Get tag
        tag = entry_data.get("Tag", "")

        return TranslatableItem(
            text=text,
            context=f"Journal entry in category {category_index}",
            item_id=f"entry_{index}",
            location=str(file_path),
            metadata={
                "type": "journal_entry",
                "category": category_index,
                "tag": tag,
                "priority": priority,
            }
        )
