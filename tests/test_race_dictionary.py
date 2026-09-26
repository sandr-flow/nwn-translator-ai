"""The static race and creature term dictionary and its matcher."""

import pytest

from nwn_translator.race_dictionary import RACE_TERMS, match_race_terms

ALL_LANGS = list(RACE_TERMS)

#: English keys every language defines.
_REQUIRED_KEYS = {
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


def test_every_language_is_present():
    assert {
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
        "english",
    } <= set(RACE_TERMS)


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_language_table_shape(lang):
    terms = RACE_TERMS[lang]
    assert not _REQUIRED_KEYS - set(terms), f"{lang} lacks required keys"
    for key, value in terms.items():
        assert key == key.lower(), f"{lang} has a non-lowercase key: {key!r}"
        assert isinstance(value, str) and value.strip(), f"{lang}[{key!r}] is empty"
    block = match_race_terms("dwarves and goblins", lang)
    assert block.startswith("RACE/CREATURE TERMS")


@pytest.mark.parametrize(
    "text, lang, present",
    [
        ("Kill the dwarves!", "russian", ['"dwarves"', "дварфы"]),
        (
            "The bugbear and the kobolds attacked the elves.",
            "russian",
            ["багбир", "кобольды", "эльфы"],
        ),
        ("The BUGBEAR roared.", "russian", ["багбир"]),
        ("A Dwarven fortress.", "russian", ["дварфийский"]),
        ("The yuan-ti temple was ancient.", "russian", ["юань-ти"]),
        ("a goblin", "russian", ["→"]),
        ("The dwarf spoke.", "russian", ["дварф"]),
        ("The dwarf spoke.", "french", ["nain"]),
        ("A bugbear and a hobgoblin.", "czech", ["gobr", "skurut"]),
    ],
)
def test_matched_terms(text, lang, present):
    block = match_race_terms(text, lang)
    for part in present:
        assert part in block


def test_half_elf_does_not_also_match_a_bare_elf():
    """The hyphen keeps the ``elf`` inside ``half-elf`` from matching on its own."""
    block = match_race_terms("She is a half-elf ranger.", "russian")
    assert "полуэльф" in block
    keys = [line.split('"')[1] for line in block.splitlines() if line.strip().startswith("*")]
    assert "elf" not in keys


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Hello, traveler! Nice weather today.", "russian"),
        ("", "russian"),
        (None, "russian"),
        ("Kill the dwarves!", "klingon"),
        # Word boundaries: "orc" is not in "workforce" or "sorcery", "elf" not in "herself".
        ("The workforce improved sorcery.", "russian"),
        ("She proved herself near the bookshelf.", "russian"),
    ],
)
def test_no_match_gives_an_empty_block(text, lang):
    assert match_race_terms(text, lang) == ""
