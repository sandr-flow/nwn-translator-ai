"""Tests for GFF header block validation against the file size.

A corrupt or non-GFF header can declare billions of labels/fields; a parser
that trusted the counts would allocate one placeholder per declared label
(memory blowup) or spin through every declared index (minutes of busy work).
A header block that lies outside the file must fail fast instead.

Fixtures are built by writing a valid GFF and corrupting one header DWORD.
"""

import struct
import time

import pytest

from nwn_translator.formats.gff import GFFParseError, parse_gff, read_gff
from tests.support.gff_writer import write_gff

# Header DWORD offsets (GFF v3.2).
_STRUCT_COUNT = 12
_FIELD_COUNT = 20
_LABEL_COUNT = 28
_FIELDDATA_SIZE = 36


def _make_valid_gff(tmp_path, filename="victim.uti"):
    path = tmp_path / filename
    write_gff(
        path,
        {
            "StructType": "UTI",
            "Tag": "some_item",
            "LocalizedName": {"StrRef": -1, "Value": "Plain Dagger"},
            "Charges": 3,
        },
    )
    return path


def _corrupt_header_dword(path, offset, value):
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, offset, value)
    path.write_bytes(data)


def _assert_fails_fast(path, expected_block):
    started = time.monotonic()
    with pytest.raises(GFFParseError) as exc_info:
        parse_gff(path)
    elapsed = time.monotonic() - started
    assert expected_block in str(exc_info.value)
    assert "outside the file" in str(exc_info.value)
    # The point of the check: no per-declared-element work happens at all.
    assert elapsed < 1.0


class TestHeaderBlockValidation:
    """Declared header blocks must fit the file, or parse fails immediately."""

    def test_valid_file_parses(self, tmp_path):
        path = _make_valid_gff(tmp_path)
        parsed = read_gff(path)
        assert parsed["LocalizedName"]["Value"] == "Plain Dagger"

    def test_huge_label_count_fails_fast(self, tmp_path):
        """The OOM scenario: billions of labels declared in a tiny file."""
        path = _make_valid_gff(tmp_path)
        _corrupt_header_dword(path, _LABEL_COUNT, 4_294_902_017)
        _assert_fails_fast(path, "labels")

    def test_huge_field_count_fails_fast(self, tmp_path):
        """The busy-spin scenario: tens of millions of declared fields."""
        path = _make_valid_gff(tmp_path)
        _corrupt_header_dword(path, _FIELD_COUNT, 84_279_808)
        _assert_fails_fast(path, "fields")

    def test_huge_struct_count_fails_fast(self, tmp_path):
        path = _make_valid_gff(tmp_path)
        _corrupt_header_dword(path, _STRUCT_COUNT, 503_513_662)
        _assert_fails_fast(path, "structs")

    def test_fielddata_block_past_eof_fails(self, tmp_path):
        path = _make_valid_gff(tmp_path)
        _corrupt_header_dword(path, _FIELDDATA_SIZE, 1_000_000)
        _assert_fails_fast(path, "field data")

    def test_truncated_file_fails(self, tmp_path):
        """A file cut in half no longer contains its declared blocks."""
        path = _make_valid_gff(tmp_path)
        data = path.read_bytes()
        truncated = tmp_path / "truncated.uti"
        truncated.write_bytes(data[: max(160, len(data) // 2)])
        with pytest.raises(GFFParseError) as exc_info:
            parse_gff(truncated)
        assert "outside the file" in str(exc_info.value)

    def test_real_garbage_header_rejected(self, tmp_path):
        """A non-GFF payload (NCS-like magic + junk) is rejected, not parsed."""
        garbage = tmp_path / "script.ncs"
        garbage.write_bytes(b"NCS V1.0B" + bytes(range(256)) * 4)
        with pytest.raises(GFFParseError):
            parse_gff(garbage)


class TestReadGffErrors:
    """read_gff() names the file in every failure; these texts reach run statistics."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(GFFParseError, match="File not found: .*absent.uti"):
            read_gff(tmp_path / "absent.uti")

    def test_corrupt_file(self, tmp_path):
        path = _make_valid_gff(tmp_path)
        _corrupt_header_dword(path, _LABEL_COUNT, 4_294_902_017)
        with pytest.raises(GFFParseError) as exc_info:
            read_gff(path)
        message = str(exc_info.value)
        assert message.startswith(f"Failed to parse GFF file {path.resolve()}: ")
        assert "labels block outside the file" in message

    def test_cache_returns_the_same_object(self, tmp_path):
        path = _make_valid_gff(tmp_path)
        cache = {}
        first = read_gff(path, cache=cache)
        assert read_gff(path, cache=cache) is first
        assert list(cache) == [path.resolve()]
