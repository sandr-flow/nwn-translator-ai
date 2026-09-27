"""Diagnostic tool that dumps every CExoLocString field of a GFF file or module resource.

Usage:
    python scripts/dump_gff_strings.py file <path/to/file.utc> [--compare <original>]
    python scripts/dump_gff_strings.py module <path/to/module.mod> <resource.ext> [resource2.ext ...]

Module resources are named as the pipeline extracts them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nwn_translator.formats.erf import UNUSED_OFFSET, ERFReader
from nwn_translator.formats.gff import (
    HEADER,
    LABEL,
    LOCSTRING_HEAD,
    RECORD,
    SUBSTRING_HEAD,
    GFFHeader,
    GFFType,
)
from nwn_translator.formats.text_codec import decode_fixed_ascii, decode_module_text


def _decode_string(raw_bytes: bytes) -> tuple[str, str]:
    """Decodes *raw_bytes* as the pipeline does; also names the branch taken."""
    try:
        return raw_bytes.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return decode_module_text(raw_bytes), "legacy"


def _parse_labels(data: bytes, label_offset: int, label_count: int) -> list[str]:
    """Reads the label table; labels outside the file become ``<invalid_N>``."""
    labels: list[str] = []
    for i in range(label_count):
        offset = label_offset + i * LABEL.size
        if offset + LABEL.size > len(data):
            labels.append(f"<invalid_{i}>")
            continue
        labels.append(decode_fixed_ascii(data[offset : offset + LABEL.size]))
    return labels


def dump_gff_bytes(data: bytes, name: str, compare_data: bytes | None = None) -> None:
    """Prints every CExoLocString field of a GFF file with its raw substrings.

    Args:
        data: The GFF file bytes.
        name: Name to print in the banner.
        compare_data: Optional original bytes to compare the field data size with.
    """
    file_size = len(data)
    print(f"\n{'=' * 70}")
    print(f"=== GFF String Dump: {name} ===")
    print(f"File size: {file_size} bytes")

    if file_size < HEADER.size:
        print("ERROR: File too small for GFF header")
        return

    header = GFFHeader.read(data)
    print(f"Type: {header.file_type}  Version: {header.version}")
    print(f"  Fields:       offset={header.field_offset}, count={header.field_count}")
    print(f"  Labels:       offset={header.label_offset}, count={header.label_count}")
    print(f"  FieldData:    offset={header.field_data_offset}, size={header.field_data_size}")
    fd_end = header.field_data_offset + header.field_data_size
    print(f"  FieldData end: {fd_end} (file size: {file_size}, delta: {file_size - fd_end})")

    labels = _parse_labels(data, header.label_offset, header.label_count)
    found = 0

    for field_index in range(header.field_count):
        record_offset = header.field_offset + field_index * RECORD.size
        if record_offset + RECORD.size > file_size:
            break
        type_id, label_index, data_or_offset = RECORD.unpack_from(data, record_offset)
        if type_id != GFFType.CExoLocString:
            continue

        label = labels[label_index] if label_index < len(labels) else f"<idx_{label_index}>"
        absolute_offset = header.field_data_offset + data_or_offset

        print(f'\n--- Field #{field_index}: "{label}" ---')
        print(f"  Record @ {record_offset} (0x{record_offset:08X})")
        print(f"  DataOffset: {data_or_offset} -> abs {absolute_offset} (0x{absolute_offset:08X})")

        if absolute_offset + LOCSTRING_HEAD.size > file_size:
            print(f"  ERROR: Payload beyond file end ({absolute_offset}+12 > {file_size})")
            continue

        total_size, str_ref, substring_count = LOCSTRING_HEAD.unpack_from(data, absolute_offset)
        print(f"  TotalSize={total_size}  StrRef={str_ref}  SubCount={substring_count}")

        substring_offset = absolute_offset + LOCSTRING_HEAD.size
        for index in range(substring_count):
            if substring_offset + SUBSTRING_HEAD.size > file_size:
                print(f"  Sub{index}: TRUNCATED")
                break
            language_id, string_len = SUBSTRING_HEAD.unpack_from(data, substring_offset)
            substring_offset += SUBSTRING_HEAD.size
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

    if compare_data is not None and len(compare_data) >= HEADER.size:
        original_field_data_size = GFFHeader.read(compare_data).field_data_size
        print(f"\n{'=' * 70}")
        print("COMPARISON")
        print(f"  Original FieldDataSize: {original_field_data_size}")
        print(f"  Current  FieldDataSize: {header.field_data_size}")
        print(f"  Delta:                 {header.field_data_size - original_field_data_size}")


def _load_file_bytes(path: Path) -> bytes:
    """Reads a file, with a clear error when it is missing."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_bytes()


def _extract_resource_bytes(module_path: Path, resource_name: str) -> bytes:
    """Returns the bytes of the resource the pipeline would extract as *resource_name*."""
    reader = ERFReader(module_path)
    for entry in reader.read_entries():
        if entry.offset == UNUSED_OFFSET:
            continue
        if reader.filename_for(entry).lower() != resource_name.lower():
            continue
        with module_path.open("rb") as handle:
            handle.seek(entry.offset)
            return handle.read(entry.size)
    raise FileNotFoundError(f"Resource not found in module: {resource_name}")


def _build_parser() -> argparse.ArgumentParser:
    """Builds the command-line parser.

    Returns:
        The parser of the ``file`` and ``module`` commands.
    """
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
    """Runs the command line.

    Raises:
        FileNotFoundError: If a file, the module or a resource is missing.
    """
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
