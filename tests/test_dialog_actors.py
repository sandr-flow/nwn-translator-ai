"""Dialog owners and tagged speakers among placed creatures, placeables and doors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from nwn_translator.config import TranslationConfig
from nwn_translator.context.dialog_speakers import (
    dialog_line_speaker,
    dialog_owners,
    tagged_speakers,
)
from nwn_translator.context.world_context import NPCInfo, WorldContext, WorldScanner
from nwn_translator.extractors.base import DialogNode
from nwn_translator.file_handlers.gff_writer import write_gff
from nwn_translator.translators.context_translator import ContextualTranslationManager

OWNER_UNKNOWN = {"kind": "owner_unknown", "name": "", "tag": ""}

# Aurora ids: race 0 = Dwarf, 6 = Human; gender 0 = Male, 1 = Female.
DWARF, HUMAN = 0, 6
MALE, FEMALE = 0, 1


def _loc(text: str) -> dict:
    return {"StrRef": -1, "Value": text}


def _creature(tag: str, first: str, conversation: str = "", **fields) -> dict:
    return {
        "Tag": tag,
        "FirstName": _loc(first),
        "LastName": _loc(fields.pop("last", "")),
        "Race": fields.pop("race", HUMAN),
        "Gender": fields.pop("gender", MALE),
        "Conversation": conversation,
        **fields,
    }


def _thing(tag: str, name: str, conversation: str = "") -> dict:
    """A placeable or door struct: its name is ``LocName``."""
    return {"Tag": tag, "LocName": _loc(name), "Conversation": conversation}


@pytest.fixture
def module_dir(tmp_path: Path) -> Path:
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
    write_gff(
        tmp_path / "market.git",
        {
            "StructType": "GIT",
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
        file_type="GIT",
    )
    return tmp_path


@pytest.fixture
def world(module_dir: Path) -> WorldContext:
    return WorldScanner().scan_directory(module_dir)


# ---------------------------------------------------------------------------
# World scan
# ---------------------------------------------------------------------------


def test_scan_keeps_placed_objects_out_of_the_character_registry(world: WorldContext) -> None:
    """Placements only feed dialog speakers: the world block and glossary are unchanged."""
    assert list(world.npcs) == ["GUARD"]
    assert "Marta" not in world.to_prompt_block()
    assert ("Marta Vane", "character") not in world.get_all_names()


def test_identical_placements_are_indexed_once(world: WorldContext) -> None:
    assert [npc.first_name for npc in world.dialog_actors_by_conversation["guard_talk"]] == [
        "Guard"
    ]
    assert world.register_dialog_actor(world.dialog_actors_by_tag["MARTA"][0]) is False


def test_scanned_objects_carry_their_kind(world: WorldContext) -> None:
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


def test_door_instance_name_falls_back_to_localized_name(tmp_path: Path) -> None:
    write_gff(
        tmp_path / "a.git",
        {
            "StructType": "GIT",
            "Door List": [{"Tag": "D", "LocalizedName": _loc("Trapdoor"), "Conversation": "d"}],
        },
        file_type="GIT",
    )

    world = WorldScanner().scan_directory(tmp_path)

    assert [actor.first_name for actor in dialog_owners(world, "d")] == ["Trapdoor"]


def test_objects_without_tag_or_name_are_skipped(tmp_path: Path) -> None:
    write_gff(
        tmp_path / "a.git",
        {"StructType": "GIT", "Placeable List": [{"Tag": "", "Conversation": "x"}]},
        file_type="GIT",
    )

    world = WorldScanner().scan_directory(tmp_path)

    assert dialog_owners(world, "x") == []


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
def test_owner_lines_name_the_object_that_owns_the_dialog(
    world: WorldContext, dialog: str, expected: dict
) -> None:
    assert dialog_line_speaker(world, dialog, is_entry=True) == expected


def test_tagged_line_names_a_placed_creature(world: WorldContext) -> None:
    speaker = dialog_line_speaker(world, "marta_talk", is_entry=True, speaker_tag="SMITH")

    assert speaker == {"kind": "npc", "name": "Borin", "tag": "SMITH"}


def test_objects_sharing_a_tag_are_named_in_order() -> None:
    world = WorldContext()
    world.register_dialog_actor(NPCInfo("GUARD", "Mira", "", "", "Elf", "Female", ""))
    world.register_dialog_actor(NPCInfo("GUARD", "Jon", "", "", "Human", "Male", ""))

    speaker = dialog_line_speaker(world, "gate", is_entry=True, speaker_tag="GUARD")

    assert speaker == {"kind": "npc", "name": "Jon / Mira", "tag": "GUARD"}


def test_creature_without_race_is_described_as_a_creature(tmp_path: Path) -> None:
    write_gff(
        tmp_path / "a.git",
        {
            "StructType": "GIT",
            "Creature List": [
                {"Tag": "BLOB", "FirstName": _loc("Blob"), "Gender": MALE, "Conversation": "b"}
            ],
        },
        file_type="GIT",
    )
    world = WorldScanner().scan_directory(tmp_path)
    node_map = {"E0": DialogNode(node_id=0, text="Blub.", is_entry=True)}

    assert _speaker_lines(world, "b", node_map) == [
        "- In b.dlg, lines marked [NPC]: spoken by Blob (Creature, Male)"
    ]


def test_blueprint_and_renamed_placement_are_both_owners() -> None:
    world = WorldContext()
    world.npcs["COMMONER"] = NPCInfo("COMMONER", "Commoner", "", "", "Human", "Male", "commoner")
    world.register_dialog_actor(
        NPCInfo("COMMONER", "Ada", "", "", "Human", "Female", "commoner"),
    )
    world.register_dialog_actor(
        NPCInfo("COMMONER", "Commoner", "", "", "Human", "Male", "commoner"),
    )

    assert dialog_line_speaker(world, "commoner", is_entry=True) == {
        "kind": "npc",
        "name": "Ada / Commoner",
        "tag": "COMMONER",
    }
    assert [npc.first_name for npc in tagged_speakers(world, "COMMONER")] == [
        "Commoner",
        "Ada",
    ]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _speaker_lines(world: WorldContext, stem: str, node_map: dict) -> list[str]:
    manager = ContextualTranslationManager(
        TranslationConfig(api_key="k", input_file=Path("m.mod")), Mock(), world
    )
    return manager._speaker_lines(stem, node_map, f"{stem}.dlg")


def test_prompt_names_placed_owners_and_tagged_speakers(world: WorldContext) -> None:
    node_map = {
        "E0": DialogNode(node_id=0, text="Hello.", is_entry=True),
        "E1": DialogNode(node_id=1, text="Busy.", speaker="SMITH", is_entry=True),
    }

    assert _speaker_lines(world, "marta_talk", node_map) == [
        "- In marta_talk.dlg, lines marked [NPC]: spoken by Marta Vane (Human, Female)",
        "- In marta_talk.dlg, lines marked [SMITH]: spoken by Borin (Dwarf, Male)",
    ]


def test_prompt_marks_placeable_and_door_owners(world: WorldContext) -> None:
    node_map = {"E0": DialogNode(node_id=0, text="Read me.", is_entry=True)}

    assert _speaker_lines(world, "sign_talk", node_map) == [
        "- In sign_talk.dlg, lines marked [NPC]: spoken by Notice Board (placeable); "
        "or Post (placeable)"
    ]
    assert _speaker_lines(world, "cell_talk", node_map) == [
        "- In cell_talk.dlg, lines marked [NPC]: spoken by Cell Door (door)"
    ]


def test_prompt_works_without_creature_blueprints() -> None:
    world = WorldContext()
    world.register_dialog_actor(NPCInfo("SIGN", "Sign", "", "", "", "", "sign", kind="placeable"))
    node_map = {"E0": DialogNode(node_id=0, text="Read me.", is_entry=True)}

    assert _speaker_lines(world, "sign", node_map) == [
        "- In sign.dlg, lines marked [NPC]: spoken by Sign (placeable)"
    ]


def test_prompt_lists_every_object_sharing_a_speaker_tag() -> None:
    world = WorldContext()
    world.register_dialog_actor(NPCInfo("GUARD", "Jon", "", "", "Human", "Male", ""))
    world.register_dialog_actor(NPCInfo("GUARD", "Mira", "", "", "Elf", "Female", ""))
    node_map = {"E0": DialogNode(node_id=0, text="Halt!", speaker="GUARD", is_entry=True)}

    assert _speaker_lines(world, "gate", node_map) == [
        "- In gate.dlg, lines marked [GUARD]: spoken by Jon (Human, Male); " "or Mira (Elf, Female)"
    ]
