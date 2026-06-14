"""Corpus discovery and low-level helpers for realdata tests.

The corpus lives at ``test_corpus/`` by default, overridable via the
``NWN_TEST_CORPUS`` environment variable. All helpers degrade to "no corpus"
(empty list / ``None``) so collection never fails when the corpus is absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# tests/realdata/_corpus.py -> repo root is two parents up from this file's dir.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Resource archive extensions the pipeline accepts.
_ARCHIVE_GLOBS = ("*.mod", "*.erf", "*.hak")


def corpus_dir() -> Optional[Path]:
    """Return the configured corpus directory, or ``None`` if it does not exist."""
    env = os.environ.get("NWN_TEST_CORPUS")
    candidate = Path(env) if env else REPO_ROOT / "test_corpus"
    return candidate if candidate.is_dir() else None


def list_module_paths() -> List[Path]:
    """All module archives in the corpus, sorted by name (empty if no corpus)."""
    directory = corpus_dir()
    if directory is None:
        return []
    found: List[Path] = []
    for pattern in _ARCHIVE_GLOBS:
        found.extend(directory.glob(pattern))
    return sorted(found, key=lambda p: p.name.lower())


def load_manifest() -> Dict[str, object]:
    """Parse ``manifest.json`` if present, else an empty dict."""
    directory = corpus_dir()
    if directory is None:
        return {}
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return {}
    return json.loads(manifest.read_text(encoding="utf-8"))


def read_raw_resources(mod_path: Path) -> Tuple[list, Dict[Tuple[str, int], bytes]]:
    """Read each resource's raw bytes straight from the archive by offset/size.

    This is intentionally parser-independent: it does not decode GFF/NCS, so it
    can serve as the ground truth for identity round-trip byte comparisons.

    Returns ``(entries, {(res_ref_lower, res_type_id): raw_bytes})``.
    """
    from nwn_translator.file_handlers.erf_reader import ERFReader

    reader = ERFReader(mod_path)
    entries = reader.read_entries()
    raw: Dict[Tuple[str, int], bytes] = {}
    with open(mod_path, "rb") as handle:
        for entry in entries:
            handle.seek(entry.offset)
            raw[(entry.res_ref.lower(), entry.res_type)] = handle.read(entry.size)
    reader.cleanup()
    return entries, raw


def extract_module(mod_path: Path, dest: Path) -> Path:
    """Extract *mod_path* into *dest* and return *dest*."""
    from nwn_translator.file_handlers.erf_reader import ERFReader

    reader = ERFReader(mod_path)
    reader.read_entries()
    reader.extract_all(dest)
    reader.cleanup()
    return dest
