"""Round-trip tests for GFF v3.2 writer.

These tests verify that GFFWriter.to_bytes() produces a valid binary that,
when re-parsed by GFFParser, yields a dict equivalent to what was written.
"""

import struct
import tempfile
from pathlib import Path

import pytest

from src.nwn_translator.file_handlers.gff_writer import GFFWriter, GFFWriteError, write_gff_bytes
from src.nwn_translator.file_handlers.gff_handler import GFFHandler, GFFHandlerError
from src.nwn_translator.file_handlers.gff_parser import GFFParser, gff_to_dict

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _roundtrip(data: dict) -> dict:
    """Write *data* to a temp file, re-read it, and return the parsed dict."""
    with tempfile.NamedTemporaryFile(suffix=".gff", delete=False) as f:
        tmp = Path(f.name)
    try:
        GFFHandler.write(tmp, data)
        return GFFHandler.read(tmp)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Basic smoke tests
# ---------------------------------------------------------------------------


class TestGFFWriterSmoke:
    """Basic smoke tests for GFFWriter."""

    def test_produces_bytes(self):
        """GFFWriter.to_bytes() must return non-empty bytes."""
        data = {"StructType": "UTI", "LocalizedName": {"StrRef": -1, "Value": "Sword"}}
        result = write_gff_bytes(data)
        assert isinstance(result, bytes)
        assert len(result) >= 160

    def test_header_magic(self):
        """First 4 bytes must match the file type tag."""
        data = {"StructType": "DLG"}
        result = write_gff_bytes(data)
        assert result[0:4] == b"DLG "

    def test_header_version(self):
        """Bytes 4-8 must be 'V3.2'."""
        data = {"StructType": "DLG"}
        result = write_gff_bytes(data)
        assert result[4:8] == b"V3.2"

    def test_empty_dict_writes(self):
        """An empty GFF dict (no fields) must still produce a valid file."""
        data = {"StructType": "GFF"}
        result = write_gff_bytes(data)
        assert len(result) >= 160

    def test_write_to_disk(self, tmp_path):
        """write() must create a file on disk."""
        out = tmp_path / "test.gff"
        data = {"StructType": "UTI", "Tag": "TestTag"}
        writer = GFFWriter(data)
        writer.write(out)
        assert out.exists()
        assert out.stat().st_size >= 160


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestGFFRoundTrip:
    """Verify that write → read produces equivalent data."""

    def test_roundtrip_simple_integer(self):
        """DWORD field survives round-trip."""
        data = {"StructType": "GFF", "HP": 100}
        result = _roundtrip(data)
        assert result.get("HP") == 100

    def test_roundtrip_negative_integer(self):
        """Negative INT field survives round-trip."""
        data = {"StructType": "GFF", "Penalty": -5}
        result = _roundtrip(data)
        assert result.get("Penalty") == -5

    def test_roundtrip_float(self):
        """FLOAT field survives round-trip (within float32 precision)."""
        data = {"StructType": "GFF", "Speed": 1.5}
        result = _roundtrip(data)
        assert abs(result.get("Speed", 0) - 1.5) < 1e-5

    def test_roundtrip_exostring(self):
        """CExoString field survives round-trip."""
        data = {"StructType": "GFF", "Description": "A long description text."}
        result = _roundtrip(data)
        assert result.get("Description") == "A long description text."

    def test_roundtrip_locstring_with_value(self):
        """CExoLocString with embedded Value survives round-trip."""
        data = {
            "StructType": "UTI",
            "LocalizedName": {"StrRef": -1, "Value": "Magic Sword"},
        }
        result = _roundtrip(data)
        loc = result.get("LocalizedName", {})
        assert loc.get("Value") == "Magic Sword"
        assert loc.get("StrRef") == -1

    def test_roundtrip_locstring_empty(self):
        """CExoLocString with empty Value survives round-trip."""
        data = {
            "StructType": "UTI",
            "LocalizedName": {"StrRef": 1234, "Value": ""},
        }
        result = _roundtrip(data)
        loc = result.get("LocalizedName", {})
        assert loc.get("StrRef") == 1234

    def test_roundtrip_multiple_fields(self):
        """Multiple fields in root struct all survive round-trip."""
        data = {
            "StructType": "UTI",
            "Tag": "sword",
            "LocalizedName": {"StrRef": -1, "Value": "Longsword"},
            "BaseItem": 5,
        }
        result = _roundtrip(data)
        assert result.get("LocalizedName", {}).get("Value") == "Longsword"
        assert result.get("BaseItem") == 5

    def test_roundtrip_list_of_structs(self):
        """A GFF List containing child structs survives round-trip."""
        data = {
            "StructType": "DLG",
            "EntryList": [
                {
                    "Text": {"StrRef": -1, "Value": "Hello traveler!"},
                    "Speaker": "Guard",
                },
                {
                    "Text": {"StrRef": -1, "Value": "Who goes there?"},
                    "Speaker": "Guard",
                },
            ],
            "ReplyList": [
                {"Text": {"StrRef": -1, "Value": "Just passing."}},
            ],
        }
        result = _roundtrip(data)
        entries = result.get("EntryList", [])
        assert len(entries) == 2
        texts = {e.get("Text", {}).get("Value") for e in entries}
        assert "Hello traveler!" in texts
        assert "Who goes there?" in texts

        replies = result.get("ReplyList", [])
        assert len(replies) == 1
        assert replies[0].get("Text", {}).get("Value") == "Just passing."

    def test_roundtrip_unicode_text(self):
        """Unicode / Cyrillic text in a CExoLocString survives round-trip."""
        data = {
            "StructType": "UTI",
            "LocalizedName": {"StrRef": -1, "Value": "Меч огня"},
        }
        result = _roundtrip(data)
        assert result.get("LocalizedName", {}).get("Value") == "Меч огня"

    def test_roundtrip_nested_structs(self):
        """Nested list of structs with nested lists survives round-trip."""
        data = {
            "StructType": "DLG",
            "EntryList": [
                {
                    "Text": {"StrRef": -1, "Value": "Root entry"},
                    "RepliesList": [
                        {"Index": 0, "IsChild": 1},
                    ],
                }
            ],
        }
        result = _roundtrip(data)
        entries = result.get("EntryList", [])
        assert len(entries) == 1
        replies = entries[0].get("RepliesList", [])
        assert len(replies) == 1


# ---------------------------------------------------------------------------
# GFFHandler.write integration
# ---------------------------------------------------------------------------


class TestGFFHandlerWrite:
    """Integration tests using GFFHandler.write()."""

    def test_write_then_read_returns_same_type(self, tmp_path):
        """GFFHandler.write + read preserves StructType."""
        data = {"StructType": "JRL", "Categories": []}
        out = tmp_path / "journal.jrl"
        GFFHandler.write(out, data)
        result = GFFHandler.read(out)
        assert result.get("StructType") == "JRL"

    def test_write_creates_file(self, tmp_path):
        """GFFHandler.write() must create a file at the given path."""
        data = {"StructType": "UTI", "LocalizedName": {"StrRef": -1, "Value": "Helm"}}
        out = tmp_path / "helm.uti"
        GFFHandler.write(out, data)
        assert out.exists()
        assert out.stat().st_size > 160
