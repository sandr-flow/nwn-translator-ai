"""GIT injector for NWN area instance files.

This module patches CExoLocString fields inside .git (Game Instance Data) files.
.git files contain placed object instances (creatures, doors, placeables, etc.)
whose names may differ from the blueprint templates (.utc, .utd, .utp, …).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Set

from ..file_handlers.gff_handler import read_gff
from ..file_handlers.gff_patcher import GFFPatcher, GFFPatchError

logger = logging.getLogger(__name__)

# Mapping: GFF list key -> list of CExoLocString field names to translate
INSTANCE_LISTS = {
    "Creature List": ["FirstName", "LastName", "Description"],
    "Placeable List": ["LocName", "Description"],
    "Door List": ["LocalizedName", "Description"],
    "Trigger List": ["LocalizedName", "Description"],
    "WaypointList": ["LocalizedName", "Description"],
    "StoreList": ["LocalizedName", "Description"],
}

# Instance lists that may contain nested inventory ItemList structs
INSTANCE_LISTS_WITH_INVENTORY = frozenset({"Creature List", "Placeable List"})

# CExoLocString fields on each entry inside ItemList (container / creature inventory)
ITEM_INVENTORY_FIELDS = ["LocalizedName", "Description", "DescIdentified"]


def _iter_item_list_entries(instance: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = instance.get("ItemList", [])
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _add_string_values_from_fields(
    obj: Dict[str, Any],
    field_names: List[str],
    bucket: Set[str],
    existing: Dict[str, str],
) -> None:
    """Collect embedded locstring Values not already present in *existing*."""
    for field_name in field_names:
        field_obj = obj.get(field_name)
        if not isinstance(field_obj, dict):
            continue
        original_text = field_obj.get("Value", "")
        if (
            original_text
            and isinstance(original_text, str)
            and original_text not in existing
        ):
            bucket.add(original_text)


def collect_git_strings_missing_from_translations(
    gff_data: Dict[str, Any],
    existing_translations: Dict[str, str],
) -> Set[str]:
    """Gather unique locstring texts from a parsed .git that need translation.

    Walks the same structure as :func:`patch_git_file` (instance lists + nested
    ``ItemList``). Strings that already appear as keys in *existing_translations*
    are skipped.
    """
    found: Set[str] = set()

    for list_key, field_names in INSTANCE_LISTS.items():
        instances = gff_data.get(list_key, [])
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            _add_string_values_from_fields(
                instance, field_names, found, existing_translations
            )
            if list_key in INSTANCE_LISTS_WITH_INVENTORY:
                for inv_item in _iter_item_list_entries(instance):
                    _add_string_values_from_fields(
                        inv_item,
                        ITEM_INVENTORY_FIELDS,
                        found,
                        existing_translations,
                    )

    return found


def _patch_locale_fields_on_struct(
    patcher: GFFPatcher,
    struct: Dict[str, Any],
    list_key: str,
    field_names: List[str],
    translations: Dict[str, str],
    git_basename: str,
) -> int:
    """Patch CExoLocString fields on one GFF struct (instance or inventory row)."""
    items_patched = 0
    record_offsets = struct.get("_record_offsets", {})

    for field_name in field_names:
        field_obj = struct.get(field_name)
        if not isinstance(field_obj, dict):
            continue

        original_text = field_obj.get("Value", "")
        if not original_text or original_text not in translations:
            continue

        translated_text = translations[original_text]
        if translated_text == original_text:
            continue

        rec_offset = record_offsets.get(field_name, 0)
        if rec_offset <= 0:
            logger.debug(
                "No record offset for %s.%s in %s, skipping",
                list_key,
                field_name,
                git_basename,
            )
            continue

        try:
            patcher.patch_local_string(rec_offset, translated_text)
            items_patched += 1
            logger.debug(
                "Patched %s.%s in %s: '%s' -> '%s'",
                list_key,
                field_name,
                git_basename,
                original_text[:30],
                translated_text[:30],
            )
        except GFFPatchError as e:
            logger.error(
                "Failed to patch %s.%s in %s: %s",
                list_key,
                field_name,
                git_basename,
                e,
            )

    return items_patched


def _patch_inventory_items(
    patcher: GFFPatcher,
    instance: Dict[str, Any],
    parent_list_key: str,
    translations: Dict[str, str],
    git_basename: str,
) -> int:
    """Patch ``ItemList`` rows under a creature or placeable instance."""
    total = 0
    for inv_item in _iter_item_list_entries(instance):
        total += _patch_locale_fields_on_struct(
            patcher,
            inv_item,
            f"{parent_list_key}.ItemList",
            ITEM_INVENTORY_FIELDS,
            translations,
            git_basename,
        )
    return total


def patch_git_file(
    git_path: Path,
    translations: Dict[str, str],
    tlk=None,
) -> int:
    """Patch translatable strings inside a .git area instance file.

    Iterates over every instance list (creatures, placeables, doors, …)
    and patches CExoLocString fields whose original Value is found in
    *translations*.

    Args:
        git_path: Path to the extracted .git file on disk.
        translations: Mapping of original text -> translated text.
        tlk: Optional TLK file for resolving StrRef-only names.

    Returns:
        Number of individual fields that were patched.
    """
    if not translations:
        return 0

    gff_data = read_gff(git_path, tlk=tlk)

    try:
        patcher = GFFPatcher(git_path)
    except Exception as e:
        logger.error("Failed to initialize GFFPatcher for %s: %s", git_path, e)
        return 0

    items_patched = 0

    for list_key, field_names in INSTANCE_LISTS.items():
        instances = gff_data.get(list_key, [])
        if not isinstance(instances, list):
            continue

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            items_patched += _patch_locale_fields_on_struct(
                patcher,
                instance,
                list_key,
                field_names,
                translations,
                git_path.name,
            )

            if list_key in INSTANCE_LISTS_WITH_INVENTORY:
                items_patched += _patch_inventory_items(
                    patcher,
                    instance,
                    list_key,
                    translations,
                    git_path.name,
                )

    if items_patched:
        logger.info("Patched %d instance fields in %s", items_patched, git_path.name)

    return items_patched
