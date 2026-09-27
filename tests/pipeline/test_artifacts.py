"""Stage artifacts: every seam of the pipeline round-trips through its file."""

from pathlib import Path

import pytest

from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.glossary import Glossary
from nwn_translator.pipeline import artifacts


def test_items_round_trip(tmp_path: Path) -> None:
    text = "First second\u0085third\r\nfourth"  # not line breaks in JSONL
    contents = [
        ExtractedContent(
            content_type="item",
            items=[
                TranslatableItem(
                    text="Sword of Truth",
                    context="Item name",
                    item_id="sword_name",
                    location=str(tmp_path / "sword.uti"),
                    metadata={"type": "item_name", "tag": "sword"},
                ),
                TranslatableItem(text=text, item_id="sword_desc"),
            ],
            source_file=tmp_path / "sword.uti",
            metadata={"tag": "sword", "item_count": 2},
        )
    ]
    path = tmp_path / "items.jsonl"
    artifacts.dump_items(path, contents)

    (loaded,) = artifacts.load_items(path)

    assert (loaded.content_type, loaded.metadata) == ("item", {"tag": "sword", "item_count": 2})
    assert [i.text for i in loaded.items] == ["Sword of Truth", text]
    assert loaded.items[0].metadata == {"type": "item_name", "tag": "sword"}
    assert loaded.items[0].item_id == "sword_name"


def _reloaded(tmp_path: Path, world: WorldContext) -> WorldContext:
    path = tmp_path / "world_context.json"
    artifacts.dump_world_context(path, world)
    return artifacts.load_world_context(path)


def test_world_context_round_trip(tmp_path: Path) -> None:
    world = WorldContext()
    world.npcs["npc_a"] = NPCInfo(
        "npc_a", "Elora", "Swift", "A ranger.", "elf", "female", "elora_conv"
    )
    world.areas = {"area1": "The Forest"}
    world.quests = {"q1": "Find the amulet"}
    world.items = {"i1": "Amulet"}
    world.extracted_names = [("Elora Swift", "character"), ("The Forest", "location")]
    # Placed creatures, placeables and doors still resolve dialog speakers after reload.
    for actor in [
        NPCInfo("MARTA", "Marta", "", "", "Human", "Female", "marta_talk"),
        NPCInfo("SMITH", "Borin", "", "", "Dwarf", "Male", ""),
        NPCInfo("", "Notice Board", "", "", "", "", "sign_talk", kind="placeable"),
        NPCInfo("CELL", "Cell Door", "", "", "", "", "cell_talk", kind="door"),
    ]:
        world.register_dialog_actor(actor)
    # Script strings get the same speaker hint from a reloaded world context.
    world.register_script_owner(
        "marta_spawn", NPCInfo("MARTA", "Marta", "", "", "Human", "Female", "")
    )
    for i in range(2):
        world.register_script_owner(
            "gob_bark", NPCInfo(f"GOB{i}", "", "", "", "Goblin", "Male", "")
        )

    loaded = _reloaded(tmp_path, world)

    assert loaded.npcs["npc_a"] == world.npcs["npc_a"]
    assert (loaded.areas, loaded.quests, loaded.items) == (world.areas, world.quests, world.items)
    assert loaded.extracted_names == world.extracted_names
    assert loaded.dialog_actors_by_conversation == world.dialog_actors_by_conversation
    assert loaded.dialog_actors_by_tag == world.dialog_actors_by_tag
    assert loaded.script_owners == world.script_owners
    for script in ("marta_spawn", "gob_bark"):
        assert loaded.speaker_hint_for_script(script) == world.speaker_hint_for_script(script)


def test_world_context_without_actors_and_owners_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "world_context.json"
    path.write_text('{"npcs": {}, "areas": {}}', encoding="utf-8")

    loaded = artifacts.load_world_context(path)

    assert (loaded.dialog_actors_by_conversation, loaded.dialog_actors_by_tag) == ({}, {})
    assert loaded.script_owners == {}


def test_candidates_round_trip_with_their_curation(tmp_path: Path) -> None:
    registry = EntityCandidateRegistry()
    registry.add(
        "Aragorn",
        category="character",
        source="dialog",
        resource="conv.dlg",
        context="Hail, Aragorn!",
        is_speaker_or_dialog_actor=True,
    )
    registry.mark_curated("Aragorn", decision="keep", reason="protagonist", priority=99)
    path = tmp_path / "candidates.json"
    artifacts.dump_candidates(path, registry)

    original, loaded = registry.values()[0], artifacts.load_candidates(path).values()[0]

    for field in (
        "name",
        "normalized_name",
        "category",
        "frequency",
        "curation_decision",
        "priority",
        "technical_score",
    ):
        assert getattr(loaded, field) == getattr(original, field)
    assert loaded.priority == 99 and loaded.is_speaker_or_dialog_actor is True
    assert len(loaded.evidence) == len(original.evidence) == 1
    assert loaded.evidence[0].resource == "conv.dlg"


def test_glossary_round_trip_keeps_its_alias_family(tmp_path: Path) -> None:
    glossary = Glossary(
        {"Melee vs Mayhem": "Игра", "Melee vs. Mayhem": "Игра", "MVM": "MVM"},
        {"Melee vs. Mayhem": "Melee vs Mayhem", "MVM": "Melee vs Mayhem"},
    )
    path = tmp_path / "glossary.json"
    artifacts.dump_glossary(path, glossary)

    restored = artifacts.load_glossary(path)

    assert restored == glossary
    assert restored.matching_entries(["Tell me about MVM."]) == glossary.entries


def test_translations_are_addressed_by_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "translations.json"
    values = {("a.utc", "name"): "First", ("b.dlg", "b:entry:0"): "Second"}
    artifacts.dump_translations(path, values)
    assert artifacts.load_translations(path) == values
    # An old text-keyed map is ambiguous and is refused.
    path.write_text('{"Commoner": "One answer"}', encoding="utf-8")
    with pytest.raises(ValueError):
        artifacts.load_translations(path)
