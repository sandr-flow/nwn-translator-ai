"""V2.2 identity round-trip: extract then repack without translating.

The output archive must contain the same resources, with the same type IDs and
byte-identical contents, as the input. This pins ERF read/write fidelity (C3
type-id table, M-W8 module description) without any translation in the loop.
"""

from __future__ import annotations

from pathlib import Path

from nwn_translator.file_handlers.erf_writer import create_mod_from_directory

from ._corpus import extract_module, read_raw_resources


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
