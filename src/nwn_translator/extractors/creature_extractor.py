"""Extractor for creature blueprints (``.utc``) and shared NPC name contexts.

The name contexts and the name-fields suffix are also used for creature
instances placed in areas (:mod:`~nwn_translator.extractors.git_fields`).
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from ..nwn_constants import gender_label, race_label
from .base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    record_offset,
)

#: CExoLocString fields holding an NPC's name, in item order.
NAME_FIELDS = ("FirstName", "LastName")

#: Name field -> (prompt label, translation instruction).
_NAME_CONTEXTS = {
    "FirstName": ("NPC first name", "Translate ONLY this name, do not add surname."),
    "LastName": ("NPC last name or title", "Translate ONLY this, do not prepend first name."),
}


def creature_traits(struct: Dict[str, Any]) -> str:
    """Returns ``"<race>, <gender>"`` of a creature struct, omitting unknown values."""
    race = race_label(struct.get("Race", -1))
    gender = gender_label(struct.get("Gender", -1))
    return ", ".join(filter(None, [race, gender]))


def name_fields(struct: Dict[str, Any]) -> Dict[str, str]:
    """Returns ``{"FirstName": …, "LastName": …}`` of a creature struct, ``""`` when missing."""
    return {field: extract_local_string(struct.get(field, {})) or "" for field in NAME_FIELDS}


def name_fields_suffix(fields: Dict[str, str]) -> str:
    """Returns ``" NPC name fields: {json}"``, the context suffix showing both names."""
    return " NPC name fields: " + json.dumps(fields, ensure_ascii=False)


def creature_name_context(field_name: str, qualifier: str) -> str:
    """Returns the context of an NPC ``FirstName`` or ``LastName``, without the name suffix.

    Args:
        field_name: ``"FirstName"`` or ``"LastName"``.
        qualifier: Parenthesised detail (traits, placement); omitted when empty.
    """
    label, instruction = _NAME_CONTEXTS[field_name]
    if qualifier:
        return f"{label} ({qualifier}). {instruction}"
    return f"{label}. {instruction}"


class CreatureExtractor(BaseExtractor):
    """Creature blueprint (``.utc``): first name, last name and description."""

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts the names and description of a creature blueprint.

        Args:
            file_path: Path of the ``.utc`` resource.
            parsed_data: Parsed GFF root struct.

        Returns:
            Up to three items (first name, last name, description) sharing one
            translation group.
        """
        tag = parsed_data.get("Tag", file_path.stem)
        traits = creature_traits(parsed_data)
        gender = gender_label(parsed_data.get("Gender", -1))
        names = name_fields(parsed_data)
        suffix = name_fields_suffix(names)
        items: List[TranslatableItem] = []

        for field_name, id_suffix in zip(NAME_FIELDS, ("first_name", "last_name")):
            if names[field_name]:
                items.append(
                    TranslatableItem(
                        text=names[field_name],
                        context=creature_name_context(field_name, traits) + suffix,
                        item_id=f"{tag}_{id_suffix}",
                        metadata={
                            "type": f"creature_{id_suffix}",
                            "name_fields": names,
                            "name_field": field_name,
                            "name_group": "root",
                            "gender": gender,
                            "record_offset": record_offset(parsed_data, field_name),
                            "tag": tag,
                        },
                    )
                )

        description = extract_local_string(parsed_data.get("Description", {}))
        if description:
            full_name = " ".join(filter(None, names.values()))
            detail = ", ".join(filter(None, [f"name: {full_name}", traits])) if full_name else ""
            items.append(
                TranslatableItem(
                    text=description,
                    context=f"NPC description ({detail})" if detail else "NPC description",
                    item_id=f"{tag}_description",
                    metadata={
                        "type": "creature_description",
                        "record_offset": record_offset(parsed_data, "Description"),
                        "tag": tag,
                    },
                )
            )

        for item in items:
            item.metadata.update(
                translation_group="root",
                shared_context=f"NPC ({traits})." + suffix,
                batch_context="",
            )

        return ExtractedContent(
            content_type="creature",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )
