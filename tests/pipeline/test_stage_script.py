"""The isolated stage runner ``scripts/stage.py``."""

import json
from pathlib import Path

import pytest

from nwn_translator.formats.erf import ERFReader, ERFWriter
from nwn_translator.pipeline import artifacts
from scripts import stage
from tests.support.gff_writer import write_gff_bytes
from tests.support.ncs import consts, retn, script


def _module(tmp_path: Path) -> Path:
    """Write a module with one script string and one dialog line."""
    dialog = {
        "StartingList": [{"Index": 0}],
        "EntryList": [{"Text": {"StrRef": -1, "Value": "Good day."}, "RepliesList": []}],
        "ReplyList": [],
    }
    path = tmp_path / "my_mod.mod"
    writer = ERFWriter(path)
    writer.add_resource("greet", ".ncs", script(consts("Hello world!"), retn()))
    writer.add_resource("talk", ".dlg", write_gff_bytes(dialog, file_type="DLG"))
    writer.write()
    return path


def _run(*args: str, tmp_path: Path) -> None:
    # A missing env file keeps the developer's .env out of the test.
    assert stage.main([*args, "--env-file", str(tmp_path / "missing.env")]) == 0


def _unpacked(tmp_path: Path, *extra: str):
    """Unpack the test module into ``work``; return the module, ``work`` and stage options."""
    module, work = _module(tmp_path), tmp_path / "work"
    _run("unpack", str(module), "--out", str(work), *extra, tmp_path=tmp_path)
    return module, work, ["--extract-dir", str(work / "extract"), "--out", str(work)]


def _add_creature(work: Path, **fields) -> None:
    creature = {"Tag": "MARTA", "FirstName": {"StrRef": -1, "Value": "Marta"}, **fields}
    (work / "extract" / "marta.utc").write_bytes(write_gff_bytes(creature, file_type="UTC"))


def _record_llm_stages(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the model stages by stubs that record their calls."""
    calls = []

    def collect_entities(state, extracted_map) -> None:
        calls.append("collect_entities")
        state.world_context.extracted_names = [("Marta", "character")]

    monkeypatch.setattr(stage, "stage_collect_entities", collect_entities)
    monkeypatch.setattr(stage, "stage_build_glossary", lambda state: calls.append("build_glossary"))
    return calls


def test_repack_writes_the_module_into_out(tmp_path):
    module, work, extract = _unpacked(tmp_path)

    _run("repack", str(module), *extract, tmp_path=tmp_path)

    entries = ERFReader(work / "my-mod-rus.mod").read_entries()
    assert sorted(entry.res_ref for entry in entries) == ["greet", "talk"]
    assert not (tmp_path / "my-mod-rus.mod").exists()
    with pytest.raises(SystemExit, match="repack requires the original archive"):
        _run("repack", *extract, tmp_path=tmp_path)


def test_temp_dir_is_still_accepted_and_ignored(tmp_path):
    _module, work, _extract = _unpacked(tmp_path, "--temp-dir", str(tmp_path / "unused"))
    assert sorted(path.name for path in (work / "extract").iterdir()) == ["greet.ncs", "talk.dlg"]
    assert not (tmp_path / "unused").exists()


def test_worldscan_also_saves_the_scan_candidates(tmp_path):
    """'entities --from' needs the creature names the scan found."""
    _module, work, extract = _unpacked(tmp_path)
    _add_creature(work, Conversation="talk", ScriptSpawn="greet")

    _run("worldscan", *extract, tmp_path=tmp_path)

    candidates = artifacts.load_candidates(work / "candidates.json")
    assert [c.name for c in candidates.values()] == ["Marta"]
    world = artifacts.load_world_context(work / "world_context.json")
    assert [owner.tag for owner in world.script_owners["greet"]] == ["MARTA"]


def test_glossary_collects_the_entities_unless_they_are_saved(tmp_path, monkeypatch):
    """The scan's own candidates do not replace the entity stage; its artifact does."""
    _module, work, extract = _unpacked(tmp_path)
    _add_creature(work)
    _run("worldscan", *extract, tmp_path=tmp_path)
    calls = _record_llm_stages(monkeypatch)

    _run("glossary", *extract, tmp_path=tmp_path)
    assert calls == ["collect_entities", "build_glossary"]

    _run("entities", *extract, tmp_path=tmp_path)
    calls.clear()
    _run("glossary", *extract, tmp_path=tmp_path)
    assert calls == ["build_glossary"]


def test_only_ext_restricts_extraction_to_one_file_type(tmp_path):
    _module, work, extract = _unpacked(tmp_path)

    def extracted_types():
        lines = (work / "items.jsonl").read_text(encoding="utf-8").splitlines()
        return {json.loads(line)["ext"] for line in lines}

    _run("extract", *extract, tmp_path=tmp_path)
    assert extracted_types() == {".dlg", ".ncs"}
    _run("extract", *extract, "--only-ext", "ncs", tmp_path=tmp_path)
    assert extracted_types() == {".ncs"}
