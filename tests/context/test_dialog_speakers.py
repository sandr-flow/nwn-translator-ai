"""Dialog speakers: owners and tagged speakers among creatures, placeables and doors."""

from __future__ import annotations

from pathlib import Path

import pytest

from nwn_translator.context.dialog_speakers import (
    dialog_line_speaker,
    dialog_owners,
    speaker_lines,
    tagged_speakers,
)
from nwn_translator.context.world_context import NPCInfo, WorldContext, WorldScanner
from nwn_translator.extractors.base import DialogNode
from nwn_translator.prompts.dialog import speakers_block
from tests.support.gff_writer import loc, write_gff

PLAYER = {"kind": "player", "name": "", "tag": ""}
OWNER_UNKNOWN = {"kind": "owner_unknown", "name": "", "tag": ""}

# Aurora ids: race 0 = Dwarf, 6 = Human; gender 0 = Male, 1 = Female.
DWARF, HUMAN = 0, 6
MALE, FEMALE = 0, 1


def _creature(tag: str, first: str, conversation: str = "", **fields) -> dict:
    return {
        "Tag": tag,
        "FirstName": loc(first),
        "LastName": loc(fields.pop("last", "")),
        "Race": fields.pop("race", HUMAN),
        "Gender": fields.pop("gender", MALE),
        "Conversation": conversation,
        **fields,
    }


def _thing(tag: str, name: str, conversation: str = "") -> dict:
    """A placeable or door struct: its name is ``LocName``."""
    return {"Tag": tag, "LocName": loc(name), "Conversation": conversation}


def _npc(tag, first="", last="", conversation="", race="Human", gender="Female", **kw):
    return NPCInfo(tag, first, last, "", race, gender, conversation, **kw)


def _blueprints(*npcs: NPCInfo) -> WorldContext:
    world = WorldContext()
    for npc in npcs:
        world.npcs[npc.tag] = npc
    return world


def _placed(*actors: NPCInfo) -> WorldContext:
    world = WorldContext()
    for actor in actors:
        world.register_dialog_actor(actor)
    return world


def _scan(tmp_path: Path, **lists) -> WorldContext:
    write_gff(tmp_path / "a.git", {"StructType": "GIT", **lists}, file_type="GIT")
    return WorldScanner().scan_directory(tmp_path)


def _lines(world, stem: str, *nodes: DialogNode) -> list[str]:
    node_map = {("E" if n.is_entry else "R") + str(n.node_id): n for n in nodes}
    return speaker_lines(world, stem, node_map, f"{stem}.dlg")


def _entry(node_id: int, speaker: str = "") -> DialogNode:
    return DialogNode(node_id=node_id, text="Hello.", speaker=speaker, is_entry=True)


@pytest.fixture
def world(tmp_path: Path) -> WorldContext:
    """A module with speakers spread over blueprints and one area's placements."""
    write_gff(
        tmp_path / "guard.utc",
        {"StructType": "UTC", **_creature("GUARD", "Guard", "guard_talk")},
        file_type="UTC",
    )
    write_gff(
        tmp_path / "statue.utp",
        {"StructType": "UTP", **_thing("STATUE", "Old Statue", "statue_talk")},
        file_type="UTP",
    )
    write_gff(
        tmp_path / "gate.utd",
        {"StructType": "UTD", **_thing("GATE", "City Gate", "gate_talk")},
        file_type="UTD",
    )
    return _scan(
        tmp_path,
        **{
            "Creature List": [
                # Placed from the guard blueprint, unchanged: the same speaker.
                _creature("GUARD", "Guard", "guard_talk", ItemList=[]),
                _creature("GUARD", "Guard", "guard_talk", ItemList=[]),
                # A commoner renamed and given its own dialog when placed.
                _creature("MARTA", "Marta", "Marta_Talk", last="Vane", gender=FEMALE),
                # Only reachable by tag from another dialog's Speaker field.
                _creature("SMITH", "Borin", race=DWARF),
            ],
            "Placeable List": [
                _thing("SIGN", "Notice Board", "sign_talk"),
                # Untagged, but named: still an owner, with no tag to show.
                _thing("", "Post", "sign_talk"),
            ],
            "Door List": [_thing("CELL", "Cell Door", "cell_talk")],
        },
    )


# ---------------------------------------------------------------------------
# World scan
# ---------------------------------------------------------------------------


def test_placed_objects_are_speakers_but_not_characters(world):
    """Placements only feed dialog speakers: the world block and glossary are unchanged."""
    assert list(world.npcs) == ["GUARD"]
    assert "Marta" not in world.to_prompt_block()
    assert ("Marta Vane", "character") not in world.get_all_names()
    # Identical placements are indexed once.
    assert [n.first_name for n in world.dialog_actors_by_conversation["guard_talk"]] == ["Guard"]
    assert world.register_dialog_actor(world.dialog_actors_by_tag["MARTA"][0]) is False
    kinds = {
        tag: (actor.kind, actor.first_name, actor.race, actor.gender)
        for tag, actors in world.dialog_actors_by_tag.items()
        for actor in actors
    }
    assert kinds == {
        "GUARD": ("creature", "Guard", "Human", "Male"),
        "MARTA": ("creature", "Marta", "Human", "Female"),
        "SMITH": ("creature", "Borin", "Dwarf", "Male"),
        "SIGN": ("placeable", "Notice Board", "", ""),
        "CELL": ("door", "Cell Door", "", ""),
        "STATUE": ("placeable", "Old Statue", "", ""),
        "GATE": ("door", "City Gate", "", ""),
    }


def test_door_names_fall_back_to_localized_name_and_nameless_objects_are_skipped(tmp_path):
    world = _scan(
        tmp_path,
        **{
            "Door List": [{"Tag": "D", "LocalizedName": loc("Trapdoor"), "Conversation": "d"}],
            "Placeable List": [{"Tag": "", "Conversation": "x"}],
        },
    )
    assert [actor.first_name for actor in dialog_owners(world, "d")] == ["Trapdoor"]
    assert dialog_owners(world, "x") == []


def test_creature_without_race_is_described_as_a_creature(tmp_path):
    creature = {"Tag": "BLOB", "FirstName": loc("Blob"), "Gender": MALE, "Conversation": "b"}
    world = _scan(tmp_path, **{"Creature List": [creature]})
    assert _lines(world, "b", _entry(0)) == [
        "- In b.dlg, lines marked [NPC]: spoken by Blob (Creature, Male)"
    ]


# ---------------------------------------------------------------------------
# Editor labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dialog", "expected"),
    [
        ("guard_talk", {"kind": "npc", "name": "Guard", "tag": "GUARD"}),
        ("MARTA_TALK", {"kind": "npc", "name": "Marta Vane", "tag": "MARTA"}),
        ("sign_talk", {"kind": "npc", "name": "Notice Board / Post", "tag": "SIGN"}),
        ("cell_talk", {"kind": "npc", "name": "Cell Door", "tag": "CELL"}),
        ("statue_talk", {"kind": "npc", "name": "Old Statue", "tag": "STATUE"}),
        ("gate_talk", {"kind": "npc", "name": "City Gate", "tag": "GATE"}),
        ("scripted_talk", OWNER_UNKNOWN),
    ],
)
def test_owner_lines_name_the_object_that_owns_the_dialog(world, dialog, expected):
    assert dialog_line_speaker(world, dialog, is_entry=True) == expected


def test_tagged_line_names_a_placed_creature(world):
    speaker = dialog_line_speaker(world, "marta_talk", is_entry=True, speaker_tag="SMITH")
    assert speaker == {"kind": "npc", "name": "Borin", "tag": "SMITH"}


@pytest.mark.parametrize(
    "world, dialog, tag, expected",
    [
        (
            _blueprints(_npc("stumpy_tag", "Stumpy", "Stoneaxe", "stumpy")),
            "severina",
            "stumpy_tag",
            {"kind": "npc", "name": "Stumpy Stoneaxe", "tag": "stumpy_tag"},
        ),
        # An unknown tag is kept.
        (
            _blueprints(_npc("sev_tag", "Severina", conversation="severina")),
            "severina",
            "ghost",
            {"kind": "npc", "name": "", "tag": "ghost"},
        ),
        (
            _blueprints(_npc("sev_tag", "Severina", conversation="Severina")),
            "severina",
            None,
            {"kind": "npc", "name": "Severina", "tag": "sev_tag"},
        ),
        (
            _blueprints(
                _npc("b_tag", "Bob", conversation="tavern"),
                _npc("a_tag", "Anna", conversation="tavern"),
                _npc("c_tag", "Carl", conversation="other"),
            ),
            "tavern",
            None,
            {"kind": "npc", "name": "Anna / Bob", "tag": "a_tag / b_tag"},
        ),
        # Owners with one name are named once.
        (
            _blueprints(
                _npc("GUARD2", "Guard", conversation="guard"),
                _npc("GUARD1", "Guard", conversation="guard"),
            ),
            "guard",
            None,
            {"kind": "npc", "name": "Guard", "tag": "GUARD1 / GUARD2"},
        ),
        # Owners beyond three are counted.
        (
            _blueprints(
                *(_npc(f"c{i}", f"Commoner {i}", conversation="commoner") for i in range(5))
            ),
            "commoner",
            None,
            {
                "kind": "npc",
                "name": "Commoner 0 / Commoner 1 / Commoner 2 +2",
                "tag": "c0 / c1 / c2 +2",
            },
        ),
        (
            _blueprints(_npc("sev_tag", "Severina", conversation="severina")),
            "door_talk",
            None,
            OWNER_UNKNOWN,
        ),
        # Creatures without a localized name show their tag.
        (
            _blueprints(_npc("sev_tag", conversation="severina"), _npc("bare_tag", " ", " ")),
            "severina",
            None,
            {"kind": "npc", "name": "sev_tag", "tag": "sev_tag"},
        ),
        (
            _blueprints(_npc("sev_tag", conversation="severina"), _npc("bare_tag", " ", " ")),
            "severina",
            "bare_tag",
            {"kind": "npc", "name": "bare_tag", "tag": "bare_tag"},
        ),
        # Objects sharing a tag are named in order.
        (
            _placed(_npc("GUARD", "Mira", race="Elf"), _npc("GUARD", "Jon", gender="Male")),
            "gate",
            "GUARD",
            {"kind": "npc", "name": "Jon / Mira", "tag": "GUARD"},
        ),
        (None, "severina", None, OWNER_UNKNOWN),
        (None, "severina", "bob", {"kind": "npc", "name": "", "tag": "bob"}),
    ],
)
def test_entry_speaker(world, dialog, tag, expected):
    assert dialog_line_speaker(world, dialog, is_entry=True, speaker_tag=tag) == expected


@pytest.mark.parametrize("tag", [None, "x"])
def test_reply_is_the_player(tag):
    world = _blueprints(_npc("sev_tag", "Severina", conversation="severina"))
    assert dialog_line_speaker(world, "severina", is_entry=False, speaker_tag=tag) == PLAYER
    assert dialog_line_speaker(None, "severina", is_entry=False, speaker_tag=tag) == PLAYER


def test_blueprint_and_renamed_placement_are_both_owners():
    world = _blueprints(_npc("COMMONER", "Commoner", conversation="commoner", gender="Male"))
    world.register_dialog_actor(_npc("COMMONER", "Ada", conversation="commoner"))
    world.register_dialog_actor(
        _npc("COMMONER", "Commoner", conversation="commoner", gender="Male")
    )

    assert dialog_line_speaker(world, "commoner", is_entry=True) == {
        "kind": "npc",
        "name": "Ada / Commoner",
        "tag": "COMMONER",
    }
    assert [npc.first_name for npc in tagged_speakers(world, "COMMONER")] == ["Commoner", "Ada"]


# ---------------------------------------------------------------------------
# Prompt lines
# ---------------------------------------------------------------------------


def test_prompt_names_owners_and_tagged_speakers():
    world = _blueprints(
        _npc("anna_tag", " Anna ", "Smith", "Tavern"),
        _npc("bare_tag", conversation="tavern", race="Dwarf", gender=""),
        _npc("bob_tag", "Bob", conversation="bob", race="Halfling", gender="Male"),
    )
    reply = DialogNode(node_id=0, text="Hey", is_entry=False)
    assert _lines(world, "tavern", _entry(0), _entry(1, "bob_tag"), _entry(2, "ghost"), reply) == [
        "- In tavern.dlg, lines marked [NPC]: spoken by Anna Smith (Human, Female); "
        "or bare_tag (Dwarf)",
        "- In tavern.dlg, lines marked [bob_tag]: spoken by Bob (Halfling, Male)",
    ]


def test_prompt_names_placed_owners_and_speakers(world):
    assert _lines(world, "marta_talk", _entry(0), _entry(1, "SMITH")) == [
        "- In marta_talk.dlg, lines marked [NPC]: spoken by Marta Vane (Human, Female)",
        "- In marta_talk.dlg, lines marked [SMITH]: spoken by Borin (Dwarf, Male)",
    ]
    assert _lines(world, "sign_talk", _entry(0)) == [
        "- In sign_talk.dlg, lines marked [NPC]: spoken by Notice Board (placeable); "
        "or Post (placeable)"
    ]
    assert _lines(world, "cell_talk", _entry(0)) == [
        "- In cell_talk.dlg, lines marked [NPC]: spoken by Cell Door (door)"
    ]


def test_prompt_works_without_creature_blueprints():
    world = _placed(_npc("SIGN", "Sign", conversation="sign", race="", gender="", kind="placeable"))
    assert _lines(world, "sign", _entry(0)) == [
        "- In sign.dlg, lines marked [NPC]: spoken by Sign (placeable)"
    ]
    guards = _placed(_npc("GUARD", "Jon", gender="Male"), _npc("GUARD", "Mira", race="Elf"))
    assert _lines(guards, "gate", _entry(0, "GUARD")) == [
        "- In gate.dlg, lines marked [GUARD]: spoken by Jon (Human, Male); or Mira (Elf, Female)"
    ]


def test_speakers_block_of_the_dialog_prompt():
    world = _blueprints(
        _npc("sev_tag", "Severina", conversation="severina", race="Dwarf"),
        _npc("stumpy_tag", "Stumpy", conversation="stumpy", race="Dwarf", gender="Male"),
    )
    node_map = {
        "E0": _entry(0),
        "E1": _entry(1, "stumpy_tag"),
        "R0": DialogNode(node_id=0, text="Hi", is_entry=False),
    }

    block = speakers_block(speaker_lines(world, "Severina", node_map))

    assert block.startswith("DIALOG SPEAKERS:")
    assert "- Lines marked [NPC]: spoken by Severina (Dwarf, Female)" in block
    assert "- Lines marked [stumpy_tag]: spoken by Stumpy (Dwarf, Male)" in block
    assert "grammatical forms" in block
    assert speakers_block(speaker_lines(world, "unrelated", {"E0": _entry(0)})) == ""
    assert speakers_block(speaker_lines(WorldContext(), "severina", {"E0": _entry(0)})) == ""
