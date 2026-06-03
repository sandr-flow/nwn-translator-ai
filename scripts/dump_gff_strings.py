"""Diagnostic tool: dump all CExoLocString fields from a GFF file or module resource.

Usage:
    python scripts/dump_gff_strings.py file <path/to/file.utc> [--compare <original>]
    python scripts/dump_gff_strings.py module <path/to/module.mod> <resource.ext> [resource2.ext ...]
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nwn_translator.file_handlers.erf_reader import ERFReader

_CEXOLOCSTRING_TYPE = 12
_GFF_HEADER_SIZE = 56


def _decode_string(raw_bytes: bytes) -> tuple[str, str]:
    """Decode *raw_bytes* using the most likely encoding for diagnostics."""
    try:
        return raw_bytes.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return raw_bytes.decode("cp1251"), "CP1251"
        except UnicodeDecodeError:
            return repr(raw_bytes), "RAW"


def _parse_labels(data: bytes, label_offset: int, label_count: int) -> list[str]:
    labels: list[str] = []
    file_size = len(data)
    for i in range(label_count):
        offset = label_offset + i * 16
        if offset + 16 > file_size:
            labels.append(f"<invalid_{i}>")
            continue
        raw = data[offset : offset + 16]
        labels.append(raw.split(b"\x00")[0].decode("ascii", errors="replace"))
    return labels


def dump_gff_bytes(data: bytes, name: str, compare_data: bytes | None = None) -> None:
    """Dump all CExoLocString fields from raw GFF bytes."""
    file_size = len(data)
    print(f"\n{'=' * 70}")
    print(f"=== GFF String Dump: {name} ===")
    print(f"File size: {file_size} bytes")

    if file_size < _GFF_HEADER_SIZE:
        print("ERROR: File too small for GFF header")
        return

    file_type = data[0:4]
    version = data[4:8]
    field_offset = struct.unpack("<I", data[16:20])[0]
    field_count = struct.unpack("<I", data[20:24])[0]
    label_offset = struct.unpack("<I", data[24:28])[0]
    label_count = struct.unpack("<I", data[28:32])[0]
    field_data_offset = struct.unpack("<I", data[32:36])[0]
    field_data_size = struct.unpack("<I", data[36:40])[0]

    print(f"Type: {file_type}  Version: {version}")
    print(f"  Fields:       offset={field_offset}, count={field_count}")
    print(f"  Labels:       offset={label_offset}, count={label_count}")
    print(f"  FieldData:    offset={field_data_offset}, size={field_data_size}")
    fd_end = field_data_offset + field_data_size
    print(f"  FieldData end: {fd_end} (file size: {file_size}, delta: {file_size - fd_end})")

    labels = _parse_labels(data, label_offset, label_count)
    found = 0

    for field_index in range(field_count):
        record_offset = field_offset + field_index * 12
        if record_offset + 12 > file_size:
            break

        type_id = struct.unpack("<I", data[record_offset : record_offset + 4])[0]
        label_index = struct.unpack("<I", data[record_offset + 4 : record_offset + 8])[0]
        data_or_offset = struct.unpack("<I", data[record_offset + 8 : record_offset + 12])[0]

        if type_id != _CEXOLOCSTRING_TYPE:
            continue

        label = labels[label_index] if label_index < len(labels) else f"<idx_{label_index}>"
        absolute_offset = field_data_offset + data_or_offset

        print(f'\n--- Field #{field_index}: "{label}" ---')
        print(f"  Record @ {record_offset} (0x{record_offset:08X})")
        print(f"  DataOffset: {data_or_offset} -> abs {absolute_offset} (0x{absolute_offset:08X})")

        if absolute_offset + 12 > file_size:
            print(f"  ERROR: Payload beyond file end ({absolute_offset}+12 > {file_size})")
            continue

        total_size = struct.unpack("<I", data[absolute_offset : absolute_offset + 4])[0]
        str_ref = struct.unpack("<i", data[absolute_offset + 4 : absolute_offset + 8])[0]
        substring_count = struct.unpack("<I", data[absolute_offset + 8 : absolute_offset + 12])[0]

        print(f"  TotalSize={total_size}  StrRef={str_ref}  SubCount={substring_count}")

        substring_offset = absolute_offset + 12
        for index in range(substring_count):
            if substring_offset + 8 > file_size:
                print(f"  Sub{index}: TRUNCATED")
                break

            language_id = struct.unpack("<I", data[substring_offset : substring_offset + 4])[0]
            string_len = struct.unpack("<I", data[substring_offset + 4 : substring_offset + 8])[0]
            substring_offset += 8

            if substring_offset + string_len > file_size:
                print(f"  Sub{index}: lang={language_id} len={string_len} TRUNCATED")
                break

            raw_bytes = data[substring_offset : substring_offset + string_len]
            substring_offset += string_len
            text, encoding = _decode_string(raw_bytes)

            hex_preview = raw_bytes[:40].hex(" ")
            if len(raw_bytes) > 40:
                hex_preview += " ..."

            print(f"  Sub{index}: lang={language_id} len={string_len} enc={encoding}")
            print(f"    hex: {hex_preview}")
            print(f'    txt: "{text[:120]}{"..." if len(text) > 120 else ""}"')

        found += 1

    print(f"\nTotal CExoLocString fields found: {found}")

    if compare_data is not None and len(compare_data) >= _GFF_HEADER_SIZE:
        original_field_data_size = struct.unpack("<I", compare_data[36:40])[0]
        print(f"\n{'=' * 70}")
        print("COMPARISON")
        print(f"  Original FieldDataSize: {original_field_data_size}")
        print(f"  Current  FieldDataSize: {field_data_size}")
        print(f"  Delta:                 {field_data_size - original_field_data_size}")


def _load_file_bytes(path: Path) -> bytes:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_bytes()


def _extract_resource_bytes(module_path: Path, resource_name: str) -> bytes:
    reader = ERFReader(module_path)
    for entry in reader.read_entries():
        extension = reader.get_resource_type(entry.res_type)
        if f"{entry.res_ref}{extension}".lower() != resource_name.lower():
            continue
        with module_path.open("rb") as handle:
            handle.seek(entry.offset)
            return handle.read(entry.size)
    raise FileNotFoundError(f"Resource not found in module: {resource_name}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    file_parser = subparsers.add_parser("file", help="Dump a standalone GFF file")
    file_parser.add_argument("path", type=Path)
    file_parser.add_argument("--compare", type=Path, default=None)

    module_parser = subparsers.add_parser(
        "module", help="Dump one or more resources from a .mod/.erf"
    )
    module_parser.add_argument("module_path", type=Path)
    module_parser.add_argument("resources", nargs="+")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.mode == "file":
        data = _load_file_bytes(args.path)
        compare_data = _load_file_bytes(args.compare) if args.compare else None
        dump_gff_bytes(data, args.path.name, compare_data=compare_data)
        return

    module_path: Path = args.module_path
    if not module_path.exists():
        raise FileNotFoundError(f"Module not found: {module_path}")

    print(f"Opening module: {module_path}")
    for resource_name in args.resources:
        data = _extract_resource_bytes(module_path, resource_name)
        dump_gff_bytes(data, resource_name)


if __name__ == "__main__":
    main()
