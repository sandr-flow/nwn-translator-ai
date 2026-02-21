"""Context builder for enhancing translations with additional context.

This module builds context information for translations, especially for
dialog trees where context is crucial for accurate translation.
"""

from typing import Any, Dict, List, Optional

from ..extractors.base import DialogNode


class ContextBuilder:
    """Builder for creating translation context."""

    @staticmethod
    def build_dialog_context(tree: List[DialogNode]) -> str:
        """Build context information for a dialog tree.

        Args:
            tree: List of root DialogNode objects

        Returns:
            Context string for translation
        """
        if not tree:
            return ""

        # Extract speaker information
        speakers = set()
        ContextBuilder._collect_speakers(tree, speakers)

        # Build context
        context_parts = []

        if speakers:
            speaker_list = ", ".join(sorted(speakers))
            context_parts.append(f"Speakers in this dialog: {speaker_list}")

        # Count entries and replies
        entry_count = [0]
        reply_count = [0]
        ContextBuilder._count_nodes(tree, entry_count, reply_count)

        context_parts.append(f"Dialog contains {entry_count[0]} entries and {reply_count[0]} replies.")

        return "\n".join(context_parts)

    @staticmethod
    def _collect_speakers(nodes: List[DialogNode], speakers: set) -> None:
        """Recursively collect all speaker names.

        Args:
            nodes: List of DialogNode objects
            speakers: Set to populate with speaker names
        """
        for node in nodes:
            if node.speaker:
                speakers.add(node.speaker)
            if node.replies:
                ContextBuilder._collect_speakers(node.replies, speakers)

    @staticmethod
    def _count_nodes(nodes: List[DialogNode], entry_count: list, reply_count: list) -> None:
        """Recursively count entries and replies.

        Args:
            nodes: List of DialogNode objects
            entry_count: List containing entry count (mutable for recursive update)
            reply_count: List containing reply count (mutable for recursive update)
        """
        for node in nodes:
            if node.is_entry:
                entry_count[0] += 1
            else:
                reply_count[0] += 1

            if node.replies:
                ContextBuilder._count_nodes(node.replies, entry_count, reply_count)

    @staticmethod
    def build_item_context(item_type: str, metadata: Dict[str, Any]) -> str:
        """Build context for an item translation.

        Args:
            item_type: Type of item (dialog, journal, item, etc.)
            metadata: Item metadata

        Returns:
            Context string
        """
        if item_type == "dialog":
            return "Complete dialog tree - maintain context and tone throughout"

        if item_type == "journal":
            category = metadata.get("category", "")
            if category:
                return f"Journal entry in category: {category}"
            return "Journal entry - maintain formal tone"

        if item_type == "item":
            tag = metadata.get("tag", "")
            field = metadata.get("type", "")
            if tag:
                return f"Item ({field}): {tag}"
            return f"Item ({field})"

        if item_type == "creature":
            tag = metadata.get("tag", "")
            if tag:
                return f"Creature: {tag}"
            return "Creature name or description"

        if item_type == "area":
            tag = metadata.get("tag", "")
            if tag:
                return f"Area: {tag}"
            return "Area name or description"

        return ""
