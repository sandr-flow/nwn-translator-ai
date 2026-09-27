"""Extractor for item blueprints (``.uti``) and shared item description contexts.

The description contexts are also used for item instances in area files
(:mod:`~nwn_translator.extractors.git_fields`).
"""

from pathlib import Path
from typing import Any, Dict, List

from ..nwn_constants import base_item_label
from .base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    record_offset,
)

#: Description field -> (label with a known base item, generic label).
_DESCRIPTION_LABELS = {
    "Description": ("Description of", "Item description"),
    "DescIdentified": ("Identified description of", "Item identified description"),
}


def item_description_context(field_name: str, base_item: str, name: str) -> str:
    """Returns the prompt context of an item description.

    Args:
        field_name: ``"Description"`` or ``"DescIdentified"``.
        base_item: Base item label (empty when unknown).
        name: Item name (empty when the item has none).
    """
    typed_label, generic_label = _DESCRIPTION_LABELS[field_name]
    if base_item and name:
        return f"{typed_label} {base_item} '{name}'"
    if name:
        return f"{generic_label} for '{name}'"
    return generic_label


class ItemExtractor(BaseExtractor):
    """Item blueprint (``.uti``): name, description and identified description."""

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts the name and descriptions of an item blueprint.

        Args:
            file_path: Path of the ``.uti`` resource.
            parsed_data: Parsed GFF root struct.

        Returns:
            Up to three items sharing one translation group.
        """
        tag = parsed_data.get("Tag", file_path.stem)
        base_item = base_item_label(parsed_data.get("BaseItem", -1))
        name = extract_local_string(parsed_data.get("LocalizedName", {}))
        name_context = (
            f"Game item name ({base_item}). Translate the name naturally."
            if base_item
            else "Game item name. Translate the name naturally."
        )
        fields = (
            ("LocalizedName", "item_name", "name", name_context),
            (
                "Description",
                "item_description",
                "description",
                item_description_context("Description", base_item, name or ""),
            ),
            (
                "DescIdentified",
                "item_identified_description",
                "identified_description",
                item_description_context("DescIdentified", base_item, name or ""),
            ),
        )
        items: List[TranslatableItem] = []
        for field_name, item_type, id_suffix, context in fields:
            text = extract_local_string(parsed_data.get(field_name, {}))
            if text:
                items.append(
                    TranslatableItem(
                        text=text,
                        context=context,
                        item_id=f"{tag}_{id_suffix}",
                        metadata={
                            "type": item_type,
                            "record_offset": record_offset(parsed_data, field_name),
                            "tag": tag,
                        },
                    )
                )

        for item in items:
            item.metadata.update(
                translation_group="root",
                shared_context=f"Game item (name: {name or ''}, type: {base_item}).",
                batch_context="",
            )

        return ExtractedContent(
            content_type="item",
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )
