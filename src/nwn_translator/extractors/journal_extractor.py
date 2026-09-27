"""Extractor for journals (``.jrl``): quest titles and their entries."""

from pathlib import Path
from typing import Any, Dict, List

from .base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    list_field,
    record_offset,
)


class JournalExtractor(BaseExtractor):
    """Journal (``.jrl``): category names and entry texts, grouped per quest."""

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts every quest title followed by its non-blank entries.

        Args:
            file_path: Path of the ``.jrl`` resource.
            parsed_data: Parsed GFF root struct (``Categories`` list).

        Returns:
            One item per quest title and per entry; each quest forms one
            translation group whose shared context names the quest.
        """
        items: List[TranslatableItem] = []
        categories = list_field(parsed_data, "Categories")
        for i, category in enumerate(categories):
            # The parser keeps out-of-range struct references as raw ints.
            if not isinstance(category, dict):
                continue
            quest_name = extract_local_string(category.get("Name", {})) or ""
            group = f"category[{i}]"
            shared_context = f"Journal quest: {quest_name}."
            if quest_name:
                tag = category.get("Tag", "")
                items.append(
                    TranslatableItem(
                        text=quest_name,
                        context=f"Journal category name: {tag}" if tag else "Journal category name",
                        item_id=f"category_{i}_name",
                        metadata={
                            "type": "journal_category_name",
                            "translation_group": group,
                            "record_offset": record_offset(category, "Name"),
                            "tag": tag,
                            "priority": category.get("Priority", 0),
                            "shared_context": shared_context,
                            "batch_context": "Quest title.",
                        },
                    )
                )
            # The visible quest title is context; the engine tag is not a title.
            entry_context = (
                f"Journal entry (quest title: '{quest_name}'). "
                "The quest title is context only — do not substitute it into the entry text."
                if quest_name
                else f"Journal entry in category {i}"
            )
            for j, entry in enumerate(list_field(category, "EntryList")):
                if not isinstance(entry, dict):
                    continue
                text = extract_local_string(entry.get("Text", {})) or ""
                if not text.strip():
                    continue
                entry_id = entry.get("ID", 0)
                items.append(
                    TranslatableItem(
                        text=text,
                        context=entry_context,
                        item_id=f"entry_{i}_{j}",
                        metadata={
                            "type": "journal_entry",
                            "record_offset": record_offset(entry, "Text"),
                            "category": i,
                            "translation_group": group,
                            "entry_id": entry_id,
                            "shared_context": shared_context,
                            "batch_context": (
                                f"Journal entry, state ID {entry_id}. The quest title is "
                                "context only; do not substitute it into the entry."
                            ),
                        },
                    )
                )

        return ExtractedContent(
            content_type="journal",
            items=items,
            source_file=file_path,
            metadata={"category_count": len(categories)},
        )
