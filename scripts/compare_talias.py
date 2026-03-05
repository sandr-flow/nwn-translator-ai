"""Compare talias.utc between original and translated modules.

Dumps FirstName/LastName fields plus raw hex from the CExoLocString payload
so we can see whether the patcher actually wrote CP1251 bytes.
"""

import struct
import tempfile
from pathlib import Path

from nwn_translator.file_handlers.erf_reader import ERFReader
from nwn_translator.file_handlers.gff_handler import read_gff


def extract_resource_bytes(mod_path: Path, res_name: str) -> bytes:
    """Extract raw bytes of a named resource from a .mod file."""
    reader = ERFReader(mod_path)
    entries = reader.read_entries()
    for entry in entries:
        ext = reader.get_resource_type(entry.res_type)
        if f"{entry.res_ref}{ext}".lower() == res_name.lower():
            with open(mod_path, "rb") as f:
                f.seek(entry.offset)
                return f.read(entry.size)
    return b""


def dump_locstring_field(data: bytes, record_offset: int, label: str) -> None:
    """Read the raw CExoLocString payload for a field and print hex + decoded text."""
    if record_offset <= 0 or record_offset + 12 > len(data):
        print(f"  [{label}] invalid record_offset={record_offset}")
        return

    field_data_offset = struct.unpack("<I", data[32:36])[0]
    data_or_offset = struct.unpack("<I", data[record_offset + 8 : record_offset + 12])[0]
    abs_offset = field_data_offset + data_or_offset

    if abs_offset + 12 > len(data):
        print(f"  [{label}] payload beyond file end (abs_offset={abs_offset})")
        return

    total_size = struct.unpack("<I", data[abs_offset : abs_offset + 4])[0]
    str_ref = struct.unpack("<i", data[abs_offset + 4 : abs_offset + 8])[0]
    sub_count = struct.unpack("<I", data[abs_offset + 8 : abs_offset + 12])[0]

    print(f"  [{label}] TotalSize={total_size}  StrRef={str_ref}  SubCount={sub_count}")

    sub_off = abs_offset + 12
    for s in range(sub_count):
        if sub_off + 8 > len(data):
            break
        lang_id = struct.unpack("<I", data[sub_off : sub_off + 4])[0]
        str_len = struct.unpack("<I", data[sub_off + 4 : sub_off + 8])[0]
        sub_off += 8
        raw = data[sub_off : sub_off + str_len]
        sub_off += str_len

        hex_preview = raw[:60].hex(" ")
        # Try UTF-8 first, then CP1251
        try:
            text = raw.decode("utf-8")
            enc = "UTF-8"
        except UnicodeDecodeError:
            try:
                text = raw.decode("cp1251")
                enc = "CP1251"
            except Exception:
                text = repr(raw)
                enc = "RAW"

        print(f"    Sub{s}: lang={lang_id} len={str_len} enc={enc}")
        print(f"      hex: {hex_preview}")
        print(f"      txt: \"{text}\"")

    if sub_count == 0:
        print(f"    (no substrings)")


def dump_creature(mod_path: Path, res_name: str, dump_name: str):
    """Dump FirstName/LastName from a creature resource in a module."""
    raw = extract_resource_bytes(mod_path, res_name)
    if not raw:
        print(f"[{dump_name}] Resource {res_name} not found in {mod_path.name}")
        return

    # Write temp file for read_gff
    tmp_file = Path(f"{dump_name}_{res_name}")
    with open(tmp_file, "wb") as f:
        f.write(raw)

    gff = read_gff(tmp_file)
    offsets = gff.get("_record_offsets", {})

    print(f"\n[{dump_name}] {mod_path.name} -> {res_name}")
    print(f"  FirstName (parsed): {gff.get('FirstName')}")
    print(f"  LastName  (parsed): {gff.get('LastName')}")
    print(f"  _record_offsets: FirstName={offsets.get('FirstName')}, LastName={offsets.get('LastName')}")

    print(f"\n  --- RAW FirstName CExoLocString ---")
    dump_locstring_field(raw, offsets.get("FirstName", 0), "FirstName")

    print(f"  --- RAW LastName CExoLocString ---")
    dump_locstring_field(raw, offsets.get("LastName", 0), "LastName")
    print("=" * 60)


if __name__ == "__main__":
    orig = Path(r"test_modules\The Dark Ranger's Treasure.mod")
    trans = Path(r"workspace\The Dark Ranger's Treasure_rus\The Dark Ranger's Treasure_rus.mod")

    dump_creature(orig, "talias.utc", "ORIG")
    dump_creature(trans, "talias.utc", "TRANS")
