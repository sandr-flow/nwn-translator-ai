"""Creature extractor for NWN creature files.

This module handles extraction of creature names and descriptions from .utc GFF files.
"""

from pathlib import Path
import json
from typing import Any, Dict, List

from ..nwn_constants import race_label, gender_label
from .base import BaseExtractor, ExtractedContent, TranslatableItem


class CreatureExtractor(BaseExtractor):
    """Extractor for creature (.utc) files."""

    SUPPORTED_TYPES = [".utc"]

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract creature content from a .utc file.

        Args:
            file_path: Path to the .utc file
            parsed_data: Parsed GFF data

        Returns:
            ExtractedContent with creature data
        """
        items = []

        # Get tag for reference
        tag = parsed_data.get("Tag", file_path.stem)

        # Build traits string from GFF metadata (empty for custom/unknown IDs)
        race = race_label(parsed_data.get("Race", -1))
        gender = gender_label(parsed_data.get("Gender", -1))
        traits = ", ".join(filter(None, [race, gender]))
        name_fields = {
            field: self._extract_text_from_local_string(parsed_data.get(field, {})) or ""
            for field in ("FirstName", "LastName")
        }
        name_context = " NPC name fields: " + json.dumps(name_fields, ensure_ascii=False)

        # Extract first name as separate item
        name_obj = parsed_data.get("FirstName", {})
        first_name = self._extract_text_from_local_string(name_obj)
        if first_name:
            name_ctx = (
                f"NPC first name ({traits}). Translate ONLY this name, do not add surname."
                if traits
                else "NPC first name. Translate ONLY this name, do not add surname."
            )
            items.append(
                TranslatableItem(
                    text=first_name,
                    context=name_ctx + name_context,
                    item_id=f"{tag}_first_name",
                    location=str(file_path),
                    metadata={
                        "type": "creature_first_name",
                        "name_fields": name_fields,
                        "name_field": "FirstName",
                        "name_group": "root",
                        "gender": gender,
                        "record_offset": parsed_data.get("_record_offsets", {}).get("FirstName", 0),
                        "tag": tag,
                    },
                )
            )

        # Extract last name as separate item
        last_name_obj = parsed_data.get("LastName", {})
        last_name = self._extract_text_from_local_string(last_name_obj)
        if last_name:
            ln_ctx = (
                f"NPC last name or title ({traits}). Translate ONLY this, do not prepend first name."
                if traits
                else "NPC last name or title. Translate ONLY this, do not prepend first name."
            )
            items.append(
                TranslatableItem(
                    text=last_name,
                    context=ln_ctx + name_context,
                    item_id=f"{tag}_last_name",
                    location=str(file_path),
                    metadata={
                        "type": "creature_last_name",
                        "name_fields": name_fields,
                        "name_field": "LastName",
                        "name_group": "root",
                        "gender": gender,
                        "record_offset": parsed_data.get("_record_offsets", {}).get("LastName", 0),
                        "tag": tag,
                    },
                )
            )

        # Extract description as separate item
        desc_obj = parsed_data.get("Description", {})
        description = self._extract_text_from_local_string(desc_obj)
        if description:
            full_name = " ".join(filter(None, [first_name, last_name]))
            if full_name and traits:
                desc_ctx = f"NPC description (name: {full_name}, {traits})"
            elif full_name:
                desc_ctx = f"NPC description (name: {full_name})"
            else:
                desc_ctx = "NPC description"
            items.append(
                TranslatableItem(
                    text=description,
                    context=desc_ctx,
                    item_id=f"{tag}_description",
                    location=str(file_path),
                    metadata={
                        "type": "creature_description",
                        "record_offset": parsed_data.get("_record_offsets", {}).get(
                            "Description", 0
                        ),
                        "tag": tag,
                    },
                )
            )

        for item in items:
            item.metadata["translation_group"] = "root"
            item.metadata["shared_context"] = f"NPC ({traits})." + name_context
            item.metadata["batch_context"] = ""

        return ExtractedContent(
            content_type="creature",
            items=items,
            source_file=file_path,
            metadata={
                "tag": tag,
                "item_count": len(items),
            },
        )
