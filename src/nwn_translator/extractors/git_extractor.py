"""Extractor for area instance (.git) files.

Walks the same structure as :mod:`~nwn_translator.injectors.git_injector` so
strings are translated in Phase A/B and patched in :func:`patch_git_file`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..injectors.git_injector import (
    INSTANCE_LISTS,
    INSTANCE_NESTED_ITEM_LISTS,
    ITEM_INVENTORY_FIELDS,
    _iter_nested_item_entries,
    is_internal_tag,
)
from ..nwn_constants import race_label, gender_label, base_item_label
from .base import BaseExtractor, ExtractedContent, TranslatableItem, extract_local_string


def _meta_type_for_instance_field(list_key: str, field_name: str) -> str:
    """Return metadata ``type`` string for batching decisions."""
    if list_key == "Creature List":
        if field_name == "FirstName":
            return "creature_first_name"
        if field_name == "LastName":
            return "creature_last_name"
        if field_name == "Description":
            return "creature_description"
    if list_key == "Placeable List":
        if field_name == "LocName":
            return "placeable_name"
        if field_name == "Description":
            return "placeable_description"
    if list_key == "Door List":
        if field_name == "LocalizedName":
            return "door_name"
        if field_name == "Description":
            return "door_description"
    if list_key == "TriggerList":
        if field_name == "LocalizedName":
            return "trigger_name"
        if field_name == "Description":
            return "trigger_description"
    if list_key == "WaypointList":
        if field_name == "LocalizedName":
            return "waypoint_name"
        if field_name == "Description":
            return "waypoint_description"
        if field_name == "MapNote":
            return "waypoint_map_note"
    if list_key == "Encounter List":
        if field_name == "LocalizedName":
            return "encounter_name"
    if list_key == "StoreList":
        if field_name in ("LocName", "LocalizedName"):
            return "store_name"
        if field_name == "Description":
            return "store_description"
    return "git_instance_string"


def _meta_type_for_inventory_field(field_name: str) -> str:
    """Return metadata ``type`` for an inventory/equipped item field."""
    if field_name == "LocalizedName":
        return "item_name"
    if field_name == "Description":
        return "item_description"
    if field_name == "DescIdentified":
        return "item_identified_description"
    return "git_instance_string"


def _build_npc_name_index(
    parsed_data: Dict[str, Any],
) -> Dict[str, str]:
    """Build a mapping of NPC first names to their gender from Creature List.

    Returns ``{first_name_lower: gender_label}`` for all creatures with
    a non-empty first name and a recognised gender.  Used to enrich context
    for placeables/descriptions that mention an NPC possessively (``X's …``).
    """
    index: Dict[str, str] = {}
    creatures = parsed_data.get("Creature List", [])
    if not isinstance(creatures, list):
        return index
    for creature in creatures:
        if not isinstance(creature, dict):
            continue
        first = extract_local_string(creature.get("FirstName", {})) or ""
        first = first.strip()
        if not first:
            continue
        gend = gender_label(creature.get("Gender", -1))
        if gend:
            index[first.lower()] = gend
    return index


def _npc_possessive_hint(
    text: str,
    npc_index: Dict[str, str],
) -> str:
    """If *text* contains ``<Name>'s``, return a context hint about that NPC's gender."""
    if not npc_index or "'s" not in text:
        return ""
    for name_lower, gend in npc_index.items():
        needle = name_lower + "'s"
        if needle in text.lower():
            original_name = text[text.lower().index(name_lower):
                                 text.lower().index(name_lower) + len(name_lower)]
            return f" (contains possessive of NPC '{original_name}', gender: {gend})"
    return ""


def _build_instance_context(
    list_key: str,
    field_name: str,
    instance: Dict[str, Any],
    npc_index: Optional[Dict[str, str]] = None,
) -> str:
    """Build an enriched context string using metadata from the instance struct."""
    if list_key == "Creature List":
        race = race_label(instance.get("Race", -1))
        gend = gender_label(instance.get("Gender", -1))
        traits = ", ".join(filter(None, [race, gend]))
        if field_name == "FirstName":
            base = "NPC first name"
            if traits:
                return f"{base} ({traits}, area instance). Translate ONLY this name, do not add surname."
            return f"{base} (area instance). Translate ONLY this name, do not add surname."
        if field_name == "LastName":
            base = "NPC last name or title"
            if traits:
                return f"{base} ({traits}, area instance). Translate ONLY this, do not prepend first name."
            return f"{base} (area instance). Translate ONLY this, do not prepend first name."
        if field_name == "Description":
            first = extract_local_string(instance.get("FirstName", {})) or ""
            last = extract_local_string(instance.get("LastName", {})) or ""
            full_name = " ".join(filter(None, [first, last]))
            parts = filter(None, [f"name: {full_name}" if full_name else "", traits])
            detail = ", ".join(parts)
            if detail:
                return f"Creature description ({detail}, area instance)"
            return "Creature description (area instance)"

    if list_key == "Placeable List":
        text = extract_local_string(instance.get("LocName", {})) or ""
        if field_name == "LocName":
            hint = _npc_possessive_hint(text, npc_index) if npc_index else ""
            return f"Placeable name (area instance){hint}"
        plc_name = text
        if field_name == "Description" and plc_name:
            desc_text = extract_local_string(instance.get("Description", {})) or ""
            hint = _npc_possessive_hint(desc_text, npc_index) if npc_index else ""
            return f"Description of placeable '{plc_name}'{hint}"
        return "Placeable description (area instance)"

    if list_key == "Door List":
        if field_name == "LocalizedName":
            return "Door name (area instance)"
        return "Door description (area instance)"

    if list_key == "TriggerList":
        trig_type = instance.get("Type", 0)
        if field_name == "LocalizedName":
            if trig_type == 1:
                return (
                    "Area transition tooltip, shown when the player hovers over "
                    "the transition (area instance)"
                )
            if trig_type == 2 or instance.get("TrapFlag"):
                return "Trap name, shown when the trap is detected (area instance)"
            return (
                "Generic trigger name. Often retrieved by scripts via "
                "GetLocalizedName() and shown to the player as floating text / "
                "SpeakString when crossing the trigger. Quoted text in \"…\" is an "
                "NPC one-liner; bracketed text in […] is an internal thought / "
                "narrator comment — preserve the surrounding punctuation."
            )
        return "Trigger description (area instance)"

    if list_key == "WaypointList":
        if field_name == "LocalizedName":
            return "Waypoint name (area instance)"
        if field_name == "MapNote":
            return "Waypoint map note label (area instance)"
        return "Waypoint description (area instance)"

    if list_key == "Encounter List":
        if field_name == "LocalizedName":
            return (
                "Encounter group label (area instance). Often a toolset-style "
                "classifier (e.g. 'Orc, Low Group') — translate as a short "
                "label, not a sentence."
            )

    if list_key == "StoreList":
        if field_name in ("LocName", "LocalizedName"):
            return "Store name (area instance)"
        return "Store description (area instance)"

    return f"Area instance field ({list_key}.{field_name})"


def _build_inventory_context(
    field_name: str,
    inv_item: Dict[str, Any],
) -> str:
    """Build an enriched context string for an inventory / equipped item field."""
    bi = base_item_label(inv_item.get("BaseItem", -1))
    item_name = extract_local_string(inv_item.get("LocalizedName", {})) or ""

    if field_name == "LocalizedName":
        if bi:
            return f"Item name ({bi}, inventory instance)"
        return "Item name (inventory instance)"

    if field_name == "Description":
        if bi and item_name:
            return f"Description of {bi} '{item_name}' (inventory instance)"
        if item_name:
            return f"Item description for '{item_name}' (inventory instance)"
        return "Item description (inventory instance)"

    if field_name == "DescIdentified":
        if bi and item_name:
            return f"Identified description of {bi} '{item_name}' (inventory instance)"
        if item_name:
            return f"Item identified description for '{item_name}' (inventory instance)"
        return "Item identified description (inventory instance)"

    return f"Item field ({field_name})"


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
                meta_type = _meta_type_for_inventory_field(inv_field)
                ctx_label = _build_inventory_context(inv_field, inv_item)
                self._append_loc_string_item(
                    inv_item,
                    inv_field,
                    file_path,
                    meta_type=meta_type,
                    context=ctx_label,
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

        npc_index = _build_npc_name_index(parsed_data)

        for list_key, field_names in INSTANCE_LISTS.items():
            instances = parsed_data.get(list_key, [])
            if not isinstance(instances, list):
                continue
            for inst_idx, instance in enumerate(instances):
                if not isinstance(instance, dict):
                    continue
                for field_name in field_names:
                    meta_type = _meta_type_for_instance_field(list_key, field_name)
                    ctx_label = _build_instance_context(
                        list_key, field_name, instance, npc_index
                    )
                    self._append_loc_string_item(
                        instance,
                        field_name,
                        file_path,
                        meta_type=meta_type,
                        context=ctx_label,
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
                                meta_type = _meta_type_for_inventory_field(inv_field)
                                ctx_label = _build_inventory_context(
                                    inv_field, inv_item
                                )
                                self._append_loc_string_item(
                                    inv_item,
                                    inv_field,
                                    file_path,
                                    meta_type=meta_type,
                                    context=ctx_label,
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
