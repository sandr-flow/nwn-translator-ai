"""Dialog formatter for contextual translation.

Converts a hierarchical dialog tree into a flat, numbered script format
suitable for LLM contextual translation.
"""

from typing import List, Set, Dict, Any, Optional
from ..extractors.base import DialogNode


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
        visited = set()

        # We process roots first, then all nodes discovered through BFS/DFS to
        # ensure all referenced "Go to [E...]" blocks are eventually printed.
        queue = list(tree)
        nodes_to_process = []

        # Flatten tree to maintain a stable order of processing (Entries first)
        def collect_nodes(nodes: List[DialogNode]):
            for node in nodes:
                node_id = f"{'E' if node.is_entry else 'R'}{node.node_id}"
                if node_id not in visited:
                    visited.add(node_id)
                    nodes_to_process.append(node)
                    collect_nodes(node.replies)

        collect_nodes(queue)

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

                    # For flow context, we only need a short preview
                    reply_raw = overrides.get(reply_key, reply.text or "")
                    reply_text = reply_raw.replace("\n", " ").strip()
                    preview = reply_text[:60] + "..." if len(reply_text) > 60 else reply_text

                    if node.is_entry:
                        lines.append(f'   -> Player Reply [{reply_key}]: "{preview}"')
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
        """Format a specific subset of nodes for a retry request.

        Args:
            keys: Node IDs (e.g. ["E5", "R12"]) to include.
            node_map: Full mapping of node ID → DialogNode.
            text_map: Mapping of node ID → original text (used for speaker lookup).
            text_overrides: Optional mapping of node key to text that should be
                used instead of ``node.text``.

        Returns:
            Formatted script string containing only the requested nodes.
        """
        overrides = text_overrides or {}
        lines = []
        for key in keys:
            node = node_map.get(key)
            if node is None:
                continue
            speaker = node.speaker if node.speaker else ("NPC" if node.is_entry else "Player")
            node_text = overrides.get(key, node.text or "")
            lines.append(f"[{key}] [{speaker}]:")
            lines.append(f"<<<{node_text}>>>")
            lines.append("")
        return "\n".join(lines).strip()
