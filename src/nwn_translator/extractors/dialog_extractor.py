"""Dialog extractor for NWN dialog files.

This module handles extraction of dialog trees from .dlg GFF files.
Dialog trees are complex structures with entries, replies, and links between them.

NWN .dlg GFF structure:
    Root fields:
        StartingList  — list of starting entry indices (roots of conversation)
        EntryList     — flat list of all NPC lines  (speaker set per entry)
        ReplyList     — flat list of all player lines

    Each entry in EntryList:
        Text          — CExoLocString with the NPC text
        Speaker       — tag of the speaker creature (empty = owner)
        RepliesList   — list of reply link structs; each has an Index into ReplyList

    Each entry in ReplyList:
        Text          — CExoLocString with the player text
        EntriesList   — list of entry link structs; each has an Index into EntryList
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import BaseExtractor, ExtractedContent, TranslatableItem, DialogNode

logger = logging.getLogger(__name__)


class DialogExtractor(BaseExtractor):
    """Extractor for dialog (.dlg) files."""

    SUPPORTED_TYPES = [".dlg"]

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract dialog content from a .dlg file.

        Produces one TranslatableItem per dialog node (entry or reply) that
        contains non-empty text, with a stable item_id for round-tripping.

        Args:
            file_path: Path to the .dlg file
            parsed_data: Parsed GFF data from gff_to_dict

        Returns:
            ExtractedContent with one TranslatableItem per text node
        """
        entry_list = self._get_list_value(parsed_data, "EntryList")
        reply_list = self._get_list_value(parsed_data, "ReplyList")

        items: List[TranslatableItem] = []
        stem = file_path.stem

        record_offsets = parsed_data.get("_record_offsets", {})

        # Extract all entry texts
        for i, entry in enumerate(entry_list):
            if not isinstance(entry, dict):
                continue
            text = self._extract_text_from_local_string(entry.get("Text", {}))
            if not text:
                continue
            speaker = entry.get("Speaker", "")
            items.append(
                TranslatableItem(
                    text=text,
                    context=(
                        f"Dialog line in {stem}.dlg (speaker: {speaker})"
                        if speaker
                        else f"NPC dialog line in {stem}.dlg"
                    ),
                    item_id=f"{stem}:entry:{i}",
                    location=str(file_path),
                    metadata={
                        "type": "entry",
                        "index": i,
                        "speaker": speaker,
                        "record_offset": (
                            entry.get("_record_offsets", {}).get("Text", 0)
                            if isinstance(entry.get("_record_offsets"), dict)
                            else 0
                        ),
                    },
                )
            )

        # Extract all reply texts
        for i, reply in enumerate(reply_list):
            if not isinstance(reply, dict):
                continue
            text = self._extract_text_from_local_string(reply.get("Text", {}))
            if not text:
                continue
            items.append(
                TranslatableItem(
                    text=text,
                    context=f"Player reply in {stem}.dlg",
                    item_id=f"{stem}:reply:{i}",
                    location=str(file_path),
                    metadata={
                        "type": "reply",
                        "index": i,
                        "record_offset": (
                            reply.get("_record_offsets", {}).get("Text", 0)
                            if isinstance(reply.get("_record_offsets"), dict)
                            else 0
                        ),
                    },
                )
            )

        return ExtractedContent(
            content_type="dialog",
            items=items,
            source_file=file_path,
            metadata={
                "entry_count": len(entry_list),
                "reply_count": len(reply_list),
                "text_node_count": len(items),
            },
        )

    def build_dialog_tree(self, parsed_data: Dict[str, Any]) -> List[DialogNode]:
        """Build a hierarchical dialog tree from flat GFF data.

        Useful for generating a human-readable dialog preview, but extraction
        uses the flat approach (see extract()) which is safer for translation.

        The walk is iterative: recursion depth would otherwise scale with the
        conversation length, and long cutscene chains can exceed Python's
        recursion limit. Non-struct entries/replies are skipped with a warning.

        Args:
            parsed_data: Parsed GFF data

        Returns:
            List of root DialogNode objects reachable from StartingList
        """
        entry_list = self._get_list_value(parsed_data, "EntryList")
        reply_list = self._get_list_value(parsed_data, "ReplyList")
        starting_list = self._get_list_value(parsed_data, "StartingList")

        # Index by position
        entries: Dict[int, Dict[str, Any]] = {i: e for i, e in enumerate(entry_list)}
        replies: Dict[int, Dict[str, Any]] = {i: r for i, r in enumerate(reply_list)}

        tree: List[DialogNode] = []
        visited_entries: Set[int] = set()

        # Work items: (is_entry, node_id, parent); parent None = root of the tree.
        # A LIFO stack with children pushed in reverse order reproduces the
        # depth-first order of the recursive walk, including the visited check
        # firing only after the previous sibling's subtree is fully built.
        stack: List[Tuple[bool, Any, Optional[DialogNode]]] = []
        for link in reversed(starting_list):
            if not isinstance(link, dict):
                continue
            entry_idx = link.get("Index")
            if entry_idx is None:
                # Try direct integer (some tool versions store index directly)
                continue
            stack.append((True, entry_idx, None))

        while stack:
            is_entry, node_id, parent = stack.pop()

            if is_entry:
                if node_id not in entries or node_id in visited_entries:
                    continue
                visited_entries.add(node_id)
                data = entries[node_id]
                if not isinstance(data, dict):
                    logger.warning(
                        "Dialog entry %s is not a struct (%s); skipping node",
                        node_id,
                        type(data).__name__,
                    )
                    continue
                node = DialogNode(
                    node_id=node_id,
                    text=self._extract_text_from_local_string(data.get("Text") or {}) or "",
                    speaker=data.get("Speaker", ""),
                    is_entry=True,
                    metadata={"type": "entry"},
                )
                # Each entry has a RepliesList of link structs: {Index: <reply_index>, ...}
                links = data.get("RepliesList") or []
            else:
                if node_id not in replies:
                    continue
                data = replies[node_id]
                if not isinstance(data, dict):
                    logger.warning(
                        "Dialog reply %s is not a struct (%s); skipping node",
                        node_id,
                        type(data).__name__,
                    )
                    continue
                node = DialogNode(
                    node_id=node_id,
                    text=self._extract_text_from_local_string(data.get("Text") or {}) or "",
                    speaker="Player",
                    is_entry=False,
                    metadata={"type": "reply"},
                )
                # Each reply has an EntriesList of link structs: {Index: <entry_index>, ...}
                links = data.get("EntriesList") or []

            if parent is None:
                tree.append(node)
            else:
                parent.replies.append(node)

            for link in reversed(links):
                if not isinstance(link, dict):
                    continue
                child_idx = link.get("Index")
                if child_idx is None:
                    continue
                stack.append((not is_entry, child_idx, node))

        return tree
