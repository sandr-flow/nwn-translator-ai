"""Unified load + inject path used by rebuild and the inject stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.formats.ncs import parse_ncs
from nwn_translator.injectors.base import InjectedContent
from nwn_translator.main import (
    ModuleTranslator,
    inject_translations_into_file,
    load_parsed_and_extracted,
)
from nwn_translator.pipeline import stages
from nwn_translator.pipeline.stages import PipelineState, stage_inject

from tests.test_ncs import _consts, _retn, _write_ncs


class CapturingWriter:
    def __init__(self) -> None:
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def test_load_and_inject_ncs_from_text_translation_map(tmp_path: Path) -> None:
    """Rebuild-style: a translation addressed by occurrence is patched into the script."""
    path = _write_ncs(tmp_path, "s.ncs", _consts("Hello world!"), _retn())
    loaded = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded is not None
    parsed, extracted = loaded
    inject_translations_into_file(
        path,
        parsed,
        extracted,
        {extracted.items[0].key: "Hi there all!"},
    )
    ncs2 = parse_ncs(path)
    assert any((i.string_value or "") == "Hi there all!" for i in ncs2.string_constants)


def test_load_and_inject_ncs_prefers_explicit_item_id_map(tmp_path: Path) -> None:
    path = _write_ncs(tmp_path, "t.ncs", _consts("Hello world!"), _retn())
    loaded = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded is not None
    parsed, extracted = loaded
    item_id = extracted.items[0].item_id
    assert item_id
    inject_translations_into_file(
        path,
        parsed,
        extracted,
        {(path.name, item_id): text for item_id, text in ({item_id: "ZZ"}).items()},
    )
    ncs2 = parse_ncs(path)
    assert any(i.string_value == "ZZ" for i in ncs2.string_constants)


def test_inject_records_ncs_patch_failure_stats(tmp_path: Path, monkeypatch) -> None:
    """A script whose patch failed is counted, sampled and logged by the inject stage."""
    writer = CapturingWriter()
    monkeypatch.setattr(stages, "create_provider", lambda *args, **kwargs: Mock())
    translator = ModuleTranslator(
        TranslationConfig(
            api_key="test-key",
            model="test-model",
            source_lang="english",
            target_lang="russian",
            input_file=tmp_path / "m.mod",
            translation_log_writer=writer,
        )
    )
    script = tmp_path / "s.ncs"
    failure = {"type": "ncs_script", "ncs_patch_failed": True, "error": "validation failed"}
    monkeypatch.setattr(
        stages,
        "inject_translations_into_file",
        lambda *args, **kwargs: InjectedContent(script, False, 0, failure),
    )
    translator.state.extract_dir = tmp_path
    content = ExtractedContent(content_type="ncs_script", items=[], source_file=script)

    stage_inject(translator.state, {script: ({}, content, ".ncs")}, {})

    stats = translator.stats["ncs_diagnostics"]
    assert stats["patch_failed"] == 1
    assert stats["samples"][0]["reason"] == "patch_failed"
    assert translator.stats["files_processed"] == 1
    assert writer.entries[1:] == [
        {
            "event": "ncs_diagnostic",
            "file": "s.ncs",
            "reason": "patch_failed",
            "error": "validation failed",
        }
    ]


def test_log_per_file_emits_failed_originals(tmp_path: Path) -> None:
    writer = CapturingWriter()
    config = TranslationConfig(
        api_key="test-key",
        input_file=tmp_path / "m.mod",
        translation_log_writer=writer,
    )
    src = tmp_path / "a.uti"
    extracted = ExtractedContent(
        content_type="item",
        items=[TranslatableItem(text="Boom", item_id="x:0", location=str(src))],
        source_file=src,
    )
    skipped = ExtractedContent(
        content_type="item",
        items=[TranslatableItem(text="Internal", item_id="skip", location=str(src))],
        source_file=src,
    )
    state = PipelineState(config=config, provider=Mock())
    failed = {("a.uti", "x:0")}
    stages._log_editor_rows(state, {src: ({}, extracted, ".uti")}, {}, failed)
    failed_rows = [e for e in writer.entries if e.get("success") is False]
    assert len(failed_rows) == 1
    assert failed_rows[0]["original"] == "Boom"
    assert failed_rows[0]["translated"] == "Boom"
    assert failed_rows[0]["item_id"] == "x:0"
    assert failed_rows[0]["file"] == "a.uti"

    writer.entries.clear()
    other = tmp_path / "b.uti"
    stages._log_editor_rows(state, {other: ({}, skipped, ".uti")}, {}, failed)
    assert writer.entries == []


def test_ncs_item_id_stable_after_length_changing_patch(tmp_path: Path) -> None:
    """CONSTS index ids survive a length-changing patch of an earlier string."""
    from tests.test_ncs import _action

    path = _write_ncs(
        tmp_path,
        "scene.ncs",
        _consts("NW_TAG"),
        _action(200, 1),
        _consts("Alpha line."),
        _action(39, 1),
        _consts("Beta line."),
        _action(39, 1),
        _consts("Gamma line."),
        _action(39, 1),
        _retn(),
    )
    loaded = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded is not None
    parsed, extracted = loaded
    ids = [item.item_id for item in extracted.items]
    assert ids == ["scene:c1", "scene:c2", "scene:c3"]
    offsets_before = {item.item_id: item.metadata["offset"] for item in extracted.items}

    inject_translations_into_file(
        path,
        parsed,
        extracted,
        {
            (path.name, item_id): text
            for item_id, text in (
                {
                    "scene:c1": "Alpha line is now much longer than before!",
                }
            ).items()
        },
    )

    loaded2 = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded2 is not None
    parsed2, extracted2 = loaded2
    assert [item.item_id for item in extracted2.items] == ["scene:c1", "scene:c2", "scene:c3"]
    offsets_after = {item.item_id: item.metadata["offset"] for item in extracted2.items}
    assert offsets_after["scene:c1"] == offsets_before["scene:c1"]
    assert offsets_after["scene:c2"] != offsets_before["scene:c2"]
    assert offsets_after["scene:c3"] != offsets_before["scene:c3"]

    inject_translations_into_file(
        path,
        parsed2,
        extracted2,
        {(path.name, item_id): text for item_id, text in ({"scene:c3": "Gamma-EDITED"}).items()},
    )
    values = [instr.string_value for instr in parse_ncs(path).string_constants]
    assert values[0] == "NW_TAG"
    assert "Alpha line is now much longer than before!" in values
    assert "Beta line." in values
    assert "Gamma-EDITED" in values
    assert "Gamma line." not in values
