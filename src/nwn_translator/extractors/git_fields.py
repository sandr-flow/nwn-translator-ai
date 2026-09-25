"""GIT field definitions and extraction filters for NWN area instances.

This module identifies visible CExoLocString fields in .git (Game Instance Data).
.git files contain placed object instances (creatures, doors, placeables, etc.)
whose names may differ from the blueprint templates (.utc, .utd, .utp, …).
"""

import logging
import threading
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set

from ..context.string_filters import should_skip_entity_source_text
from ..file_handlers.gff_handler import read_gff

logger = logging.getLogger(__name__)

# Mapping: GFF list key -> list of CExoLocString field names to translate
INSTANCE_LISTS = {
    "Creature List": ["FirstName", "LastName", "Description"],
    "Placeable List": ["LocName", "Description"],
    # Door instances carry their name in ``LocName`` (the .utd label);
    # ``LocalizedName`` is only a fallback for toolsets that write it instead.
    "Door List": ["LocName", "LocalizedName", "Description"],
    # GFF label is ``TriggerList`` (no space); ``Trigger List`` would never match.
    "TriggerList": ["LocalizedName", "Description"],
    # Only MapNote is player-visible (minimap/automap label).
    # LocalizedName and Description are toolset-only — not translated.
    "WaypointList": ["MapNote"],
    # Encounter instance LocalizedName may surface via GetLocalizedName in scripts.
    "Encounter List": ["LocalizedName"],
    "StoreList": ["LocName", "LocalizedName", "Description"],
}

# Mapping: instance list key -> nested item list keys to process.
# Creatures have both loose inventory (ItemList) and equipped gear
# (Equip_ItemList); placeables/stores only carry inventory.
INSTANCE_NESTED_ITEM_LISTS: Dict[str, List[str]] = {
    "Creature List": ["ItemList", "Equip_ItemList"],
    "Placeable List": ["ItemList"],
    "StoreList": ["ItemList"],
}

# CExoLocString fields on each entry inside ItemList / Equip_ItemList
ITEM_INVENTORY_FIELDS = ["LocalizedName", "Description", "DescIdentified"]

# Top-level list of items dropped directly onto the area floor in the toolset.
# Unlike named instance lists, its GFF label is the bare word "List"; each entry
# carries the same CExoLocString fields as an inventory row. Visited areas bake
# these into the save, so only unvisited areas will pick up retranslations.
AREA_ITEM_LIST_KEY = "List"
AREA_ITEM_FIELDS = ITEM_INVENTORY_FIELDS


def _meta_type_for_instance_field(list_key: str, field_name: str) -> str:
    """Return metadata ``type`` string for .git filtering decisions."""
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
        if field_name in ("LocName", "LocalizedName"):
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
    """Return metadata ``type`` for a .git inventory/equipped item field."""
    if field_name == "LocalizedName":
        return "item_name"
    if field_name == "Description":
        return "item_description"
    if field_name == "DescIdentified":
        return "item_identified_description"
    return "git_instance_string"


def should_translate_git_string(
    text: object,
    meta_type: str = "git_instance_string",
    known_names: Optional[FrozenSet[str]] = None,
) -> bool:
    """Return True when a .git locstring is suitable for translation.

    This is shared by extraction and fallback string collection so code-like
    route labels, resrefs, placeholders, and toolset/system terms are filtered
    consistently before they can reach the translator. *known_names* is the
    module's blueprint-name oracle (see :func:`get_module_creature_names`):
    a code-like string matching a blueprint creature name is a real name
    (``McGee``, ``DeVir``) and passes.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return not should_skip_entity_source_text(stripped, {"type": meta_type}, known_names)


def collect_blueprint_creature_names(root: Path) -> FrozenSet[str]:
    """Collect casefolded FirstName/LastName values of every .utc under *root*.

    Blueprint names are the translatability oracle for .git creature names:
    the .utc extractor translates them unfiltered, so any .git occurrence of
    the same text must be translatable too, however code-like it looks.
    Encoding does not matter here — the oracle is only consulted for strings
    whose code-like shape is pure ASCII, which decodes identically in every
    supported code page.
    """
    names: Set[str] = set()
    try:
        utc_files = sorted(root.glob("*.utc"))
    except OSError:
        return frozenset()
    for utc_path in utc_files:
        try:
            data = read_gff(utc_path)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Skipping unreadable blueprint %s", utc_path, exc_info=True)
            continue
        for field_name in ("FirstName", "LastName"):
            field_obj = data.get(field_name)
            if isinstance(field_obj, dict):
                value = field_obj.get("Value", "")
                if isinstance(value, str) and value.strip():
                    names.add(value.strip().casefold())
    return frozenset(names)


_creature_name_cache: "OrderedDict[Path, FrozenSet[str]]" = OrderedDict()
_CREATURE_NAME_CACHE_MAX = 4
# Guards the cache and the per-directory build locks below.
_creature_name_cache_lock = threading.Lock()
# One lock per directory whose oracle is being built, so concurrent extractor
# workers wait for a single .utc scan instead of each running their own.
_creature_name_build_locks: Dict[Path, threading.Lock] = {}


def _cached_creature_names(key: Path) -> Optional[FrozenSet[str]]:
    with _creature_name_cache_lock:
        cached = _creature_name_cache.get(key)
        if cached is not None:
            _creature_name_cache.move_to_end(key)
        return cached


def get_module_creature_names(root: Path) -> FrozenSet[str]:
    """Return the (cached) blueprint creature-name oracle for a module dir.

    The entry built during extraction is deliberately reused at any later
    lookup for the same directory: by injection time the on-disk .utc files
    may already be patched with translated names, and rebuilding would break
    original-text matching. The oracle is built once per directory even when
    extraction workers ask for it concurrently.
    """
    key = root.resolve()
    cached = _cached_creature_names(key)
    if cached is not None:
        return cached
    with _creature_name_cache_lock:
        build_lock = _creature_name_build_locks.setdefault(key, threading.Lock())
    with build_lock:
        cached = _cached_creature_names(key)
        if cached is not None:
            return cached
        names = collect_blueprint_creature_names(root)
        logger.debug("Blueprint name oracle for %s: %d names", root, len(names))
        with _creature_name_cache_lock:
            _creature_name_cache[key] = names
            while len(_creature_name_cache) > _CREATURE_NAME_CACHE_MAX:
                _creature_name_cache.popitem(last=False)
            _creature_name_build_locks.pop(key, None)
    return names


def clear_creature_name_cache() -> None:
    """Drop all cached name oracles (tests / long-lived processes)."""
    with _creature_name_cache_lock:
        _creature_name_cache.clear()


def _collect_strings_from_store_tree(
    store_node: Dict[str, Any],
    found: Set[str],
    existing: Dict[str, str],
    known_names: Optional[FrozenSet[str]] = None,
) -> None:
    """Gather locstrings from *store_node* ItemList and nested StoreList shelves."""
    for inv_item in _iter_nested_item_entries(store_node, "ItemList"):
        _add_string_values_from_fields(
            inv_item,
            ITEM_INVENTORY_FIELDS,
            found,
            existing,
            _meta_type_for_inventory_field,
            known_names,
        )
    children = store_node.get("StoreList", [])
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            _collect_strings_from_store_tree(child, found, existing, known_names)


def _iter_nested_item_entries(instance: Dict[str, Any], nested_key: str) -> List[Dict[str, Any]]:
    """Return dict entries from a nested item list field of an instance struct.

    Args:
        instance: Parsed GFF struct for a creature, placeable, or store instance.
        nested_key: Name of the nested list (``"ItemList"`` or ``"Equip_ItemList"``).

    Returns:
        List of dict entries representing inventory/equipped items.
    """
    raw = instance.get(nested_key, [])
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _add_string_values_from_fields(
    obj: Dict[str, Any],
    field_names: List[str],
    bucket: Set[str],
    existing: Dict[str, str],
    meta_type_for_field: Optional[Callable[[str], str]] = None,
    known_names: Optional[FrozenSet[str]] = None,
) -> None:
    """Collect embedded CExoLocString Values not already present in *existing*.

    Internal engine tags (waypoints, script markers) are skipped automatically.

    Args:
        obj: Parsed GFF struct containing CExoLocString fields.
        field_names: Names of CExoLocString fields to inspect.
        bucket: Mutable set to which discovered texts are added.
        existing: Already-translated texts to skip.
    """
    for field_name in field_names:
        field_obj = obj.get(field_name)
        if not isinstance(field_obj, dict):
            continue
        original_text = field_obj.get("Value", "")
        meta_type = (
            meta_type_for_field(field_name)
            if meta_type_for_field is not None
            else "git_instance_string"
        )
        if (
            original_text
            and isinstance(original_text, str)
            and original_text not in existing
            and should_translate_git_string(original_text, meta_type, known_names)
        ):
            bucket.add(original_text)


def collect_git_strings_missing_from_translations(
    parsed_data: Dict[str, Any],
    existing_translations: Dict[str, str],
    known_names: Optional[FrozenSet[str]] = None,
) -> Set[str]:
    """Gather unique locstring texts from a parsed .git that need translation.

    Walks the extracted GIT structure (instance lists + nested
    ``ItemList``). Strings that already appear as keys in *existing_translations*
    are skipped. Pass the same *known_names* oracle the extractor used so both
    sides stay symmetric.
    """
    found: Set[str] = set()

    for list_key, field_names in INSTANCE_LISTS.items():
        instances = parsed_data.get(list_key, [])
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            _add_string_values_from_fields(
                instance,
                field_names,
                found,
                existing_translations,
                partial(_meta_type_for_instance_field, list_key),
                known_names,
            )
            if list_key == "StoreList":
                _collect_strings_from_store_tree(
                    instance, found, existing_translations, known_names
                )
            else:
                for nested_key in INSTANCE_NESTED_ITEM_LISTS.get(list_key, []):
                    for inv_item in _iter_nested_item_entries(instance, nested_key):
                        _add_string_values_from_fields(
                            inv_item,
                            ITEM_INVENTORY_FIELDS,
                            found,
                            existing_translations,
                            _meta_type_for_inventory_field,
                            known_names,
                        )

    for area_item in _iter_area_item_entries(parsed_data):
        _add_string_values_from_fields(
            area_item,
            AREA_ITEM_FIELDS,
            found,
            existing_translations,
            _meta_type_for_inventory_field,
            known_names,
        )

    return found


def _iter_area_item_entries(parsed_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return dict entries from the top-level ``List`` (area floor items)."""
    raw = parsed_data.get(AREA_ITEM_LIST_KEY, [])
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]
