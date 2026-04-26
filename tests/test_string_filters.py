"""Tests for deterministic entity/glossary string filtering."""

import pytest

from src.nwn_translator.context.string_filters import (
    classify_entity_candidate,
    classify_string,
    is_generic_entity_label,
    is_valid_entity_name,
    should_skip_entity_source_text,
)


@pytest.mark.parametrize(
    "name",
    [
        "Almraiven : A Trap to Spring",
        "Forest of Mir : The Spirit of Volothamp",
        "Bookworm : The Cloak of Almraiven",
        "Almraiven : Bumps in the Night",
    ],
)
def test_quest_hierarchy_label_dropped(name):
    result = classify_entity_candidate(name, "quest")
    assert result.decision == "drop"
    assert result.reason == "quest_hierarchy_label"


@pytest.mark.parametrize(
    "name",
    [
        "Bejala",
        "Gewia the Wererat",
        "Tower of the Evelyn Society of Thinkers",
        "Almraiven",
    ],
)
def test_real_proper_names_not_dropped_as_quest_hierarchy(name):
    result = classify_entity_candidate(name, "location")
    assert result.decision != "drop" or result.reason != "quest_hierarchy_label"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Human Female", True),
        ("Human Male", True),
        ("Almraiven Resident", True),
        ("Auren Shopper", True),
        ("Halfling Female", True),
        ("Tiefling Female", True),
        ("Half-Elf Female", True),
        ("Half-Orc Male", True),
        ("Dwarven Female", True),
        ("Elven Male", True),
        ("Gnome Female", True),
        ("Ogre Male", True),
        ("Human Boy", True),
        ("Human Girl", True),
        ("[KC] Human Male", True),
        ("[FOM] Halfling Female", True),
        ("Gewia the Wererat", False),
        ("Brynlo", False),
        ("Diving Dolphin", False),
    ],
)
def test_is_generic_entity_label(name, expected):
    assert is_generic_entity_label(name) is expected


@pytest.mark.parametrize(
    "text,reason",
    [
        ("<FirstName>", "placeholder"),
        ("<FullName>", "placeholder"),
        ("<race>", "placeholder"),
        ("<man/woman>", "placeholder"),
        ("AD&D", "system_term"),
        ("D&D", "system_term"),
        ("DMFI", "system_term"),
        ("NWN", "system_term"),
        ("Bioware", "system_term"),
        ("ARCH_TARGET", "system_term"),
        ("Court of the Count *", "wildcard_or_format_artifact"),
        ("WILL_O_WISP", "code_like_identifier"),
        ("BakersPlea", "code_like_identifier"),
        ("CloudkillTarget", "code_like_identifier"),
        ("CastleExt1To2South", "code_like_identifier"),
        ("WWBite1d6", "code_like_identifier"),
        ("WWBiteWolfForm", "code_like_identifier"),
    ],
)
def test_ravenloft_negative_examples_blocked(text, reason):
    cls = classify_string(text)
    assert cls.blocked
    assert reason in cls.reasons
    assert not is_valid_entity_name(text, "location")


@pytest.mark.parametrize(
    "text",
    [
        "Stout Village",
        "Guild of Middlemen",
        "Madam Eva",
        "Barovia",
        "Dragon Bones",
    ],
)
def test_positive_examples_valid(text):
    cls = classify_string(text)
    assert not cls.blocked
    assert cls.natural_language
    assert is_valid_entity_name(text, "location")


def test_unknown_requires_natural_multiword_name():
    assert is_valid_entity_name("Madam Eva", "unknown")
    assert not is_valid_entity_name("Barovia", "unknown")
    assert not is_valid_entity_name("BakersPlea", "unknown")


def test_git_technical_code_like_source_text_skipped():
    assert should_skip_entity_source_text(
        "CastleExt1To2South",
        {"type": "trigger_name"},
    )


@pytest.mark.parametrize(
    "text,decision",
    [
        ("Rat 1", "drop"),
        ("Food 5", "drop"),
        ("Candle 003", "drop"),
        ("Human Female", "deprioritize"),
        ("Almraiven Resident", "deprioritize"),
    ],
)
def test_candidate_prefilter_rejects_or_deprioritizes_generic_labels(text, decision):
    assert classify_entity_candidate(text).decision == decision


@pytest.mark.parametrize(
    "text",
    ["Brynlo", "Gewia", "Diving Dolphin", "The North Wall", "Mount Talath"],
)
def test_candidate_prefilter_keeps_specific_names(text):
    assert classify_entity_candidate(text, "character").decision == "keep"
    assert not should_skip_entity_source_text(
        "To the Sewers",
        {"type": "trigger_name"},
    )
