"""Dialog trees, journals and translatable items.

The output of every extractor is pinned by ``test_extractor_snapshot``; these
tests cover what the snapshot does not show.
"""

import logging
from pathlib import Path

from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.extractors.journal_extractor import JournalExtractor
from tests.support.dialogs import deep_chain


def _loc(text: str) -> dict:
    return {"StrRef": -1, "Value": text}


def _entry(text, reply_indices=(), speaker=""):
    return {
        "Text": _loc(text),
        "Speaker": speaker,
        "RepliesList": [{"Index": i} for i in reply_indices],
    }


def _reply(text, entry_indices=()):
    return {"Text": _loc(text), "EntriesList": [{"Index": i} for i in entry_indices]}


def _tree(entries, replies, starts=(0,)):
    return DialogExtractor().build_dialog_tree(
        {
            "StructType": "DLG",
            "EntryList": entries,
            "ReplyList": replies,
            "StartingList": [{"Index": i} for i in starts],
        }
    )


def test_items_and_text_checks():
    assert TranslatableItem(text="Hello world").has_text()
    assert not TranslatableItem(text="").has_text()
    assert not TranslatableItem(text="   ").has_text()
    result = DialogExtractor().extract(
        Path("test.dlg"),
        {
            "EntryList": [{"Text": _loc("Hello there!"), "Speaker": "Guard"}],
            "ReplyList": [{"Text": _loc("Just passing through.")}],
        },
    )
    assert (result.content_type, result.source_file) == ("dialog", Path("test.dlg"))
    assert [item.item_id for item in result.items] == ["test:entry:0", "test:reply:0"]


def test_deep_chain_builds_without_recursion_error():
    """1000 entry/reply alternations: depth 2000, far past the recursion limit."""
    tree = DialogExtractor().build_dialog_tree(deep_chain(1000))

    assert len(tree) == 1
    depth, node = 1, tree[0]
    while node.replies:
        assert len(node.replies) == 1
        depth, node = depth + 1, node.replies[0]
    assert depth == 2000
    assert node.text == "R999"


def test_non_struct_nodes_are_skipped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        entry_tree = _tree(
            [_entry("Hello", reply_indices=[0]), 12345],
            [_reply("Take me to the broken one", entry_indices=[1])],
        )
        reply_tree = _tree(
            [_entry("Hello", reply_indices=[0, 1])], ["not-a-struct", _reply("A valid reply")]
        )

    assert "Dialog entry 1 is not a struct (int)" in caplog.text
    assert "Dialog reply 0 is not a struct (str)" in caplog.text
    (reply,) = entry_tree[0].replies
    assert (reply.text, reply.replies) == ("Take me to the broken one", [])
    assert [r.text for r in reply_tree[0].replies] == ["A valid reply"]


def test_cycles_and_shared_nodes_are_attached_once():
    loop = _tree([_entry("Loop?", reply_indices=[0])], [_reply("Again!", entry_indices=[0])])
    assert [r.text for r in loop[0].replies] == ["Again!"]
    assert loop[0].replies[0].replies == []  # the back edge to entry 0 is dropped

    diamond = _tree(
        [_entry("Root", reply_indices=[0, 1]), _entry("Shared destination")],
        [_reply("Left path", entry_indices=[1]), _reply("Right path", entry_indices=[1])],
    )
    left, right = diamond[0].replies
    assert [n.text for n in left.replies] == ["Shared destination"]
    assert right.replies == []  # already visited through the left path


def test_order_and_node_fields_are_kept():
    tree = _tree(
        [_entry("First root", reply_indices=[0, 1, 2], speaker="Guard"), _entry("Second root")],
        [_reply("A"), _reply("B"), _reply("C")],
        starts=(0, 1),
    )
    assert [n.text for n in tree] == ["First root", "Second root"]
    assert [r.text for r in tree[0].replies] == ["A", "B", "C"]
    entry, reply = tree[0], tree[0].replies[0]
    assert (entry.is_entry, entry.speaker, entry.node_id) == (True, "Guard", 0)
    assert (reply.is_entry, reply.speaker) == (False, "Player")


def test_non_struct_journal_categories_and_entries_are_skipped():
    """Out-of-range struct references stay raw ints; they must not abort the file."""
    parsed = {
        "Categories": [
            7,
            {"Name": _loc("Side Quest"), "EntryList": [3, {"ID": 1, "Text": _loc("Found it.")}]},
        ]
    }

    result = JournalExtractor().extract(Path("test.jrl"), parsed)

    assert [(item.item_id, item.text) for item in result.items] == [
        ("category_1_name", "Side Quest"),
        ("entry_1_1", "Found it."),
    ]
    assert result.metadata == {"category_count": 2}
