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
