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
in place (:class:`GFFPatcher`).
"""

import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple, Union

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
# Types whose value is stored in the field record itself.
_INLINE_TYPES = frozenset(
    {
        GFFType.BYTE,
        GFFType.CHAR,
        GFFType.WORD,
        GFFType.SHORT,
        GFFType.DWORD,
        GFFType.INT,
        GFFType.FLOAT,
    }
)
# 8-byte types: the field record holds an offset into the field data block.
_WIDE_FORMATS = {GFFType.DWORD64: "<Q", GFFType.INT64: "<q", GFFType.DOUBLE: "<d"}


class GFFHeader(NamedTuple):
    """GFF V3.2 header.

    Attributes:
        file_type: FileType tag, e.g. ``b"DLG "``.
        version: Version tag, e.g. ``b"V3.2"``.
        struct_offset: File offset of the struct records.
        struct_count: Number of struct records.
        field_offset: File offset of the field records.
        field_count: Number of field records.
        label_offset: File offset of the labels.
        label_count: Number of labels.
        field_data_offset: File offset of the field data block.
        field_data_size: Byte size of the field data block.
        field_indices_offset: File offset of the field indices block.
        field_indices_size: Byte size of the field indices block.
        list_indices_offset: File offset of the list indices block.
        list_indices_size: Byte size of the list indices block.
    """

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
        """Unpacks the header at the start of *data*.

        Args:
            data: File bytes, at least 56 of them.

        Returns:
            The header.
        """
        return cls._make(HEADER.unpack_from(data))


class GFFParseError(Exception):
    """Raised when a GFF file cannot be read or parsed."""


class GFFPatchError(Exception):
    """Raised when a GFF file cannot be patched."""


@dataclass
class GFFValue:
    """A parsed field value with its type and location.

    Attributes:
        type: Field type.
        value: Decoded value; a CExoLocString is ``{"StrRef", "Value"}``, a
            List is a list of struct indices, a Struct is a struct index.
        record_offset: Absolute byte offset of the 12-byte field record.
    """

    type: GFFType
    value: Any
    record_offset: int = 0


@dataclass
class GFFStruct:
    """A parsed struct.

    Attributes:
        struct_id: StructID of the record.
        fields: Label -> value, in field order.
    """

    struct_id: int
    fields: Dict[str, GFFValue] = field(default_factory=dict)


@dataclass
class GFFFile:
    """A parsed GFF file.

    Attributes:
        file_type: FileType tag, e.g. ``b"DLG "``.
        version: Version tag, e.g. ``b"V3.2"``.
        struct_type: FileType without trailing spaces; ``None`` if not ASCII.
        structs: All structs; the first one is the root.
    """

    file_type: bytes = b""
    version: bytes = b""
    struct_type: Optional[str] = None
    structs: List[GFFStruct] = field(default_factory=list)


def parse_gff(file_path: Path, source_encoding: Optional[str] = None) -> GFFFile:
    """Parses a GFF file.

    Records that reference missing labels, fields or indices are tolerated;
    a header whose blocks lie outside the file is rejected before any
    per-element work, so a corrupt count cannot exhaust time or memory.

    Args:
        file_path: The GFF file.
        source_encoding: Declared code page of string bytes; ``None`` uses
            the cascade of :func:`~.text_codec.decode_module_text`.

    Returns:
        The parsed file.

    Raises:
        GFFParseError: If the file is too small or its header is corrupt.
    """
    data = Path(file_path).read_bytes()
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

    labels = [
        decode_fixed_ascii(raw)
        for (raw,) in LABEL.iter_unpack(
            data[header.label_offset : header.label_offset + header.label_count * LABEL.size]
        )
    ]
    fields: List[Tuple[str, GFFType, int, int]] = [
        (
            labels[label_index] if label_index < len(labels) else f"field_{label_index}",
            _TYPE_BY_ID.get(type_id, GFFType.DWORD),
            raw_value,
            header.field_offset + index * RECORD.size,
        )
        for index, (type_id, label_index, raw_value) in enumerate(
            RECORD.iter_unpack(
                data[header.field_offset : header.field_offset + header.field_count * RECORD.size]
            )
        )
    ]
    field_indices = struct.unpack_from(
        f"<{header.field_indices_size // 4}I", data, header.field_indices_offset
    )

    structs: List[GFFStruct] = []
    for struct_id, data_or_offset, field_count in RECORD.iter_unpack(
        data[header.struct_offset : header.struct_offset + header.struct_count * RECORD.size]
    ):
        # One field: DataOrDataOffset is the field index. Several: it is a
        # byte offset into the field indices block.
        members: Sequence[int] = ()
        if field_count == 1:
            members = (data_or_offset,)
        elif field_count > 1:
            start = data_or_offset // 4
            members = field_indices[start : start + field_count]
        gff_struct = GFFStruct(struct_id)
        for field_index in members:
            if field_index < len(fields):
                label, gff_type, raw_value, record_offset = fields[field_index]
                value = _field_value(data, header, gff_type, raw_value, source_encoding)
                gff_struct.fields[label] = GFFValue(gff_type, value, record_offset)
        structs.append(gff_struct)

    file_type = header.file_type
    try:
        struct_type: Optional[str] = file_type.rstrip(b" ").decode("ascii")
    except UnicodeDecodeError:
        struct_type = None
    return GFFFile(file_type, header.version, struct_type, structs)


def _field_value(
    data: bytes, header: GFFHeader, gff_type: GFFType, raw: int, encoding: Optional[str]
) -> Any:
    """Decodes one field; data that lies outside the file reads as empty."""
    if gff_type in _INLINE_TYPES:
        return _inline_value(gff_type, raw)
    if gff_type in _WIDE_FORMATS:
        offset = header.field_data_offset + raw
        if offset + 8 > len(data):
            return 0
        return struct.unpack_from(_WIDE_FORMATS[gff_type], data, offset)[0]
    if gff_type == GFFType.CExoString:
        offset = header.field_data_offset + raw
        if offset + 4 > len(data):
            return ""
        length = _DWORD.unpack_from(data, offset)[0]
        if length == 0 or offset + 4 + length > len(data):
            return ""
        return decode_module_text(data[offset + 4 : offset + 4 + length], encoding)
    if gff_type == GFFType.CResRef:
        offset = header.field_data_offset + raw
        if offset + 1 > len(data):
            return ""
        return data[offset + 1 : offset + 1 + data[offset]].decode("ascii", errors="ignore")
    if gff_type == GFFType.CExoLocString:
        return _locstring_value(data, header.field_data_offset + raw, encoding)
    if gff_type == GFFType.List:
        offset = header.list_indices_offset + raw
        if offset + 4 > len(data):
            return []
        count = min(_DWORD.unpack_from(data, offset)[0], (len(data) - offset - 4) // 4)
        return list(struct.unpack_from(f"<{count}I", data, offset + 4))
    if gff_type in (GFFType.Struct, GFFType.Unknown):
        # A Struct field holds a struct index, expanded by gff_to_dict().
        return raw
    return None  # VOID payloads are not needed by the translator.


def _inline_value(gff_type: GFFType, raw: int) -> Any:
    """Decodes a value stored in the field record's DataOrDataOffset DWORD."""
    if gff_type == GFFType.BYTE:
        return raw & 0xFF
    if gff_type == GFFType.CHAR:
        return chr(raw & 0xFF) if raw < 128 else "?"
    if gff_type == GFFType.WORD:
        return raw & 0xFFFF
    if gff_type == GFFType.SHORT:
        return raw - 0x10000 if raw & 0x8000 else raw
    if gff_type == GFFType.INT:
        return raw - 0x100000000 if raw & 0x80000000 else raw
    if gff_type == GFFType.FLOAT:
        return struct.unpack("<f", _DWORD.pack(raw))[0]
    return raw  # DWORD


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


def _expand_struct(
    struct_fields: Dict[str, GFFValue], gff: GFFFile, visited: set
) -> Dict[str, Any]:
    """Recursively expands struct fields into plain values and nested dicts.

    List fields (lists of struct indices) and Struct fields (one struct index)
    become nested dicts; invalid or already-visited indices stay ints, so
    malformed files cannot recurse forever.

    Args:
        struct_fields: Fields of one parsed struct.
        gff: The parsed file, to look up struct indices.
        visited: Struct indices on the current path.

    Returns:
        Label -> value, plus ``_field_types`` (label -> type id) and
        ``_record_offsets`` (label -> field record offset).
    """
    result: Dict[str, Any] = {}
    field_types: Dict[str, int] = {}
    record_offsets: Dict[str, int] = {}
    for key, gff_value in struct_fields.items():
        value = gff_value.value
        field_types[key] = int(gff_value.type)
        record_offsets[key] = gff_value.record_offset
        if isinstance(value, list):
            result[key] = [
                (
                    _expand_struct(gff.structs[index].fields, gff, visited | {index})
                    if isinstance(index, int)
                    and 0 <= index < len(gff.structs)
                    and index not in visited
                    else index
                )
                for index in value
            ]
        elif (
            gff_value.type == GFFType.Struct
            and isinstance(value, int)
            and 0 <= value < len(gff.structs)
            and value not in visited
        ):
            result[key] = _expand_struct(gff.structs[value].fields, gff, visited | {value})
        else:
            result[key] = value
    result["_field_types"] = field_types
    result["_record_offsets"] = record_offsets
    return result


def gff_to_dict(gff: GFFFile) -> Dict[str, Any]:
    """Converts a parsed file into one nested dict rooted at the first struct.

    Args:
        gff: The parsed file.

    Returns:
        ``{"StructType": ..., <root fields>, "_field_types": ...,
        "_record_offsets": ...}`` with lists and structs expanded, or ``{}``
        for a file without structs.
    """
    if not gff.structs:
        return {}
    return {"StructType": gff.struct_type, **_expand_struct(gff.structs[0].fields, gff, {0})}


def read_gff(
    file_path: Path,
    cache: Optional[Dict[Path, Dict[str, Any]]] = None,
    source_encoding: Optional[str] = None,
) -> Dict[str, Any]:
    """Parses a GFF file into a dict (see :func:`gff_to_dict`).

    Args:
        file_path: The GFF file.
        cache: Optional session cache keyed by resolved path. Callers sharing a
            cache must pass the same *source_encoding* for every read.
        source_encoding: Declared code page of string bytes.

    Returns:
        The parsed dict (the cached object on a cache hit).

    Raises:
        GFFParseError: If the file is missing or cannot be parsed; the
            message names the file.
    """
    path = Path(file_path).resolve()
    if cache is not None and path in cache:
        return cache[path]
    if not path.exists():
        raise GFFParseError(f"File not found: {path}")
    try:
        data = gff_to_dict(parse_gff(path, source_encoding=source_encoding))
    except GFFParseError as e:
        raise GFFParseError(f"Failed to parse GFF file {path}: {e}") from e
    except Exception as e:
        raise GFFParseError(f"Failed to read GFF file {path}: {e}") from e
    if cache is not None:
        cache[path] = data
    return data


class GFFPatcher:
    """Rewrites CExoLocString fields of one GFF file in place.

    New payloads are appended to the end of the field data block, the field
    records are pointed at them, and the blocks after field data move back by
    the total payload length. The old payloads stay in the file unreferenced.

    Attributes:
        file_path: The GFF file.
    """

    def __init__(self, file_path: Path, text_encoding: str = "cp1251"):
        """Binds the patcher to a file and a code page.

        Args:
            file_path: The GFF file to modify.
            text_encoding: One of :data:`~.text_codec.MODULE_ENCODINGS`.

        Raises:
            GFFPatchError: If the encoding is not allowed or the file is missing.
        """
        if text_encoding not in MODULE_ENCODINGS:
            raise GFFPatchError(f"Unsupported module text encoding: {text_encoding!r}")
        self.file_path = file_path
        self._text_encoding = text_encoding
        if not self.file_path.exists():
            raise GFFPatchError(f"File not found: {self.file_path}")

    def patch_multiple(self, patches: List[Tuple[int, str]]) -> None:
        """Replaces the text of several CExoLocString fields in one write.

        Payloads land in patch order, so a repeated record offset ends up
        pointing at its last payload. Each payload holds one substring with
        LanguageID 0: every client shows that slot directly or through the
        engine's language fallback, and target languages without an official
        NWN language id have no other choice. A field that carried several
        substrings (gender or language variants) is therefore collapsed, with
        a warning.

        Args:
            patches: ``(record_offset, new_text)`` per 12-byte field record.

        Raises:
            GFFPatchError: If the file is shorter than a GFF header or a
                record offset is not positive; the file is left unchanged.
        """
        if not patches:
            return
        data = bytearray(self.file_path.read_bytes())
        if len(data) < HEADER.size:
            raise GFFPatchError("File too small to be a valid GFF header")
        header = GFFHeader.read(data)
        insert_at = header.field_data_offset + header.field_data_size

        payloads: List[bytes] = []
        next_data_offset = header.field_data_size
        patched_offsets: Set[int] = set()
        for record_offset, new_text in patches:
            if record_offset <= 0:
                raise GFFPatchError("Invalid record offset provided")
            # A repeated offset already points at its (not yet spliced)
            # replacement, so its substring count means nothing.
            if record_offset not in patched_offsets:
                substring_count = _substring_count(data, record_offset)
                if substring_count > 1:
                    logger.warning(
                        "%s: overwriting %d substrings at field record offset %d with a single "
                        "LanguageID-0 substring; gender/language variants are lost",
                        self.file_path.name,
                        substring_count,
                        record_offset,
                    )
            patched_offsets.add(record_offset)
            payload = _locstring_payload(encode_module_text(new_text, self._text_encoding))
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
        self.file_path.write_bytes(new_data)


def _substring_count(data: Union[bytes, bytearray], record_offset: int) -> int:
    """Returns the SubStringCount of the CExoLocString whose record is at *record_offset*.

    Returns 0 when the record or its payload lies outside the file.
    """
    if record_offset + 12 > len(data):
        return 0
    payload_offset = GFFHeader.read(data).field_data_offset
    payload_offset += _DWORD.unpack_from(data, record_offset + 8)[0]
    if payload_offset + 12 > len(data):
        return 0
    return int(_DWORD.unpack_from(data, payload_offset + 8)[0])


def _locstring_payload(encoded: bytes) -> bytes:
    """Builds a CExoLocString payload: StrRef -1 and at most one LanguageID-0 substring."""
    if not encoded:
        return LOCSTRING_HEAD.pack(8, -1, 0)
    return (
        LOCSTRING_HEAD.pack(16 + len(encoded), -1, 1)
        + SUBSTRING_HEAD.pack(0, len(encoded))
        + encoded
    )
