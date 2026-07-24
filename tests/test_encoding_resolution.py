"""Source-language encoding hint drives module string decoding (GFF and NCS)."""

import struct
from pathlib import Path

from nwn_translator.config import source_string_encoding
from nwn_translator.file_handlers.gff_handler import read_gff, write_gff
from nwn_translator.file_handlers.gff_parser import decode_module_text
from nwn_translator.file_handlers.gff_patcher import GFFPatcher
from nwn_translator.file_handlers.ncs_parser import (
    NCS_HEADER,
    OP_CONST,
    OP_RETN,
    TYPE_STRING,
    parse_ncs,
    parse_ncs_bytes,
)
from nwn_translator.file_handlers.ncs_patcher import patch_ncs_string_replacements


class TestSourceStringEncoding:
    """Language slug -> declared read-side code page."""

    def test_known_languages(self) -> None:
        assert source_string_encoding("russian") == "cp1251"
        assert source_string_encoding("French") == "cp1252"
        assert source_string_encoding("german") == "cp1252"
        assert source_string_encoding("polish") == "cp1250"

    def test_auto_and_empty_mean_detect(self) -> None:
        assert source_string_encoding("auto") is None
        assert source_string_encoding("AUTO") is None
        assert source_string_encoding("") is None
        assert source_string_encoding(None) is None

    def test_unknown_language_means_detect(self) -> None:
        assert source_string_encoding("klingon") is None


class TestDecodeModuleText:
    """Shared byte-decoding rule for GFF and NCS string payloads."""

    def test_empty(self) -> None:
        assert decode_module_text(b"") == ""
        assert decode_module_text(b"", "cp1252") == ""

    def test_ascii_unaffected_by_hint(self) -> None:
        assert decode_module_text(b"Hello", None) == "Hello"
        assert decode_module_text(b"Hello", "cp1251") == "Hello"

    def test_valid_utf8_wins_over_hint(self) -> None:
        raw = "Привет".encode("utf-8")
        assert decode_module_text(raw, "cp1252") == "Привет"
        assert decode_module_text(raw, None) == "Привет"

    def test_declared_cp1252_not_mojibake(self) -> None:
        # Regression: 0xE9 is "é" in cp1252 but "й" in cp1251; the legacy
        # cascade tries cp1251 first, so only the hint yields correct text.
        raw = "Bonjour, étranger".encode("cp1252")
        assert decode_module_text(raw, "cp1252") == "Bonjour, étranger"
        assert decode_module_text(raw, None) == "Bonjour, йtranger"

    def test_declared_cp1251(self) -> None:
        raw = "Привет".encode("cp1251")
        assert decode_module_text(raw, "cp1251") == "Привет"

    def test_auto_cascade_prefers_cp1251(self) -> None:
        raw = "Привет".encode("cp1251")
        assert decode_module_text(raw, None) == "Привет"

    def test_bad_hint_falls_back_to_cascade(self) -> None:
        raw = "Привет".encode("cp1251")
        assert decode_module_text(raw, "no-such-codec") == "Привет"


class TestGFFReadWithSourceEncoding:
    """File-level: parser threads the hint down to CExoLocString decoding."""

    def _make_cp1252_file(self, tmp_path: Path) -> Path:
        gff_path = tmp_path / "sample.utp"
        write_gff(gff_path, {"LocalizedName": {"StrRef": -1, "Value": "Placeholder"}})
        parsed = read_gff(gff_path)
        record_offset = parsed["_record_offsets"]["LocalizedName"]
        patcher = GFFPatcher(gff_path, text_encoding="cp1252")
        patcher.patch_local_string(record_offset, "Bonjour, étranger")
        return gff_path

    def test_cp1252_field_reads_correctly_with_hint(self, tmp_path: Path) -> None:
        gff_path = self._make_cp1252_file(tmp_path)
        parsed = read_gff(gff_path, source_encoding="cp1252")
        assert parsed["LocalizedName"]["Value"] == "Bonjour, étranger"

    def test_cp1252_field_is_mojibake_without_hint(self, tmp_path: Path) -> None:
        # Documents the legacy detection behaviour the hint exists to fix.
        gff_path = self._make_cp1252_file(tmp_path)
        parsed = read_gff(gff_path)
        assert parsed["LocalizedName"]["Value"] == "Bonjour, йtranger"


def _consts(text: str, encoding: str) -> bytes:
    encoded = text.encode(encoding)
    return struct.pack(">BB", OP_CONST, TYPE_STRING) + struct.pack(">H", len(encoded)) + encoded


def _retn() -> bytes:
    return struct.pack(">BB", OP_RETN, 0x00)


class TestNCSReadWithSourceEncoding:
    """NCS string constants use the same decoding rule as GFF strings."""

    def test_cp1251_consts_with_hint(self) -> None:
        raw = NCS_HEADER + _consts("Привет, путник", "cp1251") + _retn()
        ncs = parse_ncs_bytes(raw, source_encoding="cp1251")
        assert ncs.string_constants[0].string_value == "Привет, путник"

    def test_cp1252_consts_with_hint(self) -> None:
        raw = NCS_HEADER + _consts("Bonjour, étranger", "cp1252") + _retn()
        ncs = parse_ncs_bytes(raw, source_encoding="cp1252")
        assert ncs.string_constants[0].string_value == "Bonjour, étranger"

    def test_default_matches_gff_cascade(self) -> None:
        # Without a hint the shared cascade applies (cp1251 before cp1252),
        # replacing the old NCS-only hardcoded cp1252 rule.
        raw = NCS_HEADER + _consts("Привет", "cp1251") + _retn()
        ncs = parse_ncs_bytes(raw)
        assert ncs.string_constants[0].string_value == "Привет"

    def test_patch_matches_original_via_source_encoding(self, tmp_path: Path) -> None:
        """Extract-then-patch round trip for a cp1252 source module."""
        original = "Bonjour, étranger"
        path = tmp_path / "greet.ncs"
        path.write_bytes(NCS_HEADER + _consts(original, "cp1252") + _retn())

        ncs = parse_ncs(path, source_encoding="cp1252")
        instr = ncs.string_constants[0]
        patched = patch_ncs_string_replacements(
            path,
            [(instr.offset, instr.string_value, "Привет, странник")],
            text_encoding="cp1251",
            source_encoding="cp1252",
        )
        assert patched == 1

        result = parse_ncs(path, source_encoding="cp1251")
        assert result.string_constants[0].string_value == "Привет, странник"
