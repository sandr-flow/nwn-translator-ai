"""V2.3 no-op patch: injecting ``{original: original}`` changes no bytes.

Every translatable resource is extracted and then re-injected with each string
mapped to itself. A correct injector/patcher stack must skip identical text and
leave the file byte-for-byte unchanged. This pins the "no-op is truly a no-op"
invariant that the P1 patcher refactor must preserve.
"""

from __future__ import annotations

from pathlib import Path

from nwn_translator.config import TRANSLATABLE_TYPES
from nwn_translator.pipeline.stages import (
    inject_translations_into_file,
    load_parsed_and_extracted,
)

from ._corpus import extract_module


def test_noop_patch(corpus_module: Path, tmp_path: Path) -> None:
    extract_dir = extract_module(corpus_module, tmp_path / "extract")

    scanned = 0
    changed: list[str] = []

    for path in sorted(extract_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TRANSLATABLE_TYPES:
            continue

        loaded = load_parsed_and_extracted(path, path.suffix.lower(), None)
        if loaded is None:
            continue
        parsed_data, extracted = loaded

        identity = {item.text: item.text for item in extracted.items if item.text}
        if not identity:
            continue

        scanned += 1
        before = path.read_bytes()
        inject_translations_into_file(path, parsed_data, extracted, identity)
        after = path.read_bytes()
        if before != after:
            changed.append(f"{path.name} ({extracted.content_type})")

    assert not changed, (
        f"{corpus_module.name}: {len(changed)}/{scanned} files changed by a no-op patch. "
        f"First 20:\n" + "\n".join(changed[:20])
    )
