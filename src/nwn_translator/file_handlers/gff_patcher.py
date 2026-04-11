"""Binary patcher for NWN GFF files.

This module provides a mechanism to directly overwrite CExoLocString offsets
in a binary GFF file. It inserts new string payloads INSIDE the FieldData block
(before FieldIndices/ListIndices) and shifts all subsequent structures, ensuring
the DataOffset pointers always stay within the valid FieldDataByteSize range.
"""

import struct
from pathlib import Path
from typing import List, Tuple


class GFFPatchError(Exception):
    """Exception raised for GFF patching errors."""
    pass


# TODO: Some Unicode dashes in numeric ranges may become '?' if missing from the
# active Windows code page.

# Fallback replacements for common Unicode punctuation not in every legacy page.
_UNICODE_LEGACY_FALLBACKS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
}

# Romanian standard comma-below → legacy cedilla forms present in cp1250.
_ROMANIAN_COMMA_BELOW_TO_CP1250 = str.maketrans(
    {
        "\u0219": "\u015f",
        "\u021b": "\u0163",
        "\u0218": "\u015e",
        "\u021a": "\u0162",
    }
)

_ALLOWED_MODULE_ENCODINGS = frozenset({"cp1250", "cp1251", "cp1252", "cp1254"})


def normalize_for_module_encoding(text: str, encoding: str) -> str:
    """Normalize Unicode for legacy Windows encodings (e.g. Romanian for cp1250)."""
    if encoding == "cp1250":
        return text.translate(_ROMANIAN_COMMA_BELOW_TO_CP1250)
    return text


def sanitize_for_module_encoding(text: str, encoding: str) -> str:
    """Drop or replace characters that cannot be encoded to *encoding*."""
    text = normalize_for_module_encoding(text, encoding)
    out: list[str] = []
    for ch in text:
        try:
            ch.encode(encoding)
            out.append(ch)
        except UnicodeEncodeError:
            out.append(_UNICODE_LEGACY_FALLBACKS.get(ch, ""))
    return "".join(out)


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

    def __init__(self, file_path: Path, text_encoding: str = "cp1251"):
        """Initialize the patcher.

        Args:
            file_path: Path to the GFF binary file to modify.
            text_encoding: Windows code page name (e.g. ``cp1252``, ``cp1250``).
        """
        if text_encoding not in _ALLOWED_MODULE_ENCODINGS:
            raise GFFPatchError(f"Unsupported module text encoding: {text_encoding!r}")
        self.file_path = file_path
        self._text_encoding = text_encoding
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

    def _build_cexo_locstring_payload(self, new_text: str) -> bytearray:
        """Binary CExoLocString payload for *new_text* using :attr:`_text_encoding`."""
        safe = sanitize_for_module_encoding(new_text, self._text_encoding)
        encoded = safe.encode(self._text_encoding, errors="replace")
        substring_count = 1 if encoded else 0
        total_size = 4 + 4 + (4 + 4 + len(encoded)) * substring_count

        payload = bytearray()
        payload += struct.pack("<I", total_size)
        payload += struct.pack("<i", -1)
        payload += struct.pack("<I", substring_count)

        if encoded:
            payload += struct.pack("<I", 0)
            payload += struct.pack("<I", len(encoded))
            payload += encoded

        return payload

    def _apply_payload_at_fielddata_end(
        self,
        data: bytearray,
        record_offset: int,
        payload: bytearray,
    ) -> bytearray:
        """Insert *payload* at end of FieldData; set DataOffset on 12-byte field at *record_offset*."""
        payload_len = len(payload)
        hdr = self._read_header(data)

        fielddata_offset = hdr["fielddata_offset"]
        fielddata_size = hdr["fielddata_size"]
        fieldindices_offset = hdr["fieldindices_offset"]
        fieldindices_size = hdr["fieldindices_size"]
        listindices_offset = hdr["listindices_offset"]
        listindices_size = hdr["listindices_size"]

        insert_pos = fielddata_offset + fielddata_size
        new_data = bytearray(data[:insert_pos] + payload + data[insert_pos:])

        def write_dword(buf: bytearray, off: int, val: int) -> None:
            struct.pack_into("<I", buf, off, val)

        write_dword(new_data, self._HDR_FIELDDATA_SIZE, fielddata_size + payload_len)

        if fieldindices_size > 0:
            write_dword(
                new_data,
                self._HDR_FIELDINDICES_OFFSET,
                fieldindices_offset + payload_len,
            )
        if listindices_size > 0:
            write_dword(
                new_data,
                self._HDR_LISTINDICES_OFFSET,
                listindices_offset + payload_len,
            )

        new_data_offset = fielddata_size
        struct.pack_into("<I", new_data, record_offset + 8, new_data_offset)

        return new_data

    def patch_multiple(self, patches: List[Tuple[int, str]]) -> None:
        """Apply several CExoLocString patches in one read/write pass.

        Patches are applied in order; each inserts at the then-current end of FieldData.

        Args:
            patches: ``(record_offset, new_text)`` for each 12-byte field record.
        """
        if not patches:
            return

        with open(self.file_path, "rb") as f:
            data = bytearray(f.read())

        for record_offset, new_text in patches:
            if record_offset <= 0:
                raise GFFPatchError("Invalid record offset provided")
            payload = self._build_cexo_locstring_payload(new_text)
            data = self._apply_payload_at_fielddata_end(data, record_offset, payload)

        with open(self.file_path, "wb") as f:
            f.write(data)

    def patch_local_string(self, record_offset: int, new_text: str) -> None:
        """Patch a CExoLocString field to point to a new translated string.

        Inserts the new payload at the end of the FieldData block and shifts
        FieldIndices/ListIndices forward so that DataOffset < FieldDataByteSize.

        Args:
            record_offset: Absolute byte offset of the 12-byte GFF field record.
            new_text: The translated text to inject (encoded with ``text_encoding``).
        """
        self.patch_multiple([(record_offset, new_text)])
