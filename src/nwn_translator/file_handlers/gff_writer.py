"""GFF v3.2 binary writer for Neverwinter Nights: Enhanced Edition.

This module serialises the dictionary produced by ``gff_to_dict`` /
``GFFHandler.read`` back into a valid GFF v3.2 binary file, following the
same layout as the NWN:EE engine expects.

GFF v3.2 binary layout (header at offset 0, 56 bytes):
    file_type           [0:4]   4-char type tag, e.g. b"DLG "
    version             [4:8]   always b"V3.2"
    StructOffset        [8:12]
    StructCount         [12:16]
    FieldOffset         [16:20]
    FieldCount          [20:24]
    LabelOffset         [24:28]
    LabelCount          [28:32]
    FieldDataOffset     [32:36]
    FieldDataByteSize   [36:40]
    FieldIndicesOffset  [40:44]
    FieldIndicesByteSize[44:48]
    ListIndicesOffset   [48:52]
    ListIndicesByteSize [52:56]
    [padding to 160 bytes]

struct record  — 12 bytes: StructID(4) | DataOrDataOffset(4) | FieldCount(4)
field record   — 12 bytes: Type(4) | LabelIndex(4) | DataOrDataOffset(4)
label record   — 16 bytes: null-padded ASCII string
field data     — raw bytes for complex types
field indices  — DWORD[] mapping struct → field entries
list indices   — DWORD[]: Count followed by struct indices
"""

import struct as _struct
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .gff_parser import GFFType

logger = logging.getLogger(__name__)

# Number of bytes before the first actual section (header is 160 bytes)
_HEADER_SIZE = 160


class GFFWriteError(Exception):
    """Exception raised for GFF write errors."""
    pass


class GFFWriter:
    """Serialises a GFF dict (as produced by gff_to_dict) to GFF v3.2 bytes.

    Usage::

        writer = GFFWriter(gff_dict, file_type="DLG")
        writer.write(Path("output.dlg"))
    """

    # Types stored inline (4-byte field DataOrDataOffset carries the value).
    _INLINE_TYPES = {
        GFFType.BYTE,
        GFFType.CHAR,
        GFFType.WORD,
        GFFType.SHORT,
        GFFType.DWORD,
        GFFType.INT,
        GFFType.FLOAT,
    }

    # Types stored in the Field Data block (DataOrDataOffset = byte offset).
    _FIELDDATA_TYPES = {
        GFFType.DWORD64,
        GFFType.INT64,
        GFFType.DOUBLE,
        GFFType.CExoString,
        GFFType.CResRef,
        GFFType.CExoLocString,
        GFFType.VOID,
    }

    def __init__(self, data: Dict[str, Any], file_type: Optional[str] = None):
        """Initialise the writer.

        Args:
            data: Dict as returned by GFFHandler.read() / gff_to_dict().
            file_type: 4-char GFF type tag (e.g. ``"DLG"``).  If *None*,
                taken from ``data["StructType"]``.
        """
        self._data = data
        raw_type = file_type or data.get("StructType", "GFF")
        # Pad / trim to exactly 4 bytes
        padded = (raw_type.upper() + "    ")[:4]
        self._file_type: bytes = padded.encode("ascii")

        # Build tables incrementally
        self._structs:       List[bytes] = []   # 12-byte records
        self._fields:        List[bytes] = []   # 12-byte records
        self._labels:        List[bytes] = []   # 16-byte records per label
        self._label_index:   Dict[str, int] = {}
        self._field_data:    bytearray = bytearray()
        self._field_indices: bytearray = bytearray()  # DWORD array
        self._list_indices:  bytearray = bytearray()  # DWORD arrays

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, file_path: Path) -> None:
        """Serialise the GFF dict and write to *file_path*.

        Args:
            file_path: Destination path (parent directories will be created).

        Raises:
            GFFWriteError: If serialisation fails.
        """
        try:
            binary = self._build()
        except Exception as exc:
            raise GFFWriteError(f"Failed to serialise GFF: {exc}") from exc

        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(binary)
        logger.debug("GFF written to %s (%d bytes)", file_path, len(binary))

    def to_bytes(self) -> bytes:
        """Return the GFF binary as a bytes object (without writing to disk).

        Returns:
            Binary GFF data.

        Raises:
            GFFWriteError: If serialisation fails.
        """
        try:
            return self._build()
        except Exception as exc:
            raise GFFWriteError(f"Failed to serialise GFF: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal build pipeline
    # ------------------------------------------------------------------

    def _build(self) -> bytes:
        """Build and return the complete GFF binary.

        Returns:
            Complete GFF binary data.
        """
        # Step 1: recursively convert the root dict into struct/field tables.
        # The root dict fields (minus "StructType") correspond to struct 0.
        root_fields = {k: v for k, v in self._data.items() if k != "StructType"}
        self._emit_struct(root_fields, struct_id=0xFFFFFFFF)

        # Step 2: calculate section offsets.
        struct_count   = len(self._structs)
        field_count    = len(self._fields)
        label_count    = len(self._labels)
        fd_size        = len(self._field_data)
        fi_size        = len(self._field_indices)
        li_size        = len(self._list_indices)

        struct_offset      = _HEADER_SIZE
        field_offset       = struct_offset  + struct_count * 12
        label_offset       = field_offset   + field_count  * 12
        field_data_offset  = label_offset   + label_count  * 16
        field_indices_offset = field_data_offset + fd_size
        list_indices_offset  = field_indices_offset + fi_size

        # Step 3: assemble header.
        header = bytearray(_HEADER_SIZE)
        header[0:4]   = self._file_type
        header[4:8]   = b"V3.2"
        _wi(header,  8, struct_offset)
        _wi(header, 12, struct_count)
        _wi(header, 16, field_offset)
        _wi(header, 20, field_count)
        _wi(header, 24, label_offset)
        _wi(header, 28, label_count)
        _wi(header, 32, field_data_offset)
        _wi(header, 36, fd_size)
        _wi(header, 40, field_indices_offset)
        _wi(header, 44, fi_size)
        _wi(header, 48, list_indices_offset)
        _wi(header, 52, li_size)
        # Bytes 56–159 remain zero (unused header fields).

        # Step 4: concatenate all sections.
        parts: List[bytes] = [
            bytes(header),
            b"".join(self._structs),
            b"".join(self._fields),
            b"".join(self._labels),
            bytes(self._field_data),
            bytes(self._field_indices),
            bytes(self._list_indices),
        ]
        return b"".join(parts)

    # ------------------------------------------------------------------
    # Struct / field emission
    # ------------------------------------------------------------------

    def _emit_struct(self, fields_dict: Dict[str, Any], struct_id: int) -> int:
        """Emit one struct record and all its fields.

        Args:
            fields_dict: Key/value pairs of GFF fields in this struct.
            struct_id: The GFF StructID (0xFFFFFFFF for root struct).

        Returns:
            Index of the newly emitted struct in ``self._structs``.
        """
        struct_index = len(self._structs)
        # Reserve a slot first (so forward references work during recursion).
        self._structs.append(b"\x00" * 12)

        field_count = len(fields_dict)

        if field_count == 0:
            # No fields — DataOrDataOffset is 0xFFFFFFFF by convention.
            record = _pack_struct(struct_id, 0xFFFFFFFF, 0)
            self._structs[struct_index] = record
            return struct_index

        if field_count == 1:
            # Single field: DataOrDataOffset is the direct field index.
            label, value = next(iter(fields_dict.items()))
            if label == "_field_types":
                record = _pack_struct(struct_id, 0xFFFFFFFF, 0)
                self._structs[struct_index] = record
                return struct_index
            field_types = fields_dict.get("_field_types", {})
            explicit_type = field_types.get(label)
            field_index = self._emit_field(label, value, explicit_type)
            record = _pack_struct(struct_id, field_index, 1)
            self._structs[struct_index] = record
            return struct_index

        # Multiple fields: DataOrDataOffset = byte offset into field-indices.
        # IMPORTANT: emit ALL fields first (which may recursively add to
        # _field_indices via child structs), then record the tail offset before
        # appending this struct's own field indices.
        collected_field_indices: List[int] = []
        field_types = fields_dict.get("_field_types", {})
        for label, value in fields_dict.items():
            if label.startswith("_") and label != "_struct_id":
                continue
            explicit_type = field_types.get(label)
            field_index = self._emit_field(label, value, explicit_type)
            collected_field_indices.append(field_index)

        # Update field_count since we skipped internal keys like _field_types
        field_count = len(collected_field_indices)
        if field_count == 0:
            record = _pack_struct(struct_id, 0xFFFFFFFF, 0)
            self._structs[struct_index] = record
            return struct_index

        # This offset is now safe: all recursive emissions have finished.
        fi_byte_offset = len(self._field_indices)
        for fi in collected_field_indices:
            self._field_indices += _struct.pack("<I", fi)

        record = _pack_struct(struct_id, fi_byte_offset, field_count)
        self._structs[struct_index] = record
        return struct_index

    def _emit_field(self, label: str, value: Any, explicit_type: Optional[int] = None) -> int:
        """Emit one field record plus any required side data.

        Args:
            label: GFF field label (≤16 ASCII chars).
            value: Python value to serialise.
            explicit_type: Optional exact GFFType ID to preserve formatting.

        Returns:
            Index of the new field in ``self._fields``.
        """
        label_index = self._get_label_index(label)
        gff_type, data_or_offset = self._encode_value(label, value, explicit_type)

        record = _pack_field(int(gff_type), label_index, data_or_offset)
        field_index = len(self._fields)
        self._fields.append(record)
        return field_index

    # ------------------------------------------------------------------
    # Value encoding
    # ------------------------------------------------------------------

    def _encode_value(self, label: str, value: Any, explicit_type: Optional[int] = None) -> Tuple[GFFType, int]:
        """Determine GFF type and DataOrDataOffset for a Python value.

        The type is inferred from `explicit_type` if provided, otherwise heuristically.

        Args:
            label: Field label (used for diagnostics only).
            value: Python value to encode.
            explicit_type: Exact GFFType ID to enforce.

        Returns:
            Tuple of (GFFType, data_or_offset).
        """
        if explicit_type is not None and explicit_type != 0xFF:
            try:
                gff_type = GFFType(explicit_type)
                if gff_type == GFFType.CExoLocString:
                    return gff_type, self._encode_locstring(value if isinstance(value, dict) else {"StrRef": -1, "Value": str(value)})
                elif gff_type == GFFType.Struct:
                    return gff_type, self._emit_struct(value, struct_id=0)
                elif gff_type == GFFType.List:
                    return gff_type, self._encode_list(value)
                elif gff_type == GFFType.CResRef:
                    return gff_type, self._encode_resref(str(value))
                elif gff_type == GFFType.CExoString:
                    return gff_type, self._encode_exostring(str(value))
                elif gff_type in (GFFType.BYTE, GFFType.CHAR):
                    return gff_type, int(value) & 0xFF if not isinstance(value, str) else ord(value[0])
                elif gff_type in (GFFType.WORD, GFFType.SHORT):
                    return gff_type, int(value) & 0xFFFF
                elif gff_type == GFFType.DWORD:
                    return gff_type, int(value) & 0xFFFFFFFF
                elif gff_type == GFFType.INT:
                    packed = _struct.pack("<i", max(-2**31, min(int(value), 2**31-1)))
                    return gff_type, _struct.unpack("<I", packed)[0]
                elif gff_type == GFFType.DWORD64:
                    return gff_type, self._encode_int64(int(value), signed=False)
                elif gff_type == GFFType.INT64:
                    return gff_type, self._encode_int64(int(value), signed=True)
                elif gff_type == GFFType.FLOAT:
                    packed_f = _struct.pack("<f", float(value))
                    return gff_type, _struct.unpack("<I", packed_f)[0]
                elif gff_type == GFFType.DOUBLE:
                    offset = len(self._field_data)
                    self._field_data += _struct.pack("<d", float(value))
                    return gff_type, offset
                elif gff_type == GFFType.VOID:
                    return gff_type, self._encode_void(value if isinstance(value, bytes) else b"")
            except Exception as e:
                logger.warning("Failed to encode explicit type %s for label '%s': %s", explicit_type, label, e)

        # --- dict → CExoLocString or nested Struct ---
        if isinstance(value, dict):
            if "StrRef" in value or "Value" in value:
                return GFFType.CExoLocString, self._encode_locstring(value)
            # Nested struct
            child_idx = self._emit_struct(value, struct_id=0)
            return GFFType.Struct, child_idx

        # --- list → GFF List ---
        if isinstance(value, list):
            return GFFType.List, self._encode_list(value)

        # --- str → CExoString (generic) or CResRef (small, no spaces) ---
        if isinstance(value, str):
            # Heuristic: ResRefs are ≤16 chars, lowercase, no spaces
            if len(value) <= 16 and " " not in value and value == value.lower():
                return GFFType.CResRef, self._encode_resref(value)
            return GFFType.CExoString, self._encode_exostring(value)

        # --- bool must come before int (bool is subclass of int) ---
        if isinstance(value, bool):
            return GFFType.BYTE, int(value)

        # --- int → DWORD or INT depending on sign ---
        if isinstance(value, int):
            if value < 0:
                # Store as INT (signed 32-bit); pack as unsigned for the field
                packed = _struct.pack("<i", max(-2**31, value))
                data_or_offset = _struct.unpack("<I", packed)[0]
                return GFFType.INT, data_or_offset
            # Unsigned; if > 32-bit store as DWORD64 in field data
            if value > 0xFFFFFFFF:
                return GFFType.DWORD64, self._encode_int64(value, signed=False)
            return GFFType.DWORD, value & 0xFFFFFFFF

        # --- float ---
        if isinstance(value, float):
            packed_f = _struct.pack("<f", value)
            data_or_offset = _struct.unpack("<I", packed_f)[0]
            return GFFType.FLOAT, data_or_offset

        # --- bytes → VOID ---
        if isinstance(value, bytes):
            return GFFType.VOID, self._encode_void(value)

        # --- fallback: convert to string ---
        logger.warning("GFFWriter: unknown value type %s for label '%s', storing as CExoString",
                       type(value).__name__, label)
        return GFFType.CExoString, self._encode_exostring(str(value))

    # ------------------------------------------------------------------
    # Field-data encoders (complex types)
    # ------------------------------------------------------------------

    def _encode_exostring(self, text: str) -> int:
        """Encode a CExoString into field data.

        Args:
            text: String to encode.

        Returns:
            Byte offset into ``self._field_data``.
        """
        encoded = text.encode("utf-8")
        offset = len(self._field_data)
        self._field_data += _struct.pack("<I", len(encoded))
        self._field_data += encoded
        return offset

    def _encode_resref(self, text: str) -> int:
        """Encode a CResRef into field data.

        Args:
            text: ResRef string (max 16 chars ASCII).

        Returns:
            Byte offset into ``self._field_data``.
        """
        encoded = text.encode("ascii")[:16]
        offset = len(self._field_data)
        self._field_data += bytes([len(encoded)])
        self._field_data += encoded
        return offset

    def _encode_locstring(self, loc: Dict[str, Any]) -> int:
        """Encode a CExoLocString into field data.

        The dict is expected to carry ``StrRef`` (int, default -1) and
        optionally ``Value`` (str).  We write exactly one substring (language
        ID 0 = English) when *Value* is non-empty.

        Args:
            loc: Dict with optional keys ``StrRef`` and ``Value``.

        Returns:
            Byte offset into ``self._field_data``.
        """
        str_ref = int(loc.get("StrRef", -1))
        text    = loc.get("Value", "") or ""
        encoded = text.encode("utf-8") if text else b""

        substring_count = 1 if encoded else 0
        # TotalSize covers everything after the first DWORD (TotalSize itself).
        # TotalSize = 4 (StrRef) + 4 (SubStringCount) + 8*substring_count + len(encoded)
        total_size = 4 + 4 + (8 + len(encoded)) * substring_count

        offset = len(self._field_data)
        self._field_data += _struct.pack("<I", total_size)          # TotalSize
        self._field_data += _struct.pack("<i", str_ref)             # StrRef (signed)
        self._field_data += _struct.pack("<I", substring_count)     # SubStringCount

        if encoded:
            self._field_data += _struct.pack("<I", 0)               # LanguageID = 0 (English)
            self._field_data += _struct.pack("<I", len(encoded))    # Length
            self._field_data += encoded                             # String bytes

        return offset

    def _encode_int64(self, value: int, signed: bool = True) -> int:
        """Encode a 64-bit integer into field data.

        Args:
            value: Integer value.
            signed: If True encode as INT64, otherwise DWORD64.

        Returns:
            Byte offset into ``self._field_data``.
        """
        fmt = "<q" if signed else "<Q"
        offset = len(self._field_data)
        self._field_data += _struct.pack(fmt, value)
        return offset

    def _encode_void(self, data: bytes) -> int:
        """Encode raw bytes (VOID) into field data.

        Args:
            data: Raw binary data.

        Returns:
            Byte offset into ``self._field_data``.
        """
        offset = len(self._field_data)
        self._field_data += _struct.pack("<I", len(data))
        self._field_data += data
        return offset

    # ------------------------------------------------------------------
    # List / nested struct encoding
    # ------------------------------------------------------------------

    def _encode_list(self, items: List[Any]) -> int:
        """Encode a GFF List into the list-indices block.

        Each element in *items* must be a dict representing a child struct.

        Args:
            items: List of dicts, each representing a child struct.

        Returns:
            Byte offset into ``self._list_indices``.
        """
        offset = len(self._list_indices)
        self._list_indices += _struct.pack("<I", len(items))  # Count

        for item in items:
            if not isinstance(item, dict):
                logger.warning("GFFWriter: List element is not a dict (%s), skipping.", type(item))
                self._list_indices += _struct.pack("<I", 0)
                continue
            child_idx = self._emit_struct(item, struct_id=0)
            self._list_indices += _struct.pack("<I", child_idx)

        return offset

    # ------------------------------------------------------------------
    # Label table helpers
    # ------------------------------------------------------------------

    def _get_label_index(self, label: str) -> int:
        """Return the label table index for *label*, adding it if new.

        Args:
            label: GFF field label (max 16 chars).

        Returns:
            Index into the labels table.
        """
        if label not in self._label_index:
            idx = len(self._labels)
            self._label_index[label] = idx
            encoded = label.encode("ascii", errors="replace")[:16]
            padded = encoded.ljust(16, b"\x00")
            self._labels.append(padded)
        return self._label_index[label]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _wi(buf: bytearray, offset: int, value: int) -> None:
    """Write a little-endian DWORD into *buf* at *offset* in-place.

    Args:
        buf: Mutable bytearray to write into.
        offset: Byte offset.
        value: Unsigned 32-bit value.
    """
    _struct.pack_into("<I", buf, offset, value & 0xFFFFFFFF)


def _pack_struct(struct_id: int, data_or_offset: int, field_count: int) -> bytes:
    """Pack a 12-byte struct record.

    Args:
        struct_id: GFF StructID.
        data_or_offset: DataOrDataOffset field.
        field_count: Number of fields.

    Returns:
        12-byte packed binary.
    """
    return _struct.pack(
        "<III",
        struct_id & 0xFFFFFFFF,
        data_or_offset & 0xFFFFFFFF,
        field_count,
    )


def _pack_field(gff_type: int, label_index: int, data_or_offset: int) -> bytes:
    """Pack a 12-byte field record.

    Args:
        gff_type: GFF type identifier.
        label_index: Index into the label table.
        data_or_offset: DataOrDataOffset field.

    Returns:
        12-byte packed binary.
    """
    return _struct.pack(
        "<III",
        gff_type & 0xFFFFFFFF,
        label_index & 0xFFFFFFFF,
        data_or_offset & 0xFFFFFFFF,
    )


def write_gff_bytes(data: Dict[str, Any], file_type: Optional[str] = None) -> bytes:
    """Serialise *data* to GFF v3.2 bytes without writing to disk.

    Args:
        data: Dict as returned by ``GFFHandler.read()`` / ``gff_to_dict()``.
        file_type: Optional 4-char type tag override.

    Returns:
        Complete GFF binary.

    Raises:
        GFFWriteError: If serialisation fails.
    """
    return GFFWriter(data, file_type=file_type).to_bytes()


def write_gff(file_path: Path, data: Dict[str, Any], file_type: Optional[str] = None) -> None:
    """Write *data* to a GFF v3.2 binary file at *file_path*.

    Args:
        file_path: Destination path.
        data: Dict as returned by ``GFFHandler.read()`` / ``gff_to_dict()``.
        file_type: Optional 4-char type tag override.

    Raises:
        GFFWriteError: If serialisation fails.
    """
    GFFWriter(data, file_type=file_type).write(Path(file_path))
