"""Regression tests for dialog script formatting."""

from pathlib import Path
from typing import List

from src.nwn_translator.config import TranslationConfig
from src.nwn_translator.context.dialog_formatter import DialogFormatter, iter_nodes, node_key
from src.nwn_translator.context.world_context import WorldContext
from src.nwn_translator.extractors.base import DialogNode
from src.nwn_translator.extractors.dialog_extractor import DialogExtractor
from src.nwn_translator.translators.context_translator import ContextualTranslationManager


def test_format_dialog_tree_does_not_repeat_reply_text_as_truncated_preview():
    """Long reply text must appear only in its full node block, not in routing hints."""
    long_reply = (
        "I don't mean to brag, but I just cleaned out Glod's entire mine "
        "of goblins, bugbears, minotaurs and little girls."
    )
    tree = [
        DialogNode(
            node_id=0,
            text="Hello there.",
            is_entry=True,
            replies=[DialogNode(node_id=5, text=long_reply, is_entry=False)],
        )
    ]

    script = DialogFormatter().format_dialog_tree(tree)

    assert "-> Player Reply [R5]" in script
    assert '-> Player Reply [R5]: "' not in script
    assert "cleaned out Glod's entire mine" in script
    assert "cleaned out Glod's entire mine of goblins, bugbears, minotaurs..." not in script


def _recursive_keys(tree: List[DialogNode]) -> List[str]:
    """Reference walk: recursive pre-order, first occurrence of a key wins."""
    keys: List[str] = []

    def visit(nodes: List[DialogNode]) -> None:
        for node in nodes:
            key = node_key(node)
            if key not in keys:
                keys.append(key)
                visit(node.replies)

    visit(tree)
    return keys


def test_iter_nodes_matches_recursive_pre_order_with_shared_replies():
    shared = DialogNode(node_id=7, text="Shared", is_entry=False)
    later = DialogNode(node_id=4, text="Later", is_entry=True, replies=[shared])
    tree = [
        DialogNode(
            node_id=0,
            text="Root",
            is_entry=True,
            replies=[
                DialogNode(node_id=1, text="A", is_entry=False, replies=[later]),
                shared,
            ],
        ),
        DialogNode(node_id=2, text="Second root", is_entry=True, replies=[shared]),
        later,
    ]

    assert [key for key, _node in iter_nodes(tree)] == _recursive_keys(tree)
    assert [key for key, _node in iter_nodes(tree)] == ["E0", "R1", "E4", "R7", "E2"]


def _deep_chain(n: int) -> dict:
    """Parsed .dlg with *n* entry/reply alternations (depth 2n)."""
    return {
        "StructType": "DLG",
        "EntryList": [
            {"Text": {"StrRef": -1, "Value": f"E{i}"}, "Speaker": "", "RepliesList": [{"Index": i}]}
            for i in range(n)
        ],
        "ReplyList": [
            {
                "Text": {"StrRef": -1, "Value": f"R{i}"},
                "EntriesList": [{"Index": i + 1}] if i + 1 < n else [],
            }
            for i in range(n)
        ],
        "StartingList": [{"Index": 0}],
    }


def test_deep_dialog_formats_and_prepares_without_recursion_error():
    """A chain far past the recursion limit is formatted and prepared in full."""
    parsed = _deep_chain(1000)
    tree = DialogExtractor().build_dialog_tree(parsed)

    script = DialogFormatter().format_dialog_tree(tree)
    manager = ContextualTranslationManager(
        TranslationConfig(api_key="k", input_file=Path("m.mod")), object(), WorldContext()
    )
    prepared = manager._prepare_dialog(Path("deep.dlg"), parsed)

    assert script.count("<<<") == 2000
    assert prepared is not None
    assert len(prepared.all_keys) == 2000
    assert prepared.all_keys[:3] == ["E0", "R0", "E1"]
