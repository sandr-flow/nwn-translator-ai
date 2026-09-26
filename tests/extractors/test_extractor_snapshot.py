"""Characterization snapshot of every extractor's output.

Synthetic GFF dicts (and one compiled script) exercise every branch of the
extractors. The produced ``(item_id, text, context, metadata)`` rows, the
content type and the content metadata are compared byte for byte, key order
included, with ``tests/fixtures/extractor_snapshot.json``. Item ids, context
strings and metadata reach prompts, the web database and the output module, so
any difference is a behaviour change.

Regenerate the fixture only for an intended change:
``NWN_UPDATE_EXTRACTOR_SNAPSHOT=1 pytest tests/extractors/test_extractor_snapshot.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from nwn_translator.extractors import git_fields
from nwn_translator.formats.ncs import parse_ncs
from nwn_translator.pipeline.stages import load_parsed_and_extracted
from nwn_translator.resources import RESOURCE_KINDS
from tests.support.ncs import action, add_ss, consts, cptopsp, retn, write_ncs

FIXTURE = Path(__file__).parents[1] / "fixtures" / "extractor_snapshot.json"


def _loc(value: str, strref: int = -1) -> Dict[str, Any]:
    return {"StrRef": strref, "Value": value}


def _offsets(*fields: str, base: int = 100) -> Dict[str, int]:
    return {field: base + 4 * n for n, field in enumerate(fields)}


def _item_row(name: str, desc: str, ident: str, base_item: int, offset: int) -> Dict[str, Any]:
    return {
        "BaseItem": base_item,
        "LocalizedName": _loc(name),
        "Description": _loc(desc),
        "DescIdentified": _loc(ident),
        "_record_offsets": _offsets("LocalizedName", "Description", "DescIdentified", base=offset),
    }


SIMPLE_CASES: List[Tuple[str, str, Dict[str, Any]]] = [
    (
        "area_full",
        "town.are",
        {
            "Tag": "town01",
            "Name": _loc("Market Town"),
            "Description": _loc("A busy town."),
            "_record_offsets": _offsets("Name", "Description"),
        },
    ),
    ("area_no_tag", "cave.are", {"Name": _loc("Dark Cave"), "_record_offsets": {}}),
    (
        "area_empty_tag_strref_description",
        "field.are",
        {"Tag": "", "Name": _loc("Field"), "Description": {"StrRef": 12}},
    ),
    ("area_raw_field", "raw.are", {"Tag": "raw", "Name": "not a locstring"}),
    (
        "trigger_trap",
        "trap.utt",
        {
            "Tag": "spikes",
            "TrapFlag": 1,
            "LocalizedName": _loc("Spike Trap"),
            "Description": _loc("Sharp."),
            "_record_offsets": _offsets("LocalizedName", "Description"),
        },
    ),
    (
        "trigger_scripting",
        "zone.utt",
        {"Tag": "zone", "TrapFlag": 0, "LocalizedName": _loc("Zone")},
    ),
    (
        "placeable_locname_wins",
        "chest.utp",
        {
            "Tag": "chest",
            "LocName": _loc("Chest"),
            "LocalizedName": _loc("Wrong"),
            "Name": _loc("Legacy"),
            "Description": _loc("Old."),
            "DescIdentified": _loc("Old."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Name", "Description"),
        },
    ),
    (
        "placeable_localizedname_fallback",
        "box.utp",
        {
            "Tag": "box",
            "LocName": _loc(""),
            "LocalizedName": _loc("Box"),
            "Description": _loc("   "),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description"),
        },
    ),
    (
        "placeable_name_fallback",
        "barrel.utp",
        {"Name": _loc("Barrel"), "DescIdentified": _loc("Full of ale.")},
    ),
    (
        "door",
        "gate.utd",
        {
            "Tag": "gate",
            "LocalizedName": _loc("Iron Gate"),
            "Description": _loc("Locked."),
            "_record_offsets": _offsets("LocalizedName", "Description"),
        },
    ),
    (
        "encounter",
        "orcs.ute",
        {"Tag": "orcs", "LocalizedName": _loc("Orc Band"), "Description": _loc("Loud.")},
    ),
    (
        "store_locname_wins",
        "shop.utm",
        {
            "Tag": "shop",
            "LocName": _loc("Shop"),
            "LocalizedName": _loc("Wrong"),
            "Description": _loc("Wares."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description"),
        },
    ),
    ("store_localizedname", "stall.utm", {"Tag": "stall", "LocalizedName": _loc("Stall")}),
    (
        "module",
        "module.ifo",
        {
            "Tag": "ignored",
            "Mod_Tag": "MOD_TAG",
            "Mod_Name": _loc("The Module"),
            "Mod_Description": _loc("A story."),
            "_record_offsets": _offsets("Mod_Name", "Mod_Description"),
        },
    ),
    ("module_no_tag", "module.ifo", {"Tag": "ignored", "Mod_Name": _loc("Untagged")}),
    (
        "creature_full",
        "anna.utc",
        {
            "Tag": "npc_anna",
            "Race": 6,
            "Gender": 1,
            "FirstName": _loc("Anna"),
            "LastName": _loc("Шмидт"),
            "Description": _loc("A farmer."),
            "_record_offsets": _offsets("FirstName", "LastName", "Description"),
        },
    ),
    (
        "creature_no_traits",
        "bob.utc",
        {
            "Tag": "npc_bob",
            "Race": 99,
            "Gender": 99,
            "FirstName": _loc("Bob"),
            "Description": _loc("Quiet."),
        },
    ),
    (
        "creature_race_only_last_name",
        "elf.utc",
        {"Race": 1, "LastName": _loc("the Wise"), "Description": _loc("Old elf.")},
    ),
    (
        "creature_description_only",
        "ghost.utc",
        {"Tag": "", "Race": 22, "Gender": 4, "Description": _loc("Boo.")},
    ),
    ("creature_empty", "empty.utc", {"Tag": "empty"}),
    (
        "item_full",
        "sword.uti",
        {
            "Tag": "sword",
            "BaseItem": 1,
            "LocalizedName": _loc("Flame Blade"),
            "Description": _loc("Hot."),
            "DescIdentified": _loc("Very hot."),
            "_record_offsets": _offsets("LocalizedName", "Description", "DescIdentified"),
        },
    ),
    (
        "item_unknown_base",
        "thing.uti",
        {
            "Tag": "thing",
            "BaseItem": 999,
            "LocalizedName": _loc("Thing"),
            "Description": _loc("Odd."),
            "DescIdentified": _loc("Odder."),
        },
    ),
    (
        "item_no_name",
        "ring.uti",
        {"BaseItem": 52, "Description": _loc("Plain."), "DescIdentified": _loc("Magic.")},
    ),
    (
        "item_no_name_no_base",
        "junk.uti",
        {"Tag": "junk", "Description": _loc("Junk."), "DescIdentified": _loc("Still junk.")},
    ),
    (
        "journal",
        "module.jrl",
        {
            "Categories": [
                {
                    "Tag": "q_main",
                    "Priority": 2,
                    "Name": _loc("Main Quest"),
                    "_record_offsets": {"Name": 40},
                    "EntryList": [
                        {"ID": 10, "Text": _loc("Start."), "_record_offsets": {"Text": 60}},
                        {"ID": 20, "Text": _loc("")},
                        {"ID": 30, "Text": _loc("   ")},
                        {"Text": _loc("No id.")},
                    ],
                },
                {
                    "Name": _loc("Untagged Quest"),
                    "EntryList": [{"ID": 1, "Text": _loc("Only entry.")}],
                },
                {
                    "Tag": "q_nameless",
                    "Name": {"StrRef": 77},
                    "EntryList": [{"ID": 5, "Text": _loc("Nameless entry.")}],
                },
                {"Tag": "q_empty", "Name": _loc("  ")},
            ]
        },
    ),
    (
        "dialog",
        "npc_talk.dlg",
        {
            "EntryList": [
                {"Speaker": "guard", "Text": _loc("Halt!"), "_record_offsets": {"Text": 12}},
                {"Speaker": "", "Text": _loc("Who goes there?")},
                {"Speaker": "guard", "Text": _loc("")},
                12345,
                {"Text": _loc("No speaker key."), "_record_offsets": {"Text": 99}},
            ],
            "ReplyList": [
                {"Text": _loc("A friend."), "_record_offsets": {"Text": 24}},
                "broken",
                {"Text": {"StrRef": 5}},
                {"Text": _loc("[Leave]")},
            ],
        },
    ),
]


GIT_AREA: Dict[str, Any] = {
    "Tag": "area_tag",
    "Creature List": [
        {
            "Race": 6,
            "Gender": 1,
            "FirstName": _loc("Anna"),
            "LastName": _loc("Smith"),
            "Description": _loc("A farmer."),
            "_record_offsets": _offsets("FirstName", "LastName", "Description"),
            "ItemList": [
                _item_row("Hoe", "Rusty.", "A fine hoe.", 1, 200),
                _item_row("", "Nameless.", "", 999, 220),
            ],
            "Equip_ItemList": [_item_row("Straw Hat", "", "", 999, 240), "broken"],
        },
        {"Race": 99, "Gender": 99, "FirstName": _loc("Bob"), "Description": _loc("Quiet.")},
        {"Race": 6, "Gender": 0, "Description": _loc("Stern.")},
        {"Description": _loc("Faceless.")},
        {"FirstName": _loc("McGee"), "LastName": _loc("WorkBench")},
        {"FirstName": _loc("NW_GUARD"), "LastName": _loc("Мельник")},
        "not a struct",
        {"Gender": 0, "FirstName": _loc("Joann"), "LastName": _loc("   ")},
    ],
    "Placeable List": [
        {
            "LocName": _loc("Anna's Chest"),
            "Description": _loc("Bob's gift to ANNA's family."),
            "_record_offsets": _offsets("LocName", "Description", base=300),
            "ItemList": [_item_row("Scroll", "Old paper.", "", 48, 320)],
        },
        {"LocName": _loc("Joanna's Box"), "Description": _loc("Plain box.")},
        {"LocName": _loc(""), "Description": _loc("Nameless thing.")},
        {"LocName": "raw", "Description": _loc("Raw name thing.")},
    ],
    "Door List": [
        {
            "LocName": _loc("Iron Gate"),
            "LocalizedName": _loc("Iron Gate (legacy)"),
            "Description": _loc("Rusty."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description", base=400),
        },
        {"LocalizedName": _loc("Old Door")},
        {"LocName": _loc("Beta_to_Bearpit")},
    ],
    "TriggerList": [
        {"Type": 1, "LocalizedName": _loc("To the Sewers"), "Description": _loc("Dark.")},
        {"Type": 2, "LocalizedName": _loc("Spike Trap")},
        {"Type": 0, "TrapFlag": 1, "LocalizedName": _loc("Hidden Pit")},
        {"Type": 0, "LocalizedName": _loc('"Watch your step!"')},
        {"LocalizedName": _loc("CastleExt1To2South")},
    ],
    "WaypointList": [
        {"LocalizedName": _loc("Spawn Point"), "MapNote": _loc("City Gate")},
        {"MapNote": _loc("WP_Hidden")},
    ],
    "Encounter List": [
        {"LocalizedName": _loc("Orc, Low Group"), "Description": _loc("Not read.")},
    ],
    "StoreList": [
        {
            "LocName": _loc("Bazaar"),
            "LocalizedName": _loc("Bazaar (legacy)"),
            "Description": _loc("Everything."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description", base=500),
            "ItemList": [_item_row("Rope", "", "", 999, 520)],
            "StoreList": [
                {},
                "broken shelf",
                {
                    "ItemList": [_item_row("Coffee", "Hot drink.", "", 999, 540)],
                    "StoreList": [{"ItemList": [_item_row("Tea", "", "", 999, 560)]}],
                },
            ],
        },
        {"LocalizedName": _loc("Stall"), "StoreList": "not a list"},
    ],
    "List": [
        _item_row("Dragon Bones", "Yellowed.", "Ancient.", 1, 600),
        _item_row("", "", "", 24, 620),
        _item_row("WWBite1d6", "Bite.", "", 999, 640),
    ],
}


def _write_script(tmp_path: Path) -> Path:
    path = write_ncs(
        tmp_path,
        "scene.ncs",
        consts("Welcome to the inn, friend!"),
        action(221, 1),
        consts("NW_INNKEEPER"),
        action(200, 1),
        consts("Congrats to ye, "),
        cptopsp(-8),
        add_ss(),
        consts(". How do ye feel?"),
        add_ss(),
        action(221, 1),
        consts("   "),
        consts("You look tired."),
        retn(),
    )
    path.with_suffix(".nss").write_text(
        "void main()\n{\n"
        '    SpeakString("Welcome to the inn, friend!");\n'
        '    object o = GetObjectByTag("NW_INNKEEPER");\n'
        "}\n",
        encoding="cp1252",
    )
    return path


def _dump(extracted: Any) -> Dict[str, Any]:
    return {
        "content_type": extracted.content_type,
        "metadata": extracted.metadata,
        "items": [
            [item.item_id, item.text, item.context, item.metadata] for item in extracted.items
        ],
    }


def _snapshot(tmp_path: Path) -> Dict[str, Any]:
    by_ext = {ext: kind.extractor for ext, kind in RESOURCE_KINDS.items()}
    snapshot: Dict[str, Any] = {}
    for name, filename, data in SIMPLE_CASES:
        path = tmp_path / filename
        extracted = by_ext[path.suffix].extract(path, data)
        assert all(item.location == str(path) for item in extracted.items)
        snapshot[name] = _dump(extracted)

    area_dir = tmp_path / "area"
    area_dir.mkdir()
    git_path = area_dir / "tavern.git"
    extracted = by_ext[".git"].extract(git_path, GIT_AREA)
    assert all(item.location == str(git_path) for item in extracted.items)
    snapshot["git"] = _dump(extracted)

    script = _write_script(tmp_path)
    ncs_data = {"_ncs_file": parse_ncs(script), "_source_encoding": None}
    extracted = by_ext[".ncs"].extract(script, ncs_data)
    assert all(item.location == str(script) for item in extracted.items)
    snapshot["ncs"] = _dump(extracted)
    loaded = load_parsed_and_extracted(script, ".ncs", None)
    assert loaded is not None
    assert _dump(loaded[1]) == snapshot["ncs"]
    return snapshot


@pytest.fixture
def blueprint_oracle(monkeypatch: pytest.MonkeyPatch):
    """Serve a fixed blueprint-name oracle so ``McGee`` is a known creature name."""
    git_fields.clear_creature_name_cache()
    monkeypatch.setattr(
        git_fields, "collect_blueprint_creature_names", lambda root: frozenset({"mcgee"})
    )
    yield
    git_fields.clear_creature_name_cache()


def test_extractor_output_matches_snapshot(tmp_path: Path, blueprint_oracle: None) -> None:
    actual = json.dumps(_snapshot(tmp_path), ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("NWN_UPDATE_EXTRACTOR_SNAPSHOT"):
        FIXTURE.write_text(actual, encoding="utf-8")
    expected = FIXTURE.read_text(encoding="utf-8")
    assert actual == expected
