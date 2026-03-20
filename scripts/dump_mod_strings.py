"""Extract a resource from a .mod file and dump its CExoLocString fields.

Usage:
    python dump_mod_strings.py <module.mod> <resource_name> [resource_name2 ...]

Examples:
    python dump_mod_strings.py "test_modules\The Dark Ranger's Treasure_rus.mod" drixie.dlg
    python dump_mod_strings.py "test_modules\The Dark Ranger's Treasure_rus.mod" drixie.utc drixie.dlg
"""

import struct
import sys
import os
import tempfile
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from nwn_translator.file_handlers.erf_reader import ERFReader


def dump_locstrings_from_data(data: bytes, name: str) -> None:
    """Dump all CExoLocString fields from raw GFF bytes."""
    file_size = len(data)
    print(f"\n{'='*70}")
    print(f"=== GFF String Dump: {name} ===")
    print(f"File size: {file_size} bytes")

    if file_size < 56:
        print("ERROR: File too small for GFF header")
        return

    file_type = data[0:4]
    version = data[4:8]
    print(f"Type: {file_type}  Version: {version}")

    field_offset = struct.unpack("<I", data[16:20])[0]
    field_count = struct.unpack("<I", data[20:24])[0]
    label_offset = struct.unpack("<I", data[24:28])[0]
    label_count = struct.unpack("<I", data[28:32])[0]
    field_data_offset = struct.unpack("<I", data[32:36])[0]
    field_data_size = struct.unpack("<I", data[36:40])[0]

    print(f"  Fields:       offset={field_offset}, count={field_count}")
    print(f"  Labels:       offset={label_offset}, count={label_count}")
    print(f"  FieldData:    offset={field_data_offset}, size={field_data_size}")
    fd_end = field_data_offset + field_data_size
    print(f"  FieldData end: {fd_end} (file size: {file_size}, delta: {file_size - fd_end})")

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

    # Scan fields for CExoLocString (type=12)
    CEXOLOCSTRING_TYPE = 12
    found = 0

    for i in range(field_count):
        rec_offset = field_offset + i * 12
        if rec_offset + 12 > file_size:
            break

        type_id = struct.unpack("<I", data[rec_offset:rec_offset + 4])[0]
        label_idx = struct.unpack("<I", data[rec_offset + 4:rec_offset + 8])[0]
        data_or_offset = struct.unpack("<I", data[rec_offset + 8:rec_offset + 12])[0]

        if type_id != CEXOLOCSTRING_TYPE:
            continue

        label = labels[label_idx] if label_idx < len(labels) else f"<idx_{label_idx}>"
        abs_offset = field_data_offset + data_or_offset

        print(f"\n--- Field #{i}: \"{label}\" ---")
        print(f"  Record @ {rec_offset} (0x{rec_offset:08X})")
        print(f"  DataOffset: {data_or_offset} -> abs {abs_offset} (0x{abs_offset:08X})")

        if abs_offset + 12 > file_size:
            print(f"  ERROR: Payload beyond file end ({abs_offset}+12 > {file_size})!")
            continue

        total_size = struct.unpack("<I", data[abs_offset:abs_offset + 4])[0]
        str_ref = struct.unpack("<i", data[abs_offset + 4:abs_offset + 8])[0]
        sub_count = struct.unpack("<I", data[abs_offset + 8:abs_offset + 12])[0]

        print(f"  TotalSize={total_size}  StrRef={str_ref}  SubCount={sub_count}")

        sub_off = abs_offset + 12
        for s in range(sub_count):
            if sub_off + 8 > file_size:
                print(f"  Sub{s}: TRUNCATED")
                break

            lang_id = struct.unpack("<I", data[sub_off:sub_off + 4])[0]
            str_len = struct.unpack("<I", data[sub_off + 4:sub_off + 8])[0]
            sub_off += 8

            if sub_off + str_len > file_size:
                print(f"  Sub{s}: lang={lang_id} len={str_len} TRUNCATED")
                break

            raw_bytes = data[sub_off:sub_off + str_len]
            sub_off += str_len

            # Try UTF-8 first, then CP1251
            try:
                text = raw_bytes.decode("utf-8")
                enc = "UTF-8"
            except UnicodeDecodeError:
                try:
                    text = raw_bytes.decode("cp1251")
                    enc = "CP1251"
                except Exception:
                    enc = "RAW"
                    text = repr(raw_bytes)

            hex_prev = raw_bytes[:40].hex(" ")
            if len(raw_bytes) > 40:
                hex_prev += " ..."

            print(f"  Sub{s}: lang={lang_id} len={str_len} enc={enc}")
            print(f"    hex: {hex_prev}")
            print(f"    txt: \"{text[:120]}{'...' if len(text)>120 else ''}\"")

        found += 1

    print(f"\nTotal CExoLocString fields found: {found}")


def main():
    """Extract resources from .mod and dump their CExoLocString fields."""
    if len(sys.argv) < 3:
        print("Usage: python dump_mod_strings.py <module.mod> <resource.ext> [resource2.ext ...]")
        print("Example: python dump_mod_strings.py \"test_modules\\The Dark Ranger's Treasure_rus.mod\" drixie.dlg")
        sys.exit(1)

    mod_path = Path(sys.argv[1])
    resource_names = sys.argv[2:]

    if not mod_path.exists():
        print(f"ERROR: Module not found: {mod_path}")
        sys.exit(1)

    print(f"Opening module: {mod_path}")
    reader = ERFReader(mod_path)
    entries = reader.read_entries()
    print(f"Module contains {len(entries)} resources")

    # Build a lookup: "name.ext" -> entry
    entry_map = {}
    for entry in entries:
        ext = reader.get_resource_type(entry.res_type)
        full_name = f"{entry.res_ref}{ext}"
        entry_map[full_name.lower()] = entry

    for res_name in resource_names:
        key = res_name.lower()
        if key not in entry_map:
            print(f"\nWARNING: Resource '{res_name}' not found in module!")
            print(f"Available resources with similar name:")
            for k in sorted(entry_map.keys()):
                if res_name.split('.')[0].lower() in k:
                    print(f"  {k} (type={entry_map[k].res_type}, size={entry_map[k].size})")
            continue

        entry = entry_map[key]
        print(f"\nExtracting: {res_name} (type={entry.res_type}, offset={entry.offset}, size={entry.size})")

        # Read resource data directly from the mod
        with open(mod_path, "rb") as f:
            f.seek(entry.offset)
            res_data = f.read(entry.size)

        dump_locstrings_from_data(res_data, res_name)


if __name__ == "__main__":
    main()
