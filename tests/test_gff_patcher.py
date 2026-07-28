import logging
import struct

from src.nwn_translator.file_handlers.gff_handler import read_gff, write_gff
from src.nwn_translator.file_handlers.gff_patcher import (
    GFFPatcher,
    sanitize_for_module_encoding,
)


def test_sanitize_replaces_unicode_dashes_for_cp1251():
    text = "модуль — для этого не требуется"
    sanitized = sanitize_for_module_encoding(text, "cp1251")
    assert sanitized == "модуль - для этого не требуется"


def test_sanitize_replaces_en_dash_for_cp1251():
    text = "1–2 игрока"
    sanitized = sanitize_for_module_encoding(text, "cp1251")
    assert sanitized == "1-2 игрока"


def _read_locstring_payload(path, record_offset):
    """Return (str_ref, [(language_id, text_bytes)]) for the field at *record_offset*."""
    data = path.read_bytes()
    fielddata_offset = struct.unpack_from("<I", data, 32)[0]
    data_or_offset = struct.unpack_from("<I", data, record_offset + 8)[0]
    off = fielddata_offset + data_or_offset
    str_ref = struct.unpack_from("<i", data, off + 4)[0]
    count = struct.unpack_from("<I", data, off + 8)[0]
    subs = []
    p = off + 12
    for _ in range(count):
        lang_id = struct.unpack_from("<I", data, p)[0]
        length = struct.unpack_from("<I", data, p + 4)[0]
        subs.append((lang_id, data[p + 8 : p + 8 + length]))
        p += 8 + length
    return str_ref, subs


def _splice_substrings(path, record_offset, substrings, str_ref=-1):
    """Replace the locstring payload at *record_offset* with *substrings*.

    ``substrings`` is ``[(language_id, text)]``; text is encoded as cp1252.
    Reuses the patcher's insert-at-end-of-FieldData mechanics so the result is
    a structurally valid GFF with a genuinely multi-substring field.
    """
    encoded = [(lang_id, text.encode("cp1252")) for lang_id, text in substrings]
    total_size = 4 + 4 + sum(8 + len(raw) for _, raw in encoded)
    payload = bytearray()
    payload += struct.pack("<I", total_size)
    payload += struct.pack("<i", str_ref)
    payload += struct.pack("<I", len(encoded))
    for lang_id, raw in encoded:
        payload += struct.pack("<I", lang_id)
        payload += struct.pack("<I", len(raw))
        payload += raw

    patcher = GFFPatcher(path)
    data = bytearray(path.read_bytes())
    new_data = patcher._apply_payload_at_fielddata_end(data, record_offset, payload)
    path.write_bytes(new_data)


class TestMultiSubstringCollapse:
    """Patching a multi-substring CExoLocString collapses it with a warning."""

    @staticmethod
    def _make_gendered_file(tmp_path):
        """A .utc-like GFF whose FirstName has male (id 0) and female (id 1) variants."""
        path = tmp_path / "gendered.utc"
        write_gff(path, {"StructType": "UTC", "FirstName": {"StrRef": -1, "Value": "stub"}})
        parsed = read_gff(path)
        offset = parsed["_record_offsets"]["FirstName"]
        _splice_substrings(path, offset, [(0, "Bienvenu"), (1, "Bienvenue")])
        return path, offset

    def test_fixture_is_genuinely_multi_substring(self, tmp_path):
        path, offset = self._make_gendered_file(tmp_path)
        _str_ref, subs = _read_locstring_payload(path, offset)
        assert [(lid, raw.decode("cp1252")) for lid, raw in subs] == [
            (0, "Bienvenu"),
            (1, "Bienvenue"),
        ]
        # The parser surfaces the first non-empty substring (the male variant).
        assert read_gff(path)["FirstName"]["Value"] == "Bienvenu"

    def test_patch_collapses_to_single_id0_substring_with_warning(self, tmp_path, caplog):
        path, offset = self._make_gendered_file(tmp_path)

        with caplog.at_level(logging.WARNING):
            GFFPatcher(path, text_encoding="cp1251").patch_local_string(offset, "Привет")

        assert "overwriting 2 substrings" in caplog.text
        assert "gendered.utc" in caplog.text
        _str_ref, subs = _read_locstring_payload(path, offset)
        assert len(subs) == 1
        assert subs[0][0] == 0
        assert subs[0][1].decode("cp1251") == "Привет"
        assert read_gff(path)["FirstName"]["Value"] == "Привет"

    def test_single_substring_patch_does_not_warn(self, tmp_path, caplog):
        path = tmp_path / "plain.utc"
        write_gff(path, {"StructType": "UTC", "FirstName": {"StrRef": -1, "Value": "Hero"}})
        offset = read_gff(path)["_record_offsets"]["FirstName"]

        with caplog.at_level(logging.WARNING):
            GFFPatcher(path, text_encoding="cp1251").patch_local_string(offset, "Герой")

        assert "substrings" not in caplog.text
        _str_ref, subs = _read_locstring_payload(path, offset)
        assert [(lid, raw.decode("cp1251")) for lid, raw in subs] == [(0, "Герой")]

    def test_three_language_variants_also_collapse(self, tmp_path, caplog):
        """The LES LIONS pattern: ids 0/2/3 with near-identical text."""
        path = tmp_path / "lions.git"
        write_gff(path, {"StructType": "GIT", "LocName": {"StrRef": -1, "Value": "stub"}})
        offset = read_gff(path)["_record_offsets"]["LocName"]
        _splice_substrings(
            path,
            offset,
            [(0, "Pile de Livres"), (2, "Pile de Livres"), (3, "Pile de livres")],
        )

        with caplog.at_level(logging.WARNING):
            GFFPatcher(path, text_encoding="cp1251").patch_local_string(offset, "Стопка книг")

        assert "overwriting 3 substrings" in caplog.text
        _str_ref, subs = _read_locstring_payload(path, offset)
        assert len(subs) == 1
        assert subs[0][0] == 0
        assert read_gff(path)["LocName"]["Value"] == "Стопка книг"
