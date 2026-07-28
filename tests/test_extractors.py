"""Tests for content extraction functionality."""

import pytest
from pathlib import Path
from unittest.mock import Mock

from src.nwn_translator.extractors.base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    DialogNode,
)
from src.nwn_translator.extractors.dialog_extractor import DialogExtractor
from src.nwn_translator.extractors.journal_extractor import JournalExtractor
from src.nwn_translator.extractors.item_extractor import ItemExtractor
from src.nwn_translator.extractors.area_extractor import (
    PlaceableExtractor,
    DoorExtractor,
    EncounterExtractor,
    StoreExtractor,
    TriggerExtractor,
)


class TestDialogExtractor:
    """Tests for DialogExtractor."""

    def test_can_extract_dlg_files(self):
        """Test that extractor can handle .dlg files."""
        extractor = DialogExtractor()
        assert extractor.can_extract(".dlg")
        assert extractor.can_extract(".DLG")
        assert not extractor.can_extract(".uti")

    def test_extract_simple_dialog(self):
        """Test extracting a simple dialog."""
        extractor = DialogExtractor()
        file_path = Path("test.dlg")

        # Minimal GFF data for a dialog
        parsed_data = {
            "StructType": "DLG",
            "EntryList": [],
            "ReplyList": [],
        }

        result = extractor.extract(file_path, parsed_data)

        assert isinstance(result, ExtractedContent)
        assert result.content_type == "dialog"
        assert result.source_file == file_path

    def test_extract_dialog_with_entries(self):
        """Test extracting dialog with entries."""
        extractor = DialogExtractor()
        file_path = Path("test.dlg")

        parsed_data = {
            "StructType": "DLG",
            "EntryList": [
                {
                    "Active": "1",
                    "Text": {"StrRef": -1, "Value": "Hello there!"},
                    "Speaker": "Guard",
                    "EntriesList": [],
                },
                {
                    "Active": "1",
                    "Text": {"StrRef": -1, "Value": "Who goes there?"},
                    "Speaker": "Guard",
                    "EntriesList": [],
                },
            ],
            "ReplyList": [
                {
                    "Text": {"StrRef": -1, "Value": "Just passing through."},
                    "EntriesList": [],
                }
            ],
        }

        result = extractor.extract(file_path, parsed_data)

        # One TranslatableItem per text node: 2 NPC entries + 1 player reply = 3
        assert len(result.items) == 3
        assert all(item.has_text() for item in result.items)
        ids = {item.item_id for item in result.items}
        assert "test:entry:0" in ids
        assert "test:reply:0" in ids


class TestBuildDialogTree:
    """Tests for the iterative dialog tree walk."""

    @staticmethod
    def _entry(text, reply_indices=(), speaker=""):
        return {
            "Text": {"StrRef": -1, "Value": text},
            "Speaker": speaker,
            "RepliesList": [{"Index": i} for i in reply_indices],
        }

    @staticmethod
    def _reply(text, entry_indices=()):
        return {
            "Text": {"StrRef": -1, "Value": text},
            "EntriesList": [{"Index": i} for i in entry_indices],
        }

    def _build(self, entry_list, reply_list, starting_indices):
        return DialogExtractor().build_dialog_tree(
            {
                "StructType": "DLG",
                "EntryList": entry_list,
                "ReplyList": reply_list,
                "StartingList": [{"Index": i} for i in starting_indices],
            }
        )

    def test_deep_chain_builds_without_recursion_error(self):
        """1000 entry/reply alternations: depth 2000, far past the recursion limit."""
        n = 1000
        entry_list = [self._entry(f"E{i}", reply_indices=[i]) for i in range(n)]
        reply_list = [
            self._reply(f"R{i}", entry_indices=[i + 1] if i + 1 < n else []) for i in range(n)
        ]

        tree = self._build(entry_list, reply_list, [0])

        assert len(tree) == 1
        depth = 0
        node = tree[0]
        while True:
            depth += 1
            if not node.replies:
                break
            assert len(node.replies) == 1
            node = node.replies[0]
        assert depth == 2 * n
        assert node.text == f"R{n - 1}"

    def test_non_dict_entry_skipped_with_warning(self, caplog):
        import logging

        entry_list = [self._entry("Hello", reply_indices=[0]), 12345]
        reply_list = [self._reply("Take me to the broken one", entry_indices=[1])]

        with caplog.at_level(logging.WARNING):
            tree = self._build(entry_list, reply_list, [0])

        assert "Dialog entry 1 is not a struct (int)" in caplog.text
        assert len(tree) == 1
        reply = tree[0].replies[0]
        assert reply.text == "Take me to the broken one"
        assert reply.replies == []  # the broken entry produced no node

    def test_non_dict_reply_skipped_with_warning(self, caplog):
        import logging

        entry_list = [self._entry("Hello", reply_indices=[0, 1])]
        reply_list = ["not-a-struct", self._reply("A valid reply")]

        with caplog.at_level(logging.WARNING):
            tree = self._build(entry_list, reply_list, [0])

        assert "Dialog reply 0 is not a struct (str)" in caplog.text
        assert len(tree) == 1
        assert [r.text for r in tree[0].replies] == ["A valid reply"]

    def test_cycle_terminates_and_back_edge_dropped(self):
        entry_list = [self._entry("Loop?", reply_indices=[0])]
        reply_list = [self._reply("Again!", entry_indices=[0])]

        tree = self._build(entry_list, reply_list, [0])

        assert len(tree) == 1
        assert [r.text for r in tree[0].replies] == ["Again!"]
        assert tree[0].replies[0].replies == []  # back-edge to entry 0 not attached

    def test_diamond_entry_attached_once_on_first_path(self):
        entry_list = [
            self._entry("Root", reply_indices=[0, 1]),
            self._entry("Shared destination"),
        ]
        reply_list = [
            self._reply("Left path", entry_indices=[1]),
            self._reply("Right path", entry_indices=[1]),
        ]

        tree = self._build(entry_list, reply_list, [0])

        left, right = tree[0].replies
        assert [n.text for n in left.replies] == ["Shared destination"]
        assert right.replies == []  # already visited via the left path

    def test_root_and_sibling_order_preserved(self):
        entry_list = [
            self._entry("First root", reply_indices=[0, 1, 2]),
            self._entry("Second root"),
        ]
        reply_list = [self._reply("A"), self._reply("B"), self._reply("C")]

        tree = self._build(entry_list, reply_list, [0, 1])

        assert [n.text for n in tree] == ["First root", "Second root"]
        assert [r.text for r in tree[0].replies] == ["A", "B", "C"]

    def test_node_fields_carried(self):
        entry_list = [self._entry("Halt!", reply_indices=[0], speaker="Guard")]
        reply_list = [self._reply("Just passing.")]

        tree = self._build(entry_list, reply_list, [0])

        entry = tree[0]
        assert entry.is_entry is True
        assert entry.speaker == "Guard"
        assert entry.node_id == 0
        assert entry.metadata == {"type": "entry"}
        reply = entry.replies[0]
        assert reply.is_entry is False
        assert reply.speaker == "Player"
        assert reply.metadata == {"type": "reply"}


class TestJournalExtractor:
    """Tests for JournalExtractor."""

    def test_can_extract_jrl_files(self):
        """Test that extractor can handle .jrl files."""
        extractor = JournalExtractor()
        assert extractor.can_extract(".jrl")
        assert not extractor.can_extract(".dlg")

    def test_extract_journal_categories(self):
        """Test extracting journal categories."""
        extractor = JournalExtractor()
        file_path = Path("test.jrl")

        parsed_data = {
            "StructType": "JRL",
            "Categories": [
                {
                    "Name": {"StrRef": -1, "Value": "Main Quest"},
                    "Priority": 1,
                    "Tag": "main_quest",
                    "EntryList": [],
                }
            ],
        }

        result = extractor.extract(file_path, parsed_data)

        assert result.content_type == "journal"
        assert len(result.items) >= 1

    def test_extract_journal_entries(self):
        """Test extracting journal entries."""
        extractor = JournalExtractor()
        file_path = Path("test.jrl")

        parsed_data = {
            "StructType": "JRL",
            "Categories": [
                {
                    "Name": {"StrRef": -1, "Value": "Side Quest"},
                    "Priority": 1,
                    "Tag": "side_quest",
                    "EntryList": [
                        {
                            "ID": 10,
                            "End": 0,
                            "Text": {"StrRef": -1, "Value": "You discovered a secret."},
                        }
                    ],
                }
            ],
        }

        result = extractor.extract(file_path, parsed_data)

        # 1 category name + 1 entry text
        assert len(result.items) >= 2


class TestItemExtractor:
    """Tests for ItemExtractor."""

    def test_can_extract_uti_files(self):
        """Test that extractor can handle .uti files."""
        extractor = ItemExtractor()
        assert extractor.can_extract(".uti")
        assert not extractor.can_extract(".dlg")

    def test_extract_item_with_name_and_description(self):
        """Test extracting item name and description."""
        extractor = ItemExtractor()
        file_path = Path("sword.uti")

        parsed_data = {
            "StructType": "UTI",
            "LocalizedName": {"StrRef": -1, "Value": "Longsword of Fire"},
            "Description": {"StrRef": -1, "Value": "A magical sword that burns with flame."},
            "DescIdentified": {"StrRef": -1, "Value": "Longsword +1, Flaming"},
            "Tag": "sword_fire",
        }

        result = extractor.extract(file_path, parsed_data)

        assert result.content_type == "item"
        assert len(result.items) >= 2  # Name and description

    def test_extract_item_with_only_name(self):
        """Test extracting item with only a name."""
        extractor = ItemExtractor()
        file_path = Path("potion.uti")

        parsed_data = {
            "StructType": "UTI",
            "LocalizedName": {"StrRef": -1, "Value": "Health Potion"},
            "Description": {"StrRef": 1234},  # Using StrRef instead of Value
            "Tag": "potion_health",
        }

        result = extractor.extract(file_path, parsed_data)

        assert len(result.items) >= 1
        # Should have name even if description uses StrRef


class TestPlaceableExtractorDescriptions:
    """Placeable .utp: Name, Description, DescIdentified."""

    def test_extract_placeable_with_description(self):
        extractor = PlaceableExtractor()
        file_path = Path("chest.utp")
        parsed_data = {
            "StructType": "UTP",
            "Tag": "chest01",
            "Name": {"StrRef": -1, "Value": "Old Chest"},
            "Description": {"StrRef": -1, "Value": "A weathered wooden chest."},
            "DescIdentified": {"StrRef": -1, "Value": "Contains quest items."},
        }
        result = extractor.extract(file_path, parsed_data)
        texts = {item.text for item in result.items}
        assert "Old Chest" in texts
        assert "A weathered wooden chest." in texts
        assert "Contains quest items." in texts

    def test_extract_placeable_desc_identified_same_as_description_not_duplicated(self):
        extractor = PlaceableExtractor()
        file_path = Path("box.utp")
        same = "Same text"
        parsed_data = {
            "Tag": "box",
            "Name": {"StrRef": -1, "Value": "Box"},
            "Description": {"StrRef": -1, "Value": same},
            "DescIdentified": {"StrRef": -1, "Value": same},
        }
        result = extractor.extract(file_path, parsed_data)
        assert sum(1 for item in result.items if item.text == same) == 1


class TestDoorExtractorDescriptions:
    def test_extract_door_name_and_description(self):
        extractor = DoorExtractor()
        file_path = Path("door.utd")
        parsed_data = {
            "Tag": "gate",
            "LocalizedName": {"StrRef": -1, "Value": "Iron Gate"},
            "Description": {"StrRef": -1, "Value": "Locked from the other side."},
        }
        result = extractor.extract(file_path, parsed_data)
        texts = {item.text for item in result.items}
        assert "Iron Gate" in texts
        assert "Locked from the other side." in texts


class TestTriggerExtractorDescriptions:
    def test_extract_trigger_name_and_description(self):
        extractor = TriggerExtractor()
        file_path = Path("trap.utt")
        parsed_data = {
            "Tag": "trap1",
            "TrapFlag": 1,
            "LocalizedName": {"StrRef": -1, "Value": "Spike Trap"},
            "Description": {"StrRef": -1, "Value": "Dangerous when armed."},
        }
        result = extractor.extract(file_path, parsed_data)
        texts = {item.text for item in result.items}
        assert "Spike Trap" in texts
        assert "Dangerous when armed." in texts

    def test_scripting_trigger_skipped(self):
        """Non-trap triggers (scripting only) produce no items."""
        extractor = TriggerExtractor()
        file_path = Path("scripting.utt")
        parsed_data = {
            "Tag": "SummonDawn",
            "LocalizedName": {"StrRef": -1, "Value": "Summon Dawn"},
        }
        result = extractor.extract(file_path, parsed_data)
        assert len(result.items) == 0


class TestPlaceableExtractorLocName:
    """Regression: NWN / NWN:EE .utp files store the display name in
    ``LocName`` (not ``Name``). PlaceableExtractor must pick it up, otherwise
    placeables like the ``invis_corpse_obj`` template stay untranslated and
    players see the English label ``corpse`` on hover.
    """

    def test_extract_placeable_name_from_loc_name(self):
        extractor = PlaceableExtractor()
        file_path = Path("invis_corpse_obj.utp")
        parsed_data = {
            "StructType": "UTP",
            "Tag": "invis_corpse_obj",
            "LocName": {"StrRef": -1, "Value": "corpse"},
            "Description": {"StrRef": -1, "Value": ""},
        }
        result = extractor.extract(file_path, parsed_data)
        names = [it.text for it in result.items if it.metadata.get("type") == "placeable_name"]
        assert names == ["corpse"]

    def test_loc_name_wins_over_name_when_both_present(self):
        extractor = PlaceableExtractor()
        parsed_data = {
            "Tag": "dup",
            "LocName": {"StrRef": -1, "Value": "Canonical"},
            "Name": {"StrRef": -1, "Value": "LegacyToolsetExport"},
        }
        result = extractor.extract(Path("dup.utp"), parsed_data)
        names = [it.text for it in result.items if it.metadata.get("type") == "placeable_name"]
        assert names == ["Canonical"]


class TestEncounterExtractor:
    def test_can_extract_ute_files(self):
        assert EncounterExtractor().can_extract(".ute")

    def test_extract_encounter_name_and_description(self):
        extractor = EncounterExtractor()
        file_path = Path("darkshadows.ute")
        parsed_data = {
            "Tag": "DarkShadows",
            "LocalizedName": {"StrRef": -1, "Value": "Dark Shadows"},
            "Description": {"StrRef": -1, "Value": "A group of shadows."},
        }
        result = extractor.extract(file_path, parsed_data)
        assert result.content_type == "encounter"
        texts = {item.text: item.metadata.get("type") for item in result.items}
        assert texts.get("Dark Shadows") == "encounter_name"
        assert texts.get("A group of shadows.") == "encounter_description"


class TestStoreExtractorDescriptions:
    def test_extract_store_name_and_description(self):
        extractor = StoreExtractor()
        file_path = Path("shop.utm")
        parsed_data = {
            "Tag": "merchant",
            "LocalizedName": {"StrRef": -1, "Value": "General Store"},
            "Description": {"StrRef": -1, "Value": "Weapons and potions."},
        }
        result = extractor.extract(file_path, parsed_data)
        texts = {item.text for item in result.items}
        assert "General Store" in texts
        assert "Weapons and potions." in texts

    def test_extract_store_prefers_loc_name(self):
        extractor = StoreExtractor()
        file_path = Path("coffee.utm")
        parsed_data = {
            "Tag": "coffee",
            "LocName": {"StrRef": -1, "Value": "Coffee Stall"},
            "LocalizedName": {"StrRef": -1, "Value": "Wrong Title"},
            "Description": {"StrRef": -1, "Value": ""},
        }
        result = extractor.extract(file_path, parsed_data)
        names = [item.text for item in result.items if item.metadata.get("type") == "store_name"]
        assert names == ["Coffee Stall"]


class TestTranslatableItem:
    """Tests for TranslatableItem dataclass."""

    def test_create_simple_item(self):
        """Test creating a simple translatable item."""
        item = TranslatableItem(text="Hello world")
        assert item.text == "Hello world"
        assert item.context is None
        assert item.item_id is None

    def test_create_full_item(self):
        """Test creating item with all fields."""
        item = TranslatableItem(
            text="Hello",
            context="Greeting",
            item_id="greeting_1",
            location="dialog.dlg",
            metadata={"speaker": "NPC"},
        )
        assert item.text == "Hello"
        assert item.context == "Greeting"
        assert item.item_id == "greeting_1"
        assert item.location == "dialog.dlg"
        assert item.metadata["speaker"] == "NPC"

    def test_has_text(self):
        """Test has_text method."""
        item_with_text = TranslatableItem(text="Hello")
        assert item_with_text.has_text()

        item_empty = TranslatableItem(text="")
        assert not item_empty.has_text()

        item_whitespace = TranslatableItem(text="   ")
        assert not item_whitespace.has_text()


class TestDialogNode:
    """Tests for DialogNode dataclass."""

    def test_create_entry_node(self):
        """Test creating an entry node."""
        node = DialogNode(
            node_id=1,
            text="Hello there!",
            speaker="Guard",
            is_entry=True,
        )
        assert node.node_id == 1
        assert node.text == "Hello there!"
        assert node.speaker == "Guard"
        assert node.is_entry
        assert len(node.replies) == 0

    def test_create_reply_node(self):
        """Test creating a reply node."""
        node = DialogNode(
            node_id=1,
            text="Just passing through.",
            speaker="Player",
            is_entry=False,
        )
        assert not node.is_entry

    def test_nested_replies(self):
        """Test creating nested reply structure."""
        entry = DialogNode(node_id=0, text="Hello!", is_entry=True)
        reply1 = DialogNode(node_id=1, text="Hi!", is_entry=False)
        reply2 = DialogNode(node_id=2, text="Bye!", is_entry=False)

        entry.replies.extend([reply1, reply2])

        assert len(entry.replies) == 2
        assert entry.replies[0].text == "Hi!"
        assert entry.replies[1].text == "Bye!"
