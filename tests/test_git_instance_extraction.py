"""Tests for .git instance string extraction and ItemList patching."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.nwn_translator.extractors.git_extractor import GitExtractor
from src.nwn_translator.extractors.git_fields import INSTANCE_LISTS
from src.nwn_translator.injectors.gff_injector import inject_gff


def _extracted_texts(gff):
    return {item.text for item in GitExtractor().extract(Path("area.git"), gff).items}


class TestExtractGitStrings:
    def test_extracts_placeable_loc_name(self):
        # A player-facing name: the .git filter rejects code-like CamelCase
        # labels such as "OnlyInGit" as resref-looking.
        gff = {
            "Placeable List": [
                {
                    "LocName": {"StrRef": -1, "Value": "Old Wooden Chest"},
                    "Description": {"StrRef": -1, "Value": ""},
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Old Wooden Chest" in found

    def test_extracts_nested_item_list_strings(self):
        gff = {
            "Placeable List": [
                {
                    "LocName": {"StrRef": -1, "Value": "Chest"},
                    "ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Scroll Case",
                            },
                            "Description": {
                                "StrRef": -1,
                                "Value": "Holds scrolls.",
                            },
                        }
                    ],
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Chest" in found
        assert "Scroll Case" in found
        assert "Holds scrolls." in found

    def test_extracts_equip_item_list_strings(self):
        gff = {
            "Creature List": [
                {
                    "FirstName": {"StrRef": -1, "Value": "Grandma"},
                    "LastName": {"StrRef": -1, "Value": ""},
                    "Equip_ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Grandma's Armor",
                            },
                            "Description": {
                                "StrRef": -1,
                                "Value": "Worn by Grandma.",
                            },
                            "DescIdentified": {
                                "StrRef": -1,
                                "Value": "Sturdy family armor.",
                            },
                        },
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "The Skullsplitter",
                            },
                            "Description": {
                                "StrRef": -1,
                                "Value": "A fearsome axe.",
                            },
                            "DescIdentified": {
                                "StrRef": -1,
                                "Value": "Grandma's axe.",
                            },
                        },
                    ],
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Grandma" in found
        assert "Grandma's Armor" in found
        assert "Worn by Grandma." in found
        assert "Sturdy family armor." in found
        assert "The Skullsplitter" in found
        assert "A fearsome axe." in found
        assert "Grandma's axe." in found

    def test_extracts_nested_store_list_itemlist_strings(self):
        """Merchant shelves: StoreList instance contains nested StoreList with ItemList."""
        gff = {
            "StoreList": [
                {
                    "LocName": {"StrRef": -1, "Value": "Tavern"},
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
                                {
                                    "LocalizedName": {
                                        "StrRef": -1,
                                        "Value": "Cappuchino",
                                    },
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Tavern" in found
        assert "Coffee" in found
        assert "Cappuchino" in found

    def test_extracts_store_list_loc_name(self):
        gff = {
            "StoreList": [
                {
                    "LocName": {"StrRef": -1, "Value": "Coffee Merchant"},
                    "Description": {"StrRef": -1, "Value": ""},
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Coffee Merchant" in found

    def test_extracts_waypoint_map_note_labels(self):
        gff = {
            "WaypointList": [
                {
                    "LocalizedName": {"StrRef": -1, "Value": "WP_CityGate"},
                    "MapNote": {"StrRef": -1, "Value": "City Gate"},
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "City Gate" in found
        assert "WP_CityGate" not in found

    def test_extracts_store_list_nested_item_list_strings(self):
        gff = {
            "StoreList": [
                {
                    "LocalizedName": {"StrRef": -1, "Value": "Arms Dealer"},
                    "Description": {"StrRef": -1, "Value": ""},
                    "ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Iron Longsword",
                            },
                            "Description": {
                                "StrRef": -1,
                                "Value": "A sturdy blade.",
                            },
                        }
                    ],
                }
            ]
        }
        found = _extracted_texts(gff)
        assert "Arms Dealer" in found
        assert "Iron Longsword" in found
        assert "A sturdy blade." in found


def _inject_fixture(path, data, answers):
    content = GitExtractor().extract(path, data)
    translations = {item.key: answers[item.text] for item in content.items if item.text in answers}
    return inject_gff(
        path,
        content.items,
        translations,
        content_type=content.content_type,
        text_encoding="cp1251",
    ).items_updated


class TestPatchGitInventory:
    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_waypoint_map_note_labels(self, mock_patcher_cls):
        data = {
            "WaypointList": [
                {
                    "LocalizedName": {"StrRef": -1, "Value": "WP_CityGate"},
                    "MapNote": {"StrRef": -1, "Value": "City Gate"},
                    "_record_offsets": {"LocalizedName": 0, "MapNote": 222},
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher
        path = Path(__file__).parent / "_fake_waypoint.git"
        translations = {"City Gate": "Городские ворота"}
        count = _inject_fixture(path, data, translations)
        assert count == 1
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(222, "Городские ворота")}

    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_item_list_fields(self, mock_patcher_cls):
        data = {
            "Placeable List": [
                {
                    "LocName": {"StrRef": -1, "Value": "Chest"},
                    "_record_offsets": {"LocName": 100, "Description": 0},
                    "ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Scroll Case",
                            },
                            "_record_offsets": {"LocalizedName": 200},
                        }
                    ],
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher

        path = Path(__file__).parent / "_fake.git"
        translations = {"Chest": "Сундук", "Scroll Case": "Футляр"}
        count = _inject_fixture(path, data, translations)

        assert count == 2
        patcher.patch_multiple.assert_called_once()
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(100, "Сундук"), (200, "Футляр")}

    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_store_list_item_list_fields(self, mock_patcher_cls):
        data = {
            "StoreList": [
                {
                    "LocalizedName": {"StrRef": -1, "Value": "Arms Dealer"},
                    "_record_offsets": {"LocalizedName": 300, "Description": 0},
                    "ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Iron Longsword",
                            },
                            "_record_offsets": {"LocalizedName": 400},
                        }
                    ],
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher

        path = Path(__file__).parent / "_fake_store.git"
        translations = {
            "Arms Dealer": "Оружейник",
            "Iron Longsword": "Железный длинный меч",
        }
        count = _inject_fixture(path, data, translations)

        assert count == 2
        patcher.patch_multiple.assert_called_once()
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(300, "Оружейник"), (400, "Железный длинный меч")}

    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_store_list_loc_name_fields(self, mock_patcher_cls):
        data = {
            "StoreList": [
                {
                    "LocName": {"StrRef": -1, "Value": "Coffee Merchant"},
                    "_record_offsets": {"LocName": 310, "LocalizedName": 0, "Description": 0},
                    "ItemList": [],
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher

        path = Path(__file__).parent / "_fake_store_locname.git"
        translations = {"Coffee Merchant": "Кофейня"}
        count = _inject_fixture(path, data, translations)
        assert count == 1
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(310, "Кофейня")}

    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_nested_store_list_itemlist(self, mock_patcher_cls):
        data = {
            "StoreList": [
                {
                    "LocName": {"StrRef": -1, "Value": "Bar"},
                    "_record_offsets": {"LocName": 50, "LocalizedName": 0, "Description": 0},
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
                                    "_record_offsets": {"LocalizedName": 900},
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher
        path = Path(__file__).parent / "_fake_nested_store.git"
        translations = {"Bar": "Бар", "Coffee": "Кофе"}
        count = _inject_fixture(path, data, translations)
        assert count == 2
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(50, "Бар"), (900, "Кофе")}

    @patch("src.nwn_translator.injectors.gff_injector.GFFPatcher")
    def test_patches_equip_item_list_fields(self, mock_patcher_cls):
        data = {
            "Creature List": [
                {
                    "FirstName": {"StrRef": -1, "Value": "Grandma"},
                    "_record_offsets": {"FirstName": 100},
                    "Equip_ItemList": [
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "Grandma's Armor",
                            },
                            "Description": {
                                "StrRef": -1,
                                "Value": "Worn by Grandma.",
                            },
                            "_record_offsets": {
                                "LocalizedName": 500,
                                "Description": 600,
                            },
                        },
                        {
                            "LocalizedName": {
                                "StrRef": -1,
                                "Value": "The Skullsplitter",
                            },
                            "_record_offsets": {"LocalizedName": 700},
                        },
                    ],
                }
            ]
        }
        patcher = MagicMock()
        mock_patcher_cls.return_value = patcher

        path = Path(__file__).parent / "_fake_equip.git"
        translations = {
            "Grandma": "Бабушка",
            "Grandma's Armor": "Бабушкина броня",
            "Worn by Grandma.": "Носит бабушка.",
            "The Skullsplitter": "Раскалыватель черепов",
        }
        count = _inject_fixture(path, data, translations)

        assert count == 4
        patcher.patch_multiple.assert_called_once()
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {
            (100, "Бабушка"),
            (500, "Бабушкина броня"),
            (600, "Носит бабушка."),
            (700, "Раскалыватель черепов"),
        }

    def test_instance_lists_include_description_fields(self):
        assert "Description" in INSTANCE_LISTS["Placeable List"]
        assert "Description" in INSTANCE_LISTS["Door List"]
        assert "Description" in INSTANCE_LISTS["StoreList"]
        assert "LocName" in INSTANCE_LISTS["StoreList"]
        assert "LocalizedName" in INSTANCE_LISTS["StoreList"]
