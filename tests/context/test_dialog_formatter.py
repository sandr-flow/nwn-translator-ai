"""The dialog script the model reads: node blocks, routing hints and context neighbours."""

from pathlib import Path
from typing import List

from nwn_translator.context.dialog_formatter import (
    format_dialog_tree,
    format_nodes,
    iter_nodes,
    node_key,
)
from nwn_translator.extractors.base import DialogNode
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.translators.dialog_plan import prepare_dialog
from tests.support.dialogs import deep_chain


def _branching_tree() -> List[DialogNode]:
    end = DialogNode(node_id=2, text="Farewell.", speaker="GUARD", is_entry=True)
    return [
        DialogNode(
            node_id=0,
            text="Halt!",
            is_entry=True,
            replies=[
                DialogNode(node_id=0, text="Why?", is_entry=False, replies=[end]),
                DialogNode(node_id=1, text="", is_entry=False),
            ],
        )
    ]


def test_dialog_tree_script():
    assert format_dialog_tree(_branching_tree(), {"E0": "HALT"}) == (
        "[E0] [NPC]:\n"
        "<<<HALT>>>\n"
        "   -> Player Reply [R0]\n"
        "   -> Player Reply [R1]\n"
        "\n"
        "[R0] [Player]:\n"
        "<<<Why?>>>\n"
        "   -> NPC Response [E2]\n"
        "\n"
        "[E2] [GUARD]:\n"
        "<<<Farewell.>>>\n"
        "   -> [END DIALOGUE]\n"
        "\n"
        "[R1] [Player]:\n"
        "<<<>>>\n"
        "   -> [END DIALOGUE]"
    )


def test_routing_hints_do_not_repeat_the_reply_text():
    """A long reply appears only in its own node block, not as a truncated preview."""
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

    script = format_dialog_tree(tree)

    assert "-> Player Reply [R5]" in script
    assert '-> Player Reply [R5]: "' not in script
    assert "cleaned out Glod's entire mine" in script
    assert "cleaned out Glod's entire mine of goblins, bugbears, minotaurs..." not in script


def test_selected_nodes_script_with_context_neighbours():
    node_map = dict(iter_nodes(_branching_tree()))
    node_map["E2"].text = "x" * 601

    assert format_nodes(["R0"], node_map) == (
        "[R0] [Player]:\n"
        "<<<Why?>>>\n"
        "   -> NPC Response [E2]\n"
        "\n"
        "Adjacent nodes (context only; do not return translations for these IDs):\n"
        f"Context E2 (GUARD): {'x' * 600}…\n"
        "Context E0 (NPC): Halt!"
    )


def test_a_chunk_keeps_its_edges_and_neighbours_as_context_only():
    before = DialogNode(node_id=0, text="Who are you?", is_entry=True)
    selected = DialogNode(node_id=1, text="A traveler.", is_entry=False)
    after = DialogNode(node_id=2, text="Welcome.", is_entry=True)
    before.replies = [selected]
    selected.replies = [after]
    after.replies = [selected]

    script = format_nodes(["R1"], {"E0": before, "R1": selected, "E2": after})

    assert "<<<A traveler.>>>" in script
    assert "Who are you?" in script and "Welcome." in script
    assert "<<<Who are you?>>>" not in script and "<<<Welcome.>>>" not in script
    assert "NPC Response" in script and "E2" in script
    assert "do not return translations for these IDs" in script


def test_node_walk_is_a_pre_order_where_the_first_occurrence_wins():
    shared = DialogNode(node_id=7, text="Shared", is_entry=False)
    later = DialogNode(node_id=4, text="Later", is_entry=True, replies=[shared])
    tree = [
        DialogNode(
            node_id=0,
            text="Root",
            is_entry=True,
            replies=[DialogNode(node_id=1, text="A", is_entry=False, replies=[later]), shared],
        ),
        DialogNode(node_id=2, text="Second root", is_entry=True, replies=[shared]),
        later,
    ]
    keys: List[str] = []

    def visit(nodes: List[DialogNode]) -> None:
        for node in nodes:
            if node_key(node) not in keys:
                keys.append(node_key(node))
                visit(node.replies)

    visit(tree)
    assert [key for key, _node in iter_nodes(tree)] == keys == ["E0", "R1", "E4", "R7", "E2"]


def test_deep_dialog_formats_and_prepares_without_recursion_error():
    """A chain far past the recursion limit is formatted and prepared in full."""
    parsed = deep_chain(1000)

    script = format_dialog_tree(DialogExtractor().build_dialog_tree(parsed))
    prepared = prepare_dialog(Path("deep.dlg"), parsed, 2000, preserve_tokens=True)

    assert script.count("<<<") == 2000
    assert prepared is not None
    assert len(prepared.keys) == 2000
    assert prepared.keys[:3] == ["E0", "R0", "E1"]
