"""Tests for deterministic entity/glossary string filtering."""

import pytest

from src.nwn_translator.context.string_filters import (
    classify_string,
    is_valid_entity_name,
    should_skip_entity_source_text,
)


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
    assert not should_skip_entity_source_text(
        "To the Sewers",
        {"type": "trigger_name"},
    )
