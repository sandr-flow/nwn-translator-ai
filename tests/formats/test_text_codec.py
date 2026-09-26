"""Module text decoding and encoding shared by GFF and NCS strings."""

import pytest

from nwn_translator.formats.gff import GFFPatcher, read_gff
from nwn_translator.formats.ncs import parse_ncs, parse_ncs_bytes, patch_ncs_string_replacements
from nwn_translator.formats.text_codec import (
    decode_fixed_ascii,
    decode_module_text,
    encode_module_text,
)
from tests.support.gff_writer import write_gff
from tests.support.ncs import consts, retn, script

FRENCH = "Bonjour, étranger"
# 0xE9 is "é" in cp1252 but "й" in cp1251: the legacy cascade tries cp1251 first.
FRENCH_MOJIBAKE = "Bonjour, йtranger"


@pytest.mark.parametrize(
    "raw, hint, text",
    [
        (b"", None, ""),
        (b"", "cp1252", ""),
        (b"Hello", None, "Hello"),
        (b"Hello", "cp1251", "Hello"),
        # Valid UTF-8 wins over the hint.
        ("Привет".encode("utf-8"), "cp1252", "Привет"),
        ("Привет".encode("utf-8"), None, "Привет"),
        (FRENCH.encode("cp1252"), "cp1252", FRENCH),
        (FRENCH.encode("cp1252"), None, FRENCH_MOJIBAKE),
        ("Привет".encode("cp1251"), "cp1251", "Привет"),
        # Without a hint the cascade prefers cp1251; a bad hint falls back to it.
        ("Привет".encode("cp1251"), None, "Привет"),
        ("Привет".encode("cp1251"), "no-such-codec", "Привет"),
        # 0x98 is undefined in cp1251, 0x81 in cp1252: latin-1 is the last resort.
        (b"\x98\x81", None, "\x98\x81"),
    ],
)
def test_decode_module_text(raw, hint, text):
    assert decode_module_text(raw, hint) == text


def test_fixed_ascii_stops_at_the_first_nul_and_drops_non_ascii():
    assert decode_fixed_ascii(b"na\xefme\x00junk\x00\x00") == "name"


@pytest.mark.parametrize(
    "text, encoding, raw",
    [
        ("модуль — для этого", "cp1251", "модуль - для этого".encode("cp1251")),
        ("1–2 игрока", "cp1251", "1-2 игрока".encode("cp1251")),
        # Romanian comma-below letters become their cedilla forms on cp1250.
        ("Știință", "cp1250", "Ştiinţă".encode("cp1250")),
        # Characters the code page cannot encode are dropped.
        ("Привет 😀 мир", "cp1251", "Привет  мир".encode("cp1251")),
        ("Ωmega", "cp1252", b"mega"),
    ],
)
def test_encode_module_text(text, encoding, raw):
    assert encode_module_text(text, encoding) == raw


def test_gff_reader_threads_the_hint_down_to_locstrings(tmp_path):
    path = tmp_path / "sample.utp"
    write_gff(path, {"LocalizedName": {"StrRef": -1, "Value": "Placeholder"}})
    offset = read_gff(path)["_record_offsets"]["LocalizedName"]
    GFFPatcher(path, text_encoding="cp1252").patch_multiple([(offset, FRENCH)])

    assert read_gff(path, source_encoding="cp1252")["LocalizedName"]["Value"] == FRENCH
    # The legacy detection the hint exists to fix.
    assert read_gff(path)["LocalizedName"]["Value"] == FRENCH_MOJIBAKE


@pytest.mark.parametrize(
    "text, encoding, hint",
    [
        ("Привет, путник", "cp1251", "cp1251"),
        (FRENCH, "cp1252", "cp1252"),
        # Without a hint NCS strings follow the GFF cascade (cp1251 before cp1252).
        ("Привет", "cp1251", None),
    ],
)
def test_ncs_constants_use_the_same_decoding(text, encoding, hint):
    ncs = parse_ncs_bytes(script(consts(text, encoding), retn()), source_encoding=hint)
    assert ncs.string_constants[0].string_value == text


def test_ncs_patch_matches_the_original_through_the_source_encoding(tmp_path):
    path = tmp_path / "greet.ncs"
    path.write_bytes(script(consts(FRENCH, "cp1252"), retn()))
    instr = parse_ncs(path, source_encoding="cp1252").string_constants[0]

    patched = patch_ncs_string_replacements(
        path,
        [(instr.offset, instr.string_value, "Привет, странник")],
        text_encoding="cp1251",
        source_encoding="cp1252",
    )

    assert patched == 1
    result = parse_ncs(path, source_encoding="cp1251")
    assert result.string_constants[0].string_value == "Привет, странник"
