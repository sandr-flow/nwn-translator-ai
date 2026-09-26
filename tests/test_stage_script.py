"""Tests for the isolated stage runner ``scripts/stage.py``."""

import json
from pathlib import Path

import pytest

from nwn_translator.formats.erf import ERFReader, ERFWriter
from scripts import stage

from tests.support.gff_writer import write_gff_bytes
from tests.test_ncs import _consts, _retn, _write_ncs


def _module(tmp_path: Path) -> Path:
    """Write a module with one script string and one dialog line."""
    script = _write_ncs(tmp_path, "greet.ncs", _consts("Hello world!"), _retn()).read_bytes()
    dialog = write_gff_bytes(
        {
            "StartingList": [{"Index": 0}],
            "EntryList": [{"Text": {"StrRef": -1, "Value": "Good day."}, "RepliesList": []}],
            "ReplyList": [],
        },
        file_type="DLG",
    )
    path = tmp_path / "my_mod.mod"
    writer = ERFWriter(path)
    writer.add_resource("greet", ".ncs", script)
    writer.add_resource("talk", ".dlg", dialog)
    writer.write()
    return path


def _run(*args: str, tmp_path: Path) -> None:
    # A missing env file keeps the developer's .env out of the test.
    assert stage.main([*args, "--env-file", str(tmp_path / "missing.env")]) == 0


def test_repack_writes_the_module_into_out(tmp_path: Path) -> None:
    module = _module(tmp_path)
    work = tmp_path / "work"
    _run("unpack", str(module), "--out", str(work), tmp_path=tmp_path)

    _run(
        "repack",
        str(module),
        "--extract-dir",
        str(work / "extract"),
        "--out",
        str(work),
        tmp_path=tmp_path,
    )

    repacked = work / "my-mod-rus.mod"
    reader = ERFReader(repacked)
    assert sorted(entry.res_ref for entry in reader.read_entries()) == ["greet", "talk"]
    assert not (tmp_path / "my-mod-rus.mod").exists()


def test_repack_without_the_archive_stops_with_a_message(tmp_path: Path) -> None:
    module = _module(tmp_path)
    work = tmp_path / "work"
    _run("unpack", str(module), "--out", str(work), tmp_path=tmp_path)

    with pytest.raises(SystemExit, match="repack requires the original archive"):
        _run(
            "repack", "--extract-dir", str(work / "extract"), "--out", str(work), tmp_path=tmp_path
        )


def test_only_ext_restricts_extraction_to_one_file_type(tmp_path: Path) -> None:
    module = _module(tmp_path)
    work = tmp_path / "work"
    _run("unpack", str(module), "--out", str(work), tmp_path=tmp_path)

    _run("extract", "--extract-dir", str(work / "extract"), "--out", str(work), tmp_path=tmp_path)
    all_types = {
        json.loads(line)["ext"]
        for line in (work / "items.jsonl").read_text(encoding="utf-8").splitlines()
    }
    _run(
        "extract",
        "--extract-dir",
        str(work / "extract"),
        "--out",
        str(work),
        "--only-ext",
        "ncs",
        tmp_path=tmp_path,
    )
    ncs_types = {
        json.loads(line)["ext"]
        for line in (work / "items.jsonl").read_text(encoding="utf-8").splitlines()
    }

    assert all_types == {".dlg", ".ncs"}
    assert ncs_types == {".ncs"}
