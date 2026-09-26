"""Tests for the JSONL translation log file writer."""

import gc
import json
from pathlib import Path

from nwn_translator.translation_logging import FileTranslationLogWriter

ENTRIES = [
    {"original": "Hello", "translated": "Привет", "file": "a.dlg", "item_id": "a:entry:0"},
    {"event": "model_request", "arguments": {"text": "line\nbreak   sep \u0085"}},
    {"original": "Ünïcödé", "translated": None, "success": False},
]


def _reference_bytes(path: Path) -> bytes:
    """Bytes of the same entries written by opening the file once per entry."""
    for entry in ENTRIES:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path.read_bytes()


def test_writes_the_same_bytes_as_one_open_per_entry(tmp_path: Path) -> None:
    writer = FileTranslationLogWriter(tmp_path / "log.jsonl")
    for entry in ENTRIES:
        writer.write(entry)
    writer.close()

    assert (tmp_path / "log.jsonl").read_bytes() == _reference_bytes(tmp_path / "ref.jsonl")


def test_opens_the_file_once_and_flushes_every_entry(tmp_path: Path, opened_files) -> None:
    path = tmp_path / "log.jsonl"
    writer = FileTranslationLogWriter(path)
    for index, entry in enumerate(ENTRIES, 1):
        writer.write(entry)
        # Readers see every entry while the handle is still open.
        assert path.read_text(encoding="utf-8").count("\n") == index

    appends = [handle for handle in opened_files(path) if handle.mode == "a"]
    assert len(appends) == 1
    assert not appends[0].closed
    writer.close()


def test_close_releases_the_file_and_a_later_entry_appends(tmp_path: Path, opened_files) -> None:
    path = tmp_path / "log.jsonl"
    writer = FileTranslationLogWriter(path)
    writer.write(ENTRIES[0])
    writer.close()
    assert all(handle.closed for handle in opened_files(path))
    path.rename(tmp_path / "moved.jsonl")  # fails on Windows while a handle is open

    writer.write(ENTRIES[1])
    writer.close()

    assert all(handle.closed for handle in opened_files(path))
    assert path.read_text(encoding="utf-8") == json.dumps(ENTRIES[1], ensure_ascii=False) + "\n"


def test_a_dropped_writer_releases_its_file(tmp_path: Path, opened_files) -> None:
    path = tmp_path / "log.jsonl"
    writer = FileTranslationLogWriter(path)
    writer.write(ENTRIES[0])
    del writer
    gc.collect()

    assert [handle.closed for handle in opened_files(path)] == [True]
    path.unlink()  # fails on Windows while a handle is open
