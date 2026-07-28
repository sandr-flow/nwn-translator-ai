"""Tests for ERFReader validation of crafted / corrupt archives.

A well-formed archive is produced with ERFWriter and then individual header
or table fields are patched to simulate hostile input. The reader must fail
with an explicit ERFReaderError instead of exhausting memory or disk.
"""

import struct

import pytest

from src.nwn_translator.file_handlers.erf_reader import ERFReader, ERFReaderError
from src.nwn_translator.file_handlers.erf_writer import ERFWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_valid_mod(path, resources=None):
    """Write a small valid .mod archive and return its path."""
    writer = ERFWriter(path)
    for stem, ext, data in resources or [("dialog", ".dlg", b"DLG DATA")]:
        writer.add_resource(stem, ext, data)
    writer.write()
    return path


def _patch_bytes(path, offset, payload):
    """Overwrite *payload* at *offset* inside the file at *path*."""
    raw = bytearray(path.read_bytes())
    raw[offset : offset + len(payload)] = payload
    path.write_bytes(bytes(raw))


# ---------------------------------------------------------------------------
# Header validation (M-W2 / M-W9)
# ---------------------------------------------------------------------------


class TestHeaderValidation:
    """Crafted headers must be rejected before any large allocation."""

    def test_huge_entry_count_rejected(self, tmp_path):
        """entry_count far beyond the file size fails fast in read_header."""
        mod = _write_valid_mod(tmp_path / "bomb.mod")
        _patch_bytes(mod, 16, struct.pack("<I", 0xFFFFFFFF))

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="do not fit"):
            reader.read_header()

    def test_key_list_offset_beyond_file_rejected(self, tmp_path):
        """A key list starting past the end of the file is rejected."""
        mod = _write_valid_mod(tmp_path / "bad_offset.mod")
        file_size = mod.stat().st_size
        _patch_bytes(mod, 24, struct.pack("<I", file_size + 1000))

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="do not fit"):
            reader.read_header()

    def test_resource_list_offset_beyond_file_rejected(self, tmp_path):
        """A resource list starting past the end of the file is rejected."""
        mod = _write_valid_mod(tmp_path / "bad_res_offset.mod")
        file_size = mod.stat().st_size
        _patch_bytes(mod, 28, struct.pack("<I", file_size + 1000))

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="do not fit"):
            reader.read_header()

    @pytest.mark.parametrize("version", [b"V1.1", b"V2.0", b"E1.0", b"\x00\x00\x00\x00"])
    def test_non_v10_version_rejected(self, tmp_path, version):
        """Any version other than V1.0 fails with an explicit message."""
        mod = _write_valid_mod(tmp_path / "wrong_version.mod")
        _patch_bytes(mod, 4, version)

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="only V1.0"):
            reader.read_header()

    def test_valid_archive_accepted(self, tmp_path):
        """The unpatched writer output still parses (positive control)."""
        mod = _write_valid_mod(tmp_path / "ok.mod")
        reader = ERFReader(mod)
        header = reader.read_header()
        assert header.entry_count == 1


# ---------------------------------------------------------------------------
# Entry table validation (M-W2)
# ---------------------------------------------------------------------------


class TestEntryValidation:
    """Resource list entries must stay inside the file and not overlap."""

    def test_entry_size_beyond_file_rejected(self, tmp_path):
        """An entry whose data region runs past EOF is rejected."""
        mod = _write_valid_mod(tmp_path / "bad_size.mod")
        # One entry: resource list is at 160 + 24; size field is 4 bytes in.
        _patch_bytes(mod, 160 + 24 + 4, struct.pack("<I", 0x7FFFFFFF))

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="exceeds file size"):
            reader.read_entries()

    def test_overlapping_entries_rejected(self, tmp_path):
        """Entries sharing one data region (an ERF bomb) are rejected."""
        entry_count = 3
        data = b"X" * 200
        key_list_offset = 160
        res_list_offset = key_list_offset + entry_count * 24
        data_offset = res_list_offset + entry_count * 8

        header = bytearray(160)
        header[0:4] = b"MOD "
        header[4:8] = b"V1.0"
        struct.pack_into("<I", header, 16, entry_count)
        struct.pack_into("<I", header, 24, key_list_offset)
        struct.pack_into("<I", header, 28, res_list_offset)

        key_list = b"".join(
            f"res{i}".encode("ascii").ljust(16, b"\x00") + struct.pack("<II", i, 2029)
            for i in range(entry_count)
        )
        # Every entry points at the same 200-byte region: each is valid on
        # its own, but the declared total is 600 bytes in a 456-byte file.
        res_list = struct.pack("<II", data_offset, len(data)) * entry_count

        mod = tmp_path / "overlap.mod"
        mod.write_bytes(bytes(header) + key_list + res_list + data)

        reader = ERFReader(mod)
        with pytest.raises(ERFReaderError, match="overlapping"):
            reader.read_entries()

    def test_localized_block_beyond_file_treated_as_absent(self, tmp_path):
        """A description block that does not fit the file reads back empty."""
        mod = _write_valid_mod(tmp_path / "bad_desc.mod")
        # Declare a huge LocalizedStringSize pointing past EOF.
        _patch_bytes(mod, 12, struct.pack("<I", 0x7FFFFFFF))
        _patch_bytes(mod, 20, struct.pack("<I", 160))

        reader = ERFReader(mod)
        assert reader.read_localized_strings_block() == b""

    def test_valid_entries_accepted(self, tmp_path):
        """A multi-resource writer archive passes all entry checks."""
        mod = _write_valid_mod(
            tmp_path / "ok.mod",
            [("a", ".dlg", b"one"), ("b", ".uti", b"two"), ("c", ".jrl", b"three")],
        )
        reader = ERFReader(mod)
        entries = reader.read_entries()
        assert len(entries) == 3
