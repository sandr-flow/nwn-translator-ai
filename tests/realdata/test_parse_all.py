"""V2.1 parse-all: every parsable resource of every corpus module parses.

For GFF resources this means :func:`read_gff` does not raise. For ``.dlg`` it
additionally means :meth:`DialogExtractor.build_dialog_tree` builds a tree
(the contextual translation path depends on it). For ``.ncs`` it additionally
means the instruction stream reaches exactly the end declared by the NWN:EE
preamble field ``T`` (a parser-independent synchronization check: if the
parser desyncs on an opcode with a wrong argument size, either it raises or
the declared ``T`` no longer equals the file size it walked).
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from nwn_translator.config import TRANSLATABLE_TYPES
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.file_handlers.gff_handler import read_gff
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes

from ._corpus import extract_module

_NCS_EE_SIZE_OPCODE = 0x42


def _declared_ncs_size(raw: bytes) -> int | None:
    """Return the NWN:EE preamble ``T`` (declared script size), or ``None``."""
    if len(raw) >= 13 and raw[8] == _NCS_EE_SIZE_OPCODE:
        return struct.unpack_from(">I", raw, 9)[0]
    return None


def test_parse_all(corpus_module: Path, tmp_path: Path) -> None:
    extract_dir = extract_module(corpus_module, tmp_path / "extract")

    gff_total = 0
    ncs_total = 0
    failures: list[str] = []

    for path in extract_dir.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in TRANSLATABLE_TYPES:
            continue

        if ext == ".ncs":
            ncs_total += 1
            raw = path.read_bytes()
            try:
                parse_ncs_bytes(raw)
            except Exception as exc:  # noqa: BLE001 - robustness sweep
                failures.append(f"NCS parse {path.name}: {type(exc).__name__}: {exc}")
                continue
            declared = _declared_ncs_size(raw)
            if declared is not None and declared != len(raw):
                failures.append(f"NCS size mismatch {path.name}: T={declared} actual={len(raw)}")
        else:
            gff_total += 1
            try:
                parsed = read_gff(path)
            except Exception as exc:  # noqa: BLE001 - robustness sweep
                failures.append(f"GFF parse {path.name}: {type(exc).__name__}: {exc}")
                continue
            if ext == ".dlg":
                try:
                    DialogExtractor().build_dialog_tree(parsed)
                except Exception as exc:  # noqa: BLE001 - robustness sweep
                    failures.append(f"DLG tree {path.name}: {type(exc).__name__}: {exc}")

    assert not failures, (
        f"{corpus_module.name}: {len(failures)} parse failures "
        f"(GFF scanned={gff_total}, NCS scanned={ncs_total}). "
        f"First 20:\n" + "\n".join(failures[:20])
    )
