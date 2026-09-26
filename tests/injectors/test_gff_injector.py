"""Injecting translations into GFF resources through their extracted field records."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.extractors.git_extractor import GitExtractor
from nwn_translator.formats.gff import read_gff
from nwn_translator.injectors.gff_injector import inject_gff
from nwn_translator.main import rebuild_module
from nwn_translator.pipeline.stages import inject_translations_into_file, load_parsed_and_extracted
from tests.support.gff_writer import write_gff


def _loc(value: str) -> dict:
    return {"StrRef": -1, "Value": value}


def _inject_with_mocked_patcher(extractor, path, data, answers, encoding):
    """Extract *data* and inject *answers*; return the result and the patcher mocks."""
    content = extractor.extract(path, data)
    translations = {item.key: answers[item.text] for item in content.items if item.text in answers}
    with patch("nwn_translator.injectors.gff_injector.GFFPatcher") as patcher_cls:
        patcher = MagicMock()
        patcher_cls.return_value = patcher
        result = inject_gff(
            path,
            content.items,
            translations,
            content_type=content.content_type,
            text_encoding=encoding,
        )
    return result, patcher_cls, patcher


def test_dialog_lines_are_patched_at_their_record_offsets():
    data = {
        "StructType": "DLG",
        "EntryList": [
            {
                "Text": _loc("Greetings, traveler."),
                "Speaker": "Innkeeper",
                "_record_offsets": {"Text": 100},
            }
        ],
        "ReplyList": [{"Text": _loc("Hello, innkeeper."), "_record_offsets": {"Text": 200}}],
    }
    answers = {"Greetings, traveler.": "¡Saludos, viajero!", "Hello, innkeeper.": "Hola, posadero."}
    path = Path("test_dialog.dlg")

    result, patcher_cls, patcher = _inject_with_mocked_patcher(
        DialogExtractor(), path, data, answers, "cp1252"
    )

    assert (result.modified, result.items_updated, result.metadata) == (True, 2, {"type": "dialog"})
    patcher_cls.assert_called_once_with(path, text_encoding="cp1252")
    patcher.patch_multiple.assert_called_once()
    assert set(patcher.patch_multiple.call_args[0][0]) == {
        (100, "¡Saludos, viajero!"),
        (200, "Hola, posadero."),
    }


def test_area_instances_and_their_inventories_are_patched_in_one_splice():
    data = {
        "WaypointList": [
            {
                "LocalizedName": _loc("WP_CityGate"),
                "MapNote": _loc("City Gate"),
                "_record_offsets": {"LocalizedName": 0, "MapNote": 222},
            }
        ],
        "Placeable List": [
            {
                "LocName": _loc("Chest"),
                "_record_offsets": {"LocName": 110, "Description": 0},
                "ItemList": [
                    {
                        "LocalizedName": _loc("Scroll Case"),
                        "_record_offsets": {"LocalizedName": 200},
                    }
                ],
            }
        ],
        "StoreList": [
            {
                "LocalizedName": _loc("Arms Dealer"),
                "_record_offsets": {"LocalizedName": 300, "Description": 0},
                "ItemList": [
                    {
                        "LocalizedName": _loc("Iron Longsword"),
                        "_record_offsets": {"LocalizedName": 400},
                    }
                ],
            },
            {
                "LocName": _loc("Coffee Merchant"),
                "_record_offsets": {"LocName": 310, "LocalizedName": 0, "Description": 0},
                "ItemList": [],
            },
            {
                "LocName": _loc("Bar"),
                "_record_offsets": {"LocName": 50, "LocalizedName": 0, "Description": 0},
                "StoreList": [
                    {},
                    {},
                    {
                        "ItemList": [
                            {
                                "LocalizedName": _loc("Coffee"),
                                "_record_offsets": {"LocalizedName": 900},
                            }
                        ]
                    },
                ],
            },
        ],
        "Creature List": [
            {
                "FirstName": _loc("Grandma"),
                "_record_offsets": {"FirstName": 100},
                "Equip_ItemList": [
                    {
                        "LocalizedName": _loc("Grandma's Armor"),
                        "Description": _loc("Worn by Grandma."),
                        "_record_offsets": {"LocalizedName": 500, "Description": 600},
                    },
                    {
                        "LocalizedName": _loc("The Skullsplitter"),
                        "_record_offsets": {"LocalizedName": 700},
                    },
                ],
            }
        ],
    }
    answers = {
        "City Gate": "Городские ворота",
        "Chest": "Сундук",
        "Scroll Case": "Футляр",
        "Arms Dealer": "Оружейник",
        "Iron Longsword": "Железный длинный меч",
        "Coffee Merchant": "Кофейня",
        "Bar": "Бар",
        "Coffee": "Кофе",
        "Grandma": "Бабушка",
        "Grandma's Armor": "Бабушкина броня",
        "Worn by Grandma.": "Носит бабушка.",
        "The Skullsplitter": "Раскалыватель черепов",
    }

    result, _cls, patcher = _inject_with_mocked_patcher(
        GitExtractor(), Path("area.git"), data, answers, "cp1251"
    )

    assert result.items_updated == 12
    patcher.patch_multiple.assert_called_once()
    assert set(patcher.patch_multiple.call_args[0][0]) == {
        (222, "Городские ворота"),
        (110, "Сундук"),
        (200, "Футляр"),
        (300, "Оружейник"),
        (400, "Железный длинный меч"),
        (310, "Кофейня"),
        (50, "Бар"),
        (900, "Кофе"),
        (100, "Бабушка"),
        (500, "Бабушкина броня"),
        (600, "Носит бабушка."),
        (700, "Раскалыватель черепов"),
    }


def test_door_instance_names_are_patched_and_rebuilt(tmp_path):
    extract_dir = tmp_path / "extract"
    path = extract_dir / "keep.git"
    doors = [
        {"Tag": "KeepGate", "LocName": _loc("Iron Gate"), "Description": _loc("Rusty.")},
        {"Tag": "CellarDoor", "LocName": _loc("Cellar Door")},
    ]
    write_gff(path, {"StructType": "GIT", "Door List": doors}, file_type="GIT")

    def door(index, field):
        return read_gff(path, source_encoding="cp1251")["Door List"][index][field]["Value"]

    parsed, extracted = load_parsed_and_extracted(path, ".git", None)
    answers = {"Iron Gate": "Железные ворота", "Cellar Door": "Дверь в погреб"}
    translations = {
        item.key: answers[item.text] for item in extracted.items if item.text in answers
    }
    inject_translations_into_file(path, parsed, extracted, translations, target_lang="russian")

    assert [door(0, "LocName"), door(0, "Description"), door(1, "LocName")] == [
        "Железные ворота",
        "Rusty.",
        "Дверь в погреб",
    ]
    assert read_gff(path)["Door List"][1]["Tag"] == "CellarDoor"

    rebuild_module(
        extract_dir,
        {"keep.git": {"keep_Door List_1_LocName": "Погребная дверь"}},
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="russian",
    )

    assert [door(0, "LocName"), door(1, "LocName")] == ["Железные ворота", "Погребная дверь"]
