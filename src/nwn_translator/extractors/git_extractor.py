"""Extractor for area instance (.git) files.

Walks the same structure as :mod:`~nwn_translator.injectors.git_injector` so
strings are translated in Phase A/B and patched in :func:`patch_git_file`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..injectors.git_injector import (
    INSTANCE_LISTS,
    INSTANCE_NESTED_ITEM_LISTS,
    ITEM_INVENTORY_FIELDS,
    _iter_nested_item_entries,
    is_internal_tag,
)
from .base import BaseExtractor, ExtractedContent, TranslatableItem


def _meta_for_instance_field(list_key: str, field_name: str) -> Tuple[str, str]:
    """Return (metadata ``type``, short context label) for batching / prompts."""
    if list_key == "Creature List":
        if field_name == "FirstName":
            return "creature_first_name", "Creature first name (area instance)"
        if field_name == "LastName":
            return "creature_last_name", "Creature last name (area instance)"
        if field_name == "Description":
            return "creature_description", "Creature description (area instance)"
    if list_key == "Placeable List":
        if field_name == "LocName":
            return "placeable_name", "Placeable name (area instance)"
        if field_name == "Description":
            return "placeable_description", "Placeable description (area instance)"
    if list_key == "Door List":
        if field_name == "LocalizedName":
            return "door_name", "Door name (area instance)"
        if field_name == "Description":
            return "door_description", "Door description (area instance)"
    if list_key == "Trigger List":
        if field_name == "LocalizedName":
            return "trigger_name", "Trigger name (area instance)"
        if field_name == "Description":
            return "trigger_description", "Trigger description (area instance)"
    if list_key == "WaypointList":
        if field_name == "LocalizedName":
            return "waypoint_name", "Waypoint name (area instance)"
        if field_name == "Description":
            return "waypoint_description", "Waypoint description (area instance)"
    if list_key == "StoreList":
        if field_name in ("LocName", "LocalizedName"):
            return "store_name", "Store name (area instance)"
        if field_name == "Description":
            return "store_description", "Store description (area instance)"
    return "git_instance_string", f"Area instance field ({list_key}.{field_name})"


def _meta_for_inventory_field(field_name: str) -> Tuple[str, str]:
    if field_name == "LocalizedName":
        return "item_name", "Item name (inventory / equipped instance)"
    if field_name == "Description":
        return "item_description", "Item description (inventory / equipped instance)"
    if field_name == "DescIdentified":
        return "item_identified_description", "Item identified description (instance)"
    return "git_instance_string", f"Item field ({field_name})"


class GitExtractor(BaseExtractor):
    """Extractor for .git (placed instances + nested inventories)."""

    SUPPORTED_TYPES = [".git"]

    def can_extract(self, file_type: str) -> bool:
        return file_type.lower() in self.SUPPORTED_TYPES

    def _extract_nested_store_inventory(
        self,
        store_node: Dict[str, Any],
        file_path: Path,
        stem: str,
        inst_idx: int,
        path_suffix: str,
        items: List[TranslatableItem],
    ) -> None:
        """Recurse store instance: ItemList rows + nested StoreList shelves."""
        for j, inv_item in enumerate(_iter_nested_item_entries(store_node, "ItemList")):
            for inv_field in ITEM_INVENTORY_FIELDS:
                meta_type, ctx_label = _meta_for_inventory_field(inv_field)
                self._append_loc_string_item(
                    inv_item,
                    inv_field,
                    file_path,
                    meta_type=meta_type,
                    context=(
                        f"{ctx_label} in {stem}.git "
                        f"(StoreList[{inst_idx}]{path_suffix}.ItemList[{j}])"
                    ),
                    item_id=(
                        f"{stem}_StoreList_{inst_idx}_{path_suffix}_il{j}_{inv_field}"
                    ),
                    items=items,
                )
        children = store_node.get("StoreList", [])
        if not isinstance(children, list):
            return
        for k, child in enumerate(children):
            if isinstance(child, dict):
                self._extract_nested_store_inventory(
                    child,
                    file_path,
                    stem,
                    inst_idx,
                    f"{path_suffix}.StoreList[{k}]",
                    items,
                )

    def _append_loc_string_item(
        self,
        struct: Dict[str, Any],
        field_name: str,
        file_path: Path,
        *,
        meta_type: str,
        context: str,
        item_id: str,
        items: List[TranslatableItem],
    ) -> None:
        field_obj = struct.get(field_name)
        if not isinstance(field_obj, dict):
            return
        text = self._extract_text_from_local_string(field_obj)
        if not text or is_internal_tag(text):
            return
        items.append(
            TranslatableItem(
                text=text,
                context=context,
                item_id=item_id,
                location=str(file_path),
                metadata={"type": meta_type, "git_field": field_name},
            )
        )

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
    ) -> ExtractedContent:
        items: List[TranslatableItem] = []
        stem = file_path.stem

        for list_key, field_names in INSTANCE_LISTS.items():
            instances = parsed_data.get(list_key, [])
            if not isinstance(instances, list):
                continue
            for inst_idx, instance in enumerate(instances):
                if not isinstance(instance, dict):
                    continue
                for field_name in field_names:
                    meta_type, ctx_label = _meta_for_instance_field(list_key, field_name)
                    self._append_loc_string_item(
                        instance,
                        field_name,
                        file_path,
                        meta_type=meta_type,
                        context=f"{ctx_label} in {stem}.git ({list_key}[{inst_idx}])",
                        item_id=f"{stem}_{list_key}_{inst_idx}_{field_name}",
                        items=items,
                    )

                if list_key == "StoreList":
                    self._extract_nested_store_inventory(
                        instance, file_path, stem, inst_idx, "", items
                    )
                else:
                    for nested_key in INSTANCE_NESTED_ITEM_LISTS.get(list_key, []):
                        for j, inv_item in enumerate(
                            _iter_nested_item_entries(instance, nested_key)
                        ):
                            for inv_field in ITEM_INVENTORY_FIELDS:
                                meta_type, ctx_label = _meta_for_inventory_field(
                                    inv_field
                                )
                                self._append_loc_string_item(
                                    inv_item,
                                    inv_field,
                                    file_path,
                                    meta_type=meta_type,
                                    context=(
                                        f"{ctx_label} in {stem}.git "
                                        f"({list_key}[{inst_idx}].{nested_key}[{j}])"
                                    ),
                                    item_id=(
                                        f"{stem}_{list_key}_{inst_idx}_{nested_key}_"
                                        f"{j}_{inv_field}"
                                    ),
                                    items=items,
                                )

        return ExtractedContent(
            content_type="git_instance",
            items=items,
            source_file=file_path,
            metadata={
                "type": "git_instance",
                "area_tag": parsed_data.get("Tag", stem),
                "item_count": len(items),
            },
        )
