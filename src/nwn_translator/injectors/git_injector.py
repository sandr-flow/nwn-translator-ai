"""GIT injector for NWN area instance files.

This module patches CExoLocString fields inside .git (Game Instance Data) files.
.git files contain placed object instances (creatures, doors, placeables, etc.)
whose names may differ from the blueprint templates (.utc, .utd, .utp, …).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..file_handlers.gff_handler import read_gff
from ..file_handlers.gff_patcher import GFFPatcher, GFFPatchError

logger = logging.getLogger(__name__)

# Mapping: GFF list key -> list of CExoLocString field names to translate
INSTANCE_LISTS = {
    "Creature List": ["FirstName", "LastName", "Description"],
    "Placeable List": ["LocName"],
    "Door List": ["LocalizedName"],
    "Trigger List": ["LocalizedName"],
    "WaypointList": ["LocalizedName", "Description"],
    "StoreList": ["LocalizedName"],
}


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

    items_patched = 0

    for list_key, field_names in INSTANCE_LISTS.items():
        instances = gff_data.get(list_key, [])
        if not isinstance(instances, list):
            continue

        for instance in instances:
            if not isinstance(instance, dict):
                continue

            record_offsets = instance.get("_record_offsets", {})

            for field_name in field_names:
                field_obj = instance.get(field_name)
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
                        list_key, field_name, git_path.name,
                    )
                    continue

                try:
                    patcher = GFFPatcher(git_path)
                    patcher.patch_local_string(rec_offset, translated_text)
                    items_patched += 1
                    logger.debug(
                        "Patched %s.%s in %s: '%s' -> '%s'",
                        list_key, field_name, git_path.name,
                        original_text[:30], translated_text[:30],
                    )
                except GFFPatchError as e:
                    logger.error(
                        "Failed to patch %s.%s in %s: %s",
                        list_key, field_name, git_path.name, e,
                    )

    if items_patched:
        logger.info("Patched %d instance fields in %s", items_patched, git_path.name)

    return items_patched
