"""The world context: module scan, prompt block selection and script speaker hints."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nwn_translator.context.world_context import NPCInfo, WorldContext, WorldScanner
from nwn_translator.extractors.base import TranslatableItem
from tests.support.gff_writer import loc, write_gff


def _npc(tag, first, last="", description="", race="Human", gender="Female", conversation=""):
    return NPCInfo(tag, first, last, description, race, gender, conversation)


def _world() -> WorldContext:
    return WorldContext(
        npcs={
            "Thea": _npc("Thea", "Thea", "Wendt"),
            "Merrick": _npc("Merrick", "Merrick", "Winters", race="Ooze", gender="Male"),
            "Mayor": _npc("Mayor", "Mayor", "Castelon", gender="Male"),
        },
        areas={"MasterBedroom": "Master Bedroom", "Lighthouse": "Lighthouse"},
        quests={"Vault": "Vault of Secrets"},
        items={"DarkScythe": "Dark Scythe"},
    )


def test_scan_registers_tagged_areas_items_and_quests_with_their_evidence(tmp_path, caplog):
    write_gff(tmp_path / "harbor.are", {"Tag": "HARBOR", "Name": loc("Old Harbor")}, "ARE")
    write_gff(tmp_path / "void.are", {"Name": loc("Nowhere")}, "ARE")
    write_gff(tmp_path / "sword.uti", {"Tag": "SWORD", "LocalizedName": loc("Sunblade")}, "UTI")
    write_gff(tmp_path / "blank.uti", {"Tag": "BLANK", "LocalizedName": loc("")}, "UTI")
    categories = [
        {"Tag": "q_main", "Name": loc("The Lost Heir")},
        {"Name": loc("Untagged Quest")},
        {"Tag": "q_side", "Name": loc("Rats in the Cellar")},
    ]
    write_gff(tmp_path / "module.jrl", {"Categories": categories}, "JRL")
    caplog.set_level(logging.INFO, logger="nwn_translator.context.world_context")

    world = WorldScanner().scan_directory(tmp_path)

    assert world.areas == {"HARBOR": "Old Harbor"}
    assert world.items == {"SWORD": "Sunblade"}
    assert world.quests == {"q_main": "The Lost Heir", "q_side": "Rats in the Cellar"}
    assert (
        "World context built: 0 NPCs, 1 locations, 2 quests, 1 items, 0 other dialog actors"
        in caplog.messages
    )
    evidence = {
        c.name: (c.category, [(e.source, e.resource, e.field, e.category) for e in c.evidence])
        for c in world.candidates.values()
    }
    assert evidence == {
        "Old Harbor": ("location", [("are_name", "harbor.are", "Name", "location")]),
        "Sunblade": ("item", [("uti_name", "sword.uti", "LocalizedName", "item")]),
        "The Lost Heir": ("quest", [("jrl_category", "module.jrl", "Name", "quest")]),
        "Rats in the Cellar": ("quest", [("jrl_category", "module.jrl", "Name", "quest")]),
    }


# ---------------------------------------------------------------------------
# Prompt block selection
# ---------------------------------------------------------------------------


def test_without_texts_the_block_lists_everything():
    block = _world().to_prompt_block()
    for name in ("Thea", "Mayor", "Lighthouse", "Dark Scythe"):
        assert name in block


@pytest.mark.parametrize(
    "texts, present, absent",
    [
        (
            ["I suspect the Mayor might be dead. Mr. Winters had a key."],
            ["Mayor", "Winters", "- KEY CHARACTERS IN THE GAME:"],
            # Sections without a match leave out their headers.
            ["Thea", "- LOCATIONS:", "- QUESTS:", "- KEY ITEMS:"],
        ),
        # The owner's file stem "thea2" tokenizes to "thea".
        (["Hello there.", "thea2"], ["Thea"], []),
        (
            ["Move the cabinet in the Master Bedroom to find the Dark Scythe."],
            ["Master Bedroom", "Dark Scythe"],
            ["Lighthouse"],
        ),
    ],
)
def test_the_block_keeps_what_the_texts_mention(texts, present, absent):
    block = _world().to_prompt_block(source_texts=texts)
    for text in present:
        assert text in block
    for text in absent:
        assert text not in block


def test_the_block_is_empty_when_nothing_matches():
    assert _world().to_prompt_block(source_texts=["random unrelated talk"]) == ""


def _gewia_and(*generic: NPCInfo) -> WorldContext:
    world = WorldContext()
    for npc in generic:
        world.npcs[npc.tag] = npc
    world.npcs["GEWIA"] = _npc(
        "GEWIA", "Gewia", "the Wererat", "A wererat in disguise.", "Wererat", conversation="gewia"
    )
    return world


def _human_female(tag: str) -> NPCInfo:
    return _npc(tag, "Human", "Female", "Generic citizen.")


@pytest.mark.parametrize(
    "world, text, absent",
    [
        # A budget of matches does not pull in every generic NPC.
        (
            _gewia_and(*(_human_female(f"hf_{i}") for i in range(80))),
            "Gewia the Wererat speaks in the Diving Dolphin.",
            "Human Female",
        ),
        # The tag fragment AUREN matches "Auren Society", but the generic name does not.
        (
            _gewia_and(_human_female("AUREN_CONTACT_NPC1")),
            "Gewia mentions the Auren Society.",
            "[AUREN_CONTACT_NPC1]",
        ),
        # A descriptive mention of a shared label must not admit all its carriers.
        (
            _gewia_and(*(_human_female(f"NPC_{i}") for i in range(20))),
            "There's something strange about this particular Human Female, "
            "for Gewia the Wererat is hiding among them.",
            "Human Female",
        ),
    ],
)
def test_generic_npcs_are_not_selected(world, text, absent):
    world.areas["dolphin"] = "Diving Dolphin"
    block = world.to_prompt_block(source_texts=[text])
    assert "Gewia the Wererat" in block
    assert absent not in block
    if "Diving Dolphin" in text:
        assert "Diving Dolphin" in block


# ---------------------------------------------------------------------------
# Script speaker hints
# ---------------------------------------------------------------------------


def test_script_owned_by_one_creature_names_it():
    world = WorldContext()
    world.register_script_owner(
        "kneeltozim", _npc("dawn01", "Dawn", "Ioza", conversation="dawnchat")
    )
    hint = world.speaker_hint_for_script("kneeltozim")
    assert hint is not None
    for part in ("Dawn Ioza", "Human", "Female"):
        assert part in hint


def test_shared_script_summarizes_the_race():
    world = WorldContext()
    for i in range(3):
        world.register_script_owner(
            "whineprotect", _npc(f"gob{i}", "", race="Goblin", gender="Male")
        )
    hint = world.speaker_hint_for_script("whineprotect")
    assert hint is not None
    assert "Goblin" in hint and "shared by 3" in hint and "gob0" not in hint


def test_script_item_context_gets_the_speaker_once():
    world = WorldContext()
    world.register_script_owner("bark", _npc("t", "Bob", gender="Male"))
    item = TranslatableItem(
        text="Hi!",
        context="Script text shown to player via SpeakString in bark.ncs.",
        item_id="bark:off_10",
        location=str(Path("extract") / "bark.ncs"),
        metadata={"type": "ncs_string"},
    )
    world.enrich_ncs_item_context(item)
    assert "Speaker" in item.context and "Bob" in item.context
    before = item.context
    world.enrich_ncs_item_context(item)
    assert item.context == before
