"""Who speaks a dialog (.dlg) line, resolved from the module's objects.

An object owns a dialog when its ``Conversation`` resref matches the .dlg
stem; NPC lines with an empty ``Speaker`` field are spoken by that owner. Owners
are creature blueprints and the creatures, placeables and doors placed in areas
or blueprinted with that Conversation. A non-empty ``Speaker`` names the object
by tag, and replies are the player's. The dialog prompt and the web editor share
this resolution.
"""

from typing import Iterable, List, Mapping, Optional, Tuple, TypedDict

from ..extractors.base import DialogNode
from .world_context import NPCInfo, WorldContext

#: Owners listed by name in an editor label; the rest are counted (``+N``).
#: Generic dialogs can be shared by dozens of creature blueprints.
_MAX_LISTED_OWNERS = 3


class DialogSpeaker(TypedDict):
    """Editor label for one dialog line.

    Attributes:
        kind: ``npc`` (a creature, placeable or door), ``player`` (a reply) or
            ``owner_unknown`` (an owner line of a dialog that no scanned object
            uses, e.g. one started from a script).
        name: Object name (the tag when it has none); empty when the tag is
            unknown.
        tag: Object tag.
    """

    kind: str
    name: str
    tag: str


def speaker_name(npc: NPCInfo) -> str:
    """Returns the name of a speaking object.

    Args:
        npc: Creature, placeable or door.

    Returns:
        First and last name, or the tag for an object without a localized name.
    """
    name = " ".join(str(p).strip() for p in (npc.first_name, npc.last_name) if p and str(p).strip())
    return name or npc.tag


def speaker_description(npc: NPCInfo) -> str:
    """Describes a speaking object for the dialog prompt.

    Args:
        npc: Creature, placeable or door.

    Returns:
        The name with race and gender for a creature, or with the object kind.
    """
    if npc.kind == "creature":
        traits = ", ".join(t for t in (npc.race, npc.gender) if t)
    else:
        traits = npc.kind
    name = speaker_name(npc)
    return f"{name} ({traits})" if traits else name


def _identity(npc: NPCInfo) -> Tuple[str, str, str, str, str]:
    """Returns what tells two speaking objects apart in the prompt and the editor."""
    return (npc.kind, npc.tag, speaker_name(npc), npc.race, npc.gender)


def _unique(actors: Iterable[NPCInfo]) -> List[NPCInfo]:
    """Drops repeats of one object (a blueprint and its unchanged placements)."""
    seen = set()
    unique: List[NPCInfo] = []
    for actor in actors:
        key = _identity(actor)
        if key not in seen:
            seen.add(key)
            unique.append(actor)
    return unique


def dialog_owners(world_context: Optional[WorldContext], dlg_stem: str) -> List[NPCInfo]:
    """Returns the owners of a dialog.

    Args:
        world_context: Scanned module objects, if any.
        dlg_stem: Dialog resource name without extension.

    Returns:
        Objects whose ``Conversation`` resref matches *dlg_stem*
        case-insensitively: creature blueprints first, then the other actors,
        without repeats.
    """
    if world_context is None:
        return []
    stem_key = dlg_stem.strip().casefold()
    blueprints = [
        npc
        for npc in world_context.npcs.values()
        if str(npc.conversation).strip().casefold() == stem_key
    ]
    return _unique([*blueprints, *world_context.dialog_actors_by_conversation.get(stem_key, [])])


def tagged_speakers(world_context: Optional[WorldContext], tag: str) -> List[NPCInfo]:
    """Returns the objects a ``Speaker`` tag names.

    Args:
        world_context: Scanned module objects, if any.
        tag: Speaker tag of a dialog entry.

    Returns:
        Objects tagged *tag*: the creature blueprint first, then the other
        actors, without repeats.
    """
    if world_context is None or not tag:
        return []
    blueprint = world_context.npcs.get(tag)
    return _unique(
        [
            *([blueprint] if blueprint is not None else []),
            *world_context.dialog_actors_by_tag.get(tag, []),
        ]
    )


def speaker_lines(
    world_context: Optional[WorldContext],
    dlg_stem: str,
    node_map: Mapping[str, DialogNode],
    file_label: str = "",
) -> List[str]:
    """Describes who speaks a dialog's NPC lines, for the dialog prompt.

    The owner rarely names themself in their lines, so the relevance-filtered
    world context usually omits them and the model would have to guess the
    speaker's gender. One line covers the unmarked ``[NPC]`` lines (the
    owners), then one line per ``Speaker`` tag of an entry, in tag order;
    descriptions are sorted and joined with ``; or``.

    Args:
        world_context: Scanned module objects.
        dlg_stem: Dialog resource name without extension.
        node_map: The dialog's nodes by script key.
        file_label: File name that scopes each line (``In a.dlg, lines …``),
            so lines of several dialogs can share one grouped request.

    Returns:
        The lines; none when no speaker is known.
    """
    scope = f"In {file_label}, lines" if file_label else "Lines"
    lines: List[str] = []
    owners = sorted({speaker_description(npc) for npc in dialog_owners(world_context, dlg_stem)})
    if owners:
        lines.append(f"- {scope} marked [NPC]: spoken by " + "; or ".join(owners))
    tags = sorted({node.speaker for node in node_map.values() if node.is_entry and node.speaker})
    for tag in tags:
        descs = sorted({speaker_description(npc) for npc in tagged_speakers(world_context, tag)})
        if descs:
            lines.append(f"- {scope} marked [{tag}]: spoken by " + "; or ".join(descs))
    return lines


def _join_listed(values: Iterable[str]) -> str:
    """Joins distinct non-empty values with `` / ``, up to three, plus a ``+N`` count."""
    unique = list(dict.fromkeys(value for value in values if value))
    listed = " / ".join(unique[:_MAX_LISTED_OWNERS])
    hidden = len(unique) - _MAX_LISTED_OWNERS
    return f"{listed} +{hidden}" if hidden > 0 else listed


def _label_order(npc: NPCInfo) -> Tuple[str, str, str]:
    """Returns the sort key of speaking objects in an editor label."""
    return (speaker_name(npc), npc.tag, npc.kind)


def dialog_line_speaker(
    world_context: Optional[WorldContext],
    dlg_stem: str,
    *,
    is_entry: bool,
    speaker_tag: str = "",
) -> DialogSpeaker:
    """Resolves the speaker of one dialog line for the web editor.

    Several objects are listed together: names and tags are each joined with
    `` / ``, up to three, with a ``+N`` count of the rest.

    Args:
        world_context: Scanned module objects, if any.
        dlg_stem: Dialog resource name without extension.
        is_entry: ``True`` for an NPC entry, ``False`` for a player reply.
        speaker_tag: ``Speaker`` field of the entry (empty for the owner).

    Returns:
        The editor label of the line.
    """
    if not is_entry:
        return {"kind": "player", "name": "", "tag": ""}
    if speaker_tag:
        speakers = sorted(tagged_speakers(world_context, speaker_tag), key=_label_order)
        return {
            "kind": "npc",
            "name": _join_listed(speaker_name(npc) for npc in speakers),
            "tag": speaker_tag,
        }
    owners = sorted(dialog_owners(world_context, dlg_stem), key=_label_order)
    if not owners:
        return {"kind": "owner_unknown", "name": "", "tag": ""}
    return {
        "kind": "npc",
        "name": _join_listed(speaker_name(npc) for npc in owners),
        "tag": _join_listed(npc.tag for npc in owners),
    }
