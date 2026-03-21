"""GIT injector for NWN area instance files.

This module patches CExoLocString fields inside .git (Game Instance Data) files.
.git files contain placed object instances (creatures, doors, placeables, etc.)
whose names may differ from the blueprint templates (.utc, .utd, .utp, …).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..file_handlers.gff_handler import read_gff
from ..file_handlers.gff_patcher import GFFPatcher, GFFPatchError

logger = logging.getLogger(__name__)

# Pattern for internal engine tags that must NOT be translated.
# Waypoints (WP…), destinations (DST_…), posts (POST_…), night/spawn markers, etc.
# Also matches CamelCase identifiers with no spaces (e.g. "NW_YOURTAGHERE").
_INTERNAL_TAG_RE = re.compile(
    r"^(?:"
    r"WP_?\w*"        # WP, WP_, WPBasement, WP_Spawn …
    r"|DST_\w*"       # DST_Tunnel …
    r"|POST_\w*"      # POST_Guard …
    r"|NW_\w*"        # NW_ engine prefixes
    r"|YOURTAGHERE"   # placeholder tags from Bioware templates
    r")$",
    re.IGNORECASE,
)

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
    """Return dict entries from the ``ItemList`` field of an instance struct.

    Args:
        instance: Parsed GFF struct for a creature or placeable instance.

    Returns:
        List of dict entries representing inventory items.
    """
    raw = instance.get("ItemList", [])
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def is_internal_tag(text: str) -> bool:
    """Return True if *text* looks like an internal engine tag that should not be translated.

    Covers waypoint markers (WP…), destination tags (DST_…), post tags (POST_…),
    NW_ engine prefixes, and spaceless CamelCase-only identifiers that contain no
    natural-language words.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _INTERNAL_TAG_RE.match(stripped):
        return True
    # Spaceless identifiers with underscores (e.g. "Spawn_Point_01") — skip
    if "_" in stripped and " " not in stripped:
        return True
    return False


def _add_string_values_from_fields(
    obj: Dict[str, Any],
    field_names: List[str],
    bucket: Set[str],
    existing: Dict[str, str],
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
        if (
            original_text
            and isinstance(original_text, str)
            and original_text not in existing
            and not is_internal_tag(original_text)
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


def _collect_locale_patches_on_struct(
    struct: Dict[str, Any],
    list_key: str,
    field_names: List[str],
    translations: Dict[str, str],
    git_basename: str,
    patches: List[Tuple[int, str]],
) -> int:
    """Append CExoLocString patches for one GFF struct (instance or inventory row).

    Args:
        struct: Parsed GFF struct with ``_record_offsets`` metadata.
        list_key: Instance list name (for logging, e.g. ``"Creature List"``).
        field_names: CExoLocString field names to check for translations.
        translations: Mapping of original text to translated text.
        git_basename: Filename of the .git file (for log messages).
        patches: Mutable list to which ``(record_offset, translated_text)`` tuples
            are appended.

    Returns:
        Number of patches appended.
    """
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

        patches.append((rec_offset, translated_text))
        items_patched += 1
        logger.debug(
            "Queued patch %s.%s in %s: '%s' -> '%s'",
            list_key,
            field_name,
            git_basename,
            original_text[:30],
            translated_text[:30],
        )

    return items_patched


def _collect_inventory_item_patches(
    instance: Dict[str, Any],
    parent_list_key: str,
    translations: Dict[str, str],
    git_basename: str,
    patches: List[Tuple[int, str]],
) -> int:
    """Collect patches for ``ItemList`` rows under a creature or placeable instance.

    Args:
        instance: Parsed GFF struct for a creature or placeable.
        parent_list_key: Parent list name (e.g. ``"Creature List"``).
        translations: Mapping of original text to translated text.
        git_basename: Filename of the .git file (for log messages).
        patches: Mutable list to which patch tuples are appended.

    Returns:
        Number of patches appended for inventory items.
    """
    total = 0
    for inv_item in _iter_item_list_entries(instance):
        total += _collect_locale_patches_on_struct(
            inv_item,
            f"{parent_list_key}.ItemList",
            ITEM_INVENTORY_FIELDS,
            translations,
            git_basename,
            patches,
        )
    return total


def patch_git_file(
    git_path: Path,
    translations: Dict[str, str],
    tlk=None,
    gff_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Patch translatable strings inside a .git area instance file.

    Iterates over every instance list (creatures, placeables, doors, …)
    and patches CExoLocString fields whose original Value is found in
    *translations*.

    Args:
        git_path: Path to the extracted .git file on disk.
        translations: Mapping of original text -> translated text.
        tlk: Optional TLK file for resolving StrRef-only names.
        gff_data: If provided, skip reading *git_path* (must match on-disk state).

    Returns:
        Number of individual fields that were patched.
    """
    if not translations:
        return 0

    if gff_data is None:
        gff_data = read_gff(git_path, tlk=tlk)

    try:
        patcher = GFFPatcher(git_path)
    except Exception as e:
        logger.error("Failed to initialize GFFPatcher for %s: %s", git_path, e)
        return 0

    items_patched = 0
    patches: List[Tuple[int, str]] = []

    for list_key, field_names in INSTANCE_LISTS.items():
        instances = gff_data.get(list_key, [])
        if not isinstance(instances, list):
            continue

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            items_patched += _collect_locale_patches_on_struct(
                instance,
                list_key,
                field_names,
                translations,
                git_path.name,
                patches,
            )

            if list_key in INSTANCE_LISTS_WITH_INVENTORY:
                items_patched += _collect_inventory_item_patches(
                    instance,
                    list_key,
                    translations,
                    git_path.name,
                    patches,
                )

    if patches:
        try:
            patcher.patch_multiple(patches)
        except GFFPatchError as e:
            logger.error("Failed to batch-patch %s: %s", git_path.name, e)
            return 0

    if items_patched:
        logger.info("Patched %d instance fields in %s", items_patched, git_path.name)

    return items_patched
