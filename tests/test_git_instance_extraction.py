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

    def test_instance_lists_include_description_fields(self):
        assert "Description" in INSTANCE_LISTS["Placeable List"]
        assert "Description" in INSTANCE_LISTS["Door List"]
        assert "Description" in INSTANCE_LISTS["StoreList"]
