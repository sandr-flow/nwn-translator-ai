"""Journal extractor for NWN journal files.

This module handles extraction of journal entries and categories from .jrl GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseExtractor, ExtractedContent, TranslatableItem


class JournalExtractor(BaseExtractor):
    """Extractor for journal (.jrl) files."""

    SUPPORTED_TYPES = [".jrl"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract journal content from a .jrl file.

        Args:
            file_path: Path to the .jrl file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with journal items
        """
        items = []

        # Extract journal categories (GFF field is "Categories", not "CategoriesList")
        categories = self._get_list_value(parsed_data, "Categories")
        for i, category in enumerate(categories):
            # Extract category name
            category_items = self._extract_category(category, i, file_path)
            items.extend(category_items)

            # Entries are nested inside each category as "EntryList"
            entries = self._get_list_value(category, "EntryList")
            for j, entry in enumerate(entries):
                item = self._extract_entry(entry, i, j, file_path, category)
                if item and item.has_text():
                    items.append(item)

        return ExtractedContent(
            content_type="journal",
            items=items,
            source_file=file_path,
            metadata={
                "category_count": len(categories),
            },
        )

    def _extract_category(
        self, category_data: Dict[str, Any], index: int, file_path: Path
    ) -> List[TranslatableItem]:
        """Extract translatable items from a journal category.

        Args:
            category_data: Category data dictionary
            index: Category index
            file_path: Source file path

        Returns:
            List of TranslatableItems for the category name
        """
        items = []

        # Get tag and priority
        tag = category_data.get("Tag", "")
        priority = category_data.get("Priority", 0)

        # Extract category name
        name_obj = category_data.get("Name", {})
        name = self._extract_text_from_local_string(name_obj) or ""
        if name:
            items.append(
                TranslatableItem(
                    text=name,
                    context=f"Journal category name: {tag}" if tag else "Journal category name",
                    item_id=f"category_{index}_name",
                    location=str(file_path),
                    metadata={
                        "type": "journal_category_name",
                        "tag": tag,
                        "priority": priority,
                    },
                )
            )

        return items

    def _extract_entry(
        self,
        entry_data: Dict[str, Any],
        category_index: int,
        entry_index: int,
        file_path: Path,
        category_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[TranslatableItem]:
        """Extract a journal entry.

        Args:
            entry_data: Entry data dictionary
            category_index: Parent category index
            entry_index: Entry index within the category
            file_path: Source file path
            category_data: Parent category data for context

        Returns:
            TranslatableItem for the entry, or None
        """
        # Extract text
        text_obj = entry_data.get("Text", {})
        text = self._extract_text_from_local_string(text_obj) or ""

        if not text:
            return None

        # Get entry ID
        entry_id = entry_data.get("ID", 0)

        # Get category tag for context
        cat_tag = category_data.get("Tag", "") if category_data else ""

        return TranslatableItem(
            text=text,
            context=(
                (
                    f"Journal entry (quest title: '{cat_tag}'). "
                    f"The quest title is context only — do not substitute it into the entry text."
                )
                if cat_tag
                else f"Journal entry in category {category_index}"
            ),
            item_id=f"entry_{category_index}_{entry_index}",
            location=str(file_path),
            metadata={
                "type": "journal_entry",
                "category": category_index,
                "entry_id": entry_id,
            },
        )
