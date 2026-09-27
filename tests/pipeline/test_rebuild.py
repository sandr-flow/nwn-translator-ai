"""Loading, injecting and rebuilding: translations address occurrences, never text."""

from pathlib import Path

import pytest

from nwn_translator import main
from nwn_translator.formats.gff import read_gff
from nwn_translator.formats.ncs import parse_ncs
from nwn_translator.main import (
    inject_translations_into_file,
    load_parsed_and_extracted,
    rebuild_module,
)
from tests.support.gff_writer import loc, write_gff
from tests.support.ncs import action, consts, retn, write_ncs


def _creature(path: Path, tag: str, first_name: str) -> None:
    write_gff(
        path, {"StructType": "UTC", "Tag": tag, "FirstName": loc(first_name)}, file_type="UTC"
    )


def _first_name(path: Path) -> str:
    return read_gff(path).get("FirstName", {}).get("Value", "")


def _rebuild(extract_dir: Path, edits: dict) -> None:
    root = extract_dir.parent
    rebuild_module(
        extract_dir,
        edits,
        root / "out.mod",
        original_mod_path=root / "missing.mod",
        target_lang="russian",
    )


@pytest.fixture
def extract_dir(tmp_path: Path) -> Path:
    path = tmp_path / "extract"
    path.mkdir()
    return path


def test_translation_is_injected_into_the_addressed_script_string(tmp_path):
    path = write_ncs(tmp_path, "s.ncs", consts("Hello world!"), retn())
    parsed, extracted = load_parsed_and_extracted(path, ".ncs", None)
    inject_translations_into_file(
        path, parsed, extracted, {extracted.items[0].key: "Hi there all!"}
    )
    assert [i.string_value for i in parse_ncs(path).string_constants] == ["Hi there all!"]


def test_script_item_ids_survive_a_length_changing_patch(tmp_path):
    """CONSTS index ids stay stable when an earlier string changes length."""
    path = write_ncs(
        tmp_path,
        "scene.ncs",
        consts("NW_TAG"),
        action(200, 1),
        consts("Alpha line."),
        action(39, 1),
        consts("Beta line."),
        action(39, 1),
        consts("Gamma line."),
        action(39, 1),
        retn(),
    )
    parsed, extracted = load_parsed_and_extracted(path, ".ncs", None)
    ids = ["scene:c1", "scene:c2", "scene:c3"]
    assert [item.item_id for item in extracted.items] == ids
    before = {item.item_id: item.metadata["offset"] for item in extracted.items}
    longer = "Alpha line is now much longer than before!"

    inject_translations_into_file(path, parsed, extracted, {(path.name, "scene:c1"): longer})

    parsed, extracted = load_parsed_and_extracted(path, ".ncs", None)
    assert [item.item_id for item in extracted.items] == ids
    after = {item.item_id: item.metadata["offset"] for item in extracted.items}
    assert after["scene:c1"] == before["scene:c1"]
    assert after["scene:c2"] != before["scene:c2"] and after["scene:c3"] != before["scene:c3"]

    inject_translations_into_file(
        path, parsed, extracted, {(path.name, "scene:c3"): "Gamma-EDITED"}
    )

    values = [instr.string_value for instr in parse_ncs(path).string_constants]
    assert values == ["NW_TAG", longer, "Beta line.", "Gamma-EDITED"]


def test_rebuild_edits_only_the_addressed_file(extract_dir):
    """Two files share Tag and FirstName; an edit of one item id touches one file."""
    _creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    _creature(extract_dir / "b.utc", "GOBLIN", "Гоблин")

    _rebuild(
        extract_dir,
        {"a.utc": {"GOBLIN_first_name": "Гоблин-А"}, "b.utc": {"GOBLIN_first_name": "Гоблин"}},
    )

    assert [_first_name(extract_dir / f"{n}.utc") for n in "ab"] == ["Гоблин-А", "Гоблин"]


def test_rebuild_without_edits_changes_no_bytes(extract_dir):
    _creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    before = (extract_dir / "a.utc").read_bytes()
    _rebuild(extract_dir, {"a.utc": {"GOBLIN_first_name": "Гоблин"}})
    assert (extract_dir / "a.utc").read_bytes() == before


def test_rebuild_reads_only_the_files_with_edits(extract_dir, monkeypatch):
    _creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    _creature(extract_dir / "b.utc", "ORC", "Орк")
    untouched = (extract_dir / "b.utc").read_bytes()
    loaded = []
    real_load = main.load_parsed_and_extracted

    def counting_load(file_path, *args, **kwargs):
        loaded.append(file_path.name)
        return real_load(file_path, *args, **kwargs)

    monkeypatch.setattr(main, "load_parsed_and_extracted", counting_load)

    _rebuild(extract_dir, {"a.utc": {"GOBLIN_first_name": "Гоблин-А"}})

    assert loaded == ["a.utc"]
    assert _first_name(extract_dir / "a.utc") == "Гоблин-А"
    assert (extract_dir / "b.utc").read_bytes() == untouched


def test_rebuild_edits_one_of_two_identical_dialog_lines(extract_dir):
    dlg = extract_dir / "a.dlg"
    entries = [{"Text": loc("Привет."), "Speaker": ""} for _ in range(2)]
    write_gff(dlg, {"StructType": "DLG", "EntryList": entries, "ReplyList": []}, file_type="DLG")

    _rebuild(extract_dir, {"a.dlg": {"a:entry:1": "Здорово."}})

    assert [entry["Text"]["Value"] for entry in read_gff(dlg)["EntryList"]] == [
        "Привет.",
        "Здорово.",
    ]


@pytest.mark.parametrize(
    "extension, data",
    [
        ("utc", {"Tag": "same", "FirstName": loc("Shared"), "LastName": loc("Shared")}),
        ("dlg", {"EntryList": [{"Text": loc("Shared")}, {"Text": loc("Shared")}]}),
        ("git", {"Creature List": [{"FirstName": loc("Shared")}, {"FirstName": loc("Shared")}]}),
        (
            "jrl",
            {"Categories": [{"Name": loc("Shared"), "EntryList": [{"Text": loc("Shared")}]}]},
        ),
        ("uti", {"Description": loc("Shared"), "DescIdentified": loc("Shared")}),
    ],
)
def test_identical_fields_are_injected_and_rebuilt_independently(extract_dir, extension, data):
    path = extract_dir / f"sample.{extension}"
    write_gff(path, data, file_type=extension.upper())
    parsed, extracted = load_parsed_and_extracted(path, path.suffix, None)
    first, second = extracted.items

    inject_translations_into_file(
        path, parsed, extracted, {first.key: "Alpha", second.key: "Beta"}, target_lang="english"
    )

    _, after = load_parsed_and_extracted(path, path.suffix, None)
    assert {item.key: item.text for item in after.items} == {first.key: "Alpha", second.key: "Beta"}
    # Rebuild addresses the occurrence with the offsets of the resized file.
    rebuild_module(
        extract_dir,
        {path.name: {second.item_id: "Much longer edited value"}},
        extract_dir.parent / "out.mod",
        original_mod_path=extract_dir.parent / "missing.mod",
        target_lang="english",
    )
    _, after = load_parsed_and_extracted(path, path.suffix, None)
    assert {item.key: item.text for item in after.items} == {
        first.key: "Alpha",
        second.key: "Much longer edited value",
    }
