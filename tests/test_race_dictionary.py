"""Tests for the static race/creature term dictionary and matching logic."""

import pytest

from src.nwn_translator.race_dictionary import RACE_TERMS, match_race_terms

# All language keys present in the dictionary.
ALL_LANGS = list(RACE_TERMS.keys())

# Minimum English keys each language must define (singular forms).
_EXPECTED_MIN_KEYS = {
    "dwarf",
    "dwarves",
    "halfling",
    "gnome",
    "drow",
    "tiefling",
    "goblin",
    "hobgoblin",
    "bugbear",
    "orc",
    "kobold",
    "gnoll",
    "yuan-ti",
    "ogre",
    "troll",
    "elf",
    "elves",
    "half-elf",
    "half-orc",
}


class TestRaceTermsData:
    """RACE_TERMS dictionary has correct shape and coverage."""

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_language_has_minimum_keys(self, lang: str):
        missing = _EXPECTED_MIN_KEYS - set(RACE_TERMS[lang].keys())
        assert not missing, f"{lang} missing keys: {missing}"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_values_are_nonempty_strings(self, lang: str):
        for key, value in RACE_TERMS[lang].items():
            assert (
                isinstance(value, str) and value.strip()
            ), f"{lang}[{key!r}] has empty or non-string value: {value!r}"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_keys_are_lowercase(self, lang: str):
        for key in RACE_TERMS[lang]:
            assert key == key.lower(), f"{lang} has non-lowercase key: {key!r}"

    def test_all_expected_languages_present(self):
        expected = {
            "russian",
            "ukrainian",
            "polish",
            "german",
            "french",
            "spanish",
            "italian",
            "portuguese",
            "czech",
            "romanian",
            "hungarian",
            "dutch",
            "turkish",
            "english",
        }
        missing = expected - set(RACE_TERMS.keys())
        assert not missing, f"Missing languages: {missing}"


class TestMatchRaceTerms:
    """match_race_terms() correctly scans text and builds prompt blocks."""

    def test_basic_match(self):
        result = match_race_terms("Kill the dwarves!", "russian")
        assert '"dwarves"' in result
        assert "дварфы" in result

    def test_multiple_matches(self):
        result = match_race_terms("The bugbear and the kobolds attacked the elves.", "russian")
        assert "багбир" in result
        assert "кобольды" in result
        assert "эльфы" in result

    def test_no_match_returns_empty(self):
        result = match_race_terms("Hello, traveler! Nice weather today.", "russian")
        assert result == ""

    def test_case_insensitive(self):
        result = match_race_terms("The BUGBEAR roared.", "russian")
        assert "багбир" in result

    def test_mixed_case(self):
        result = match_race_terms("A Dwarven fortress.", "russian")
        assert "дварфийский" in result

    def test_hyphenated_terms(self):
        result = match_race_terms("She is a half-elf ranger.", "russian")
        assert "полуэльф" in result

    def test_yuan_ti(self):
        result = match_race_terms("The yuan-ti temple was ancient.", "russian")
        assert "юань-ти" in result

    def test_half_elf_does_not_match_bare_elf_alone(self):
        """When the text says 'half-elf', the 'elf' inside must not produce
        a separate spurious match (the hyphen prevents word-boundary match)."""
        result = match_race_terms("She is a half-elf.", "russian")
        assert "полуэльф" in result
        lines = result.strip().split("\n")
        term_lines = [l for l in lines if l.strip().startswith("*")]
        keys_found = [l.split('"')[1] for l in term_lines]
        assert "elf" not in keys_found, "bare 'elf' should not match inside 'half-elf'"

    def test_empty_text(self):
        assert match_race_terms("", "russian") == ""

    def test_none_text(self):
        assert match_race_terms(None, "russian") == ""  # type: ignore[arg-type]

    def test_unknown_language_returns_empty(self):
        assert match_race_terms("Kill the dwarves!", "klingon") == ""

    def test_word_boundary_no_false_positive(self):
        """'orcs' should not match inside 'workforce' or 'sorcery'."""
        result = match_race_terms("The workforce improved sorcery.", "russian")
        assert "орк" not in result

    def test_elf_does_not_match_inside_self(self):
        """'elf' should not match inside 'herself' or 'bookshelf'."""
        result = match_race_terms("She proved herself near the bookshelf.", "russian")
        assert result == ""

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_matches_for_every_language(self, lang: str):
        result = match_race_terms("dwarves and goblins", lang)
        assert result != "", f"No match for {lang}"
        assert "RACE/CREATURE TERMS" in result

    def test_format_has_header(self):
        result = match_race_terms("a goblin", "russian")
        assert result.startswith("RACE/CREATURE TERMS")

    def test_format_has_arrow(self):
        result = match_race_terms("a goblin", "russian")
        assert "\u2192" in result


class TestCrossLanguageConsistency:
    """Ensure consistent behaviour across languages."""

    def test_same_text_different_langs_produce_different_translations(self):
        ru = match_race_terms("The dwarf spoke.", "russian")
        fr = match_race_terms("The dwarf spoke.", "french")
        assert "дварф" in ru
        assert "nain" in fr

    def test_czech_has_unique_terms(self):
        result = match_race_terms("A bugbear and a hobgoblin.", "czech")
        assert "gobr" in result
        assert "skurut" in result
