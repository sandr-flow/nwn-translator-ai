import logging
import struct

import pytest

from nwn_translator.formats.gff import (
    HEADER,
    LOCSTRING_HEAD,
    SUBSTRING_HEAD,
    GFFHeader,
    GFFPatcher,
    GFFPatchError,
    read_gff,
)
from tests.support.gff_writer import write_gff


def _read_locstring_payload(path, record_offset):
    """Return (str_ref, [(language_id, text_bytes)]) for the field at *record_offset*."""
    data = path.read_bytes()
    data_or_offset = struct.unpack_from("<I", data, record_offset + 8)[0]
    off = GFFHeader.read(data).field_data_offset + data_or_offset
    _total_size, str_ref, count = LOCSTRING_HEAD.unpack_from(data, off)
    subs = []
    p = off + LOCSTRING_HEAD.size
    for _ in range(count):
        lang_id, length = SUBSTRING_HEAD.unpack_from(data, p)
        subs.append((lang_id, data[p + 8 : p + 8 + length]))
        p += 8 + length
    return str_ref, subs


def _splice_substrings(path, record_offset, substrings, str_ref=-1):
    """Replace the locstring payload at *record_offset* with *substrings*.

    ``substrings`` is ``[(language_id, text)]``; text is encoded as cp1252.
    The payload is appended to the field data block the way the patcher does
    it, so the result is a structurally valid GFF with a genuinely
    multi-substring field.
    """
    encoded = [(lang_id, text.encode("cp1252")) for lang_id, text in substrings]
    payload = LOCSTRING_HEAD.pack(
        8 + sum(8 + len(raw) for _, raw in encoded), str_ref, len(encoded)
    )
    payload += b"".join(SUBSTRING_HEAD.pack(lang_id, len(raw)) + raw for lang_id, raw in encoded)

    data = bytearray(path.read_bytes())
    header = GFFHeader.read(data)
    insert_at = header.field_data_offset + header.field_data_size
    struct.pack_into("<I", data, record_offset + 8, header.field_data_size)
    data[insert_at:insert_at] = payload
    updates = {"field_data_size": header.field_data_size + len(payload)}
    if header.field_indices_size:
        updates["field_indices_offset"] = header.field_indices_offset + len(payload)
    if header.list_indices_size:
        updates["list_indices_offset"] = header.list_indices_offset + len(payload)
    HEADER.pack_into(data, 0, *header._replace(**updates))
    path.write_bytes(bytes(data))


class TestPatchMultipleSingleSplice:
    """The batched splice must be equivalent to applying patches one by one."""

    _FIELDS = {
        "StructType": "UTC",
        "FirstName": {"StrRef": -1, "Value": "aaa"},
        "LastName": {"StrRef": -1, "Value": "bbb"},
        "Description": {"StrRef": -1, "Value": "ccc"},
    }

    def _make_patches(self, path):
        write_gff(path, dict(self._FIELDS))
        offsets = read_gff(path)["_record_offsets"]
        return [
            (offsets["FirstName"], "Первый"),
            (offsets["LastName"], "Второй"),
            (offsets["Description"], "Длинное описание для сдвига блоков"),
        ]

    def test_batch_matches_sequential(self, tmp_path):
        """One patch_multiple call and N single calls produce identical bytes."""
        batch_path = tmp_path / "batch.utc"
        patches = self._make_patches(batch_path)
        GFFPatcher(batch_path, text_encoding="cp1251").patch_multiple(patches)

        seq_path = tmp_path / "seq.utc"
        for record_offset, text in self._make_patches(seq_path):
            GFFPatcher(seq_path, text_encoding="cp1251").patch_multiple([(record_offset, text)])

        assert batch_path.read_bytes() == seq_path.read_bytes()

    def test_all_fields_read_back_translated(self, tmp_path):
        """Every patched field yields its new text through the parser."""
        path = tmp_path / "multi.utc"
        patches = self._make_patches(path)
        GFFPatcher(path, text_encoding="cp1251").patch_multiple(patches)

        parsed = read_gff(path)
        assert parsed["FirstName"]["Value"] == "Первый"
        assert parsed["LastName"]["Value"] == "Второй"
        assert parsed["Description"]["Value"] == "Длинное описание для сдвига блоков"

    def test_payload_bytes(self, tmp_path):
        """The payload is appended at the old field data end with StrRef -1 and LanguageID 0."""
        path = tmp_path / "bytes.utc"
        write_gff(path, {"StructType": "UTC", "FirstName": {"StrRef": 7, "Value": "Hero"}})
        offset = read_gff(path)["_record_offsets"]["FirstName"]
        before = path.read_bytes()
        header = GFFHeader.read(before)

        GFFPatcher(path, text_encoding="cp1251").patch_multiple([(offset, "Герой")])

        after = path.read_bytes()
        payload = struct.pack("<IiIII", 16 + 5, -1, 1, 0, 5) + "Герой".encode("cp1251")
        insert_at = header.field_data_offset + header.field_data_size
        assert after[insert_at : insert_at + len(payload)] == payload
        assert len(after) == len(before) + len(payload)
        assert struct.unpack_from("<I", after, offset + 8)[0] == header.field_data_size
        assert GFFHeader.read(after).field_data_size == header.field_data_size + len(payload)

    def test_empty_text_writes_no_substring(self, tmp_path):
        path = tmp_path / "empty.utc"
        write_gff(path, {"StructType": "UTC", "FirstName": {"StrRef": -1, "Value": "Hero"}})
        offset = read_gff(path)["_record_offsets"]["FirstName"]

        GFFPatcher(path, text_encoding="cp1251").patch_multiple([(offset, "")])

        assert _read_locstring_payload(path, offset) == (-1, [])
        assert read_gff(path)["FirstName"] == {"StrRef": -1, "Value": ""}

    def test_duplicate_offset_last_wins(self, tmp_path):
        """Two patches on one field leave the last text visible."""
        path = tmp_path / "dup.utc"
        write_gff(path, {"StructType": "UTC", "FirstName": {"StrRef": -1, "Value": "stub"}})
        offset = read_gff(path)["_record_offsets"]["FirstName"]

        GFFPatcher(path, text_encoding="cp1251").patch_multiple(
            [(offset, "Первый"), (offset, "Второй")]
        )
        assert read_gff(path)["FirstName"]["Value"] == "Второй"

    def test_invalid_offset_leaves_file_untouched(self, tmp_path):
        """A bad offset anywhere in the batch aborts without writing."""
        path = tmp_path / "abort.utc"
        patches = self._make_patches(path)
        before = path.read_bytes()

        with pytest.raises(GFFPatchError):
            GFFPatcher(path, text_encoding="cp1251").patch_multiple(
                [patches[0], (0, "bad"), patches[1]]
            )
        assert path.read_bytes() == before


class TestPatcherValidation:
    """Invalid arguments fail with explicit messages."""

    def test_unsupported_encoding(self, tmp_path):
        path = tmp_path / "a.utc"
        write_gff(path, {"StructType": "UTC", "Tag": "a"})
        with pytest.raises(GFFPatchError, match="Unsupported module text encoding: 'utf-8'"):
            GFFPatcher(path, text_encoding="utf-8")

    def test_missing_file(self, tmp_path):
        with pytest.raises(GFFPatchError, match="File not found"):
            GFFPatcher(tmp_path / "absent.utc")

    def test_file_shorter_than_header(self, tmp_path):
        path = tmp_path / "tiny.utc"
        path.write_bytes(b"UTC V3.2")
        with pytest.raises(GFFPatchError, match="too small"):
            GFFPatcher(path).patch_multiple([(56, "x")])


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
            GFFPatcher(path, text_encoding="cp1251").patch_multiple([(offset, "Привет")])

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
            GFFPatcher(path, text_encoding="cp1251").patch_multiple([(offset, "Герой")])

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
            GFFPatcher(path, text_encoding="cp1251").patch_multiple([(offset, "Стопка книг")])

        assert "overwriting 3 substrings" in caplog.text
        _str_ref, subs = _read_locstring_payload(path, offset)
        assert len(subs) == 1
        assert subs[0][0] == 0
        assert read_gff(path)["LocName"]["Value"] == "Стопка книг"
