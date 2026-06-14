"""Realdata: rebuild applies edits addressed by item_id on a real module.

Mirrors the production flow: mock-translate Almraiven, then rebuild a handful of
edited GFF fields addressed by ``(file, item_id)`` and confirm every edit lands
in the output while neighbours stay put — even though the on-disk text is
already translated (so a text-keyed map would never match).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nwn_translator.config import TRANSLATABLE_TYPES, TranslationConfig
from nwn_translator.main import rebuild_module
from nwn_translator.pipeline.stages import (
    PipelineState,
    load_parsed_and_extracted,
    run_pipeline,
)

from ._corpus import corpus_dir, extract_module
from ._mock_provider import MockTranslateProvider

_MODULE = "Almraiven.mod"
_EDIT_SUFFIX = "_EDITED"
_N_EDITS = 10


def _gff_items_by_file(extract_dir: Path) -> dict[str, dict[str, str]]:
    """Map ``{filename: {item_id: current_text}}`` for GFF resources."""
    result: dict[str, dict[str, str]] = {}
    for path in sorted(extract_dir.rglob("*")):
        ext = path.suffix.lower()
        if not path.is_file() or ext == ".ncs" or ext not in TRANSLATABLE_TYPES:
            continue
        loaded = load_parsed_and_extracted(path, ext, None, None)
        if loaded is None:
            continue
        _parsed, extracted = loaded
        per_file = {it.item_id: it.text for it in extracted.items if it.item_id and it.text}
        if per_file:
            result[path.name] = per_file
    return result


def test_rebuild_applies_item_id_edits(tmp_path: Path) -> None:
    cdir = corpus_dir()
    if cdir is None or not (cdir / _MODULE).exists():
        pytest.skip(f"{_MODULE} not in corpus")
    module = cdir / _MODULE

    config = TranslationConfig(
        api_key="mock-key",
        input_file=module,
        output_file=tmp_path / "translated.mod",
        target_lang="russian",
        use_context=False,
        temp_dir=tmp_path,
        skip_cleanup=True,
        quiet=True,
    )
    state = PipelineState(config=config, provider=MockTranslateProvider())
    run_pipeline(state)
    extract_dir = state.extract_dir
    assert extract_dir is not None

    # Identity map of the already-translated on-disk text, then edit N entries.
    by_file = _gff_items_by_file(extract_dir)
    chosen: list[tuple[str, str]] = []
    for fname, items in by_file.items():
        for item_id, text in items.items():
            chosen.append((fname, item_id))
            by_file[fname][item_id] = text + _EDIT_SUFFIX
            if len(chosen) >= _N_EDITS:
                break
        if len(chosen) >= _N_EDITS:
            break
    assert len(chosen) == _N_EDITS, "corpus module yielded too few GFF items"

    rebuild_module(
        extract_dir,
        by_file,
        tmp_path / "rebuilt.mod",
        original_mod_path=module,
        target_lang="russian",
    )

    # Re-extract the rebuilt output and confirm every edit is present.
    rebuilt_dir = extract_module(tmp_path / "rebuilt.mod", tmp_path / "reextract")
    after = _gff_items_by_file(rebuilt_dir)
    missing = [
        f"{fname}:{item_id}"
        for (fname, item_id) in chosen
        if not (after.get(fname, {}).get(item_id, "")).endswith(_EDIT_SUFFIX)
    ]
    assert not missing, f"{len(missing)} edits missing from rebuilt output: {missing}"
