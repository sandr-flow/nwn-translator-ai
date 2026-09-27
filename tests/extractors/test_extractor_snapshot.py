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
from tests.support.gff_writer import loc
from tests.support.ncs import action, add_ss, consts, cptopsp, retn, write_ncs

FIXTURE = Path(__file__).parents[1] / "fixtures" / "extractor_snapshot.json"


def _offsets(*fields: str, base: int = 100) -> Dict[str, int]:
    return {field: base + 4 * n for n, field in enumerate(fields)}


def _item_row(name: str, desc: str, ident: str, base_item: int, offset: int) -> Dict[str, Any]:
    return {
        "BaseItem": base_item,
        "LocalizedName": loc(name),
        "Description": loc(desc),
        "DescIdentified": loc(ident),
        "_record_offsets": _offsets("LocalizedName", "Description", "DescIdentified", base=offset),
    }


SIMPLE_CASES: List[Tuple[str, str, Dict[str, Any]]] = [
    (
        "area_full",
        "town.are",
        {
            "Tag": "town01",
            "Name": loc("Market Town"),
            "Description": loc("A busy town."),
            "_record_offsets": _offsets("Name", "Description"),
        },
    ),
    ("area_no_tag", "cave.are", {"Name": loc("Dark Cave"), "_record_offsets": {}}),
    (
        "area_empty_tag_strref_description",
        "field.are",
        {"Tag": "", "Name": loc("Field"), "Description": {"StrRef": 12}},
    ),
    ("area_raw_field", "raw.are", {"Tag": "raw", "Name": "not a locstring"}),
    (
        "trigger_trap",
        "trap.utt",
        {
            "Tag": "spikes",
            "TrapFlag": 1,
            "LocalizedName": loc("Spike Trap"),
            "Description": loc("Sharp."),
            "_record_offsets": _offsets("LocalizedName", "Description"),
        },
    ),
    (
        "trigger_scripting",
        "zone.utt",
        {"Tag": "zone", "TrapFlag": 0, "LocalizedName": loc("Zone")},
    ),
    (
        "placeable_locname_wins",
        "chest.utp",
        {
            "Tag": "chest",
            "LocName": loc("Chest"),
            "LocalizedName": loc("Wrong"),
            "Name": loc("Legacy"),
            "Description": loc("Old."),
            "DescIdentified": loc("Old."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Name", "Description"),
        },
    ),
    (
        "placeable_localizedname_fallback",
        "box.utp",
        {
            "Tag": "box",
            "LocName": loc(""),
            "LocalizedName": loc("Box"),
            "Description": loc("   "),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description"),
        },
    ),
    (
        "placeable_name_fallback",
        "barrel.utp",
        {"Name": loc("Barrel"), "DescIdentified": loc("Full of ale.")},
    ),
    (
        "door",
        "gate.utd",
        {
            "Tag": "gate",
            "LocalizedName": loc("Iron Gate"),
            "Description": loc("Locked."),
            "_record_offsets": _offsets("LocalizedName", "Description"),
        },
    ),
    (
        "encounter",
        "orcs.ute",
        {"Tag": "orcs", "LocalizedName": loc("Orc Band"), "Description": loc("Loud.")},
    ),
    (
        "store_locname_wins",
        "shop.utm",
        {
            "Tag": "shop",
            "LocName": loc("Shop"),
            "LocalizedName": loc("Wrong"),
            "Description": loc("Wares."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description"),
        },
    ),
    ("store_localizedname", "stall.utm", {"Tag": "stall", "LocalizedName": loc("Stall")}),
    (
        "module",
        "module.ifo",
        {
            "Tag": "ignored",
            "Mod_Tag": "MOD_TAG",
            "Mod_Name": loc("The Module"),
            "Mod_Description": loc("A story."),
            "_record_offsets": _offsets("Mod_Name", "Mod_Description"),
        },
    ),
    ("module_no_tag", "module.ifo", {"Tag": "ignored", "Mod_Name": loc("Untagged")}),
    (
        "creature_full",
        "anna.utc",
        {
            "Tag": "npc_anna",
            "Race": 6,
            "Gender": 1,
            "FirstName": loc("Anna"),
            "LastName": loc("Шмидт"),
            "Description": loc("A farmer."),
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
            "FirstName": loc("Bob"),
            "Description": loc("Quiet."),
        },
    ),
    (
        "creature_race_only_last_name",
        "elf.utc",
        {"Race": 1, "LastName": loc("the Wise"), "Description": loc("Old elf.")},
    ),
    (
        "creature_description_only",
        "ghost.utc",
        {"Tag": "", "Race": 22, "Gender": 4, "Description": loc("Boo.")},
    ),
    ("creature_empty", "empty.utc", {"Tag": "empty"}),
    (
        "item_full",
        "sword.uti",
        {
            "Tag": "sword",
            "BaseItem": 1,
            "LocalizedName": loc("Flame Blade"),
            "Description": loc("Hot."),
            "DescIdentified": loc("Very hot."),
            "_record_offsets": _offsets("LocalizedName", "Description", "DescIdentified"),
        },
    ),
    (
        "item_unknown_base",
        "thing.uti",
        {
            "Tag": "thing",
            "BaseItem": 999,
            "LocalizedName": loc("Thing"),
            "Description": loc("Odd."),
            "DescIdentified": loc("Odder."),
        },
    ),
    (
        "item_no_name",
        "ring.uti",
        {"BaseItem": 52, "Description": loc("Plain."), "DescIdentified": loc("Magic.")},
    ),
    (
        "item_no_name_no_base",
        "junk.uti",
        {"Tag": "junk", "Description": loc("Junk."), "DescIdentified": loc("Still junk.")},
    ),
    (
        "journal",
        "module.jrl",
        {
            "Categories": [
                {
                    "Tag": "q_main",
                    "Priority": 2,
                    "Name": loc("Main Quest"),
                    "_record_offsets": {"Name": 40},
                    "EntryList": [
                        {"ID": 10, "Text": loc("Start."), "_record_offsets": {"Text": 60}},
                        {"ID": 20, "Text": loc("")},
                        {"ID": 30, "Text": loc("   ")},
                        {"Text": loc("No id.")},
                    ],
                },
                {
                    "Name": loc("Untagged Quest"),
                    "EntryList": [{"ID": 1, "Text": loc("Only entry.")}],
                },
                {
                    "Tag": "q_nameless",
                    "Name": {"StrRef": 77},
                    "EntryList": [{"ID": 5, "Text": loc("Nameless entry.")}],
                },
                {"Tag": "q_empty", "Name": loc("  ")},
            ]
        },
    ),
    (
        "dialog",
        "npc_talk.dlg",
        {
            "EntryList": [
                {"Speaker": "guard", "Text": loc("Halt!"), "_record_offsets": {"Text": 12}},
                {"Speaker": "", "Text": loc("Who goes there?")},
                {"Speaker": "guard", "Text": loc("")},
                12345,
                {"Text": loc("No speaker key."), "_record_offsets": {"Text": 99}},
            ],
            "ReplyList": [
                {"Text": loc("A friend."), "_record_offsets": {"Text": 24}},
                "broken",
                {"Text": {"StrRef": 5}},
                {"Text": loc("[Leave]")},
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
            "FirstName": loc("Anna"),
            "LastName": loc("Smith"),
            "Description": loc("A farmer."),
            "_record_offsets": _offsets("FirstName", "LastName", "Description"),
            "ItemList": [
                _item_row("Hoe", "Rusty.", "A fine hoe.", 1, 200),
                _item_row("", "Nameless.", "", 999, 220),
            ],
            "Equip_ItemList": [_item_row("Straw Hat", "", "", 999, 240), "broken"],
        },
        {"Race": 99, "Gender": 99, "FirstName": loc("Bob"), "Description": loc("Quiet.")},
        {"Race": 6, "Gender": 0, "Description": loc("Stern.")},
        {"Description": loc("Faceless.")},
        {"FirstName": loc("McGee"), "LastName": loc("WorkBench")},
        {"FirstName": loc("NW_GUARD"), "LastName": loc("Мельник")},
        "not a struct",
        {"Gender": 0, "FirstName": loc("Joann"), "LastName": loc("   ")},
    ],
    "Placeable List": [
        {
            "LocName": loc("Anna's Chest"),
            "Description": loc("Bob's gift to ANNA's family."),
            "_record_offsets": _offsets("LocName", "Description", base=300),
            "ItemList": [_item_row("Scroll", "Old paper.", "", 48, 320)],
        },
        {"LocName": loc("Joanna's Box"), "Description": loc("Plain box.")},
        {"LocName": loc(""), "Description": loc("Nameless thing.")},
        {"LocName": "raw", "Description": loc("Raw name thing.")},
    ],
    "Door List": [
        {
            "LocName": loc("Iron Gate"),
            "LocalizedName": loc("Iron Gate (legacy)"),
            "Description": loc("Rusty."),
            "_record_offsets": _offsets("LocName", "LocalizedName", "Description", base=400),
        },
        {"LocalizedName": loc("Old Door")},
        {"LocName": loc("Beta_to_Bearpit")},
    ],
    "TriggerList": [
        {"Type": 1, "LocalizedName": loc("To the Sewers"), "Description": loc("Dark.")},
        {"Type": 2, "LocalizedName": loc("Spike Trap")},
        {"Type": 0, "TrapFlag": 1, "LocalizedName": loc("Hidden Pit")},
        {"Type": 0, "LocalizedName": loc('"Watch your step!"')},
        {"LocalizedName": loc("CastleExt1To2South")},
    ],
    "WaypointList": [
        {"LocalizedName": loc("Spawn Point"), "MapNote": loc("City Gate")},
        {"MapNote": loc("WP_Hidden")},
    ],
    "Encounter List": [
        {"LocalizedName": loc("Orc, Low Group"), "Description": loc("Not read.")},
    ],
    "StoreList": [
        {
            "LocName": loc("Bazaar"),
            "LocalizedName": loc("Bazaar (legacy)"),
            "Description": loc("Everything."),
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
        {"LocalizedName": loc("Stall"), "StoreList": "not a list"},
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
