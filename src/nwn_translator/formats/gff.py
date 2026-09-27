"""GFF V3.2 resources: parsing into dicts and byte-patching CExoLocString fields.

Layout (little-endian DWORDs): a 56-byte header (FileType, Version, then the
offset and count or byte size of six blocks), followed by the blocks:

- structs: StructID, DataOrDataOffset, FieldCount (12 bytes each);
- fields: Type, LabelIndex, DataOrDataOffset (12 bytes each);
- labels: 16-byte NUL-padded names;
- field data: payloads of 8-byte and variable-size values;
- field indices: field indices of structs with several fields;
- list indices: per list, a count followed by struct indices.

The pipeline never rewrites a GFF file. It parses it into a dict that records
the byte offset of every field record, and translation patches those records
in place (:func:`patch_locstrings`).
"""

import logging
import struct
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, NamedTuple, Optional, Tuple, Union

from .text_codec import MODULE_ENCODINGS, decode_fixed_ascii, decode_module_text, encode_module_text

logger = logging.getLogger(__name__)

#: Header: FileType, Version, then offset/size pairs of the six blocks.
HEADER = struct.Struct("<4s4s12I")
#: Struct record (StructID, DataOrDataOffset, FieldCount) and field record
#: (Type, LabelIndex, DataOrDataOffset) share one shape.
RECORD = struct.Struct("<III")
#: Label: 16-byte NUL-padded name.
LABEL = struct.Struct("16s")
#: CExoLocString payload head: TotalSize, StrRef, SubStringCount.
LOCSTRING_HEAD = struct.Struct("<IiI")
#: CExoLocString substring head: LanguageID, Length.
SUBSTRING_HEAD = struct.Struct("<II")
_DWORD = struct.Struct("<I")


class GFFType(IntEnum):
    """GFF field type ids."""

    BYTE = 0
    CHAR = 1
    WORD = 2
    SHORT = 3
    DWORD = 4
    INT = 5
    DWORD64 = 6
    INT64 = 7
    FLOAT = 8
    DOUBLE = 9
    CExoString = 10
    CResRef = 11
    CExoLocString = 12
    VOID = 13
    Struct = 14
    List = 15
    Unknown = 0xFF


# Unknown type ids are read like a DWORD.
_TYPE_BY_ID = {t.value: t for t in GFFType}
# Decoders of the values stored in the field record's DataOrDataOffset DWORD.
_INLINE_DECODERS: Dict[GFFType, Callable[[int], Any]] = {
    GFFType.BYTE: lambda raw: raw & 0xFF,
    GFFType.CHAR: lambda raw: chr(raw & 0xFF) if raw < 128 else "?",
    GFFType.WORD: lambda raw: raw & 0xFFFF,
    GFFType.SHORT: lambda raw: raw - 0x10000 if raw & 0x8000 else raw,
    GFFType.DWORD: lambda raw: raw,
    GFFType.INT: lambda raw: raw - 0x100000000 if raw & 0x80000000 else raw,
    GFFType.FLOAT: lambda raw: struct.unpack("<f", _DWORD.pack(raw))[0],
}
# 8-byte types: the field record holds an offset into the field data block.
_WIDE_FORMATS = {GFFType.DWORD64: "<Q", GFFType.INT64: "<q", GFFType.DOUBLE: "<d"}


class GFFHeader(NamedTuple):
    """GFF V3.2 header: FileType, Version, then offset and count (or byte size) per block."""

    file_type: bytes
    version: bytes
    struct_offset: int
    struct_count: int
    field_offset: int
    field_count: int
    label_offset: int
    label_count: int
    field_data_offset: int
    field_data_size: int
    field_indices_offset: int
    field_indices_size: int
    list_indices_offset: int
    list_indices_size: int

    @classmethod
    def read(cls, data: Union[bytes, bytearray]) -> "GFFHeader":
        """Unpacks the header at the start of *data* (at least 56 bytes)."""
        return cls._make(HEADER.unpack_from(data))


class GFFParseError(Exception):
    """Raised when a GFF file cannot be read or parsed."""


class GFFPatchError(Exception):
    """Raised when a GFF file cannot be patched."""


def parse_gff(data: bytes, source_encoding: Optional[str] = None) -> Dict[str, Any]:
    """Parses GFF bytes into one nested dict rooted at the first struct.

    List and Struct fields expand into nested dicts; a struct index that is out of range
    or already on the current path stays an int, so malformed files cannot recurse
    forever. Records that reference missing labels or fields are tolerated; a header whose
    blocks lie outside the data is rejected before any per-element work, so a corrupt
    count cannot exhaust time or memory.

    Args:
        data: Complete file bytes.
        source_encoding: Declared code page of string bytes; ``None`` uses the cascade of
            :func:`~.text_codec.decode_module_text`.

    Returns:
        ``{"StructType": ..., <root fields>, "_field_types": ..., "_record_offsets": ...}``
        (every struct carries label -> type id and label -> field record offset maps), or
        ``{}`` for a file without structs.

    Raises:
        GFFParseError: If the data is too small or its header is corrupt.
    """
    size = len(data)
    if size < HEADER.size:
        raise GFFParseError("File too small to be valid GFF")
    header = GFFHeader.read(data)
    for block_name, block_offset, block_bytes in (
        ("structs", header.struct_offset, header.struct_count * RECORD.size),
        ("fields", header.field_offset, header.field_count * RECORD.size),
        ("labels", header.label_offset, header.label_count * LABEL.size),
        ("field data", header.field_data_offset, header.field_data_size),
        ("field indices", header.field_indices_offset, header.field_indices_size),
        ("list indices", header.list_indices_offset, header.list_indices_size),
    ):
        if block_offset > size or block_offset + block_bytes > size:
            raise GFFParseError(
                f"GFF header declares {block_name} block outside the file: "
                f"offset={block_offset} bytes={block_bytes} file_size={size}"
            )

    def block(offset: int, length: int) -> bytes:
        """Returns *length* bytes of *data* from *offset*."""
        return data[offset : offset + length]

    labels = [
        decode_fixed_ascii(raw)
        for (raw,) in LABEL.iter_unpack(block(header.label_offset, header.label_count * LABEL.size))
    ]
    fields: List[Tuple[str, GFFType, int, int]] = [
        (
            labels[label_index] if label_index < len(labels) else f"field_{label_index}",
            _TYPE_BY_ID.get(type_id, GFFType.DWORD),
            raw_value,
            header.field_offset + index * RECORD.size,
        )
        for index, (type_id, label_index, raw_value) in enumerate(
            RECORD.iter_unpack(block(header.field_offset, header.field_count * RECORD.size))
        )
    ]
    field_indices = struct.unpack_from(
        f"<{header.field_indices_size // 4}I", data, header.field_indices_offset
    )
    structs = list(
        RECORD.iter_unpack(block(header.struct_offset, header.struct_count * RECORD.size))
    )

    def expand(index: int, visited: FrozenSet[int]) -> Dict[str, Any]:
        """Decodes struct *index*; *visited* holds the struct indices on the current path."""

        def child(value: int) -> Any:
            """Expands a struct index, or keeps it when invalid or already on the path."""
            if value < len(structs) and value not in visited:
                return expand(value, visited | {value})
            return value

        _struct_id, data_or_offset, field_count = structs[index]
        # One field: DataOrDataOffset is the field index. Several: it is a byte offset
        # into the field indices block.
        if field_count == 1:
            members: Tuple[int, ...] = (data_or_offset,)
        else:
            start = data_or_offset // 4
            members = field_indices[start : start + field_count] if field_count else ()
        result: Dict[str, Any] = {}
        types: Dict[str, int] = {}
        offsets: Dict[str, int] = {}
        for field_index in members:
            if field_index >= len(fields):
                continue
            label, gff_type, raw_value, record_offset = fields[field_index]
            value = _field_value(data, header, gff_type, raw_value, source_encoding)
            if gff_type == GFFType.List:
                value = [child(item) for item in value]
            elif gff_type == GFFType.Struct:
                value = child(value)
            result[label] = value
            types[label] = int(gff_type)
            offsets[label] = record_offset
        result["_field_types"] = types
        result["_record_offsets"] = offsets
        return result

    if not structs:
        return {}
    try:
        struct_type: Optional[str] = header.file_type.rstrip(b" ").decode("ascii")
    except UnicodeDecodeError:
        struct_type = None
    return {"StructType": struct_type, **expand(0, frozenset({0}))}


def _field_value(
    data: bytes, header: GFFHeader, gff_type: GFFType, raw: int, encoding: Optional[str]
) -> Any:
    """Decodes one field; data that lies outside the file reads as empty.

    A Struct field decodes to its struct index and a List to its struct indices; VOID
    payloads are not needed by the translator and decode to ``None``.
    """
    if gff_type in _INLINE_DECODERS:
        return _INLINE_DECODERS[gff_type](raw)
    if gff_type in (GFFType.Struct, GFFType.Unknown):
        return raw
    if gff_type == GFFType.List:
        offset = header.list_indices_offset + raw
        if offset + 4 > len(data):
            return []
        count = min(_DWORD.unpack_from(data, offset)[0], (len(data) - offset - 4) // 4)
        return list(struct.unpack_from(f"<{count}I", data, offset + 4))
    offset = header.field_data_offset + raw
    if gff_type in _WIDE_FORMATS:
        if offset + 8 > len(data):
            return 0
        return struct.unpack_from(_WIDE_FORMATS[gff_type], data, offset)[0]
    if gff_type == GFFType.CExoString:
        if offset + 4 > len(data):
            return ""
        length = _DWORD.unpack_from(data, offset)[0]
        if length == 0 or offset + 4 + length > len(data):
            return ""
        return decode_module_text(data[offset + 4 : offset + 4 + length], encoding)
    if gff_type == GFFType.CResRef:
        if offset + 1 > len(data):
            return ""
        return data[offset + 1 : offset + 1 + data[offset]].decode("ascii", errors="ignore")
    if gff_type == GFFType.CExoLocString:
        return _locstring_value(data, offset, encoding)
    return None


def _locstring_value(data: bytes, offset: int, encoding: Optional[str]) -> Dict[str, Any]:
    """Decodes a CExoLocString to its StrRef and first non-empty substring."""
    if offset + 12 > len(data):
        return {"StrRef": -1, "Value": ""}
    _total_size, str_ref, count = LOCSTRING_HEAD.unpack_from(data, offset)
    position = offset + LOCSTRING_HEAD.size
    for _ in range(count):
        if position + SUBSTRING_HEAD.size > len(data):
            break
        _language_id, length = SUBSTRING_HEAD.unpack_from(data, position)
        position += SUBSTRING_HEAD.size
        if position + length > len(data):
            break
        value = decode_module_text(data[position : position + length], encoding)
        position += length
        if value:
            return {"StrRef": str_ref, "Value": value}
    return {"StrRef": str_ref, "Value": ""}


def read_gff(
    file_path: Path,
    cache: Optional[Dict[Path, Dict[str, Any]]] = None,
    source_encoding: Optional[str] = None,
) -> Dict[str, Any]:
    """Parses a GFF file into a dict (see :func:`parse_gff`).

    Args:
        file_path: The GFF file.
        cache: Optional session cache keyed by resolved path; callers sharing a cache must
            pass the same *source_encoding* for every read.
        source_encoding: Declared code page of string bytes.

    Returns:
        The parsed dict (the cached object on a cache hit).

    Raises:
        GFFParseError: If the file is missing or cannot be parsed; the message names the file.
    """
    path = Path(file_path).resolve()
    if cache is not None and path in cache:
        return cache[path]
    if not path.exists():
        raise GFFParseError(f"File not found: {path}")
    try:
        data = parse_gff(path.read_bytes(), source_encoding=source_encoding)
    except GFFParseError as e:
        raise GFFParseError(f"Failed to parse GFF file {path}: {e}") from e
    except Exception as e:
        raise GFFParseError(f"Failed to read GFF file {path}: {e}") from e
    if cache is not None:
        cache[path] = data
    return data


def patch_locstrings(
    file_path: Path, patches: List[Tuple[int, str]], text_encoding: str = "cp1251"
) -> None:
    """Replaces the text of several CExoLocString fields of a GFF file in one write.

    New payloads are appended to the end of the field data block in patch order, the
    field records are pointed at them, and the blocks after field data move back by the
    total payload length; the old payloads stay in the file unreferenced, and a repeated
    record offset ends up pointing at its last payload. Each payload holds one substring
    with LanguageID 0: every client shows that slot directly or through the engine's
    language fallback, and target languages without an official NWN language id have no
    other choice. A field that carried several substrings (gender or language variants)
    is therefore collapsed, with a warning.

    Args:
        file_path: The GFF file to modify.
        patches: ``(record_offset, new_text)`` per 12-byte field record.
        text_encoding: One of :data:`~.text_codec.MODULE_ENCODINGS`.

    Raises:
        GFFPatchError: If the encoding is not allowed, the file is missing or shorter than
            a GFF header, or a record offset is not positive; the file is left unchanged.
    """
    if text_encoding not in MODULE_ENCODINGS:
        raise GFFPatchError(f"Unsupported module text encoding: {text_encoding!r}")
    if not file_path.exists():
        raise GFFPatchError(f"File not found: {file_path}")
    if not patches:
        return
    data = bytearray(file_path.read_bytes())
    if len(data) < HEADER.size:
        raise GFFPatchError("File too small to be a valid GFF header")
    header = GFFHeader.read(data)
    insert_at = header.field_data_offset + header.field_data_size

    payloads: List[bytes] = []
    next_data_offset = header.field_data_size
    patched_offsets = set()
    for record_offset, new_text in patches:
        if record_offset <= 0:
            raise GFFPatchError("Invalid record offset provided")
        # A repeated offset already points at its (not yet spliced) replacement, so its
        # substring count means nothing.
        if record_offset not in patched_offsets:
            substring_count = _substring_count(data, header, record_offset)
            if substring_count > 1:
                logger.warning(
                    "%s: overwriting %d substrings at field record offset %d with a single "
                    "LanguageID-0 substring; gender/language variants are lost",
                    file_path.name,
                    substring_count,
                    record_offset,
                )
        patched_offsets.add(record_offset)
        payload = _locstring_payload(encode_module_text(new_text, text_encoding))
        # Field records precede field data, so the splice does not move them.
        _DWORD.pack_into(data, record_offset + 8, next_data_offset)
        next_data_offset += len(payload)
        payloads.append(payload)

    shift = next_data_offset - header.field_data_size
    new_data = bytearray(b"".join([data[:insert_at], *payloads, data[insert_at:]]))
    updates: Dict[str, Any] = {"field_data_size": header.field_data_size + shift}
    if header.field_indices_size:
        updates["field_indices_offset"] = header.field_indices_offset + shift
    if header.list_indices_size:
        updates["list_indices_offset"] = header.list_indices_offset + shift
    HEADER.pack_into(new_data, 0, *GFFHeader.read(new_data)._replace(**updates))
    file_path.write_bytes(new_data)


def _substring_count(data: bytearray, header: GFFHeader, record_offset: int) -> int:
    """Returns the SubStringCount of the CExoLocString whose field record is at *record_offset*.

    Returns 0 when the record or its payload lies outside the file.
    """
    if record_offset + RECORD.size > len(data):
        return 0
    payload_offset = header.field_data_offset + RECORD.unpack_from(data, record_offset)[2]
    if payload_offset + LOCSTRING_HEAD.size > len(data):
        return 0
    return int(LOCSTRING_HEAD.unpack_from(data, payload_offset)[2])


def _locstring_payload(encoded: bytes) -> bytes:
    """Builds a CExoLocString payload: StrRef -1 and at most one LanguageID-0 substring."""
    if not encoded:
        return LOCSTRING_HEAD.pack(8, -1, 0)
    return (
        LOCSTRING_HEAD.pack(16 + len(encoded), -1, 1)
        + SUBSTRING_HEAD.pack(0, len(encoded))
        + encoded
    )
