"""Render dialog trees as the numbered scripts sent to the model.

A node is addressed by its key: ``E{i}`` for NPC entry *i* and ``R{i}`` for
player reply *i*, where *i* is the node's index in ``EntryList`` /
``ReplyList``. Each node becomes a block with its key, speaker, text between
``<<<`` and ``>>>`` and the keys it leads to; the model answers with a JSON
object keyed the same way.
"""

from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple

from ..extractors.base import DialogNode

#: Longest text shown for an adjacent, context-only node.
_CONTEXT_PREVIEW_CHARS = 600


def node_key(node: DialogNode) -> str:
    """Return the script key of *node*: ``E3`` for entry 3, ``R0`` for reply 0."""
    return f"{'E' if node.is_entry else 'R'}{node.node_id}"


def speaker_label(node: DialogNode) -> str:
    """Return the speaker shown for *node*: its tag, else ``NPC`` or ``Player``."""
    return node.speaker or ("NPC" if node.is_entry else "Player")


def iter_nodes(tree: List[DialogNode]) -> Iterator[Tuple[str, DialogNode]]:
    """Walk a dialog tree depth-first in pre-order, yielding each node key once.

    The first occurrence of a key is yielded and its subtree walked; later
    occurrences (a reply linked from several entries) are skipped with their
    subtrees. The walk is iterative, so chains longer than the recursion limit
    work.

    Args:
        tree: Root nodes (from ``DialogExtractor.build_dialog_tree``).

    Yields:
        ``(key, node)`` pairs in walk order.
    """
    seen: Set[str] = set()
    # Children are pushed in reverse so the first child is walked first; a key
    # is checked when popped, after the previous sibling's subtree is complete.
    stack = list(reversed(tree))
    while stack:
        node = stack.pop()
        key = node_key(node)
        if key in seen:
            continue
        seen.add(key)
        yield key, node
        stack.extend(reversed(node.replies))


def _render_blocks(
    nodes: Iterable[Tuple[str, DialogNode]], overrides: Mapping[str, str]
) -> List[str]:
    """Render one block per node: header, text and the keys the node leads to."""
    lines: List[str] = []
    for key, node in nodes:
        lines.append(f"[{key}] [{speaker_label(node)}]:")
        lines.append(f"<<<{overrides.get(key, node.text or '')}>>>")
        if not node.replies:
            lines.append("   -> [END DIALOGUE]")
        for child in node.replies:
            # Only the child's key: a text preview here would give the model a
            # second, shortened version of a line it must translate in full.
            kind = "NPC Response" if child.is_entry else "Player Reply"
            lines.append(f"   -> {kind} [{node_key(child)}]")
        lines.append("")
    return lines


def format_dialog_tree(
    tree: List[DialogNode], text_overrides: Optional[Mapping[str, str]] = None
) -> str:
    """Render every node of a dialog tree, in :func:`iter_nodes` order.

    Nodes without text are rendered too (``<<<>>>``) so that every routing
    hint points at a block.

    Args:
        tree: Root nodes (from ``DialogExtractor.build_dialog_tree``).
        text_overrides: Key to text used instead of ``node.text`` (the
            sanitized texts, so the nodes themselves stay untouched).

    Returns:
        The script; empty for an empty tree.
    """
    return "\n".join(_render_blocks(iter_nodes(tree), text_overrides or {})).strip()


def format_nodes(
    keys: List[str],
    node_map: Dict[str, DialogNode],
    text_overrides: Optional[Mapping[str, str]] = None,
) -> str:
    """Render selected nodes, followed by their neighbours as context only.

    The neighbours are the selected nodes' children and parents that are not
    selected themselves: children in block order first, then parents in
    *node_map* order. They are listed after a header telling the model not to
    translate them, with their text cut to 600 characters.

    Args:
        keys: Keys of the nodes to translate, in output order.
        node_map: Every node of the dialog by key, in walk order.
        text_overrides: Key to text used instead of ``node.text``.

    Returns:
        The script.
    """
    overrides = text_overrides or {}
    selected = set(keys)
    lines = _render_blocks(((key, node_map[key]) for key in keys), overrides)
    neighbours: Dict[str, DialogNode] = {}
    for key in keys:
        for child in node_map[key].replies:
            child_key = node_key(child)
            if child_key not in selected:
                neighbours[child_key] = node_map.get(child_key, child)
    for key, node in node_map.items():
        if key not in selected and any(node_key(child) in selected for child in node.replies):
            neighbours[key] = node
    if neighbours:
        lines.append("Adjacent nodes (context only; do not return translations for these IDs):")
        for key, node in neighbours.items():
            text = overrides.get(key, node.text or "")
            preview = text[:_CONTEXT_PREVIEW_CHARS]
            if len(text) > _CONTEXT_PREVIEW_CHARS:
                preview += "…"
            lines.append(f"Context {key} ({speaker_label(node)}): {preview}")
    return "\n".join(lines).strip()
