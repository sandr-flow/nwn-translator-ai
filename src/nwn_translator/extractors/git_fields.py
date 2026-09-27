"""Translatable fields of area instance files (``.git``) and their filters.

A ``.git`` holds the instances placed in an area (creatures, placeables, doors,
…). Their names may differ from the blueprints (``.utc``, ``.utp``, …), so each
instance list declares its visible CExoLocString fields in
:data:`INSTANCE_FIELDS`, with the metadata type and prompt context of each.
Inventory rows and items dropped on the area floor share :func:`item_fields`.

Instance names are filtered harder than blueprint text: toolset route labels
and resrefs often sit in them. The blueprint-name oracle
(:func:`get_module_creature_names`) rescues real names that look code-like.
"""

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from ..context.string_filters import should_skip_entity_source_text
from ..formats.gff import read_gff
from ..nwn_constants import base_item_label, gender_label
from .base import extract_local_string, list_field
from .creature_extractor import creature_name_context, creature_traits, name_fields
from .item_extractor import item_description_context

logger = logging.getLogger(__name__)

#: ``{lowercased first name: gender label}`` of the creatures placed in an area.
NpcIndex = Dict[str, str]
#: Prompt context of a field computed from ``(instance struct, NPC index)``.
GitContext = Callable[[Dict[str, Any], NpcIndex], str]

_AREA_INSTANCE = "area instance"


@dataclass(frozen=True)
class GitField:
    """A translatable CExoLocString of an instance struct.

    Attributes:
        name: GFF field label.
        item_type: Metadata ``type`` of the extracted item.
        context: Fixed prompt context, or a function of the instance struct and
            the area's NPC index.
    """

    name: str
    item_type: str
    context: Union[str, GitContext]

    def context_for(self, instance: Dict[str, Any], npc_index: NpcIndex) -> str:
        """Returns the prompt context of this field on *instance*.

        Args:
            instance: Instance struct holding the field.
            npc_index: NPC index of the area (see :func:`build_npc_index`).

        Returns:
            The fixed context, or the one computed from *instance*.
        """
        if isinstance(self.context, str):
            return self.context
        return self.context(instance, npc_index)


def build_npc_index(parsed_data: Dict[str, Any]) -> NpcIndex:
    """Maps the area's NPC first names to their gender.

    Args:
        parsed_data: Parsed ``.git`` root struct.

    Returns:
        ``{lowercased first name: gender label}`` for creatures with a
        non-blank first name and a known gender.
    """
    index: NpcIndex = {}
    for creature in list_field(parsed_data, "Creature List"):
        if not isinstance(creature, dict):
            continue
        first = (extract_local_string(creature.get("FirstName", {})) or "").strip()
        gender = gender_label(creature.get("Gender", -1))
        if first and gender:
            index[first.lower()] = gender
    return index


def npc_possessive_hint(text: str, npc_index: NpcIndex) -> str:
    """Returns a gender hint when *text* contains an NPC's possessive (``Anna's``).

    Args:
        text: Placeable name or description.
        npc_index: Result of :func:`build_npc_index`.

    Returns:
        ``" (contains possessive of NPC '<name>', gender: <gender>)"`` for the
        first indexed NPC whose name starts a word followed by ``'s``, with
        the name as written in *text*; ``""`` when there is none.
    """
    if not npc_index or "'s" not in text:
        return ""
    for name_lower, gender in npc_index.items():
        match = re.search(rf"(?<!\w){re.escape(name_lower)}(?='s)", text, re.IGNORECASE)
        if match:
            return f" (contains possessive of NPC '{match.group()}', gender: {gender})"
    return ""


def _creature_name_context(field_name: str, instance: Dict[str, Any], _npcs: NpcIndex) -> str:
    """Returns the context of a placed creature's first or last name."""
    qualifier = ", ".join(filter(None, [creature_traits(instance), _AREA_INSTANCE]))
    return creature_name_context(field_name, qualifier)


def _creature_description_context(instance: Dict[str, Any], _npcs: NpcIndex) -> str:
    """Returns the context of a placed creature's description."""
    full_name = " ".join(filter(None, name_fields(instance).values()))
    detail = ", ".join(
        filter(None, [f"name: {full_name}" if full_name else "", creature_traits(instance)])
    )
    if detail:
        return f"Creature description ({detail}, {_AREA_INSTANCE})"
    return f"Creature description ({_AREA_INSTANCE})"


def _placeable_name_context(instance: Dict[str, Any], npc_index: NpcIndex) -> str:
    """Returns the context of a placed placeable's name, with an NPC possessive hint."""
    name = extract_local_string(instance.get("LocName", {})) or ""
    return f"Placeable name ({_AREA_INSTANCE}){npc_possessive_hint(name, npc_index)}"


def _placeable_description_context(instance: Dict[str, Any], npc_index: NpcIndex) -> str:
    """Returns the context of a placed placeable's description, naming the placeable."""
    name = extract_local_string(instance.get("LocName", {})) or ""
    if not name:
        return f"Placeable description ({_AREA_INSTANCE})"
    description = extract_local_string(instance.get("Description", {})) or ""
    return f"Description of placeable '{name}'{npc_possessive_hint(description, npc_index)}"


def _trigger_name_context(instance: Dict[str, Any], _npcs: NpcIndex) -> str:
    """Returns the context of a trigger name, by trigger type."""
    trigger_type = instance.get("Type", 0)
    if trigger_type == 1:
        return (
            "Area transition tooltip, shown when the player hovers over "
            f"the transition ({_AREA_INSTANCE})"
        )
    if trigger_type == 2 or instance.get("TrapFlag"):
        return f"Trap name, shown when the trap is detected ({_AREA_INSTANCE})"
    return (
        "Generic trigger name. Often retrieved by scripts via "
        "GetLocalizedName() and shown to the player as floating text / "
        'SpeakString when crossing the trigger. Quoted text in "…" is an '
        "NPC one-liner; bracketed text in […] is an internal thought / "
        "narrator comment — preserve the surrounding punctuation."
    )


#: GFF instance list label -> translatable fields, in extraction order.
#: Door and store instances carry their name in ``LocName`` (the blueprint
#: label); ``LocalizedName`` is extracted too for toolsets that write it.
INSTANCE_FIELDS: Dict[str, Tuple[GitField, ...]] = {
    "Creature List": (
        GitField("FirstName", "creature_first_name", partial(_creature_name_context, "FirstName")),
        GitField("LastName", "creature_last_name", partial(_creature_name_context, "LastName")),
        GitField("Description", "creature_description", _creature_description_context),
    ),
    "Placeable List": (
        GitField("LocName", "placeable_name", _placeable_name_context),
        GitField("Description", "placeable_description", _placeable_description_context),
    ),
    "Door List": (
        GitField("LocName", "door_name", f"Door name ({_AREA_INSTANCE})"),
        GitField("LocalizedName", "door_name", f"Door name ({_AREA_INSTANCE})"),
        GitField("Description", "door_description", f"Door description ({_AREA_INSTANCE})"),
    ),
    # The GFF label has no space, unlike the other lists.
    "TriggerList": (
        GitField("LocalizedName", "trigger_name", _trigger_name_context),
        GitField("Description", "trigger_description", f"Trigger description ({_AREA_INSTANCE})"),
    ),
    # Only the map note is player-visible (automap label); waypoint names and
    # descriptions are toolset-only.
    "WaypointList": (
        GitField("MapNote", "waypoint_map_note", f"Waypoint map note label ({_AREA_INSTANCE})"),
    ),
    # Scripts may show an encounter's name through GetLocalizedName().
    "Encounter List": (
        GitField(
            "LocalizedName",
            "encounter_name",
            f"Encounter group label ({_AREA_INSTANCE}). Often a toolset-style "
            "classifier (e.g. 'Orc, Low Group') — translate as a short "
            "label, not a sentence.",
        ),
    ),
    "StoreList": (
        GitField("LocName", "store_name", f"Store name ({_AREA_INSTANCE})"),
        GitField("LocalizedName", "store_name", f"Store name ({_AREA_INSTANCE})"),
        GitField("Description", "store_description", f"Store description ({_AREA_INSTANCE})"),
    ),
}

#: Instance list label -> nested item lists (loose inventory, equipped gear).
#: Store instances instead nest their stock in ``ItemList`` rows of recursive
#: ``StoreList`` shelves.
INSTANCE_NESTED_ITEM_LISTS: Dict[str, List[str]] = {
    "Creature List": ["ItemList", "Equip_ItemList"],
    "Placeable List": ["ItemList"],
}

#: CExoLocString field of an inventory row or area floor item -> metadata ``type``,
#: in extraction order.
_ITEM_TYPES = {
    "LocalizedName": "item_name",
    "Description": "item_description",
    "DescIdentified": "item_identified_description",
}

#: Top-level list of items dropped on the area floor in the toolset. Visited
#: areas bake these into the save, so only unvisited areas pick up changes.
AREA_ITEM_LIST_KEY = "List"


def item_fields(row: Dict[str, Any], where: str) -> List[Tuple[str, str, str]]:
    """Returns ``(field, metadata type, context)`` for each field of an item row.

    Args:
        row: Inventory row or area floor item struct.
        where: Placement shown in the context (``inventory instance`` or
            ``placed on the area floor``).

    Returns:
        One entry per item field, in extraction order.
    """
    base_item = base_item_label(row.get("BaseItem", -1))
    name = extract_local_string(row.get("LocalizedName", {})) or ""
    name_context = f"Item name ({', '.join(filter(None, [base_item, where]))})"
    return [
        (
            field,
            item_type,
            (
                name_context
                if field == "LocalizedName"
                else f"{item_description_context(field, base_item, name)} ({where})"
            ),
        )
        for field, item_type in _ITEM_TYPES.items()
    ]


def should_translate_git_string(
    text: object,
    meta_type: str,
    known_names: Optional[FrozenSet[str]] = None,
) -> bool:
    """Tells whether a ``.git`` string is suitable for translation.

    Code-like route labels, resrefs, placeholders and toolset terms are
    rejected. A code-like string matching a blueprint creature name is a real
    name (``McGee``, ``DeVir``) and passes.

    Args:
        text: Embedded CExoLocString value.
        meta_type: Metadata ``type`` of the field.
        known_names: Blueprint-name oracle (see :func:`get_module_creature_names`).

    Returns:
        ``True`` when the string should be extracted.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    return not should_skip_entity_source_text(text.strip(), {"type": meta_type}, known_names)


def collect_blueprint_creature_names(root: Path) -> FrozenSet[str]:
    """Collects casefolded FirstName/LastName values of every ``.utc`` under *root*.

    Blueprint names are the translatability oracle for ``.git`` creature
    names: the ``.utc`` extractor translates them unfiltered, so any ``.git``
    occurrence of the same text must be translatable too, however code-like
    it looks. Encoding does not matter here: the oracle is only consulted for
    strings whose code-like shape is pure ASCII, which decodes identically in
    every supported code page.

    Args:
        root: Module extraction directory.

    Returns:
        The casefolded, stripped names.
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
        for value in name_fields(data).values():
            if value.strip():
                names.add(value.strip().casefold())
    return frozenset(names)


_creature_name_cache: "OrderedDict[Path, FrozenSet[str]]" = OrderedDict()
_CREATURE_NAME_CACHE_MAX = 4
# Guards the cache and the per-directory build locks below; never held during a scan.
_creature_name_cache_lock = threading.Lock()
# One lock per directory whose oracle is being built, so concurrent extraction workers
# wait for a single .utc scan instead of each running their own, while lookups and
# builds for other directories go ahead.
_creature_name_build_locks: Dict[Path, threading.Lock] = {}


def _cached_creature_names(key: Path) -> Optional[FrozenSet[str]]:
    """Returns the cached oracle for *key* and marks it recently used."""
    with _creature_name_cache_lock:
        names = _creature_name_cache.get(key)
        if names is not None:
            _creature_name_cache.move_to_end(key)
        return names


def get_module_creature_names(root: Path) -> FrozenSet[str]:
    """Returns the blueprint creature-name oracle of a module directory.

    The oracle is built once per directory and cached for the process, even when
    extraction workers ask for it concurrently. The cached entry is deliberately reused by
    later lookups: rebuild re-extracts ``.git`` files after the ``.utc`` files on disk
    were patched with translated names, and a fresh oracle would no longer match the
    original ``.git`` text.

    Args:
        root: Module extraction directory.

    Returns:
        The casefolded blueprint first and last names.
    """
    key = root.resolve()
    names = _cached_creature_names(key)
    if names is not None:
        return names
    with _creature_name_cache_lock:
        build_lock = _creature_name_build_locks.setdefault(key, threading.Lock())
    with build_lock:
        names = _cached_creature_names(key)
        if names is not None:
            return names
        names = collect_blueprint_creature_names(root)
        logger.debug("Blueprint name oracle for %s: %d names", root, len(names))
        with _creature_name_cache_lock:
            _creature_name_cache[key] = names
            while len(_creature_name_cache) > _CREATURE_NAME_CACHE_MAX:
                _creature_name_cache.popitem(last=False)
            _creature_name_build_locks.pop(key, None)
    return names


def clear_creature_name_cache() -> None:
    """Drops every cached blueprint-name oracle."""
    with _creature_name_cache_lock:
        _creature_name_cache.clear()
