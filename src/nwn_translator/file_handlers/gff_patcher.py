"""Binary patcher for NWN GFF files.

This module provides a mechanism to directly overwrite CExoLocString offsets
in a binary GFF file. It inserts new string payloads INSIDE the FieldData block
(before FieldIndices/ListIndices) and shifts all subsequent structures, ensuring
the DataOffset pointers always stay within the valid FieldDataByteSize range.
"""

import struct
from pathlib import Path


class GFFPatchError(Exception):
    """Exception raised for GFF patching errors."""
    pass


class GFFPatcher:
    """Modifies a GFF binary file in-place by inserting data into the FieldData block.

    Each call to patch_local_string inserts the new payload at the END of the
    FieldData block (before FieldIndices/ListIndices), shifts all subsequent
    blocks forward, and updates ALL relevant header offsets.  This ensures the
    DataOffset always satisfies: DataOffset < FieldDataByteSize.
    """

    # GFF header field byte positions
    _HDR_STRUCT_OFFSET = 8
    _HDR_FIELD_OFFSET = 16
    _HDR_LABEL_OFFSET = 24
    _HDR_FIELDDATA_OFFSET = 32
    _HDR_FIELDDATA_SIZE = 36
    _HDR_FIELDINDICES_OFFSET = 40
    _HDR_FIELDINDICES_SIZE = 44
    _HDR_LISTINDICES_OFFSET = 48
    _HDR_LISTINDICES_SIZE = 52

    def __init__(self, file_path: Path):
        """Initialize the patcher.

        Args:
            file_path: Path to the GFF binary file to modify.
        """
        self.file_path = file_path
        if not self.file_path.exists():
            raise GFFPatchError(f"File not found: {self.file_path}")

        with open(self.file_path, "rb") as f:
            header = f.read(56)
            if len(header) < 56:
                raise GFFPatchError("File too small to be a valid GFF header")

    def _read_header(self, data: bytes) -> dict:
        """Parse key GFF header fields from file bytes."""
        def dword(off: int) -> int:
            return struct.unpack_from("<I", data, off)[0]

        return {
            "struct_offset":        dword(self._HDR_STRUCT_OFFSET),
            "field_offset":         dword(self._HDR_FIELD_OFFSET),
            "label_offset":         dword(self._HDR_LABEL_OFFSET),
            "fielddata_offset":     dword(self._HDR_FIELDDATA_OFFSET),
            "fielddata_size":       dword(self._HDR_FIELDDATA_SIZE),
            "fieldindices_offset":  dword(self._HDR_FIELDINDICES_OFFSET),
            "fieldindices_size":    dword(self._HDR_FIELDINDICES_SIZE),
            "listindices_offset":   dword(self._HDR_LISTINDICES_OFFSET),
            "listindices_size":     dword(self._HDR_LISTINDICES_SIZE),
        }

    def patch_local_string(self, record_offset: int, new_text: str) -> None:
        """Patch a CExoLocString field to point to a new translated string.

        Inserts the new payload at the end of the FieldData block and shifts
        FieldIndices/ListIndices forward so that DataOffset < FieldDataByteSize.

        Args:
            record_offset: Absolute byte offset of the 12-byte GFF field record.
            new_text: The translated text to inject (will be encoded as CP1251).
        """
        if record_offset <= 0:
            raise GFFPatchError("Invalid record offset provided")

        # Encode text — CP1251 so the ZOG CP1251-font can render directly
        encoded = new_text.encode("cp1251", errors="replace")

        # Build the CExoLocString payload
        # Layout: TotalSize(4) StrRef(4) SubStringCount(4) [LangID(4) Length(4) Bytes…]
        # TotalSize = total bytes EXCLUDING the 4-byte TotalSize field itself
        substring_count = 1 if encoded else 0
        # TotalSize = StrRef(4) + SubCount(4) + [LangID(4) + Length(4) + text] * N
        total_size = 4 + 4 + (4 + 4 + len(encoded)) * substring_count

        payload = bytearray()
        payload += struct.pack("<I", total_size)           # TotalSize (excl. itself)
        payload += struct.pack("<i", -1)                   # StrRef = -1 (use local)
        payload += struct.pack("<I", substring_count)      # SubStringCount

        if encoded:
            payload += struct.pack("<I", 0)                # LanguageID = 0
            payload += struct.pack("<I", len(encoded))     # Length
            payload += encoded                             # String bytes

        payload_len = len(payload)

        # Read the whole file into a mutable bytearray
        with open(self.file_path, "rb") as f:
            data = bytearray(f.read())

        hdr = self._read_header(data)

        fielddata_offset    = hdr["fielddata_offset"]
        fielddata_size      = hdr["fielddata_size"]
        fieldindices_offset = hdr["fieldindices_offset"]
        fieldindices_size   = hdr["fieldindices_size"]
        listindices_offset  = hdr["listindices_offset"]
        listindices_size    = hdr["listindices_size"]

        # The insert position: right at the end of the current FieldData block
        insert_pos = fielddata_offset + fielddata_size

        # Sanity: insert_pos should be the start of FieldIndices (or thereabouts)
        # It's fine if FieldIndices/ListIndices follow immediately; we insert before them.

        # Build new file bytes
        new_data = data[:insert_pos] + payload + data[insert_pos:]

        # ── Update header fields ─────────────────────────────────────────────
        def write_dword(buf: bytearray, off: int, val: int) -> None:
            struct.pack_into("<I", buf, off, val)

        # FieldDataByteSize grows by payload_len
        write_dword(new_data, self._HDR_FIELDDATA_SIZE,
                    fielddata_size + payload_len)

        # FieldIndices and ListIndices are shifted by payload_len
        if fieldindices_size > 0:
            write_dword(new_data, self._HDR_FIELDINDICES_OFFSET,
                        fieldindices_offset + payload_len)
        if listindices_size > 0:
            write_dword(new_data, self._HDR_LISTINDICES_OFFSET,
                        listindices_offset + payload_len)

        # The DataOffset stored in the field record = offset relative to FieldData start
        # New payload lives at: fielddata_offset + fielddata_size (i.e. old end of FieldData)
        new_data_offset = fielddata_size  # relative to fielddata_offset

        # Overwrite DataOffset in the field record (bytes 8-11 of the 12-byte record)
        struct.pack_into("<I", new_data, record_offset + 8, new_data_offset)

        # Write back
        with open(self.file_path, "wb") as f:
            f.write(new_data)
