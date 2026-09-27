"""GFF parsing: header validation, nested structs and lists, and fixture round trips.

Fixtures are written by the test-only :mod:`tests.support.gff_writer`, so these
tests also pin that the writer produces what the parser reads back.
"""

import struct
import time

import pytest

from nwn_translator.formats.gff import (
    HEADER,
    GFFFile,
    GFFHeader,
    GFFParseError,
    GFFStruct,
    GFFType,
    GFFValue,
    _expand_struct,
    parse_gff,
    read_gff,
)
from tests.support.gff_writer import loc, write_gff, write_gff_bytes

# Header DWORD offsets (GFF v3.2).
_STRUCT_COUNT, _FIELD_COUNT, _LABEL_COUNT, _FIELDDATA_SIZE = 12, 20, 28, 36


def _item_gff(path):
    write_gff(
        path,
        {
            "StructType": "UTI",
            "Tag": "some_item",
            "LocalizedName": loc("Plain Dagger"),
            "Charges": 3,
        },
    )
    return path


def _set_dword(path, offset, value):
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, offset, value)
    path.write_bytes(data)


def _roundtrip(tmp_path, data: dict) -> dict:
    path = tmp_path / "roundtrip.gff"
    write_gff(path, data)
    return read_gff(path)


def _fields_only(value):
    """Drop parser metadata (``_field_types``, ``_record_offsets``) recursively."""
    if isinstance(value, dict):
        return {k: _fields_only(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_fields_only(v) for v in value]
    return value


def _dialog_graph() -> dict:
    """Branching dialog: link lists on every node, some of them empty."""
    return {
        "StructType": "DLG",
        "StartingList": [{"Index": 0}, {"Index": 2}],
        "EntryList": [
            {
                "Text": loc("Hello."),
                "Speaker": "",
                "RepliesList": [{"Index": 0, "IsChild": 0}, {"Index": 1, "IsChild": 0}],
            },
            {"Text": loc("Farewell."), "Speaker": "bob_tag", "RepliesList": []},
            {"Text": loc("Back again?"), "Speaker": "", "RepliesList": [{"Index": 1}]},
        ],
        "ReplyList": [
            {"Text": loc("Who are you?"), "EntriesList": [{"Index": 1, "IsChild": 0}]},
            {"Text": loc("Bye."), "EntriesList": []},
        ],
    }


# ---------------------------------------------------------------------------
# Header blocks must fit the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset, value, block",
    [
        (_LABEL_COUNT, 4_294_902_017, "labels"),  # billions of labels: the OOM scenario
        (_FIELD_COUNT, 84_279_808, "fields"),  # millions of fields: the busy-spin scenario
        (_STRUCT_COUNT, 503_513_662, "structs"),
        (_FIELDDATA_SIZE, 1_000_000, "field data"),
    ],
)
def test_header_block_outside_the_file_fails_fast(tmp_path, offset, value, block):
    path = _item_gff(tmp_path / "victim.uti")
    _set_dword(path, offset, value)
    started = time.monotonic()
    with pytest.raises(GFFParseError) as exc_info:
        parse_gff(path)
    assert block in str(exc_info.value)
    assert "outside the file" in str(exc_info.value)
    # No per-declared-element work happens at all.
    assert time.monotonic() - started < 1.0


def test_truncated_or_foreign_files_are_rejected(tmp_path):
    data = _item_gff(tmp_path / "victim.uti").read_bytes()
    truncated = tmp_path / "truncated.uti"
    truncated.write_bytes(data[: max(160, len(data) // 2)])
    with pytest.raises(GFFParseError, match="outside the file"):
        parse_gff(truncated)
    garbage = tmp_path / "script.ncs"
    garbage.write_bytes(b"NCS V1.0B" + bytes(range(256)) * 4)
    with pytest.raises(GFFParseError):
        parse_gff(garbage)
    stub = tmp_path / "stub.uti"
    stub.write_bytes(b"UTI V3.2" + bytes(40))
    with pytest.raises(GFFParseError, match="File too small to be valid GFF"):
        parse_gff(stub)


def test_read_gff_names_the_file_in_every_failure(tmp_path):
    """These texts reach the run statistics."""
    with pytest.raises(GFFParseError, match="File not found: .*absent.uti"):
        read_gff(tmp_path / "absent.uti")
    path = _item_gff(tmp_path / "victim.uti")
    _set_dword(path, _LABEL_COUNT, 4_294_902_017)
    with pytest.raises(GFFParseError) as exc_info:
        read_gff(path)
    message = str(exc_info.value)
    assert message.startswith(f"Failed to parse GFF file {path.resolve()}: ")
    assert "labels block outside the file" in message


def test_read_gff_cache_returns_the_same_object(tmp_path):
    path = _item_gff(tmp_path / "victim.uti")
    cache = {}
    first = read_gff(path, cache=cache)
    assert read_gff(path, cache=cache) is first
    assert first["LocalizedName"]["Value"] == "Plain Dagger"
    assert list(cache) == [path.resolve()]


def test_gff_smaller_than_160_bytes_parses(tmp_path):
    """The header is 56 bytes; nothing requires the fixture writer's padding after it."""
    path = tmp_path / "tiny.uti"
    write_gff(path, {"StructType": "UTI", "Tag": "x", "LocalizedName": {"StrRef": 5}})
    padded = read_gff(path)
    data = path.read_bytes()
    header = GFFHeader.read(data)
    pad = 160 - HEADER.size
    compact = header._replace(
        struct_offset=header.struct_offset - pad,
        field_offset=header.field_offset - pad,
        label_offset=header.label_offset - pad,
        field_data_offset=header.field_data_offset - pad,
        field_indices_offset=header.field_indices_offset - pad,
        list_indices_offset=header.list_indices_offset - pad,
    )
    path.write_bytes(HEADER.pack(*compact) + data[160:])
    assert path.stat().st_size < 160

    parsed = read_gff(path)
    assert parsed["Tag"] == padded["Tag"] == "x"
    assert parsed["LocalizedName"] == {"StrRef": 5, "Value": ""}
    assert parsed["_record_offsets"]["Tag"] == padded["_record_offsets"]["Tag"] - 104


# ---------------------------------------------------------------------------
# Direct Struct fields (type 14)
# ---------------------------------------------------------------------------


def test_direct_struct_field_expands_to_a_patchable_nested_dict(tmp_path):
    path = tmp_path / "wrapper.uti"
    write_gff(
        path,
        {
            "StructType": "UTI",
            "Tag": "outer_tag",
            "Wrapper": {"LocalizedName": loc("Ancient Blade"), "Charges": 3},
        },
    )
    parsed = read_gff(path)

    assert (parsed["StructType"], parsed["Tag"]) == ("UTI", "outer_tag")
    wrapper = parsed["Wrapper"]
    assert (wrapper["LocalizedName"]["Value"], wrapper["Charges"]) == ("Ancient Blade", 3)
    assert parsed["_field_types"]["Wrapper"] == int(GFFType.Struct) == 14
    assert wrapper["_field_types"]["LocalizedName"] == int(GFFType.CExoLocString)
    # The nested locstring stays byte-patchable: a real field record offset.
    assert wrapper["_record_offsets"]["LocalizedName"] > 0
    # The 12-byte field record on disk carries type id 14, not 16.
    field = parse_gff(path).structs[0].fields["Wrapper"]
    assert field.type == GFFType.Struct
    assert struct.unpack_from("<I", path.read_bytes(), field.record_offset)[0] == 14
    # Two write/read cycles do not degrade the nested struct.
    again = _roundtrip(tmp_path, parsed)
    assert again["Wrapper"]["LocalizedName"]["Value"] == "Ancient Blade"
    assert (again["Wrapper"]["Charges"], again["_field_types"]["Wrapper"]) == (3, 14)


@pytest.mark.parametrize(
    "structs, expected",
    [
        ([{"Self": GFFValue(GFFType.Struct, 0, record_offset=100)}], {"Self": 0}),
        ([{"Broken": GFFValue(GFFType.Struct, 99, record_offset=100)}], {"Broken": 99}),
        ([{"Broken": GFFValue(GFFType.Struct, -1, record_offset=100)}], {"Broken": -1}),
        # A plain DWORD that happens to equal a valid struct index stays an int.
        (
            [
                {"HP": GFFValue(GFFType.DWORD, 1, record_offset=100)},
                {"Decoy": GFFValue(GFFType.DWORD, 7, record_offset=112)},
            ],
            {"HP": 1},
        ),
        # The back-edge of a two-struct cycle to the visited root stays an int.
        (
            [
                {"Child": GFFValue(GFFType.Struct, 1, record_offset=100)},
                {"Parent": GFFValue(GFFType.Struct, 0, record_offset=112)},
            ],
            {"Child": {"Parent": 0}},
        ),
    ],
)
def test_invalid_struct_indices_stay_ints_and_never_loop(structs, expected):
    gff = GFFFile()
    gff.structs.extend(GFFStruct(struct_id=0, fields=fields) for fields in structs)
    result = _fields_only(_expand_struct(gff.structs[0].fields, gff, {0}))
    assert {key: result[key] for key in expected} == expected


# ---------------------------------------------------------------------------
# Fixture writer round trips
# ---------------------------------------------------------------------------


def test_writer_header_and_file(tmp_path):
    raw = write_gff_bytes({"StructType": "DLG"})
    assert (raw[0:4], raw[4:8]) == (b"DLG ", b"V3.2")
    assert len(raw) >= 160
    out = tmp_path / "journal.jrl"
    write_gff(out, {"StructType": "JRL", "Categories": [], "LocalizedName": loc("Helm")})
    assert out.stat().st_size > 160
    assert read_gff(out)["StructType"] == "JRL"


@pytest.mark.parametrize(
    "data",
    [
        {
            "StructType": "UTI",
            "Tag": "sword",
            "HP": 100,
            "Penalty": -5,
            "Speed": 1.5,
            "Description": "A long description text.",
            "LocalizedName": loc("Longsword"),
            "Unicode": loc("Меч огня"),
            "Unnamed": loc("", strref=1234),
        },
        {"StructType": "DLG", "EntryList": [{"Text": loc("A"), "RepliesList": [{"Index": 0}]}]},
        {
            "StructType": "DLG",
            "EntryList": [{"Text": loc("A")}, {"Text": loc("B"), "RepliesList": []}],
        },
        {
            "StructType": "GIT",
            "Creature List": [
                {"Tag": "a", "ItemList": [{"Tag": "sword", "PropertiesList": [{"Param": 1}]}]},
                {"Tag": "b", "ItemList": []},
                {"Tag": "c", "ItemList": [{"Tag": "helm"}, {"Tag": "ring"}]},
            ],
            "Door List": [{"Tag": "door"}],
        },
        _dialog_graph(),
    ],
    ids=["scalars", "nested_list_first", "empty_nested_list", "three_levels", "dialog_graph"],
)
def test_written_values_read_back_unchanged(tmp_path, data):
    assert _fields_only(_roundtrip(tmp_path, data)) == data


def test_non_dict_list_elements_are_skipped(tmp_path):
    data = {"StructType": "DLG", "EntryList": [{"Text": loc("A")}, 7, {"Text": loc("B")}]}
    assert _fields_only(_roundtrip(tmp_path, data)["EntryList"]) == [
        {"Text": loc("A")},
        {"Text": loc("B")},
    ]


@pytest.mark.parametrize(
    "value, field_type",
    [(2**40, None), (-5, int(GFFType.INT64)), (1.5, int(GFFType.DOUBLE))],
)
def test_8_byte_values_live_in_the_field_data_block(tmp_path, value, field_type):
    data = {"StructType": "GFF", "Tag": "x", "Big": value}
    if field_type is not None:
        data["_field_types"] = {"Big": field_type}
    result = _roundtrip(tmp_path, data)
    assert result["Big"] == value
    if field_type is not None:
        assert result["_field_types"]["Big"] == field_type


@pytest.mark.parametrize(
    "data",
    [
        _dialog_graph(),
        # Metadata keys do not count as fields: one field is stored inline.
        {"StructType": "UTI", "Tag": "a", "Cost": 2, "PropertiesList": [{"Param": 3}]},
    ],
)
def test_parsed_dict_writes_again_unchanged(tmp_path, data):
    parsed = _roundtrip(tmp_path, data)
    assert "_field_types" in parsed
    assert _fields_only(_roundtrip(tmp_path, parsed)) == data


def test_parsed_field_types_and_struct_ids_are_kept(tmp_path):
    """``_field_types`` pins types the heuristics would guess differently."""
    parsed = _roundtrip(tmp_path, {"StructType": "UTC", "Conversation": "bob"})
    parsed["_field_types"]["Conversation"] = int(GFFType.CExoString)
    out = tmp_path / "a.utc"
    write_gff(out, parsed)
    field = parse_gff(out).structs[0].fields["Conversation"]
    assert (field.type, field.value) == (GFFType.CExoString, "bob")

    write_gff(out, {"StructType": "DLG", "EntryList": [{"_struct_id": 5, "Text": loc("A")}]})
    gff = parse_gff(out)
    entry = gff.structs[gff.structs[0].fields["EntryList"].value[0]]
    assert entry.struct_id == 5
    assert "_struct_id" not in entry.fields
