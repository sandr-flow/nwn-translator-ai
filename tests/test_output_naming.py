"""Translated .mod output naming: no underscores in filename."""

from __future__ import annotations

from pathlib import Path

from nwn_translator.config import create_output_path, lang_suffix, sanitized_mod_stem


def test_lang_suffix_uses_hyphen_not_underscore() -> None:
    assert lang_suffix("russian") == "-rus"
    assert lang_suffix("de") == "-de"
    assert not lang_suffix("english").startswith("_")
    assert "_" not in lang_suffix("french")


def test_sanitized_mod_stem_replaces_underscores() -> None:
    assert sanitized_mod_stem("foo_bar") == "foo-bar"
    assert "_" not in sanitized_mod_stem("a_b_c")


def test_create_output_path_no_underscores_in_name() -> None:
    p = create_output_path(Path("in") / "my_mod_name.mod", "russian")
    assert p.name == "my-mod-name-rus.mod"
    assert "_" not in p.name
