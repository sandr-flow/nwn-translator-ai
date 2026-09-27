""".git area instances: which strings are extracted, and the creature-name oracle."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

import pytest

from nwn_translator.extractors import git_fields
from nwn_translator.extractors.git_extractor import GitExtractor
from nwn_translator.extractors.git_fields import (
    INSTANCE_FIELDS,
    npc_possessive_hint,
    should_translate_git_string,
)
from nwn_translator.formats.gff import read_gff
from tests.support.gff_writer import loc, write_gff


def _item(name: str, desc: str = "", ident: str = "", **fields: Any) -> Dict[str, Any]:
    return {
        **fields,
        "LocalizedName": loc(name),
        "Description": loc(desc),
        "DescIdentified": loc(ident),
    }


ROUTE_LABELS = ["CastleExt1To2South", "BakersPlea", "GolemStopAttackTrigger", "CloudkillTarget"]
DOOR_TAGS = ["Beta_to_Bearpit", "LL_EXIT", "HouseToBasement", "NW_Door"]

AREA: Dict[str, Any] = {
    "Creature List": [
        {
            "FirstName": loc("Grandma"),
            "LastName": loc(""),
            "Description": loc(""),
            "Equip_ItemList": [
                _item("Family Axe", "Heavy."),
                _item("Grandma's Armor", "Worn by Grandma.", "Sturdy family armor."),
                _item("The Skullsplitter", "A fearsome axe.", "Grandma's axe."),
            ],
            # Inventory labels that look like resrefs stay untranslated.
            "ItemList": [_item("WWBite1d6"), _item("WWBiteWolfForm")],
        }
    ],
    "Placeable List": [
        {"LocName": loc("Old Wooden Chest"), "Description": loc("")},
        {"LocName": loc("Chest"), "ItemList": [_item("Scroll Case", "Holds scrolls.")]},
        {"Description": loc("*The lever is stuck*")},
    ],
    "Door List": [{"Tag": f"door{i}", "LocName": loc(tag)} for i, tag in enumerate(DOOR_TAGS)]
    + [{"Tag": "door_ok", "LocName": loc("Wooden Door")}],
    "StoreList": [
        {"LocName": loc("Bazaar"), "Description": loc(""), "ItemList": [_item("Rope")]},
        # Merchant shelves: a nested StoreList with its own ItemList (Penultima's coffee bar).
        {
            "LocName": loc("Tavern"),
            "StoreList": [{}, {}, {"ItemList": [_item("Coffee"), _item("Cappuchino")]}],
        },
        {"LocName": loc("Coffee Merchant"), "Description": loc("")},
        {
            "LocalizedName": loc("Arms Dealer"),
            "Description": loc(""),
            "ItemList": [_item("Iron Longsword", "A sturdy blade.")],
        },
    ],
    # Real .git files use the key ``TriggerList``, not ``Trigger List``.
    "TriggerList": [
        {"TrapFlag": 1, "LocalizedName": loc("Market Square"), "Description": loc("")},
        # Non-trap triggers carry area-transition tooltips and names that scripts
        # show through SpeakString / FloatingText.
        {
            "Tag": "at_CastleToSewers",
            "Type": 1,
            "TrapFlag": 0,
            "LocalizedName": loc("To the Sewers"),
        },
        {
            "Tag": "Telios",
            "Type": 0,
            "TrapFlag": 0,
            "LocalizedName": loc('"My lovely boots are getting mud on them!"'),
        },
        {"Tag": "tr_vico", "Type": 0, "TrapFlag": 0, "LocalizedName": loc("tr_vico")},
        {
            "Tag": "Comment",
            "Type": 0,
            "TrapFlag": 0,
            "LocalizedName": loc(
                "[Strange. There was something that looked like an eye reflected in the water.]"
            ),
        },
        {"LocalizedName": loc("*gasp*")},
        {"LocalizedName": loc("*whispers* There is such rage among these ruins...")},
        # A scripter comment stored in a trigger name is never translated.
        {"LocalizedName": loc("// * * * SCENE: Drinking dwarves  * * *")},
    ]
    + [{"Type": 1, "TrapFlag": 0, "LocalizedName": loc(label)} for label in ROUTE_LABELS],
    "WaypointList": [
        {"LocalizedName": loc("WP_CityGate"), "MapNote": loc("City Gate")},
        {"LocalizedName": loc("WP_Spawn"), "Description": loc("")},
    ],
    "Encounter List": [
        {"LocalizedName": loc("Human, Bandit Group")},
        {"LocalizedName": loc("enc_internal_tag")},
    ],
    # Items dropped on the ground in the toolset.
    "List": [
        _item(
            "Dragon Bones",
            "Yellowed with age, these are the bones of a Dragon.",
            TemplateResRef="dragonbones",
            BaseItem=79,
        ),
        _item("", TemplateResRef="nw_it_thnmisc001", BaseItem=24),
        _item("Silver Nuggets", "Raw silver ore."),
        _item("WILL_O_WISP"),
    ],
}


def test_player_visible_instance_strings_are_extracted():
    result = GitExtractor().extract(Path("area.git"), AREA)
    types = {item.text: item.metadata["type"] for item in result.items}

    assert result.content_type == "git_instance"
    assert {
        "Grandma": "creature_first_name",
        "Family Axe": "item_name",
        "Bazaar": "store_name",
        "Rope": "item_name",
        "City Gate": "waypoint_map_note",
        "Dragon Bones": "item_name",
        "Yellowed with age, these are the bones of a Dragon.": "item_description",
        "Human, Bandit Group": "encounter_name",
    }.items() <= types.items()
    assert {
        "Heavy.",
        "Grandma's Armor",
        "Worn by Grandma.",
        "Sturdy family armor.",
        "The Skullsplitter",
        "A fearsome axe.",
        "Grandma's axe.",
        "Old Wooden Chest",
        "Chest",
        "Scroll Case",
        "Holds scrolls.",
        "*The lever is stuck*",
        "Wooden Door",
        "Tavern",
        "Coffee",
        "Cappuchino",
        "Coffee Merchant",
        "Arms Dealer",
        "Iron Longsword",
        "A sturdy blade.",
        "Market Square",
        "To the Sewers",
        '"My lovely boots are getting mud on them!"',
        "[Strange. There was something that looked like an eye reflected in the water.]",
        "*gasp*",
        "*whispers* There is such rage among these ruins...",
        "Silver Nuggets",
        "Raw silver ore.",
    } <= set(types)
    blocked = {"", "WP_CityGate", "WP_Spawn", "tr_vico", "enc_internal_tag", "WILL_O_WISP"}
    blocked |= {"WWBite1d6", "WWBiteWolfForm", *ROUTE_LABELS, *DOOR_TAGS}
    assert not blocked & set(types)
    assert not any(text.startswith("//") for text in types)


def test_instance_lists_include_description_fields():
    names = {key: {field.name for field in fields} for key, fields in INSTANCE_FIELDS.items()}
    for name in ("Placeable List", "Door List", "StoreList"):
        assert "Description" in names[name]
    assert {"LocName", "LocalizedName"} <= names["StoreList"]


def test_door_instance_names_come_from_locname(tmp_path):
    path = tmp_path / "keep.git"
    doors = [
        {"Tag": "KeepGate", "LocName": loc("Iron Gate"), "Description": loc("Rusty.")},
        {"Tag": "CellarDoor", "LocName": loc("Cellar Door")},
        # StrRef-only names resolve from the player's dialog.tlk.
        {"Tag": "TlkDoor", "LocName": loc("", strref=1234)},
        # The fallback label, in case a toolset writes it instead of LocName.
        {"Tag": "OldDoor", "LocalizedName": loc("Old Door")},
    ]
    write_gff(path, {"StructType": "GIT", "Door List": doors}, file_type="GIT")
    parsed = read_gff(path)

    items = GitExtractor().extract(path, parsed).items
    names = {item.item_id: item for item in items if item.metadata["type"] == "door_name"}

    assert set(names) == {
        "keep_Door List_0_LocName",
        "keep_Door List_1_LocName",
        "keep_Door List_3_LocalizedName",
    }
    gate = names["keep_Door List_0_LocName"]
    assert (gate.text, gate.context) == ("Iron Gate", "Door name (area instance)")
    assert gate.metadata["git_field"] == "LocName"
    assert gate.metadata["record_offset"] == parsed["Door List"][0]["_record_offsets"]["LocName"]
    assert names["keep_Door List_3_LocalizedName"].text == "Old Door"
    descriptions = {item.text for item in items if item.metadata["type"] == "door_description"}
    assert descriptions == {"Rusty."}
    assert {item.text for item in items} == {"Iron Gate", "Cellar Door", "Old Door", "Rusty."}


def test_area_properties_struct_does_not_change_the_extraction(tmp_path):
    """The parser expands the AreaProperties struct; its values are not strings to translate."""
    creature = {
        "FirstName": loc("Old fisherman"),
        "LastName": loc(""),
        "Description": loc("A weathered old man."),
        "Race": 6,
        "Gender": 0,
    }
    properties = {name: 3 for name in ("AmbientSndDay", "AmbientSndNight", "MusicDay")}
    texts = []
    for name, extra in (("plain.git", {}), ("props.git", {"AreaProperties": properties})):
        path = tmp_path / name
        write_gff(path, {"StructType": "GIT", "Creature List": [creature], **extra})
        parsed = read_gff(path)
        texts.append(sorted(item.text for item in GitExtractor().extract(path, parsed).items))
    assert isinstance(parsed["AreaProperties"], dict)
    assert texts[0] == texts[1]
    assert "A weathered old man." in texts[1]


# ---------------------------------------------------------------------------
# Creature-name oracle from the module's .utc blueprints
# ---------------------------------------------------------------------------


@pytest.fixture
def oracle_cache():
    git_fields.clear_creature_name_cache()
    yield
    git_fields.clear_creature_name_cache()


def _blueprints(monkeypatch, directory, *records: dict) -> None:
    """Place one .utc per record in *directory*; reading it returns the record."""
    by_name = {f"npc{i}.utc": record for i, record in enumerate(records)}
    for name in by_name:
        (directory / name).write_bytes(b"")
    monkeypatch.setattr(git_fields, "read_gff", lambda path, **kwargs: by_name[path.name])


def test_blueprint_names_rescue_camel_case_creature_names(tmp_path, monkeypatch, oracle_cache):
    _blueprints(
        monkeypatch,
        tmp_path,
        {"FirstName": loc("McGee"), "LastName": loc("DeVir")},
        # Blank and missing names add nothing.
        {"FirstName": loc(" ")},
    )
    area = {
        "Creature List": [{"FirstName": loc("McGee"), "LastName": loc("DeVir")}],
        # Camel-case junk with no blueprint counterpart stays blocked.
        "Placeable List": [{"LocName": loc("WorkBench")}],
    }

    texts = {item.text for item in GitExtractor().extract(tmp_path / "area.git", area).items}

    assert texts == {"McGee", "DeVir"}
    assert git_fields.get_module_creature_names(tmp_path) == {"mcgee", "devir"}


def test_camel_case_names_without_blueprints_stay_blocked(tmp_path, oracle_cache):
    area = {"Creature List": [{"FirstName": loc("McGee")}]}
    assert GitExtractor().extract(tmp_path / "area.git", area).items == []
    assert not should_translate_git_string("McGee", "creature_first_name")
    assert should_translate_git_string("McGee", "creature_first_name", frozenset({"mcgee"}))


def test_oracle_keeps_the_original_names_after_blueprints_are_patched(
    tmp_path, monkeypatch, oracle_cache
):
    """By rebuild time the .utc files may carry translated names already."""
    _blueprints(monkeypatch, tmp_path, {"FirstName": loc("McGee")})
    assert "mcgee" in git_fields.get_module_creature_names(tmp_path)
    _blueprints(monkeypatch, tmp_path, {"FirstName": loc("МакГи")})
    assert "mcgee" in git_fields.get_module_creature_names(tmp_path)


def test_oracle_is_built_once_under_concurrency(tmp_path, monkeypatch, oracle_cache):
    """Extraction workers asking for the oracle at once share a single .utc scan."""
    workers = 8
    calls = []
    start = threading.Barrier(workers)

    def slow_collect(root):
        calls.append(root)
        time.sleep(0.05)  # keep the build in flight while the other workers arrive
        return frozenset({"mcgee"})

    def lookup(_):
        start.wait()
        return git_fields.get_module_creature_names(tmp_path)

    monkeypatch.setattr(git_fields, "collect_blueprint_creature_names", slow_collect)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lookup, range(workers)))

    assert len(calls) == 1
    assert all(result is results[0] for result in results)
    assert results[0] == frozenset({"mcgee"})


@pytest.mark.parametrize(
    "text, npcs, hint",
    [
        ("Joanna's Box", {"anna": "Female"}, ""),  # not a possessive of Anna
        ("Anna's Box", {"anna": "Female"}, " (contains possessive of NPC 'Anna', gender: Female)"),
        # The quoted name is the possessive occurrence, even after case-changing text.
        (
            "ANNA met Anna's aunt",
            {"joann": "Male", "anna": "Female"},
            " (contains possessive of NPC 'Anna', gender: Female)",
        ),
        # "İ".lower() is two characters: indices into the lowered text are off by one.
        (
            "İstanbul is Anna's home",
            {"joann": "Male", "anna": "Female"},
            " (contains possessive of NPC 'Anna', gender: Female)",
        ),
    ],
)
def test_possessive_hint(text, npcs, hint):
    assert npc_possessive_hint(text, npcs) == hint
