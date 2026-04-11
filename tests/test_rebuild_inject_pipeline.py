"""Unified load + inject path used by rebuild and Phase C."""

from __future__ import annotations

from pathlib import Path

from nwn_translator.file_handlers.ncs_parser import parse_ncs
from nwn_translator.main import inject_translations_into_file, load_parsed_and_extracted

from tests.test_ncs import _consts, _retn, _write_ncs


def test_load_and_inject_ncs_from_text_translation_map(tmp_path: Path) -> None:
    """Rebuild-style: ``translations`` keyed by original text derives NCS item map."""
    path = _write_ncs(tmp_path, "s.ncs", _consts("Hello world!"), _retn())
    loaded = load_parsed_and_extracted(path, ".ncs", None, None)
    assert loaded is not None
    parsed, extracted = loaded
    inject_translations_into_file(
        path,
        parsed,
        extracted,
        {"Hello world!": "Hi there all!"},
        ncs_translations_by_item_id=None,
    )
    ncs2 = parse_ncs(path)
    assert any((i.string_value or "") == "Hi there all!" for i in ncs2.string_constants)


def test_load_and_inject_ncs_prefers_explicit_item_id_map(tmp_path: Path) -> None:
    path = _write_ncs(tmp_path, "t.ncs", _consts("Hello world!"), _retn())
    loaded = load_parsed_and_extracted(path, ".ncs", None, None)
    assert loaded is not None
    parsed, extracted = loaded
    item_id = extracted.items[0].item_id
    assert item_id
    inject_translations_into_file(
        path,
        parsed,
        extracted,
        {},
        ncs_translations_by_item_id={item_id: "ZZ"},
    )
    ncs2 = parse_ncs(path)
    assert any(i.string_value == "ZZ" for i in ncs2.string_constants)
