"""Tests for deterministic entity/glossary string filtering."""

import pytest

from src.nwn_translator.context.string_filters import (
    ENGINE_PLACEHOLDER_TAGS,
    ENGINE_TAG_PREFIXES,
    classify_entity_candidate,
    classify_string,
    is_generic_entity_label,
    is_valid_entity_name,
    should_skip_entity_source_text,
)
from src.nwn_translator.extractors import ncs_extractor
from src.nwn_translator.injectors import git_injector


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


# --------------------------------------------------------------------------
# Engine tag prefixes: one shared source of truth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "arch_target",
        "ARCH_TARGET",
        "nw_c2_default9",
        "NW_Thing",
        "wp_spawn_01",
        "WP_MerudocRuns_01",
        "dst_tunnel",
        "DST_Tunnel",
        "post_guard",
        "POST_Guard",
    ],
)
def test_engine_tag_prefixes_blocked(text):
    cls = classify_string(text)
    assert cls.blocked
    assert "system_term" in cls.reasons
    assert should_skip_entity_source_text(text)
    assert classify_entity_candidate(text).decision == "drop"


@pytest.mark.parametrize(
    "text",
    [
        "Archery Range",
        "Northwest Gate",
        "Post Road Inn",
        "Destined Hall",
        "Weapon Rack",
    ],
)
def test_prose_resembling_engine_prefixes_kept(text):
    assert not classify_string(text).blocked
    assert not should_skip_entity_source_text(text)


@pytest.mark.parametrize("text", ["YOURTAGHERE", "yourtaghere", "Yourtaghere", "YourTagHere"])
def test_toolset_placeholder_tag_blocked_in_any_case(text):
    """Bioware template placeholders leak through the toolset in mixed case."""
    assert classify_string(text).blocked
    assert should_skip_entity_source_text(text)


def test_engine_prefixes_are_not_duplicated_per_module():
    """The NCS extractor must reuse the shared list, not keep a parallel copy."""
    assert set(ENGINE_TAG_PREFIXES) <= set(ncs_extractor._SKIP_PREFIXES)
    assert ENGINE_PLACEHOLDER_TAGS == {"yourtaghere"}
    assert not hasattr(git_injector, "is_internal_tag")


@pytest.mark.parametrize(
    "text",
    ["WP_DPR1_CloseDoor", "WP_CoN_Parishoner", "WP_MerudocRuns_01", "WP_DP1_GateFXCenter"],
)
def test_ncs_rejects_waypoint_tags_at_extraction(text):
    """Corpus waypoint tags used to survive extraction and reach the LLM gate."""
    assert ncs_extractor._is_definitely_not_translatable(text)


@pytest.mark.parametrize("text", ["Help me, please!", "I will not go back there."])
def test_ncs_keeps_player_barks(text):
    assert not ncs_extractor._is_definitely_not_translatable(text)


# --------------------------------------------------------------------------
# Emote markup vs wildcard artifacts (.git emotion triggers)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "*The lever is stuck*",
        "*gasp*",
        "*whispers* There is such rage among these ruins...",
        "*Hassir is silent as he looks in awe upon the great hall before you*",
        "SAY MY NAME, BITCH! *WHIPCRACK*",
    ],
)
def test_emote_markup_translates(text):
    cls = classify_string(text)
    assert cls.emote_markup
    assert not should_skip_entity_source_text(text)
    assert not should_skip_entity_source_text(text, {"type": "trigger_name"})


@pytest.mark.parametrize(
    "text",
    [
        "Court of the Count *",  # unpaired trailing wildcard
        "// * * * SCENE: Drinking dwarves  * * *",  # scripter comment
        "\n// * * * SCENE: Rigrin and Sagrirry talk  * * *",
        "{0} gold",  # format placeholder
        "*gasp* {0}",  # emote mixed with a format artifact
        "***",  # letterless decoration
        "*waves at <FirstName>*",  # mixed inline placeholder stays conservative
    ],
)
def test_wildcard_artifacts_still_skipped(text):
    assert should_skip_entity_source_text(text)


def test_emote_markup_never_becomes_entity_name():
    """The glossary gates stay strict even though translation is allowed."""
    for text in ("*gasp*", "*The lever is stuck*"):
        assert not is_valid_entity_name(text, "location")
        assert classify_entity_candidate(text).decision == "drop"
