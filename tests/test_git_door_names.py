"""Door instance names in .git files live in ``LocName``, the .utd blueprint label.

Fixtures are real GFF files built with ``write_gff``, so record offsets come from
the parser exactly as they do for module resources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from nwn_translator.extractors.git_extractor import GitExtractor
from nwn_translator.extractors.git_fields import collect_git_strings_missing_from_translations
from nwn_translator.file_handlers.gff_handler import read_gff
from nwn_translator.file_handlers.gff_writer import write_gff
from nwn_translator.main import rebuild_module
from nwn_translator.pipeline.stages import (
    inject_translations_into_file,
    load_parsed_and_extracted,
)


def _loc(value: str, strref: int = -1) -> Dict[str, Any]:
    return {"StrRef": strref, "Value": value}


def _write_area(path: Path, doors: list) -> None:
    write_gff(path, {"StructType": "GIT", "Door List": doors}, file_type="GIT")


def _door_value(path: Path, index: int, field: str) -> str:
    return read_gff(path, source_encoding="cp1251")["Door List"][index][field]["Value"]


def test_door_instance_name_is_extracted_from_locname(tmp_path: Path) -> None:
    path = tmp_path / "keep.git"
    _write_area(
        path,
        [
            {"Tag": "KeepGate", "LocName": _loc("Iron Gate"), "Description": _loc("Rusty.")},
            {"Tag": "CellarDoor", "LocName": _loc("Cellar Door")},
            # StrRef-only names resolve from the player's dialog.tlk.
            {"Tag": "TlkDoor", "LocName": _loc("", strref=1234)},
            # Fallback label, in case a toolset writes it instead of LocName.
            {"Tag": "OldDoor", "LocalizedName": _loc("Old Door")},
        ],
    )
    parsed = read_gff(path)

    items = GitExtractor().extract(path, parsed).items
    names = {item.item_id: item for item in items if item.metadata["type"] == "door_name"}

    assert set(names) == {
        "keep_Door List_0_LocName",
        "keep_Door List_1_LocName",
        "keep_Door List_3_LocalizedName",
    }
    gate = names["keep_Door List_0_LocName"]
    assert gate.text == "Iron Gate"
    assert gate.context == "Door name (area instance)"
    assert gate.metadata["git_field"] == "LocName"
    assert gate.metadata["record_offset"] == parsed["Door List"][0]["_record_offsets"]["LocName"]
    assert names["keep_Door List_3_LocalizedName"].text == "Old Door"
    assert {item.text for item in items if item.metadata["type"] == "door_description"} == {
        "Rusty."
    }

    assert collect_git_strings_missing_from_translations(parsed, {}) == {
        "Iron Gate",
        "Cellar Door",
        "Old Door",
        "Rusty.",
    }


def test_door_instance_internal_tags_are_skipped(tmp_path: Path) -> None:
    """Toolset route labels and engine tags in door LocName stay untranslated."""
    blocked = ["Beta_to_Bearpit", "LL_EXIT", "HouseToBasement", "NW_Door"]
    path = tmp_path / "routes.git"
    _write_area(
        path,
        [{"Tag": f"door{i}", "LocName": _loc(value)} for i, value in enumerate(blocked)]
        + [{"Tag": "door_ok", "LocName": _loc("Wooden Door")}],
    )
    parsed = read_gff(path)

    extracted = {item.text for item in GitExtractor().extract(path, parsed).items}
    collected = collect_git_strings_missing_from_translations(parsed, {})

    assert extracted == collected == {"Wooden Door"}


def test_door_instance_name_is_patched_and_rebuilt(tmp_path: Path) -> None:
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    path = extract_dir / "keep.git"
    _write_area(
        path,
        [
            {"Tag": "KeepGate", "LocName": _loc("Iron Gate"), "Description": _loc("Rusty.")},
            {"Tag": "CellarDoor", "LocName": _loc("Cellar Door")},
        ],
    )

    loaded = load_parsed_and_extracted(path, ".git", None)
    assert loaded is not None
    parsed, extracted = loaded
    answers = {"Iron Gate": "Железные ворота", "Cellar Door": "Дверь в погреб"}
    translations = {
        item.key: answers[item.text] for item in extracted.items if item.text in answers
    }
    inject_translations_into_file(path, parsed, extracted, translations, target_lang="russian")

    assert _door_value(path, 0, "LocName") == "Железные ворота"
    assert _door_value(path, 0, "Description") == "Rusty."
    assert _door_value(path, 1, "LocName") == "Дверь в погреб"
    assert read_gff(path)["Door List"][1]["Tag"] == "CellarDoor"

    rebuild_module(
        extract_dir,
        {"keep.git": {"keep_Door List_1_LocName": "Погребная дверь"}},
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="russian",
    )

    assert _door_value(path, 0, "LocName") == "Железные ворота"
    assert _door_value(path, 1, "LocName") == "Погребная дверь"
