"""Round-trip tests for ERFWriter.

These tests verify that ERFWriter produces a valid ERF v1.0 binary that
ERFReader can parse back, yielding the same resources.
"""

import struct
import tempfile
from pathlib import Path

import pytest

from src.nwn_translator.file_handlers.erf_writer import ERFWriter, ERFWriterError
from src.nwn_translator.file_handlers.erf_reader import ERFReader, ERFHeader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_and_read(resources: dict, suffix: str = ".mod") -> list:
    """Write resources to a temp ERF file and read them back.

    Args:
        resources: {filename_with_ext: bytes} mapping.
        suffix: Output file extension.

    Returns:
        List of ERFEntry objects read back.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = Path(f.name)
    try:
        writer = ERFWriter(tmp)
        for filename, data in resources.items():
            stem = Path(filename).stem
            ext = Path(filename).suffix
            writer.add_resource(stem, ext, data)
        writer.write()

        reader = ERFReader(tmp)
        reader.read_header()
        return reader.read_entries()
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Header format tests
# ---------------------------------------------------------------------------

class TestERFWriterHeader:
    """Verify the ERF v1.0 header layout."""

    def test_file_type_mod(self, tmp_path):
        """A .mod output path must produce b'MOD ' file type."""
        out = tmp_path / "test.mod"
        writer = ERFWriter(out)
        writer.write()
        data = out.read_bytes()
        assert data[0:4] == b"MOD "

    def test_file_type_erf(self, tmp_path):
        """A .erf output path must produce b'ERF ' file type."""
        out = tmp_path / "test.erf"
        writer = ERFWriter(out)
        writer.write()
        data = out.read_bytes()
        assert data[0:4] == b"ERF "

    def test_version_field(self, tmp_path):
        """Version field must be b'V1.0' at bytes [4:8]."""
        out = tmp_path / "test.mod"
        writer = ERFWriter(out)
        writer.write()
        data = out.read_bytes()
        assert data[4:8] == b"V1.0"

    def test_entry_count_in_header(self, tmp_path):
        """Entry count at bytes [16:20] must match number of resources."""
        out = tmp_path / "test.mod"
        writer = ERFWriter(out)
        writer.add_resource("alpha", ".dlg", b"DLG DATA")
        writer.add_resource("beta",  ".uti", b"UTI DATA")
        writer.write()

        data = out.read_bytes()
        count = struct.unpack("<I", data[16:20])[0]
        assert count == 2

    def test_key_list_offset_after_header(self, tmp_path):
        """OffsetToKeyList at [24:28] must equal 160 (right after header)."""
        out = tmp_path / "test.mod"
        writer = ERFWriter(out)
        writer.add_resource("x", ".dlg", b"x")
        writer.write()

        data = out.read_bytes()
        offset = struct.unpack("<I", data[24:28])[0]
        assert offset == 160

    def test_resource_list_offset_after_key_list(self, tmp_path):
        """OffsetToResourceList at [28:32] must equal 160 + N*24."""
        N = 3
        out = tmp_path / "test.mod"
        writer = ERFWriter(out)
        for i in range(N):
            writer.add_resource(f"r{i}", ".dlg", b"data")
        writer.write()

        data = out.read_bytes()
        res_offset = struct.unpack("<I", data[28:32])[0]
        assert res_offset == 160 + N * 24


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestERFWriterRoundTrip:
    """Verify write → read produces the same resources."""

    def test_single_resource_name(self):
        """Resource name is preserved in round-trip."""
        entries = _write_and_read({"dialog.dlg": b"DLG CONTENT"})
        assert len(entries) == 1
        assert entries[0].res_ref == "dialog"

    def test_single_resource_content(self):
        """Resource content is preserved in round-trip."""
        payload = b"HELLO WORLD DATA"

        with tempfile.NamedTemporaryFile(suffix=".mod", delete=False) as f:
            tmp = Path(f.name)
        try:
            writer = ERFWriter(tmp)
            writer.add_resource("test", ".dlg", payload)
            writer.write()

            reader = ERFReader(tmp)
            reader.read_header()
            entries = reader.read_entries()
            assert len(entries) == 1

            # Read raw data using offset/size from the entry
            raw = tmp.read_bytes()
            entry = entries[0]
            extracted = raw[entry.offset: entry.offset + entry.size]
            assert extracted == payload
        finally:
            tmp.unlink(missing_ok=True)

    def test_multiple_resources_count(self):
        """All added resources appear in re-read ERF."""
        resources = {
            "dialog.dlg": b"dlg data",
            "item.uti":   b"uti data",
            "journal.jrl": b"jrl data",
        }
        entries = _write_and_read(resources)
        assert len(entries) == 3

    def test_multiple_resources_names(self):
        """All resource names survive round-trip."""
        resources = {
            "qst_001.dlg": b"a",
            "sword_fire.uti": b"b",
        }
        entries = _write_and_read(resources)
        names = {e.res_ref for e in entries}
        assert "qst_001" in names
        assert "sword_fire" in names

    def test_resource_type_id_dlg(self):
        """Dialog resources use NWN:EE-style type IDs from ERFWriter map."""
        entries = _write_and_read({"x.dlg": b"content"})
        assert entries[0].res_type == ERFWriter.RESOURCE_TYPE_IDS[".dlg"]

    def test_resource_type_id_uti(self):
        """Item resources use NWN:EE-style type IDs from ERFWriter map."""
        entries = _write_and_read({"x.uti": b"content"})
        assert entries[0].res_type == ERFWriter.RESOURCE_TYPE_IDS[".uti"]

    def test_resource_type_id_jrl(self):
        """Journal resources use NWN:EE-style type IDs from ERFWriter map."""
        entries = _write_and_read({"x.jrl": b"content"})
        assert entries[0].res_type == ERFWriter.RESOURCE_TYPE_IDS[".jrl"]

    def test_resource_size_correct(self):
        """Resource size in Resource List must match actual data length."""
        with tempfile.NamedTemporaryFile(suffix=".mod", delete=False) as f:
            tmp = Path(f.name)
        try:
            payload = b"A" * 123
            writer = ERFWriter(tmp)
            writer.add_resource("test", ".dlg", payload)
            writer.write()

            reader = ERFReader(tmp)
            reader.read_header()
            entries = reader.read_entries()
            assert entries[0].size == 123
        finally:
            tmp.unlink(missing_ok=True)

    def test_all_data_extractable(self):
        """All resources can be extracted and match original data."""
        payloads = {
            "qst": b"QUEST DIALOG DATA" * 10,
            "helm": b"\x00\x01\x02" * 5,
            "journal": b"Journal entry text",
        }
        extensions = {"qst": ".dlg", "helm": ".uti", "journal": ".jrl"}

        with tempfile.NamedTemporaryFile(suffix=".mod", delete=False) as f:
            tmp = Path(f.name)
        try:
            writer = ERFWriter(tmp)
            for stem, data in payloads.items():
                writer.add_resource(stem, extensions[stem], data)
            writer.write()

            raw = tmp.read_bytes()
            reader = ERFReader(tmp)
            reader.read_header()
            entries = reader.read_entries()
            assert len(entries) == 3

            for entry in entries:
                extracted = raw[entry.offset: entry.offset + entry.size]
                assert extracted == payloads[entry.res_ref], \
                    f"Data mismatch for {entry.res_ref}"
        finally:
            tmp.unlink(missing_ok=True)

    def test_empty_archive_writes(self, tmp_path):
        """An empty ERF archive with no resources must still write successfully."""
        out = tmp_path / "empty.mod"
        writer = ERFWriter(out)
        writer.write()
        assert out.exists()

        # ERFHeader must parse without error
        header = ERFHeader(out.read_bytes()[:160])
        assert header.entry_count == 0

    def test_add_file_roundtrip(self, tmp_path):
        """add_file() from disk is equivalent to add_resource()."""
        src = tmp_path / "dialog.dlg"
        src.write_bytes(b"DLG FILE CONTENT")

        out = tmp_path / "oneresource.mod"
        writer = ERFWriter(out)
        writer.add_file(src)
        writer.write()

        raw = out.read_bytes()
        reader = ERFReader(out)
        reader.read_header()
        entries = reader.read_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert raw[entry.offset: entry.offset + entry.size] == b"DLG FILE CONTENT"
