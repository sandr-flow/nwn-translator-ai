"""Compare resources between original and translated .mod files.

Usage:
    python compare_mods.py <original.mod> <translated.mod>
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from nwn_translator.file_handlers.erf_reader import ERFReader


def list_resources(mod_path: Path) -> dict:
    """List all resources in a .mod file."""
    reader = ERFReader(mod_path)
    entries = reader.read_entries()
    resources = {}
    for entry in entries:
        ext = reader.get_resource_type(entry.res_type)
        full_name = f"{entry.res_ref}{ext}"
        resources[full_name] = {
            "res_ref": entry.res_ref,
            "res_type": entry.res_type,
            "ext": ext,
            "size": entry.size,
            "offset": entry.offset,
        }
    return resources


def check_gff_type(mod_path: Path, entry_info: dict) -> str:
    """Read the GFF file type header from a resource."""
    with open(mod_path, "rb") as f:
        f.seek(entry_info["offset"])
        header = f.read(8)
        if len(header) >= 8:
            file_type = header[0:4].decode("ascii", errors="replace").strip()
            return file_type
    return "?"


def main():
    """Compare resources between two modules."""
    if len(sys.argv) < 3:
        print("Usage: python compare_mods.py <original.mod> <translated.mod>")
        sys.exit(1)

    orig_path = Path(sys.argv[1])
    trans_path = Path(sys.argv[2])

    print(f"Original:   {orig_path} ({orig_path.stat().st_size:,} bytes)")
    print(f"Translated: {trans_path} ({trans_path.stat().st_size:,} bytes)")

    orig_res = list_resources(orig_path)
    trans_res = list_resources(trans_path)

    print(f"\nOriginal:   {len(orig_res)} resources")
    print(f"Translated: {len(trans_res)} resources")

    # Show ALL resources from original with their types
    print(f"\n{'='*80}")
    print(f"{'Resource':<30} {'Type':>6} {'Ext':<6} {'Size':>8}  {'GFF Header':<10}")
    print(f"{'='*80}")

    # Check important file types in original
    for name in sorted(orig_res.keys()):
        info = orig_res[name]
        gff_type = ""
        if info["ext"] in (".dlg", ".utc", ".uti", ".utp", ".utd", ".uts",
                           ".utt", ".utw", ".ute", ".are", ".ifo", ".jrl",
                           ".git", ".fac", ".itp"):
            gff_type = check_gff_type(orig_path, info)
        print(f"  {name:<28} {info['res_type']:>6} {info['ext']:<6} {info['size']:>8}  {gff_type}")

    # Now show translated and highlight differences
    print(f"\n{'='*80}")
    print(f"TRANSLATED module resources:")
    print(f"{'='*80}")
    print(f"{'Resource':<30} {'Type':>6} {'Ext':<6} {'Size':>8}  {'GFF Header':<10} {'Status'}")
    print(f"{'-'*95}")

    for name in sorted(trans_res.keys()):
        info = trans_res[name]
        gff_type = ""
        if info["ext"] in (".dlg", ".utc", ".uti", ".utp", ".utd", ".uts",
                           ".utt", ".utw", ".ute", ".are", ".ifo", ".jrl",
                           ".git", ".fac", ".itp"):
            gff_type = check_gff_type(trans_path, info)

        # Compare with original
        status = ""
        if name in orig_res:
            orig_info = orig_res[name]
            if info["size"] != orig_info["size"]:
                status = f"SIZE CHANGED ({orig_info['size']} -> {info['size']})"
            elif info["res_type"] != orig_info["res_type"]:
                status = f"TYPE CHANGED ({orig_info['res_type']} -> {info['res_type']})"
            else:
                status = "unchanged"
        else:
            status = "NEW"

        # Check for GFF type mismatch
        expected_gff = {
            ".dlg": "DLG", ".utc": "UTC", ".uti": "UTI", ".utp": "UTP",
            ".utd": "UTD", ".uts": "UTS", ".utt": "UTT", ".utw": "UTW",
            ".ute": "UTE", ".are": "ARE", ".ifo": "IFO", ".jrl": "JRL",
            ".git": "GIT", ".fac": "FAC", ".itp": "ITP",
        }
        expected = expected_gff.get(info["ext"], "")
        if gff_type and expected and gff_type != expected:
            status += f" *** GFF MISMATCH: expected {expected}, got {gff_type} ***"

        print(f"  {name:<28} {info['res_type']:>6} {info['ext']:<6} {info['size']:>8}  {gff_type:<10} {status}")

    # Show resources missing from translated
    missing = set(orig_res.keys()) - set(trans_res.keys())
    if missing:
        print(f"\n*** MISSING from translated module ({len(missing)}):")
        for name in sorted(missing):
            print(f"  {name}")

    # Show resources only in translated
    extra = set(trans_res.keys()) - set(orig_res.keys())
    if extra:
        print(f"\n*** EXTRA in translated module ({len(extra)}):")
        for name in sorted(extra):
            print(f"  {name}")


if __name__ == "__main__":
    main()
