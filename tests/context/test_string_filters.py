"""Deterministic filtering of entity candidates and of texts to translate."""

import pytest

from nwn_translator.context.string_filters import (
    ENGINE_PLACEHOLDER_TAGS,
    ENGINE_TAG_PREFIXES,
    classify_entity_candidate,
    classify_string,
    is_generic_entity_label,
    is_valid_entity_name,
    should_skip_entity_source_text,
)
from nwn_translator.extractors import git_fields, ncs_extractor

CAMEL_NAMES = ["McGee", "DeVir", "MacReady", "VanCleef"]


@pytest.mark.parametrize(
    "name",
    [
        "Almraiven : A Trap to Spring",
        "Forest of Mir : The Spirit of Volothamp",
        "Bookworm : The Cloak of Almraiven",
        "Almraiven : Bumps in the Night",
    ],
)
def test_quest_hierarchy_labels_are_dropped(name):
    result = classify_entity_candidate(name, "quest")
    assert (result.decision, result.reason) == ("drop", "quest_hierarchy_label")


@pytest.mark.parametrize(
    "name", ["Bejala", "Gewia the Wererat", "Tower of the Evelyn Society of Thinkers", "Almraiven"]
)
def test_real_proper_names_are_not_quest_hierarchy_labels(name):
    result = classify_entity_candidate(name, "location")
    assert result.decision != "drop" or result.reason != "quest_hierarchy_label"


@pytest.mark.parametrize(
    "name, generic",
    [
        *(
            (label, True)
            for label in (
                "Human Female",
                "Human Male",
                "Almraiven Resident",
                "Auren Shopper",
                "Halfling Female",
                "Tiefling Female",
                "Half-Elf Female",
                "Half-Orc Male",
                "Dwarven Female",
                "Elven Male",
                "Gnome Female",
                "Ogre Male",
                "Human Boy",
                "Human Girl",
                "[KC] Human Male",
                "[FOM] Halfling Female",
            )
        ),
        ("Gewia the Wererat", False),
        ("Brynlo", False),
        ("Diving Dolphin", False),
    ],
)
def test_generic_entity_labels(name, generic):
    assert is_generic_entity_label(name) is generic


@pytest.mark.parametrize(
    "text, reason",
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
def test_ravenloft_negative_examples_are_blocked(text, reason):
    classified = classify_string(text)
    assert classified.blocked and reason in classified.reasons
    assert not is_valid_entity_name(text, "location")


@pytest.mark.parametrize(
    "text", ["Stout Village", "Guild of Middlemen", "Madam Eva", "Barovia", "Dragon Bones"]
)
def test_natural_names_are_valid(text):
    classified = classify_string(text)
    assert not classified.blocked and classified.natural_language
    assert is_valid_entity_name(text, "location")


def test_unknown_category_requires_a_natural_multiword_name():
    assert is_valid_entity_name("Madam Eva", "unknown")
    assert not is_valid_entity_name("Barovia", "unknown")
    assert not is_valid_entity_name("BakersPlea", "unknown")


@pytest.mark.parametrize(
    "text, decision",
    [
        ("Rat 1", "drop"),
        ("Food 5", "drop"),
        ("Candle 003", "drop"),
        ("Human Female", "deprioritize"),
        ("Almraiven Resident", "deprioritize"),
    ],
)
def test_candidate_prefilter_drops_or_deprioritizes_generic_labels(text, decision):
    assert classify_entity_candidate(text).decision == decision


@pytest.mark.parametrize(
    "text", ["Brynlo", "Gewia", "Diving Dolphin", "The North Wall", "Mount Talath"]
)
def test_candidate_prefilter_keeps_specific_names(text):
    assert classify_entity_candidate(text, "character").decision == "keep"


def test_git_trigger_names_are_skipped_only_when_code_like():
    assert should_skip_entity_source_text("CastleExt1To2South", {"type": "trigger_name"})
    assert not should_skip_entity_source_text("To the Sewers", {"type": "trigger_name"})


# ---------------------------------------------------------------------------
# Engine tag prefixes: one shared source of truth
# ---------------------------------------------------------------------------


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
def test_engine_tag_prefixes_are_blocked(text):
    classified = classify_string(text)
    assert classified.blocked and "system_term" in classified.reasons
    assert should_skip_entity_source_text(text)
    assert classify_entity_candidate(text).decision == "drop"


@pytest.mark.parametrize(
    "text", ["Archery Range", "Northwest Gate", "Post Road Inn", "Destined Hall", "Weapon Rack"]
)
def test_prose_resembling_engine_prefixes_is_kept(text):
    assert not classify_string(text).blocked
    assert not should_skip_entity_source_text(text)


@pytest.mark.parametrize("text", ["YOURTAGHERE", "yourtaghere", "Yourtaghere", "YourTagHere"])
def test_toolset_placeholder_tag_is_blocked_in_any_case(text):
    """Bioware template placeholders leak through the toolset in mixed case."""
    assert classify_string(text).blocked
    assert should_skip_entity_source_text(text)


def test_engine_prefixes_are_not_duplicated_per_module():
    """The NCS extractor reuses the shared list instead of keeping a parallel copy."""
    assert set(ENGINE_TAG_PREFIXES) <= set(ncs_extractor._SKIP_PREFIXES)
    assert ENGINE_PLACEHOLDER_TAGS == {"yourtaghere"}
    assert not hasattr(git_fields, "is_internal_tag")


# ---------------------------------------------------------------------------
# Emote markup versus wildcard artifacts (.git emotion triggers)
# ---------------------------------------------------------------------------


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
def test_emote_markup_is_translated(text):
    assert classify_string(text).emote_markup
    assert not should_skip_entity_source_text(text)
    assert not should_skip_entity_source_text(text, {"type": "trigger_name"})


@pytest.mark.parametrize(
    "text",
    [
        "Court of the Count *",  # an unpaired trailing wildcard
        "// * * * SCENE: Drinking dwarves  * * *",  # a scripter comment
        "\n// * * * SCENE: Rigrin and Sagrirry talk  * * *",
        "{0} gold",  # a format placeholder
        "*gasp* {0}",  # an emote mixed with a format artifact
        "***",  # letterless decoration
        "*waves at <FirstName>*",  # a mixed inline placeholder stays conservative
    ],
)
def test_wildcard_artifacts_are_skipped(text):
    assert should_skip_entity_source_text(text)


@pytest.mark.parametrize("text", ["*gasp*", "*The lever is stuck*", "McGee"])
def test_translatable_markup_and_rescued_names_never_become_entity_names(text):
    """The glossary gates stay strict even where translation is allowed."""
    assert not is_valid_entity_name(text, "location")
    assert not is_valid_entity_name(text)
    assert classify_entity_candidate(text).decision == "drop"


# ---------------------------------------------------------------------------
# The blueprint-name oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CAMEL_NAMES)
def test_blueprint_oracle_rescues_camel_case_names(name):
    oracle = frozenset(n.casefold() for n in CAMEL_NAMES)
    assert should_skip_entity_source_text(name, {"type": "creature_first_name"})
    assert not should_skip_entity_source_text(name, {"type": "creature_first_name"}, oracle)
    assert not should_skip_entity_source_text(name, {"type": "placeable_name"}, oracle)


@pytest.mark.parametrize("name", ["WP_DoorTrigger", "NW_SomeTag", "YOURTAGHERE"])
def test_oracle_never_rescues_engine_tags(name):
    """system_term is its own blocking reason; a sloppy blueprint name cannot lift it."""
    oracle = frozenset({name.casefold()})
    assert should_skip_entity_source_text(name, {"type": "creature_first_name"}, oracle)


@pytest.mark.parametrize("meta_type", ["trigger_name", "item_name", "waypoint_map_note"])
def test_oracle_never_rescues_technical_git_types(meta_type):
    assert should_skip_entity_source_text("McGee", {"type": meta_type}, frozenset({"mcgee"}))
