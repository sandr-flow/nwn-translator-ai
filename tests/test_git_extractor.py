"""Tests for GitExtractor (.git area instance files)."""

from pathlib import Path

from src.nwn_translator.extractors.git_extractor import GitExtractor


def test_git_extractor_collects_creature_equip_and_store_items():
    extractor = GitExtractor()
    path = Path("testarea.git")
    gff = {
        "Creature List": [
            {
                "FirstName": {"StrRef": -1, "Value": "Grandma"},
                "LastName": {"StrRef": -1, "Value": ""},
                "Description": {"StrRef": -1, "Value": ""},
                "Equip_ItemList": [
                    {
                        "LocalizedName": {"StrRef": -1, "Value": "Family Axe"},
                        "Description": {"StrRef": -1, "Value": "Heavy."},
                        "DescIdentified": {"StrRef": -1, "Value": ""},
                    }
                ],
            }
        ],
        "StoreList": [
            {
                "LocName": {"StrRef": -1, "Value": "Bazaar"},
                "Description": {"StrRef": -1, "Value": ""},
                "ItemList": [
                    {
                        "LocalizedName": {"StrRef": -1, "Value": "Rope"},
                        "Description": {"StrRef": -1, "Value": ""},
                        "DescIdentified": {"StrRef": -1, "Value": ""},
                    }
                ],
            }
        ],
    }
    result = extractor.extract(path, gff)
    assert result.content_type == "git_instance"
    texts = {item.text for item in result.items}
    assert "Grandma" in texts
    assert "Family Axe" in texts
    assert "Heavy." in texts
    assert "Bazaar" in texts
    assert "Rope" in texts

    types = {(item.text, item.metadata.get("type")) for item in result.items}
    assert ("Grandma", "creature_first_name") in types
    assert ("Family Axe", "item_name") in types
    assert ("Bazaar", "store_name") in types
    assert ("Rope", "item_name") in types


def test_git_extractor_nested_store_shelves():
    """Nested StoreList -> ItemList (same layout as Penultima coffee merchant)."""
    extractor = GitExtractor()
    path = Path("area.git")
    gff = {
        "StoreList": [
            {
                "LocName": {"StrRef": -1, "Value": "Bar"},
                "StoreList": [
                    {},
                    {},
                    {
                        "ItemList": [
                            {
                                "LocalizedName": {
                                    "StrRef": -1,
                                    "Value": "Coffee",
                                },
                            },
                        ],
                    },
                ],
            }
        ],
    }
    result = extractor.extract(path, gff)
    texts = {item.text for item in result.items}
    assert "Bar" in texts
    assert "Coffee" in texts


def test_git_extractor_collects_trigger_list_map_note_names():
    """Real .git files use the GFF key ``TriggerList`` (not ``Trigger List``)."""
    extractor = GitExtractor()
    path = Path("town.git")
    gff = {
        "TriggerList": [
            {
                "TrapFlag": 1,
                "LocalizedName": {"StrRef": -1, "Value": "Market Square"},
                "Description": {"StrRef": -1, "Value": ""},
            }
        ],
    }
    result = extractor.extract(path, gff)
    texts = {item.text for item in result.items}
    assert "Market Square" in texts


def test_git_extractor_collects_waypoint_map_note_labels():
    extractor = GitExtractor()
    path = Path("city.git")
    gff = {
        "WaypointList": [
            {
                "LocalizedName": {"StrRef": -1, "Value": "WP_CityGate"},
                "MapNote": {"StrRef": -1, "Value": "City Gate"},
            }
        ]
    }
    result = extractor.extract(path, gff)
    by_text = {item.text: item.metadata.get("type") for item in result.items}
    assert by_text.get("City Gate") == "waypoint_map_note"
    assert "WP_CityGate" not in by_text


def test_git_extractor_skips_internal_tags():
    extractor = GitExtractor()
    path = Path("way.git")
    gff = {
        "WaypointList": [
            {
                "LocalizedName": {"StrRef": -1, "Value": "WP_Spawn"},
                "Description": {"StrRef": -1, "Value": ""},
            }
        ]
    }
    result = extractor.extract(path, gff)
    texts = {item.text for item in result.items}
    assert "WP_Spawn" not in texts


def test_git_extractor_translates_non_trap_triggers():
    """Non-trap triggers carry player-visible text: area-transition tooltips and
    generic-trigger names used by scripts for SpeakString / FloatingText."""
    extractor = GitExtractor()
    path = Path("area.git")
    gff = {
        "TriggerList": [
            {
                "Tag": "at_CastleToSewers",
                "Type": 1,
                "TrapFlag": 0,
                "LocalizedName": {"StrRef": -1, "Value": "To the Sewers"},
                "Description": {"StrRef": -1, "Value": ""},
            },
            {
                "Tag": "Telios",
                "Type": 0,
                "TrapFlag": 0,
                "LocalizedName": {
                    "StrRef": -1,
                    "Value": '"My lovely boots are getting mud on them!"',
                },
                "Description": {"StrRef": -1, "Value": ""},
            },
            {
                "Tag": "tr_vico",
                "Type": 0,
                "TrapFlag": 0,
                "LocalizedName": {"StrRef": -1, "Value": "tr_vico"},
                "Description": {"StrRef": -1, "Value": ""},
            },
        ]
    }
    result = extractor.extract(path, gff)
    texts = {item.text for item in result.items}
    assert "To the Sewers" in texts
    assert '"My lovely boots are getting mud on them!"' in texts
    assert "tr_vico" not in texts


def test_git_injector_collects_non_trap_trigger_strings():
    """Injector string collector must mirror the extractor for non-trap triggers."""
    from src.nwn_translator.injectors.git_injector import (
        collect_git_strings_missing_from_translations,
    )

    gff = {
        "TriggerList": [
            {
                "Tag": "Comment",
                "Type": 0,
                "TrapFlag": 0,
                "LocalizedName": {
                    "StrRef": -1,
                    "Value": "[Strange. There was something that looked like an eye reflected in the water.]",
                },
                "Description": {"StrRef": -1, "Value": ""},
            }
        ]
    }
    found = collect_git_strings_missing_from_translations(gff, {})
    assert any("eye reflected" in s for s in found)


def test_git_extractor_collects_area_floor_items():
    """Top-level ``List`` in .git holds items dropped on the ground in the toolset."""
    extractor = GitExtractor()
    path = Path("forestcave.git")
    gff = {
        "List": [
            {
                "TemplateResRef": "dragonbones",
                "BaseItem": 79,
                "LocalizedName": {"StrRef": -1, "Value": "Dragon Bones"},
                "Description": {
                    "StrRef": -1,
                    "Value": "Yellowed with age, these are the bones of a Dragon.",
                },
                "DescIdentified": {"StrRef": -1, "Value": ""},
            },
            {
                "TemplateResRef": "nw_it_thnmisc001",
                "BaseItem": 24,
                "LocalizedName": {"StrRef": -1, "Value": ""},
                "Description": {"StrRef": -1, "Value": ""},
                "DescIdentified": {"StrRef": -1, "Value": ""},
            },
        ]
    }
    result = extractor.extract(path, gff)
    by_text = {item.text: item.metadata.get("type") for item in result.items}
    assert by_text.get("Dragon Bones") == "item_name"
    assert any(
        item.text.startswith("Yellowed with age")
        and item.metadata.get("type") == "item_description"
        for item in result.items
    )
    # Empty values on the engine-default entry must not emit TranslatableItems.
    assert "" not in by_text


def test_git_injector_collects_area_floor_item_strings():
    """Injector string collector must mirror the extractor for area floor items."""
    from src.nwn_translator.injectors.git_injector import (
        collect_git_strings_missing_from_translations,
    )

    gff = {
        "List": [
            {
                "LocalizedName": {"StrRef": -1, "Value": "Silver Nuggets"},
                "Description": {"StrRef": -1, "Value": "Raw silver ore."},
                "DescIdentified": {"StrRef": -1, "Value": ""},
            }
        ]
    }
    found = collect_git_strings_missing_from_translations(gff, {})
    assert "Silver Nuggets" in found
    assert "Raw silver ore." in found


def test_git_extractor_collects_encounter_instance_names():
    """Encounter instances in .git expose LocalizedName retrievable by scripts."""
    extractor = GitExtractor()
    path = Path("area.git")
    gff = {
        "Encounter List": [
            {
                "LocalizedName": {"StrRef": -1, "Value": "Human, Bandit Group"},
            },
            {
                "LocalizedName": {"StrRef": -1, "Value": "enc_internal_tag"},
            },
        ]
    }
    result = extractor.extract(path, gff)
    texts = {item.text: item.metadata.get("type") for item in result.items}
    assert texts.get("Human, Bandit Group") == "encounter_name"
    assert "enc_internal_tag" not in texts
