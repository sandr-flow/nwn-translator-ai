"""Regression tests for dialog script formatting."""

from src.nwn_translator.context.dialog_formatter import DialogFormatter
from src.nwn_translator.extractors.base import DialogNode


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
