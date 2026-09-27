"""Who speaks a dialog (.dlg) line, resolved from the module's objects.

An object owns a dialog when its ``Conversation`` resref matches the .dlg stem;
NPC lines with an empty ``Speaker`` field are spoken by that owner. Owners are
creature blueprints and the creatures, placeables and doors placed in areas or
blueprinted with that Conversation. A non-empty ``Speaker`` names the object by
tag, and replies are the player's. The dialog prompt and the web editor share
this resolution.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Tuple, TypedDict

from ..extractors.base import DialogNode
from .world_context import NPCInfo, WorldContext

#: Speakers named in an editor label; the rest are counted (``+N``). Generic
#: dialogs can be shared by dozens of creature blueprints.
_MAX_LISTED = 3


class DialogSpeaker(TypedDict):
    """Editor label of one dialog line.

    Attributes:
        kind: ``npc`` (a creature, placeable or door), ``player`` (a reply) or
            ``owner_unknown`` (an owner line of a dialog no scanned object uses,
            e.g. one started from a script).
        name: Speaker names (the tag of a nameless object); empty for an unknown tag.
        tag: Speaker tags.
    """

    kind: str
    name: str
    tag: str


def _unique(actors: Iterable[NPCInfo]) -> List[NPCInfo]:
    """Drops repeats of one object (a blueprint and its unchanged placements)."""
    unique: Dict[Tuple[str, ...], NPCInfo] = {}
    for actor in actors:
        key = (actor.kind, actor.tag, actor.speaker_name, actor.race, actor.gender)
        unique.setdefault(key, actor)
    return list(unique.values())


def dialog_owners(world_context: Optional[WorldContext], dlg_stem: str) -> List[NPCInfo]:
    """Returns the objects whose ``Conversation`` matches *dlg_stem* case-insensitively.

    Args:
        world_context: Scanned module objects, if any.
        dlg_stem: Dialog resource name without extension.

    Returns:
        Creature blueprints first, then the other actors, without repeats.
    """
    if world_context is None:
        return []
    key = dlg_stem.strip().casefold()
    blueprints = [
        npc for npc in world_context.npcs.values() if npc.conversation.strip().casefold() == key
    ]
    return _unique(blueprints + world_context.dialog_actors_by_conversation.get(key, []))


def tagged_speakers(world_context: Optional[WorldContext], tag: str) -> List[NPCInfo]:
    """Returns the objects a ``Speaker`` tag names.

    Args:
        world_context: Scanned module objects, if any.
        tag: Speaker tag of a dialog entry.

    Returns:
        The creature blueprint first, then the other actors, without repeats.
    """
    if world_context is None or not tag:
        return []
    blueprint = world_context.npcs.get(tag)
    placed = world_context.dialog_actors_by_tag.get(tag, [])
    return _unique(([blueprint] if blueprint else []) + placed)


def _describe(npc: NPCInfo) -> str:
    """Returns the speaker name with its race and gender, or with its kind if not a creature."""
    traits = npc.traits if npc.kind == "creature" else npc.kind
    return f"{npc.speaker_name} ({traits})" if traits else npc.speaker_name


def speaker_lines(
    world_context: Optional[WorldContext],
    dlg_stem: str,
    node_map: Mapping[str, DialogNode],
    file_label: str = "",
) -> List[str]:
    """Describes who speaks a dialog's NPC lines, for the dialog prompt.

    The owner rarely names themself, so the relevance-filtered world context
    usually omits them and the model would have to guess the speaker's gender.
    One line covers the unmarked ``[NPC]`` lines (the owners), then one line per
    entry ``Speaker`` tag, in tag order; descriptions are sorted and joined with
    ``; or``.

    Args:
        world_context: Scanned module objects.
        dlg_stem: Dialog resource name without extension.
        node_map: The dialog's nodes by script key.
        file_label: File name that scopes each line (``In a.dlg, lines …``), so
            several dialogs can share one request.

    Returns:
        The lines; none when no speaker is known.
    """
    scope = f"In {file_label}, lines" if file_label else "Lines"
    tags = sorted({node.speaker for node in node_map.values() if node.is_entry and node.speaker})
    groups = [("NPC", dialog_owners(world_context, dlg_stem))]
    groups += [(tag, tagged_speakers(world_context, tag)) for tag in tags]
    lines: List[str] = []
    for label, speakers in groups:
        descriptions = sorted({_describe(npc) for npc in speakers})
        if descriptions:
            lines.append(f"- {scope} marked [{label}]: spoken by " + "; or ".join(descriptions))
    return lines


def _join_listed(values: Iterable[str]) -> str:
    """Joins distinct non-empty values with `` / ``, up to three, plus a ``+N`` count."""
    unique = list(dict.fromkeys(value for value in values if value))
    hidden = len(unique) - _MAX_LISTED
    return " / ".join(unique[:_MAX_LISTED]) + (f" +{hidden}" if hidden > 0 else "")


def dialog_line_speaker(
    world_context: Optional[WorldContext],
    dlg_stem: str,
    *,
    is_entry: bool,
    speaker_tag: str = "",
) -> DialogSpeaker:
    """Resolves the speaker of one dialog line for the web editor.

    Args:
        world_context: Scanned module objects, if any.
        dlg_stem: Dialog resource name without extension.
        is_entry: ``True`` for an NPC entry, ``False`` for a player reply.
        speaker_tag: ``Speaker`` field of the entry (empty for the owner).

    Returns:
        The label; several objects' names and tags are each joined with `` / ``.
    """
    if not is_entry:
        return {"kind": "player", "name": "", "tag": ""}
    if speaker_tag:
        speakers = tagged_speakers(world_context, speaker_tag)
    else:
        speakers = dialog_owners(world_context, dlg_stem)
        if not speakers:
            return {"kind": "owner_unknown", "name": "", "tag": ""}
    speakers.sort(key=lambda npc: (npc.speaker_name, npc.tag, npc.kind))
    return {
        "kind": "npc",
        "name": _join_listed(npc.speaker_name for npc in speakers),
        "tag": speaker_tag or _join_listed(npc.tag for npc in speakers),
    }
