"""Native GFF parser for Neverwinter Nights: Enhanced Edition.

This module implements a proper GFF (Generic File Format) parser that handles
the binary structure used by NWN:EE with UTF-8 encoding support.
"""

import struct
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from enum import IntEnum

logger = logging.getLogger(__name__)


class GFFType(IntEnum):
    """GFF data type identifiers."""

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
    CExoLocSubString = 14  # Subtype of CExoLocString
    List = 15
    Struct = 16
    Unknown = 0xFF

    @staticmethod
    def _missing_(value):
        """Handle unknown GFF types by treating them as DWORD/INT."""
        return GFFType.DWORD


class GFFParseError(Exception):
    """Exception raised for GFF parsing errors."""
    pass


class GFFField:
    """Represents a field in a GFF struct."""

    def __init__(self, label: str, gff_type: GFFType, data_or_offset: int, record_offset: int = 0):
        """Initialize a GFF field.

        Args:
            label: Field name (16 chars, null-terminated)
            gff_type: GFF data type
            data_or_offset: For simple types, the data; for complex types, offset
            record_offset: Absolute byte offset of this field record in the binary file
        """
        self.label = label
        self.type = gff_type
        self.data_or_offset = data_or_offset
        self.record_offset = record_offset

    def __repr__(self) -> str:
        return f"GFFField({self.label}, {self.type.name}, {self.data_or_offset})"


@dataclass
class GFFValue:
    """Wrapper for parsed GFF field values to retain their original type."""
    type: GFFType
    value: Any
    record_offset: int = 0


class GFFStruct:
    """Represents a structure in GFF format."""

    def __init__(self, struct_id: int, data_offset: int, field_count: int):
        """Initialize a GFF struct.

        Args:
            struct_id: Structure type ID
            data_offset: Offset to field indices
            field_count: Number of fields
        """
        self.struct_id = struct_id
        self.data_offset = data_offset
        self.field_count = field_count
        self.fields: Dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"GFFStruct(id={self.struct_id}, fields={len(self.fields)})"


class GFFFile:
    """Parsed GFF file with all data."""

    def __init__(self):
        """Initialize empty GFF file."""
        self.file_type: bytes = b""
        self.version: bytes = b""
        self.struct_type: Optional[str] = None
        self.structs: List[GFFStruct] = []
        self.labels: List[str] = []
        self.field_indices: List[int] = []
        self.list_indices: List[int] = []
        self.raw_data: bytes = b""

    def get_root_struct(self) -> Optional[GFFStruct]:
        """Get the root structure (usually first or type-matched)."""
        if not self.structs:
            return None
        return self.structs[0]

    def get_field_value(self, struct: GFFStruct, field_name: str) -> Any:
        """Get field value from a struct.

        Args:
            struct: GFF struct
            field_name: Name of the field

        Returns:
            Field value or None if not found
        """
        return struct.fields.get(field_name)

    def __repr__(self) -> str:
        return f"GFFFile(type={self.struct_type}, structs={len(self.structs)})"


class GFFParser:
    """Parser for GFF files used by Neverwinter Nights."""

    # Type mappings
    TYPE_SIZES = {
        GFFType.BYTE: 1,
        GFFType.CHAR: 1,
        GFFType.WORD: 2,
        GFFType.SHORT: 2,
        GFFType.DWORD: 4,
        GFFType.INT: 4,
        GFFType.DWORD64: 8,
        GFFType.INT64: 8,
        GFFType.FLOAT: 4,
        GFFType.DOUBLE: 8,
    }

    def __init__(self, file_path: Path):
        """Initialize parser.

        Args:
            file_path: Path to GFF file
        """
        self.file_path = Path(file_path)
        self.data: bytes = b""
        # Absolute byte offsets saved during parse() for use in _parse_field_value()
        self.field_data_offset: int = 0
        self.list_indices_offset: int = 0

    def parse(self) -> GFFFile:
        """Parse the GFF file.

        Returns:
            Parsed GFFFile object

        Raises:
            GFFParseError: If parsing fails
        """
        with open(self.file_path, "rb") as f:
            self.data = f.read()

        if len(self.data) < 160:
            raise GFFParseError("File too small to be valid GFF")

        # Parse header
        gff = GFFFile()
        gff.file_type = self.data[0:4]
        gff.version = self.data[4:8]

        # Derive the struct type from the 4-char file type header (strip trailing spaces)
        try:
            gff.struct_type = gff.file_type.rstrip(b" ").decode('ascii')
        except Exception:
            gff.struct_type = None

        # GFF v3.2 header layout (56 bytes total, 14 x DWORD):
        #  8: StructOffset      12: StructCount
        # 16: FieldOffset       20: FieldCount
        # 24: LabelOffset       28: LabelCount
        # 32: FieldDataOffset   36: FieldDataByteSize
        # 40: FieldIndicesOffset 44: FieldIndicesByteSize
        # 48: ListIndicesOffset  52: ListIndicesByteSize
        struct_offset      = struct.unpack("<I", self.data[8:12])[0]
        struct_count       = struct.unpack("<I", self.data[12:16])[0]
        field_offset       = struct.unpack("<I", self.data[16:20])[0]
        field_count        = struct.unpack("<I", self.data[20:24])[0]
        label_offset       = struct.unpack("<I", self.data[24:28])[0]
        label_count        = struct.unpack("<I", self.data[28:32])[0]
        field_data_offset  = struct.unpack("<I", self.data[32:36])[0]  # base for complex fields
        # field_data_byte_size = 36:40 (not needed for parsing)
        field_indices_offset    = struct.unpack("<I", self.data[40:44])[0]
        field_indices_byte_size = struct.unpack("<I", self.data[44:48])[0]
        list_indices_offset     = struct.unpack("<I", self.data[48:52])[0]
        # list_indices_byte_size = 52:56 (not needed; we read on-demand)

        # Store for use in _parse_field_value
        self.field_data_offset  = field_data_offset
        self.list_indices_offset = list_indices_offset

        # Number of DWORD elements in the field-indices block
        field_indices_count = field_indices_byte_size // 4

        # Parse labels (16-char strings)
        gff.labels = []
        for i in range(label_count):
            offset = label_offset + i * 16
            if offset + 16 > len(self.data):
                gff.labels.append("")
                continue
            label_data = self.data[offset:offset+16]
            label = label_data.split(b'\x00')[0].decode('ascii', errors='ignore')
            gff.labels.append(label)

        # Parse field definitions.
        # GFF field record layout: [Type (4)] [LabelIndex (4)] [DataOrDataOffset (4)]
        fields = []
        for i in range(field_count):
            offset = field_offset + i * 12
            if offset + 12 > len(self.data):
                continue
            type_idx = struct.unpack("<I", self.data[offset:offset+4])[0]
            label_idx = struct.unpack("<I", self.data[offset+4:offset+8])[0]
            data_or_offset = struct.unpack("<I", self.data[offset+8:offset+12])[0]

            label = gff.labels[label_idx] if label_idx < len(gff.labels) else f"field_{label_idx}"
            gff_type = GFFType(type_idx)

            fields.append(GFFField(label, gff_type, data_or_offset, record_offset=offset))

        # Parse field indices block (DWORD array mapping struct fields to the fields array)
        gff.field_indices = []
        for i in range(field_indices_count):
            off = field_indices_offset + i * 4
            if off + 4 > len(self.data):
                break
            gff.field_indices.append(struct.unpack("<I", self.data[off:off+4])[0])

        # list_indices are not pre-parsed; List fields read directly from self.data
        # using self.list_indices_offset + field.data_or_offset.

        # Parse structs
        gff.structs = []
        for i in range(struct_count):
            offset = struct_offset + i * 12
            if offset + 12 > len(self.data):
                 # Stop processing structs if we ran out of data
                 break
            struct_id = struct.unpack("<I", self.data[offset:offset+4])[0]
            data_offset = struct.unpack("<I", self.data[offset+4:offset+8])[0]
            field_count = struct.unpack("<I", self.data[offset+8:offset+12])[0]

            struct_obj = GFFStruct(struct_id, data_offset, field_count)

            # Parse fields for this struct
            if field_count == 1:
                # If count is 1, DataOffset is a direct index into the Fields array.
                field_idx = data_offset
                if field_idx < len(fields):
                    field = fields[field_idx]
                    value = self._parse_field_value(field, gff)
                    struct_obj.fields[field.label] = GFFValue(field.type, value, field.record_offset)
            elif field_count > 1:
                # If count > 1, DataOffset is a BYTE offset into the FieldIndices block.
                # Divide by 4 (DWORD size) to get the element index.
                start_index = data_offset // 4

                for j in range(field_count):
                    list_idx = start_index + j
                    if list_idx >= len(gff.field_indices):
                        break

                    field_idx = gff.field_indices[list_idx]
                    if field_idx >= len(fields):
                        continue

                    field = fields[field_idx]
                    value = self._parse_field_value(field, gff)
                    struct_obj.fields[field.label] = GFFValue(field.type, value, field.record_offset)

            gff.structs.append(struct_obj)

        return gff

    def _parse_field_value(self, field: GFFField, gff: GFFFile) -> Any:
        """Parse a field value based on its type.

        Args:
            field: GFFField to parse
            gff: GFFFile context

        Returns:
            Parsed value
        """
        if field.type in self.TYPE_SIZES:
            # Simple types stored directly
            return self._parse_simple_type(field.type, field.data_or_offset)

        elif field.type == GFFType.CExoString:
            # Layout in Field Data block: [size DWORD (4)] [string bytes (size)]
            # data_or_offset is relative to field_data_offset
            offset = self.field_data_offset + field.data_or_offset
            if offset + 4 > len(self.data):
                return ""
            length = struct.unpack("<I", self.data[offset:offset+4])[0]
            if length == 0:
                return ""
            if offset + 4 + length > len(self.data):
                return ""
            try:
                return self.data[offset+4:offset+4+length].decode('utf-8')
            except Exception:
                return self.data[offset+4:offset+4+length].decode('utf-8', errors='ignore')

        elif field.type == GFFType.CResRef:
            # Layout in Field Data block: [size BYTE (1)] [string bytes (size)]
            # data_or_offset is relative to field_data_offset
            offset = self.field_data_offset + field.data_or_offset
            if offset + 1 > len(self.data):
                return ""
            size = self.data[offset]
            if size == 0:
                return ""
            raw = self.data[offset+1:offset+1+size]
            try:
                return raw.decode('ascii')
            except Exception:
                return raw.decode('ascii', errors='ignore')

        elif field.type == GFFType.CExoLocString:
            # Layout in Field Data block (data_or_offset is relative to field_data_offset):
            # [TotalSize DWORD (4)] [StrRef DWORD (4)] [SubStringCount DWORD (4)]
            # Followed by SubStringCount substrings:
            #   [LanguageID DWORD (4)] [Length DWORD (4)] [string bytes (Length)]
            offset = self.field_data_offset + field.data_or_offset
            if offset + 12 > len(self.data):
                return {"StrRef": -1, "Value": ""}

            str_ref = struct.unpack("<i", self.data[offset+4:offset+8])[0]
            count   = struct.unpack("<I", self.data[offset+8:offset+12])[0]

            if count == 0:
                return {"StrRef": str_ref, "Value": ""}

            try:
                sub_offset = offset + 12
                for _ in range(count):
                    if sub_offset + 8 > len(self.data):
                        break
                    sub_offset += 4  # skip LanguageID
                    length = struct.unpack("<I", self.data[sub_offset:sub_offset+4])[0]
                    sub_offset += 4
                    if sub_offset + length > len(self.data):
                        break
                    raw = self.data[sub_offset:sub_offset+length]
                    sub_offset += length
                    try:
                        value = raw.decode('utf-8')
                    except Exception:
                        value = raw.decode('utf-8', errors='ignore')
                    if value:
                        return {"StrRef": str_ref, "Value": value}
            except Exception:
                pass

            return {"StrRef": str_ref, "Value": ""}

        elif field.type == GFFType.List:
            # data_or_offset is a BYTE offset into the List Indices block.
            # Layout at that position: [Count DWORD (4)] [StructIdx DWORD * Count]
            # Each StructIdx is a direct index into the Structs array.
            offset = self.list_indices_offset + field.data_or_offset
            if offset + 4 > len(self.data):
                return []

            count = struct.unpack("<I", self.data[offset:offset+4])[0]
            result = []
            for i in range(count):
                entry_off = offset + 4 + i * 4
                if entry_off + 4 > len(self.data):
                    break
                struct_idx = struct.unpack("<I", self.data[entry_off:entry_off+4])[0]
                result.append(struct_idx)
            return result

        elif field.type == GFFType.Struct:
            # Direct struct reference
            return field.data_or_offset

        elif field.type == GFFType.Unknown:
            # Unknown type - treat as DWORD/INT
            return field.data_or_offset

        else:
            return None

    def _parse_simple_type(self, gff_type: GFFType, value: int) -> Any:
        """Parse a simple numeric type.

        Args:
            gff_type: GFF type
            value: Raw value

        Returns:
            Parsed value
        """
        if gff_type == GFFType.BYTE:
            return value & 0xFF
        elif gff_type == GFFType.CHAR:
            return chr(value & 0xFF) if value < 128 else '?'
        elif gff_type == GFFType.WORD:
            return value & 0xFFFF
        elif gff_type == GFFType.SHORT:
            # Sign-extend 16-bit
            if value & 0x8000:
                return value - 0x10000
            return value
        elif gff_type == GFFType.DWORD:
            return value & 0xFFFFFFFF
        elif gff_type == GFFType.INT:
            # DataOrDataOffset is read as unsigned; sign-extend to get signed 32-bit
            if value & 0x80000000:
                return value - 0x100000000
            return value
        elif gff_type == GFFType.DWORD64:
            return value
        elif gff_type == GFFType.INT64:
            return value
        elif gff_type == GFFType.FLOAT:
            return struct.unpack("<f", struct.pack("<I", value))[0]
        elif gff_type == GFFType.DOUBLE:
            return struct.unpack("<d", struct.pack("<Q", value))[0]
        else:
            return value


def parse_gff(file_path: Path) -> GFFFile:
    """Parse a GFF file.

    Args:
        file_path: Path to GFF file

    Returns:
        Parsed GFFFile object

    Raises:
        GFFParseError: If parsing fails
    """
    parser = GFFParser(file_path)
    return parser.parse()


def _expand_struct(struct_fields: Dict[str, Any], gff: GFFFile, visited: set) -> Dict[str, Any]:
    """Recursively expand struct fields, resolving list indices to their struct dicts.

    Args:
        struct_fields: Raw fields dict from a GFFStruct
        gff: Parsed GFFFile used to look up struct indices
        visited: Set of already-visited struct indices to prevent infinite loops

    Returns:
        Expanded fields dict
    """
    result = {}
    field_types = {}
    record_offsets = {}
    
    for key, value in struct_fields.items():
        if hasattr(value, "value"):
            gff_val = value.value
            gff_type = value.type
            field_types[key] = int(gff_type)
            record_offsets[key] = value.record_offset
        else:
            gff_val = value
            field_types[key] = int(GFFType.Unknown)
            record_offsets[key] = 0

        if isinstance(gff_val, list):
            expanded = []
            for idx in gff_val:
                if not isinstance(idx, int):
                    expanded.append(idx)
                    continue
                if 0 <= idx < len(gff.structs) and idx not in visited:
                    child_fields = gff.structs[idx].fields
                    expanded.append(_expand_struct(child_fields, gff, visited | {idx}))
                else:
                    expanded.append(idx)
            result[key] = expanded
        else:
            result[key] = gff_val
            
    result["_field_types"] = field_types
    result["_record_offsets"] = record_offsets
    return result


def gff_to_dict(gff: GFFFile) -> Dict[str, Any]:
    """Convert GFF file to a fully-expanded dictionary.

    All list fields are recursively resolved so that nested structures
    (e.g. EntryList → RepliesList → EntriesList) become plain Python dicts.

    Args:
        gff: Parsed GFFFile

    Returns:
        Dictionary representation with all structs fully expanded
    """
    if not gff.structs:
        return {}

    root = gff.structs[0]
    expanded = _expand_struct(root.fields, gff, {0})
    return {"StructType": gff.struct_type, **expanded}
