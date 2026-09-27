"""Byte-patching CExoLocString fields of GFF files."""

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

_FIELDS = {
    "StructType": "UTC",
    "FirstName": {"StrRef": -1, "Value": "aaa"},
    "LastName": {"StrRef": -1, "Value": "bbb"},
    "Description": {"StrRef": -1, "Value": "ccc"},
}


def _patch(path, patches, encoding="cp1251"):
    GFFPatcher(path, text_encoding=encoding).patch_multiple(patches)


def _one_field(path, label="FirstName", value="Hero", strref=-1, struct_type="UTC"):
    """Write a GFF with one locstring field; return its record offset."""
    write_gff(path, {"StructType": struct_type, label: {"StrRef": strref, "Value": value}})
    return read_gff(path)["_record_offsets"][label]


def _three_patches(path):
    write_gff(path, dict(_FIELDS))
    offsets = read_gff(path)["_record_offsets"]
    return [
        (offsets["FirstName"], "Первый"),
        (offsets["LastName"], "Второй"),
        (offsets["Description"], "Длинное описание для сдвига блоков"),
    ]


def _substrings(path, record_offset):
    """Return ``(str_ref, [(language_id, raw_text)])`` of the field at *record_offset*."""
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
    """Replace the locstring payload at *record_offset* with ``[(language_id, text)]``.

    The payload (cp1252 text) is appended to the field data block the way the
    patcher does it, so the file is a valid GFF with a multi-substring field.
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


def test_one_batched_splice_equals_patching_field_by_field(tmp_path):
    batch_path = tmp_path / "batch.utc"
    _patch(batch_path, _three_patches(batch_path))
    seq_path = tmp_path / "seq.utc"
    for patch in _three_patches(seq_path):
        _patch(seq_path, [patch])

    assert batch_path.read_bytes() == seq_path.read_bytes()
    parsed = read_gff(batch_path)
    assert [parsed[label]["Value"] for label in ("FirstName", "LastName", "Description")] == [
        "Первый",
        "Второй",
        "Длинное описание для сдвига блоков",
    ]


def test_payload_is_appended_with_strref_minus_one_and_language_zero(tmp_path):
    path = tmp_path / "bytes.utc"
    offset = _one_field(path, strref=7)
    before = path.read_bytes()
    header = GFFHeader.read(before)

    _patch(path, [(offset, "Герой")])

    after = path.read_bytes()
    payload = struct.pack("<IiIII", 16 + 5, -1, 1, 0, 5) + "Герой".encode("cp1251")
    insert_at = header.field_data_offset + header.field_data_size
    assert after[insert_at : insert_at + len(payload)] == payload
    assert len(after) == len(before) + len(payload)
    assert struct.unpack_from("<I", after, offset + 8)[0] == header.field_data_size
    assert GFFHeader.read(after).field_data_size == header.field_data_size + len(payload)


def test_empty_text_writes_no_substring(tmp_path):
    path = tmp_path / "empty.utc"
    offset = _one_field(path)
    _patch(path, [(offset, "")])
    assert _substrings(path, offset) == (-1, [])
    assert read_gff(path)["FirstName"] == {"StrRef": -1, "Value": ""}


def test_two_patches_of_one_field_leave_the_last_text(tmp_path):
    path = tmp_path / "dup.utc"
    offset = _one_field(path, value="stub")
    _patch(path, [(offset, "Первый"), (offset, "Второй")])
    assert read_gff(path)["FirstName"]["Value"] == "Второй"


def test_invalid_offset_anywhere_in_the_batch_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "abort.utc"
    patches = _three_patches(path)
    before = path.read_bytes()
    with pytest.raises(GFFPatchError):
        _patch(path, [patches[0], (0, "bad"), patches[1]])
    assert path.read_bytes() == before


def test_invalid_arguments_fail_with_explicit_messages(tmp_path):
    path = tmp_path / "a.utc"
    write_gff(path, {"StructType": "UTC", "Tag": "a"})
    with pytest.raises(GFFPatchError, match="Unsupported module text encoding: 'utf-8'"):
        GFFPatcher(path, text_encoding="utf-8")
    with pytest.raises(GFFPatchError, match="File not found"):
        GFFPatcher(tmp_path / "absent.utc")
    tiny = tmp_path / "tiny.utc"
    tiny.write_bytes(b"UTC V3.2")
    with pytest.raises(GFFPatchError, match="too small"):
        GFFPatcher(tiny).patch_multiple([(56, "x")])


def test_locstring_inside_a_direct_struct_field_is_patchable(tmp_path):
    path = tmp_path / "wrapper.uti"
    write_gff(
        path,
        {
            "StructType": "UTI",
            "Tag": "outer_tag",
            "Wrapper": {"LocalizedName": {"StrRef": -1, "Value": "Ancient Blade"}, "Charges": 3},
        },
    )
    offset = read_gff(path)["Wrapper"]["_record_offsets"]["LocalizedName"]

    _patch(path, [(offset, "Древний клинок")])

    reread = read_gff(path)
    assert reread["Wrapper"]["LocalizedName"]["Value"] == "Древний клинок"
    assert (reread["Wrapper"]["Charges"], reread["Tag"], reread["StructType"]) == (
        3,
        "outer_tag",
        "UTI",
    )


# ---------------------------------------------------------------------------
# Multi-substring fields collapse to one language-0 substring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "struct_type, label, variants, first_value",
    [
        # Male (id 0) and female (id 1) variants of a greeting.
        ("UTC", "FirstName", [(0, "Bienvenu"), (1, "Bienvenue")], "Bienvenu"),
        # The LES LIONS pattern: ids 0/2/3 with near-identical text.
        (
            "GIT",
            "LocName",
            [(0, "Pile de Livres"), (2, "Pile de Livres"), (3, "Pile de livres")],
            "Pile de Livres",
        ),
    ],
)
def test_multi_substring_field_collapses_with_a_warning(
    tmp_path, caplog, struct_type, label, variants, first_value
):
    path = tmp_path / f"gendered.{struct_type.lower()}"
    offset = _one_field(path, label=label, value="stub", struct_type=struct_type)
    _splice_substrings(path, offset, variants)
    assert [(lid, raw.decode("cp1252")) for lid, raw in _substrings(path, offset)[1]] == variants
    # The parser surfaces the first non-empty substring.
    assert read_gff(path)[label]["Value"] == first_value

    with caplog.at_level(logging.WARNING):
        _patch(path, [(offset, "Привет")])

    assert f"overwriting {len(variants)} substrings" in caplog.text
    assert path.name in caplog.text
    _str_ref, subs = _substrings(path, offset)
    assert [(lid, raw.decode("cp1251")) for lid, raw in subs] == [(0, "Привет")]
    assert read_gff(path)[label]["Value"] == "Привет"


def test_single_substring_patch_does_not_warn(tmp_path, caplog):
    path = tmp_path / "plain.utc"
    offset = _one_field(path)
    with caplog.at_level(logging.WARNING):
        _patch(path, [(offset, "Герой")])
    assert "substrings" not in caplog.text
    assert [(lid, raw.decode("cp1251")) for lid, raw in _substrings(path, offset)[1]] == [
        (0, "Герой")
    ]
