"""Diagnostic tool: dump all CExoLocString fields from a GFF binary file.

Usage:
    python dump_gff_strings.py <file.dlg|file.utc|file.jrl> [--compare <original_file>]

Outputs for every CExoLocString field:
  - Field label
  - Field record offset (absolute byte in file)
  - DataOrDataOffset value (relative to FieldData block)
  - Absolute position of the LocString payload
  - StrRef, SubStringCount
  - For each substring: LanguageID, Length, raw hex, decoded text
  - TotalSize header value
"""

import struct
import sys
import os
from pathlib import Path


def dump_gff_strings(file_path: str, compare_path: str = None) -> None:
    """Dump all CExoLocString fields from a GFF file."""
    path = Path(file_path)
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        return

    with open(path, "rb") as f:
        data = bytearray(f.read())

    file_size = len(data)
    print(f"=== GFF String Dump: {path.name} ===")
    print(f"File size: {file_size} bytes")

    if file_size < 56:
        print("ERROR: File too small for GFF header")
        return

    # Parse GFF header
    file_type = data[0:4]
    version = data[4:8]
    print(f"Type: {file_type}  Version: {version}")

    struct_offset = struct.unpack("<I", data[8:12])[0]
    struct_count = struct.unpack("<I", data[12:16])[0]
    field_offset = struct.unpack("<I", data[16:20])[0]
    field_count = struct.unpack("<I", data[20:24])[0]
    label_offset = struct.unpack("<I", data[24:28])[0]
    label_count = struct.unpack("<I", data[28:32])[0]
    field_data_offset = struct.unpack("<I", data[32:36])[0]
    field_data_size = struct.unpack("<I", data[36:40])[0]
    field_indices_offset = struct.unpack("<I", data[40:44])[0]
    field_indices_size = struct.unpack("<I", data[44:48])[0]
    list_indices_offset = struct.unpack("<I", data[48:52])[0]
    list_indices_size = struct.unpack("<I", data[52:56])[0]

    print(f"\nHeader:")
    print(f"  Structs:      offset={struct_offset}, count={struct_count}")
    print(f"  Fields:       offset={field_offset}, count={field_count}")
    print(f"  Labels:       offset={label_offset}, count={label_count}")
    print(f"  FieldData:    offset={field_data_offset}, size={field_data_size}")
    print(f"  FieldIndices: offset={field_indices_offset}, size={field_indices_size}")
    print(f"  ListIndices:  offset={list_indices_offset}, size={list_indices_size}")

    # Verify: does FieldData + FieldDataSize point past the end?
    fd_end = field_data_offset + field_data_size
    print(f"\n  FieldData end: {fd_end} (file size: {file_size})")
    if fd_end > file_size:
        print(f"  WARNING: FieldData extends {fd_end - file_size} bytes beyond file!")
    elif fd_end < file_size:
        print(f"  NOTE: {file_size - fd_end} bytes after FieldData block (may be FieldIndices/ListIndices)")

    # Parse labels
    labels = []
    for i in range(label_count):
        off = label_offset + i * 16
        if off + 16 > file_size:
            labels.append(f"<invalid_{i}>")
            continue
        raw = data[off:off + 16]
        label = raw.split(b'\x00')[0].decode('ascii', errors='replace')
        labels.append(label)

    # Parse fields and find CExoLocString (type == 12)
    CEXOLOCSTRING_TYPE = 12
    loc_fields = []

    print(f"\n{'='*70}")
    print(f"Scanning {field_count} fields for CExoLocString (type=12)...")
    print(f"{'='*70}\n")

    for i in range(field_count):
        rec_offset = field_offset + i * 12
        if rec_offset + 12 > file_size:
            break

        type_id = struct.unpack("<I", data[rec_offset:rec_offset + 4])[0]
        label_idx = struct.unpack("<I", data[rec_offset + 4:rec_offset + 8])[0]
        data_or_offset = struct.unpack("<I", data[rec_offset + 8:rec_offset + 12])[0]

        label = labels[label_idx] if label_idx < len(labels) else f"<idx_{label_idx}>"

        if type_id != CEXOLOCSTRING_TYPE:
            continue

        # This is a CExoLocString field
        abs_offset = field_data_offset + data_or_offset

        print(f"--- Field #{i}: \"{label}\" ---")
        print(f"  Record offset:     {rec_offset} (0x{rec_offset:08X})")
        print(f"  Type:              {type_id} (CExoLocString)")
        print(f"  DataOrDataOffset:  {data_or_offset} (relative to FieldData)")
        print(f"  Absolute offset:   {abs_offset} (0x{abs_offset:08X})")

        if abs_offset + 12 > file_size:
            print(f"  ERROR: Payload at {abs_offset} is beyond file size {file_size}!")
            print()
            continue

        # CExoLocString payload layout:
        # [TotalSize: DWORD (4)] [StrRef: INT (4)] [SubStringCount: DWORD (4)]
        # For each substring:
        #   [LanguageID: DWORD (4)] [Length: DWORD (4)] [String: bytes (Length)]
        total_size = struct.unpack("<I", data[abs_offset:abs_offset + 4])[0]
        str_ref = struct.unpack("<i", data[abs_offset + 4:abs_offset + 8])[0]
        sub_count = struct.unpack("<I", data[abs_offset + 8:abs_offset + 12])[0]

        print(f"  TotalSize:         {total_size}")
        print(f"  StrRef:            {str_ref}")
        print(f"  SubStringCount:    {sub_count}")

        sub_off = abs_offset + 12
        for s in range(sub_count):
            if sub_off + 8 > file_size:
                print(f"  Substring {s}: ERROR - beyond file end")
                break

            lang_id = struct.unpack("<I", data[sub_off:sub_off + 4])[0]
            str_len = struct.unpack("<I", data[sub_off + 4:sub_off + 8])[0]
            sub_off += 8

            if sub_off + str_len > file_size:
                print(f"  Substring {s}: lang={lang_id}, len={str_len} ERROR - beyond file end")
                break

            raw_bytes = bytes(data[sub_off:sub_off + str_len])
            sub_off += str_len

            # Try decode as UTF-8, then CP1251
            try:
                text_utf8 = raw_bytes.decode("utf-8")
                enc = "UTF-8"
                text = text_utf8
            except UnicodeDecodeError:
                try:
                    text_cp = raw_bytes.decode("cp1251")
                    enc = "CP1251"
                    text = text_cp
                except Exception:
                    enc = "UNKNOWN"
                    text = repr(raw_bytes)

            hex_preview = raw_bytes[:32].hex(" ")
            if len(raw_bytes) > 32:
                hex_preview += " ..."

            print(f"  Substring {s}:")
            print(f"    LanguageID:  {lang_id}")
            print(f"    Length:      {str_len}")
            print(f"    Encoding:    {enc}")
            print(f"    Hex:         {hex_preview}")
            print(f"    Text:        \"{text}\"")

        print()

    # --- Comparison mode ---
    if compare_path:
        cmp = Path(compare_path)
        if cmp.exists():
            orig_size = os.path.getsize(cmp)
            print(f"\n{'='*70}")
            print(f"COMPARISON: {cmp.name} ({orig_size} bytes) vs {path.name} ({file_size} bytes)")
            print(f"Size delta: {file_size - orig_size} bytes")
            print(f"{'='*70}")

            with open(cmp, "rb") as f:
                orig_data = f.read()

            # Compare headers
            if len(orig_data) >= 56:
                orig_fd_size = struct.unpack("<I", orig_data[36:40])[0]
                new_fd_size = struct.unpack("<I", data[36:40])[0]
                print(f"  Original FieldDataSize: {orig_fd_size}")
                print(f"  Patched  FieldDataSize: {new_fd_size}")
                print(f"  Delta:                  {new_fd_size - orig_fd_size}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dump_gff_strings.py <file.dlg|file.utc|file.jrl> [--compare <original>]")
        sys.exit(1)

    target = sys.argv[1]
    compare = None
    if "--compare" in sys.argv:
        idx = sys.argv.index("--compare")
        if idx + 1 < len(sys.argv):
            compare = sys.argv[idx + 1]

    dump_gff_strings(target, compare)
