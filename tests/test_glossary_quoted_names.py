"""Glossary keys that carry quotation marks from the game data itself."""

from __future__ import annotations

from nwn_translator.glossary import GlossaryBuilder


def test_quoted_key_matches_bare_model_key() -> None:
    """A module named an area ``"Thesis Paper Room"``, quotation marks included.

    The model answers with the bare name as its JSON key — it cannot echo the
    quotes back without escaping them — so an exact-match lookup dropped the
    entry and the name silently fell out of the glossary.
    """
    raw = '{"Thesis Paper Room": "Комната диссертаций"}'
    entries = GlossaryBuilder._parse_glossary_json(raw, {'"Thesis Paper Room"'})

    assert entries == {'"Thesis Paper Room"': '"Комната диссертаций"'}


def test_wrapping_quotes_survive_into_the_translation() -> None:
    """The glossary seeds the exact-match cache, so its value replaces the whole
    game string — dropping the author's quotes would patch them out of the module."""
    raw = '{"Thesis Paper Room": "Комната диссертаций"}'
    entries = GlossaryBuilder._parse_glossary_json(raw, {"«Thesis Paper Room»"})

    assert entries == {"«Thesis Paper Room»": "«Комната диссертаций»"}


def test_quotes_the_model_already_returned_are_not_doubled() -> None:
    raw = '{"Thesis Paper Room": "\\"Комната диссертаций\\""}'
    entries = GlossaryBuilder._parse_glossary_json(raw, {'"Thesis Paper Room"'})

    assert entries == {'"Thesis Paper Room"': '"Комната диссертаций"'}


def test_quoted_key_with_category_suffix_still_matches() -> None:
    """Quote stripping must compose with the existing ``(suffix)`` handling."""
    raw = '{"Planar Studies Section": "Секция изучения планов"}'
    expected = '"Planar Studies Section (Restricted)"'
    entries = GlossaryBuilder._parse_glossary_json(raw, {expected})

    assert entries == {expected: '"Секция изучения планов"'}


def test_unquoted_keys_are_unaffected() -> None:
    raw = '{"Dewey Plowshare": "Дьюи Плаушер"}'
    entries = GlossaryBuilder._parse_glossary_json(raw, {"Dewey Plowshare"})

    assert entries == {"Dewey Plowshare": "Дьюи Плаушер"}


def test_inner_quotes_are_not_stripped() -> None:
    """Only a matched pair wrapping the whole name is noise; inner quotes are not."""
    key = 'He said "hi"'
    assert GlossaryBuilder._glossary_key_variants(key) == [key]
