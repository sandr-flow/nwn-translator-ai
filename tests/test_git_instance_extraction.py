"""Tests for .git instance string collection and ItemList patching."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.nwn_translator.injectors.git_injector import (
    INSTANCE_LISTS,
    collect_git_strings_missing_from_translations,
    patch_git_file,
)


class TestCollectGitStrings:
    def test_collects_placeable_loc_name_not_in_templates(self):
        gff = {
            "Placeable List": [
                {
                    "LocName": {"StrRef": -1, "Value": "OnlyInGit"},
                    "Description": {"StrRef": -1, "Value": ""},
                }
            ]
        }
        existing = {"TemplateName": "T"}
        found = collect_git_strings_missing_from_translations(gff, existing)
        assert "OnlyInGit" in found
        assert "TemplateName" not in found

    def test_skips_strings_already_in_translation_map(self):
        gff = {
            "Creature List": [
                {
                    "FirstName": {"StrRef": -1, "Value": "Bob"},
                    "LastName": {"StrRef": -1, "Value": "Smith"},
                }
            ]
        }
        existing = {"Bob": "Боб", "Smith": "Смит"}
        found = collect_git_strings_missing_from_translations(gff, existing)
        assert not found

    def test_collects_nested_item_list_strings(self):
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
        found = collect_git_strings_missing_from_translations(gff, {})
        assert "Chest" in found
        assert "Scroll Case" in found
        assert "Holds scrolls." in found

    def test_collects_equip_item_list_strings(self):
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
        found = collect_git_strings_missing_from_translations(gff, {})
        assert "Grandma" in found
        assert "Grandma's Armor" in found
        assert "Worn by Grandma." in found
        assert "Sturdy family armor." in found
        assert "The Skullsplitter" in found
        assert "A fearsome axe." in found
        assert "Grandma's axe." in found

    def test_collects_nested_store_list_itemlist_strings(self):
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
        found = collect_git_strings_missing_from_translations(gff, {})
        assert "Tavern" in found
        assert "Coffee" in found
        assert "Cappuchino" in found

    def test_collects_store_list_loc_name(self):
        gff = {
            "StoreList": [
                {
                    "LocName": {"StrRef": -1, "Value": "Coffee Merchant"},
                    "Description": {"StrRef": -1, "Value": ""},
                }
            ]
        }
        found = collect_git_strings_missing_from_translations(gff, {})
        assert "Coffee Merchant" in found

    def test_collects_store_list_nested_item_list_strings(self):
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
        found = collect_git_strings_missing_from_translations(gff, {})
        assert "Arms Dealer" in found
        assert "Iron Longsword" in found
        assert "A sturdy blade." in found


class TestPatchGitInventory:
    @patch("src.nwn_translator.injectors.git_injector.GFFPatcher")
    @patch("src.nwn_translator.injectors.git_injector.read_gff")
    def test_patches_item_list_fields(self, mock_read_gff, mock_patcher_cls):
        mock_read_gff.return_value = {
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
        count = patch_git_file(path, translations, tlk=None)

        assert count == 2
        patcher.patch_multiple.assert_called_once()
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(100, "Сундук"), (200, "Футляр")}

    @patch("src.nwn_translator.injectors.git_injector.GFFPatcher")
    @patch("src.nwn_translator.injectors.git_injector.read_gff")
    def test_patches_store_list_item_list_fields(self, mock_read_gff, mock_patcher_cls):
        mock_read_gff.return_value = {
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
        count = patch_git_file(path, translations, tlk=None)

        assert count == 2
        patcher.patch_multiple.assert_called_once()
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(300, "Оружейник"), (400, "Железный длинный меч")}

    @patch("src.nwn_translator.injectors.git_injector.GFFPatcher")
    @patch("src.nwn_translator.injectors.git_injector.read_gff")
    def test_patches_store_list_loc_name_fields(self, mock_read_gff, mock_patcher_cls):
        mock_read_gff.return_value = {
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
        count = patch_git_file(path, translations, tlk=None)
        assert count == 1
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(310, "Кофейня")}

    @patch("src.nwn_translator.injectors.git_injector.GFFPatcher")
    @patch("src.nwn_translator.injectors.git_injector.read_gff")
    def test_patches_nested_store_list_itemlist(self, mock_read_gff, mock_patcher_cls):
        mock_read_gff.return_value = {
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
        count = patch_git_file(path, translations, tlk=None)
        assert count == 2
        plist = patcher.patch_multiple.call_args[0][0]
        assert set(plist) == {(50, "Бар"), (900, "Кофе")}

    @patch("src.nwn_translator.injectors.git_injector.GFFPatcher")
    @patch("src.nwn_translator.injectors.git_injector.read_gff")
    def test_patches_equip_item_list_fields(self, mock_read_gff, mock_patcher_cls):
        mock_read_gff.return_value = {
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
        count = patch_git_file(path, translations, tlk=None)

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
