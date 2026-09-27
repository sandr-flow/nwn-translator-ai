"""ERF archives: writer layout, reader validation, extraction naming and repacking."""

import struct
from pathlib import Path

import pytest

from nwn_translator.formats import erf
from nwn_translator.formats.erf import (
    RESOURCE_TYPES,
    TYPE_ID_BY_EXTENSION,
    ERFError,
    ERFHeader,
    ERFReader,
    ERFWriter,
    create_mod_from_directory,
)
from nwn_translator.resources import TRANSLATABLE_TYPES

# Canonical Aurora ids (nwn.h / xoreos) of the resource types the translator touches.
CANONICAL_IDS = {
    1: ".bmp",
    3: ".tga",
    4: ".wav",
    6: ".plt",
    7: ".ini",
    10: ".txt",
    2002: ".mdl",
    2010: ".ncs",
    2012: ".are",
    2014: ".ifo",
    2023: ".git",
    2025: ".uti",
    2027: ".utc",
    2029: ".dlg",
    2032: ".utt",
    2035: ".uts",
    2040: ".ute",
    2042: ".utd",
    2044: ".utp",
    2051: ".utm",
    2056: ".jrl",
}


def _write(path: Path, resources=(("dialog", ".dlg", b"DLG DATA"),), **kwargs) -> Path:
    """Write ``(stem, ext, data)`` resources to an archive at *path*."""
    writer = ERFWriter(path, **kwargs)
    for stem, ext, data in resources:
        writer.add_resource(stem, ext, data)
    writer.write()
    return path


def _patch(path: Path, offset: int, payload: bytes) -> None:
    raw = bytearray(path.read_bytes())
    raw[offset : offset + len(payload)] = payload
    path.write_bytes(bytes(raw))


def _dword(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _data(path: Path, entry) -> bytes:
    return path.read_bytes()[entry.offset : entry.offset + entry.size]


# ---------------------------------------------------------------------------
# Writer layout and round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix, file_type", [(".mod", b"MOD "), (".erf", b"ERF ")])
def test_header_layout(tmp_path, suffix, file_type):
    out = _write(tmp_path / f"test{suffix}", [(f"r{i}", ".dlg", b"data") for i in range(3)])
    raw = out.read_bytes()
    assert (raw[0:4], raw[4:8]) == (file_type, b"V1.0")
    assert _dword(raw, 16) == 3  # EntryCount
    assert _dword(raw, 24) == 160  # OffsetToKeyList: right after the header
    assert _dword(raw, 28) == 160 + 3 * 24  # OffsetToResourceList: after the key list
    assert ERFReader(out).read_header().entry_count == 3


def test_resources_round_trip_with_names_types_sizes_and_bytes(tmp_path):
    payloads = {
        "qst_001": (".dlg", b"QUEST DIALOG DATA" * 10),
        "helm": (".uti", b"\x00\x01\x02" * 5),
        "journal": (".jrl", b"Journal entry text"),
    }
    out = _write(tmp_path / "a.mod", [(stem, ext, data) for stem, (ext, data) in payloads.items()])
    reader = ERFReader(out)
    reader.read_header()
    entries = reader.read_entries()
    assert {e.res_ref for e in entries} == set(payloads)
    assert len(entries) == len(payloads)
    for entry in entries:
        ext, data = payloads[entry.res_ref]
        assert entry.res_type == TYPE_ID_BY_EXTENSION[ext]
        assert entry.size == len(data)
        assert _data(out, entry) == data


def test_resref_is_limited_to_16_characters(tmp_path):
    with pytest.raises(ERFError, match="limited to 16"):
        _write(tmp_path / "long.mod", [("a_very_long_resref", ".dlg", b"data")])
    out = _write(tmp_path / "max.mod", [("sixteen_chars_ab", ".dlg", b"data")])
    assert ERFReader(out).read_entries()[0].res_ref == "sixteen_chars_ab"


def test_empty_archive_writes(tmp_path):
    out = _write(tmp_path / "empty.mod", [])
    assert ERFHeader.from_bytes(out.read_bytes()[:160]).entry_count == 0


def test_add_file_streams_a_file_larger_than_the_copy_chunk(tmp_path):
    payload = bytes(range(256)) * (10 * 1024)  # 2.5 MiB, more than two 1 MiB chunks
    src = tmp_path / "big.dlg"
    src.write_bytes(payload)
    writer = ERFWriter(tmp_path / "big.mod")
    writer.add_file(src)
    writer.write()
    (entry,) = ERFReader(tmp_path / "big.mod").read_entries()
    assert (entry.res_ref, _data(tmp_path / "big.mod", entry)) == ("big", payload)


# ---------------------------------------------------------------------------
# Module description (Localized String List)
# ---------------------------------------------------------------------------


def _mod_with_description(path: Path) -> bytes:
    """Write a .mod with a one-language description block; return the block."""
    text = b"Module description text"
    block = struct.pack("<II", 0, len(text)) + text
    writer = ERFWriter(path)
    writer.set_localized_strings(1, block, 0xDEADBEEF)
    writer.add_resource("dialog", ".dlg", b"DLG DATA")
    writer.write()
    return block


def test_description_block_is_written_and_read_back(tmp_path):
    out = tmp_path / "desc.mod"
    block = _mod_with_description(out)

    raw = out.read_bytes()
    assert _dword(raw, 8) == 1  # LanguageCount
    assert _dword(raw, 12) == len(block)  # LocalizedStringSize
    assert _dword(raw, 20) == 160  # OffsetToLocalizedString
    assert raw[160 : 160 + len(block)] == block
    assert _dword(raw, 24) == 160 + len(block)  # OffsetToKeyList
    assert _dword(raw, 40) == 0xDEADBEEF  # DescriptionStrRef
    reader = ERFReader(out)
    header = reader.read_header()
    assert (header.language_count, header.description_strref) == (1, 0xDEADBEEF)
    assert reader.read_localized_strings_block() == block
    # The shifted key list still yields byte-identical resource data.
    (entry,) = reader.read_entries()
    assert (entry.res_ref, _data(out, entry)) == ("dialog", b"DLG DATA")


def test_repacking_carries_the_description(tmp_path):
    src = tmp_path / "source.mod"
    block = _mod_with_description(src)
    out = tmp_path / "repacked.mod"
    create_mod_from_directory(
        ERFReader(src).extract_all(tmp_path / "extract"), out, original_mod=src
    )

    reader = ERFReader(out)
    header = reader.read_header()
    assert (header.language_count, header.description_strref) == (1, 0xDEADBEEF)
    assert reader.read_localized_strings_block() == block


def test_without_description_the_header_keeps_the_defaults(tmp_path):
    raw = _write(tmp_path / "plain.mod").read_bytes()
    assert [_dword(raw, offset) for offset in (8, 12, 20, 40)] == [0, 0, 0, 0xFFFFFFFF]


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_failed_write_keeps_the_previous_archive(tmp_path, monkeypatch):
    out = _write(tmp_path / "result.mod", [("old", ".dlg", b"OLD DATA")])
    old_bytes = out.read_bytes()
    writer = ERFWriter(out)
    writer.add_resource("new", ".dlg", b"NEW DATA")

    def broken_copy(out_fp, src):
        out_fp.write(b"PARTIAL")
        raise OSError("disk full")

    monkeypatch.setattr(erf, "_copy_into", broken_copy)
    with pytest.raises(OSError, match="disk full"):
        writer.write()
    monkeypatch.undo()

    assert out.read_bytes() == old_bytes
    assert [e.res_ref for e in ERFReader(out).read_entries()] == ["old"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_source_file_resized_during_write_aborts(tmp_path, monkeypatch):
    out = _write(tmp_path / "result.mod", [("old", ".dlg", b"OLD DATA")])
    old_bytes = out.read_bytes()
    src = tmp_path / "racy.dlg"
    src.write_bytes(b"A" * 100)
    writer = ERFWriter(out)
    writer.add_file(src)
    original = erf._copy_into

    def shrink_then_copy(out_fp, source):
        if isinstance(source, Path):
            source.write_bytes(b"A" * 10)
        return original(out_fp, source)

    monkeypatch.setattr(erf, "_copy_into", shrink_then_copy)
    with pytest.raises(ERFError, match="changed size"):
        writer.write()
    monkeypatch.undo()

    assert out.read_bytes() == old_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_replaces_an_existing_archive(tmp_path):
    out = _write(tmp_path / "result.mod", [("old", ".dlg", b"OLD DATA")])
    _write(out, [("new", ".dlg", b"NEW DATA")])
    assert [e.res_ref for e in ERFReader(out).read_entries()] == ["new"]
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Reader validation of crafted archives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset, value",
    [
        (16, lambda size: 0xFFFFFFFF),  # entry count far beyond the file size
        (24, lambda size: size + 1000),  # key list past the end of the file
        (28, lambda size: size + 1000),  # resource list past the end of the file
    ],
)
def test_header_blocks_outside_the_file_are_rejected(tmp_path, offset, value):
    mod = _write(tmp_path / "bad.mod")
    _patch(mod, offset, struct.pack("<I", value(mod.stat().st_size)))
    with pytest.raises(ERFError, match="do not fit"):
        ERFReader(mod).read_header()


@pytest.mark.parametrize("version", [b"V1.1", b"V2.0", b"E1.0", b"\x00\x00\x00\x00"])
def test_versions_other_than_v10_are_rejected(tmp_path, version):
    mod = _write(tmp_path / "wrong_version.mod")
    _patch(mod, 4, version)
    with pytest.raises(ERFError, match="only V1.0"):
        ERFReader(mod).read_header()


def test_entry_running_past_the_end_of_the_file_is_rejected(tmp_path):
    mod = _write(tmp_path / "bad_size.mod")
    # One entry: the resource list is at 160 + 24; its size field is 4 bytes in.
    _patch(mod, 160 + 24 + 4, struct.pack("<I", 0x7FFFFFFF))
    with pytest.raises(ERFError, match="exceeds file size"):
        ERFReader(mod).read_entries()


def test_overlapping_entries_are_rejected(tmp_path):
    """Entries sharing one data region (an ERF bomb) are rejected."""
    count, data = 3, b"X" * 200
    key_list_offset = 160
    res_list_offset = key_list_offset + count * 24
    data_offset = res_list_offset + count * 8
    header = bytearray(160)
    header[0:8] = b"MOD V1.0"
    struct.pack_into("<I", header, 16, count)
    struct.pack_into("<II", header, 24, key_list_offset, res_list_offset)
    key_list = b"".join(
        f"res{i}".encode("ascii").ljust(16, b"\x00") + struct.pack("<II", i, 2029)
        for i in range(count)
    )
    # Each entry is valid on its own, but the declared total is 600 bytes in a 456-byte file.
    res_list = struct.pack("<II", data_offset, len(data)) * count
    mod = tmp_path / "overlap.mod"
    mod.write_bytes(bytes(header) + key_list + res_list + data)
    with pytest.raises(ERFError, match="overlapping"):
        ERFReader(mod).read_entries()


def test_description_block_outside_the_file_reads_as_absent(tmp_path):
    mod = _write(tmp_path / "bad_desc.mod")
    _patch(mod, 12, struct.pack("<I", 0x7FFFFFFF))  # LocalizedStringSize past EOF
    _patch(mod, 20, struct.pack("<I", 160))
    assert ERFReader(mod).read_localized_strings_block() == b""


# ---------------------------------------------------------------------------
# Extraction naming and repacking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signature, ext",
    [
        (b"DLG ", ".dlg"),
        (b"JRL ", ".jrl"),
        (b"UTI ", ".uti"),
        (b"UTC ", ".utc"),
        (b"ARE ", ".are"),
        (b"UTT ", ".utt"),
        (b"UTP ", ".utp"),
        (b"UTD ", ".utd"),
        (b"UTM ", ".utm"),
        (b"IFO ", ".ifo"),
        (b"GIT ", ".git"),
        (b"NCS ", ".ncs"),
    ],
)
def test_known_signature_names_a_resource_of_unknown_type(tmp_path, signature, ext):
    mod = _write(
        tmp_path / "custom.mod",
        [("resource", ".bin", signature + b"\x00" * 8)],
        type_overrides={"resource.bin": 6789},
    )
    reader = ERFReader(mod)
    (entry,) = reader.read_entries()
    assert entry.res_type == 6789
    assert reader.extension_for(entry) == ext
    assert reader.filename_for(entry) == f"resource{ext}"
    out = reader.extract_all(tmp_path / "out")
    assert [p.name for p in out.iterdir()] == [f"resource{ext}"]


@pytest.mark.parametrize(
    "type_id, ext",
    [
        (1, ".bmp"),
        (3, ".tga"),
        (4, ".wav"),
        (6, ".plt"),
        (7, ".ini"),
        (10, ".txt"),
        (2002, ".mdl"),
        (6789, ".6789"),  # unknown and without a signature: the numeric id
    ],
)
def test_resources_without_signature_are_named_by_type_id(tmp_path, type_id, ext):
    """Low Aurora ids are not GFF types: a text file must not become a ``.git``."""
    mod = _write(
        tmp_path / "content.hak",
        [("asset", ".bin", b"plain content")],
        type_overrides={"asset.bin": type_id},
    )
    out = ERFReader(mod).extract_all(tmp_path / "out")
    assert [p.name for p in out.iterdir()] == [f"asset{ext}"]


def test_characters_forbidden_on_windows_become_underscores(tmp_path):
    reader = ERFReader(_write(tmp_path / "odd.mod", [("a?b*c", ".dlg", b"DLG DATA")]))
    (entry,) = reader.read_entries()
    assert entry.res_ref == "a?b*c"
    assert reader.filename_for(entry) == "a_b_c.dlg"


def test_progress_callback_sees_every_entry(tmp_path):
    mod = _write(tmp_path / "two.mod", [("a", ".dlg", b"one"), ("b", ".uti", b"two")])
    calls = []
    ERFReader(mod, progress_callback=lambda *args: calls.append(args)).extract_all(tmp_path / "o")
    assert calls == [("extracting", 0, 2, "a"), ("extracting", 1, 2, "b")]


def test_repack_keeps_the_original_type_ids(tmp_path):
    """The extracted ``npc.utc`` goes back under its original type id 6789."""
    mod = _write(
        tmp_path / "custom.mod",
        [("npc", ".bin", b"UTC V3.2" + b"\x00" * 8)],
        type_overrides={"npc.bin": 6789},
    )
    repacked = tmp_path / "repacked.mod"
    create_mod_from_directory(ERFReader(mod).extract_all(tmp_path / "out"), repacked, mod)

    (entry,) = ERFReader(repacked).read_entries()
    assert (entry.res_ref, entry.res_type) == ("npc", 6789)
    assert repacked.read_bytes()[40:] == mod.read_bytes()[40:]


# ---------------------------------------------------------------------------
# Canonical resource type-id table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("res_id, ext", sorted(CANONICAL_IDS.items()))
def test_type_table_matches_the_canonical_ids(res_id, ext):
    assert RESOURCE_TYPES.get(res_id) == ext


@pytest.mark.parametrize("res_id", [0, 2, 5, 14, 27, 79])
def test_non_aurora_low_ids_are_unknown(res_id):
    assert res_id not in RESOURCE_TYPES


def test_every_extension_has_exactly_one_id():
    """Each extension maps to one id, so the writer's inverse table is exact."""
    seen: dict = {}
    for rid, ext in RESOURCE_TYPES.items():
        assert ext not in seen, f"{ext} duplicated: {seen.get(ext)} and {rid}"
        seen[ext] = rid
    assert TYPE_ID_BY_EXTENSION == seen


@pytest.mark.parametrize("ext", sorted(TRANSLATABLE_TYPES))
def test_translatable_extensions_have_canonical_20xx_ids(ext):
    res_id = TYPE_ID_BY_EXTENSION.get(ext)
    assert res_id is not None and res_id >= 2000
    assert RESOURCE_TYPES[res_id] == ext


@pytest.mark.parametrize(
    "filename, data, expected_id",
    [
        ("blueprint.utt", b"UTT V3.2" + b"\x00" * 8, 2032),
        ("blueprint.utd", b"UTD V3.2" + b"\x00" * 8, 2042),
        ("blueprint.utm", b"UTM V3.2" + b"\x00" * 8, 2051),
        ("asset.tga", b"data", 3),
        ("asset.bmp", b"data", 1),
        ("asset.wav", b"data", 4),
        ("asset.txt", b"data", 10),
        ("asset.mdl", b"data", 2002),
        ("asset.xyz", b"data", 0),
    ],
)
def test_writer_uses_the_type_id_of_the_extension(tmp_path, filename, data, expected_id):
    """Translatable blueprints and hak content get their Aurora ids; unknown types get 0."""
    name = Path(filename)
    out = _write(tmp_path / "a.mod", [(name.stem, name.suffix, data)])
    assert ERFReader(out).read_entries()[0].res_type == expected_id
