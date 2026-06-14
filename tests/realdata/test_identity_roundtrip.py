"""V2.2 identity round-trip: extract then repack without translating.

The output archive must contain the same resources, with the same type IDs and
byte-identical contents, as the input. This pins ERF read/write fidelity (C3
type-id table, M-W8 module description) without any translation in the loop.

A second run repacks with ``original_mod=None`` so resource type IDs come from
the canonical table alone (no per-file overrides) — that isolates whether the
table itself is correct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from nwn_translator.file_handlers.erf_reader import ERFReader
from nwn_translator.file_handlers.erf_writer import create_mod_from_directory

from ._corpus import extract_module, read_raw_resources


def _type_ids_by_resource(mod_path: Path) -> Dict[Tuple[str, str], int]:
    """Map ``(res_ref, detected_ext) -> res_type_id`` for every resource."""
    reader = ERFReader(mod_path)
    entries = reader.read_entries()
    result: Dict[Tuple[str, str], int] = {}
    for entry in entries:
        ext = reader.detect_type_from_header(entry)
        result[(entry.res_ref.lower(), ext)] = entry.res_type
    reader.cleanup()
    return result


def test_identity_roundtrip(corpus_module: Path, tmp_path: Path) -> None:
    _entries_in, raw_in = read_raw_resources(corpus_module)

    extract_dir = extract_module(corpus_module, tmp_path / "extract")
    out_path = tmp_path / "roundtrip.mod"
    create_mod_from_directory(extract_dir, out_path, original_mod=corpus_module)

    _entries_out, raw_out = read_raw_resources(out_path)

    keys_in = set(raw_in)
    keys_out = set(raw_out)

    missing = keys_in - keys_out
    extra = keys_out - keys_in
    byte_diffs = [k for k in keys_in & keys_out if raw_in[k] != raw_out[k]]

    problems = []
    if missing:
        problems.append(f"{len(missing)} resources missing from output, e.g. {sorted(missing)[:5]}")
    if extra:
        problems.append(f"{len(extra)} unexpected resources in output, e.g. {sorted(extra)[:5]}")
    if byte_diffs:
        problems.append(
            f"{len(byte_diffs)} resources differ in bytes, e.g. {sorted(byte_diffs)[:5]}"
        )

    assert not problems, (
        f"{corpus_module.name}: identity round-trip mismatch "
        f"(in={len(keys_in)} out={len(keys_out)}):\n" + "\n".join(problems)
    )


def test_type_ids_match_without_overrides(corpus_module: Path, tmp_path: Path) -> None:
    before = _type_ids_by_resource(corpus_module)

    extract_dir = extract_module(corpus_module, tmp_path / "extract")
    out_path = tmp_path / "no_overrides.mod"
    create_mod_from_directory(extract_dir, out_path, original_mod=None)

    after = _type_ids_by_resource(out_path)

    mismatches = [
        f"{ref}{ext}: in={before[(ref, ext)]} out={after.get((ref, ext))}"
        for (ref, ext) in before
        if after.get((ref, ext)) != before[(ref, ext)]
    ]
    assert not mismatches, (
        f"{corpus_module.name}: {len(mismatches)} type-id mismatches without overrides, "
        f"first 20:\n" + "\n".join(mismatches[:20])
    )
