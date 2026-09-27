"""GFF V3.2 serialiser for building test fixtures.

Turns a dict shaped like the output of :func:`nwn_translator.formats.gff.parse_gff`
back into a GFF binary; the translator itself only byte-patches fields. The
56-byte header is padded to 160 bytes, followed by structs, fields, labels,
field data, field indices and list indices. Strings are written as UTF-8.
"""

import logging
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nwn_translator.formats.gff import HEADER, RECORD, GFFType

logger = logging.getLogger(__name__)

_HEADER_SIZE = 160


class GFFWriter:
    """Serialises one GFF dict.

    Keys starting with ``_`` are not fields: ``_field_types`` pins the type id
    of a field, ``_struct_id`` sets the id of its struct and ``_record_offsets``
    is ignored. Other values get their type from the Python value.
    """

    def __init__(self, data: Dict[str, Any], file_type: Optional[str] = None) -> None:
        """Prepare empty tables for *data*.

        Args:
            data: Dict as returned by ``read_gff()``.
            file_type: 4-character type tag; defaults to ``data["StructType"]``.
        """
        tag = (file_type or data.get("StructType", "GFF")).upper()
        self._file_type = (tag + "    ")[:4].encode("ascii")
        self._data = data
        self._structs: List[bytes] = []
        self._fields: List[bytes] = []
        self._labels: Dict[str, int] = {}
        self._field_data = bytearray()
        self._field_indices = bytearray()
        self._list_indices = bytearray()

    def to_bytes(self) -> bytes:
        """Serialise the dict.

        Returns:
            The complete GFF binary.
        """
        self._emit_struct({k: v for k, v in self._data.items() if k != "StructType"}, 0xFFFFFFFF)
        labels = b"".join(
            label.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
            for label in self._labels
        )
        blocks = [b"".join(self._structs), b"".join(self._fields), labels]
        blocks += [bytes(self._field_data), bytes(self._field_indices), bytes(self._list_indices)]
        counts = [len(self._structs), len(self._fields), len(self._labels)]
        counts += [len(block) for block in blocks[3:]]
        values: List[int] = []
        offset = _HEADER_SIZE
        for block, count in zip(blocks, counts):
            values += [offset, count]
            offset += len(block)
        header = bytearray(_HEADER_SIZE)
        HEADER.pack_into(header, 0, self._file_type, b"V3.2", *values)
        return bytes(header) + b"".join(blocks)

    def _emit_struct(self, fields: Dict[str, Any], struct_id: int) -> int:
        """Emit a struct after its fields; its field indices form one contiguous run.

        Args:
            fields: Labels and values of the struct, plus the ``_`` keys.
            struct_id: Struct id unless ``fields["_struct_id"]`` sets one.

        Returns:
            Index of the struct.
        """
        index = len(self._structs)
        self._structs.append(b"")
        struct_id = int(fields.get("_struct_id", struct_id))
        types = fields.get("_field_types", {})
        indices = [
            self._emit_field(label, value, types.get(label))
            for label, value in fields.items()
            if not label.startswith("_")
        ]
        if not indices:
            data = 0xFFFFFFFF
        elif len(indices) == 1:
            data = indices[0]
        else:
            data = len(self._field_indices)
            self._field_indices += struct.pack(f"<{len(indices)}I", *indices)
        self._structs[index] = RECORD.pack(struct_id & 0xFFFFFFFF, data, len(indices))
        return index

    def _emit_field(self, label: str, value: Any, explicit_type: Optional[int]) -> int:
        """Emit one field record.

        Args:
            label: Field label.
            value: Field value.
            explicit_type: Pinned GFF type id, or ``None`` to derive it from *value*.

        Returns:
            Index of the field.
        """
        label_index = self._labels.setdefault(label, len(self._labels))
        gff_type, data = self._encode(label, value, explicit_type)
        self._fields.append(RECORD.pack(int(gff_type), label_index, data & 0xFFFFFFFF))
        return len(self._fields) - 1

    def _encode(self, label: str, value: Any, explicit_type: Optional[int]) -> Tuple[GFFType, int]:
        """Return the field type and DataOrDataOffset, adding any side data.

        A pinned type that is unhandled or fails to encode falls back to the type of *value*.

        Args:
            label: Field label, for the log.
            value: Field value.
            explicit_type: Pinned GFF type id; ``None`` or ``0xFF`` pin nothing.

        Returns:
            The field type and the record's data word.
        """
        if explicit_type is not None and explicit_type != 0xFF:
            try:
                encoded = self._encode_as(GFFType(explicit_type), value)
                if encoded is not None:
                    return encoded
            except Exception as exc:  # noqa: BLE001 - fall back to the value's own type
                logger.warning(
                    "Failed to encode explicit type %s for label '%s': %s",
                    explicit_type,
                    label,
                    exc,
                )
        if isinstance(value, dict):
            if "StrRef" in value or "Value" in value:
                return GFFType.CExoLocString, self._locstring(value)
            return GFFType.Struct, self._emit_struct(value, 0)
        if isinstance(value, list):
            return GFFType.List, self._list(value)
        if isinstance(value, str):
            # Short lower-case words without spaces look like resrefs.
            if len(value) <= 16 and " " not in value and value == value.lower():
                return GFFType.CResRef, self._resref(value)
            return GFFType.CExoString, self._exostring(value)
        if isinstance(value, bool):
            return GFFType.BYTE, int(value)
        if isinstance(value, int):
            if value < 0:
                return GFFType.INT, _int32(value)
            if value > 0xFFFFFFFF:
                return GFFType.DWORD64, self._append(struct.pack("<Q", value))
            return GFFType.DWORD, value
        if isinstance(value, float):
            return GFFType.FLOAT, struct.unpack("<I", struct.pack("<f", value))[0]
        if isinstance(value, bytes):
            return GFFType.VOID, self._sized(value)
        logger.warning(
            "GFFWriter: unknown value type %s for label '%s', storing as CExoString",
            type(value).__name__,
            label,
        )
        return GFFType.CExoString, self._exostring(str(value))

    def _encode_as(self, gff_type: GFFType, value: Any) -> Optional[Tuple[GFFType, int]]:
        """Encode *value* as the pinned *gff_type*.

        Args:
            gff_type: The pinned type.
            value: Field value.

        Returns:
            The type and the record's data word, or ``None`` for an unhandled type.
        """
        if gff_type == GFFType.CExoLocString:
            loc = value if isinstance(value, dict) else {"StrRef": -1, "Value": str(value)}
            return gff_type, self._locstring(loc)
        if gff_type == GFFType.Struct:
            return gff_type, self._emit_struct(value, 0)
        if gff_type == GFFType.List:
            return gff_type, self._list(value)
        if gff_type == GFFType.CResRef:
            return gff_type, self._resref(str(value))
        if gff_type == GFFType.CExoString:
            return gff_type, self._exostring(str(value))
        if gff_type in (GFFType.BYTE, GFFType.CHAR):
            return gff_type, ord(value[0]) if isinstance(value, str) else int(value) & 0xFF
        if gff_type in (GFFType.WORD, GFFType.SHORT):
            return gff_type, int(value) & 0xFFFF
        if gff_type == GFFType.DWORD:
            return gff_type, int(value) & 0xFFFFFFFF
        if gff_type == GFFType.INT:
            return gff_type, _int32(int(value))
        if gff_type in (GFFType.DWORD64, GFFType.INT64):
            return gff_type, self._append(
                struct.pack("<q" if gff_type == GFFType.INT64 else "<Q", int(value))
            )
        if gff_type == GFFType.FLOAT:
            return gff_type, struct.unpack("<I", struct.pack("<f", float(value)))[0]
        if gff_type == GFFType.DOUBLE:
            return gff_type, self._append(struct.pack("<d", float(value)))
        if gff_type == GFFType.VOID:
            return gff_type, self._sized(value if isinstance(value, bytes) else b"")
        return None

    def _append(self, raw: bytes) -> int:
        """Append *raw* to the field data and return its offset."""
        offset = len(self._field_data)
        self._field_data += raw
        return offset

    def _sized(self, raw: bytes) -> int:
        """Append *raw* after its 32-bit length and return the offset."""
        return self._append(struct.pack("<I", len(raw)) + raw)

    def _exostring(self, text: str) -> int:
        """Append a CExoString and return its offset."""
        return self._sized(text.encode("utf-8"))

    def _resref(self, text: str) -> int:
        """Append a CResRef (at most 16 bytes) and return its offset."""
        encoded = text.encode("ascii")[:16]
        return self._append(bytes([len(encoded)]) + encoded)

    def _locstring(self, loc: Dict[str, Any]) -> int:
        """Append a CExoLocString: one substring with language id 0 when the value is non-empty.

        Args:
            loc: ``{"StrRef": ..., "Value": ...}`` dict.

        Returns:
            Offset of the value in the field data.
        """
        encoded = (loc.get("Value", "") or "").encode("utf-8")
        count = 1 if encoded else 0
        payload = struct.pack(
            "<IiI", 8 + (8 + len(encoded)) * count, int(loc.get("StrRef", -1)), count
        )
        if encoded:
            payload += struct.pack("<II", 0, len(encoded)) + encoded
        return self._append(payload)

    def _list(self, items: List[Any]) -> int:
        """Emit the child structs first, then this list's count and indices.

        Args:
            items: Child structs; other values are skipped with a warning.

        Returns:
            Offset of the list in the list indices.
        """
        children: List[int] = []
        for item in items:
            if not isinstance(item, dict):
                logger.warning("GFFWriter: List element is not a dict (%s), skipping.", type(item))
                continue
            children.append(self._emit_struct(item, 0))
        offset = len(self._list_indices)
        self._list_indices += struct.pack(f"<{len(children) + 1}I", len(children), *children)
        return offset


def _int32(value: int) -> int:
    """A signed 32-bit value, clamped, as its unsigned record bits."""
    clamped = max(-(2**31), min(value, 2**31 - 1))
    return struct.unpack("<I", struct.pack("<i", clamped))[0]


def loc(text: str, strref: int = -1) -> Dict[str, Any]:
    """A CExoLocString value shaped like the output of ``read_gff()``.

    Args:
        text: The embedded string.
        strref: The ``dialog.tlk`` reference; -1 for none.

    Returns:
        The ``{"StrRef": ..., "Value": ...}`` dict.
    """
    return {"StrRef": strref, "Value": text}


def write_gff_bytes(data: Dict[str, Any], file_type: Optional[str] = None) -> bytes:
    """Serialise *data* to GFF V3.2 bytes.

    Args:
        data: Dict as returned by ``read_gff()``.
        file_type: 4-character type tag; defaults to ``data["StructType"]``.

    Returns:
        The complete GFF binary.
    """
    return GFFWriter(data, file_type).to_bytes()


def write_gff(file_path: Path, data: Dict[str, Any], file_type: Optional[str] = None) -> None:
    """Write *data* as a GFF V3.2 file, creating parent directories.

    Args:
        file_path: Destination path.
        data: Dict as returned by ``read_gff()``.
        file_type: 4-character type tag; defaults to ``data["StructType"]``.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(write_gff_bytes(data, file_type))
