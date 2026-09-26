"""Dialog formatter for contextual translation.

Converts a hierarchical dialog tree into a flat, numbered script format
suitable for LLM contextual translation.
"""

from typing import Dict, Iterator, List, Optional, Set, Tuple

from ..extractors.base import DialogNode


def node_key(node: DialogNode) -> str:
    """Return the script key of *node*: ``E3`` for entry 3, ``R0`` for reply 0."""
    return f"{'E' if node.is_entry else 'R'}{node.node_id}"


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


class DialogFormatter:
    """Formats dialog trees into script representations for LLMs."""

    def format_dialog_tree(
        self,
        tree: List[DialogNode],
        text_overrides: Optional[Dict[str, str]] = None,
    ) -> str:
        """Format an entire dialog tree into a readable script.

        Outputs a script where each node is prefixed with an ID (e.g., [E0] for Entry 0,
        [R1] for Reply 1). This allows the LLM to return translations keyed by these IDs
        while seeing the full branching structure with 'Go to' references.

        Args:
            tree: List of root DialogNodes (from DialogExtractor.build_dialog_tree).
            text_overrides: Optional mapping of node key (e.g. ``"E0"``) to text that
                should be used instead of ``node.text``.  Allows callers to substitute
                sanitized text without mutating the original dialog nodes.

        Returns:
            Formatted script string.
        """
        lines = []
        nodes_to_process = [node for _key, node in iter_nodes(tree)]

        if not nodes_to_process:
            return ""

        # Print all nodes
        overrides = text_overrides or {}
        for node in nodes_to_process:
            node_key = f"{'E' if node.is_entry else 'R'}{node.node_id}"
            speaker = node.speaker if node.speaker else ("NPC" if node.is_entry else "Player")
            node_text = overrides.get(node_key, node.text or "")

            # Format current node with EXACT text for translation
            lines.append(f"[{node_key}] [{speaker}]:")
            lines.append(f"<<<{node_text}>>>")

            # Identify where the replies lead (or if it's an end node)
            if not node.replies:
                lines.append(f"   -> [END DIALOGUE]")
            else:
                for reply in node.replies:
                    reply_key = f"{'E' if reply.is_entry else 'R'}{reply.node_id}"

                    if node.is_entry:
                        # Do not echo reply text here. Showing a truncated preview
                        # gives the model two competing versions of the same node:
                        # the full <<<...>>> block and the shortened routing hint.
                        lines.append(f"   -> Player Reply [{reply_key}]")
                    else:
                        lines.append(f"   -> NPC Response [{reply_key}]")

            lines.append("")  # Empty line between blocks

        return "\n".join(lines).strip()

    def format_nodes(
        self,
        keys: List[str],
        node_map: Dict[str, DialogNode],
        text_map: Dict[str, str],
        text_overrides: Optional[Dict[str, str]] = None,
    ) -> str:
        """Format selected targets with graph edges and bounded adjacent context.

        Args:
            keys: Node IDs (e.g. ["E5", "R12"]) to include.
            node_map: Full mapping of node ID → DialogNode.
            text_map: Mapping of node ID → original text (used for speaker lookup).
            text_overrides: Optional mapping of node key to text that should be
                used instead of ``node.text``.

        Returns:
            Script with target blocks and explicitly non-target adjacent nodes.
        """
        overrides = text_overrides or {}
        lines = []
        selected = set(keys)
        boundary: Dict[str, DialogNode] = {}
        for key in keys:
            node = node_map.get(key)
            if node is None:
                continue
            speaker = node.speaker if node.speaker else ("NPC" if node.is_entry else "Player")
            node_text = overrides.get(key, node.text or "")
            lines.append(f"[{key}] [{speaker}]:")
            lines.append(f"<<<{node_text}>>>")
            if not node.replies:
                lines.append("   -> [END DIALOGUE]")
            for reply in node.replies:
                reply_key = f"{'E' if reply.is_entry else 'R'}{reply.node_id}"
                lines.append(
                    f"   -> {'NPC Response' if reply.is_entry else 'Player Reply'} [{reply_key}]"
                )
                if reply_key not in selected:
                    boundary[reply_key] = node_map.get(reply_key, reply)
            lines.append("")
        for key, node in node_map.items():
            if key not in selected and any(
                f"{'E' if child.is_entry else 'R'}{child.node_id}" in selected
                for child in node.replies
            ):
                boundary[key] = node
        if boundary:
            lines.append("Adjacent nodes (context only; do not return translations for these IDs):")
            for key, node in boundary.items():
                speaker = node.speaker or ("NPC" if node.is_entry else "Player")
                text = overrides.get(key, node.text or "")
                preview = text[:600] + ("…" if len(text) > 600 else "")
                lines.append(f"Context {key} ({speaker}): {preview}")
        return "\n".join(lines).strip()
