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

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import BaseExtractor, ExtractedContent, TranslatableItem, DialogNode


class DialogExtractor(BaseExtractor):
    """Extractor for dialog (.dlg) files."""

    SUPPORTED_TYPES = [".dlg"]

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type."""
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        gff_data: Dict[str, Any]
    ) -> ExtractedContent:
        """Extract dialog content from a .dlg file.

        Produces one TranslatableItem per dialog node (entry or reply) that
        contains non-empty text, with a stable item_id for round-tripping.

        Args:
            file_path: Path to the .dlg file
            gff_data: Parsed GFF data from gff_to_dict

        Returns:
            ExtractedContent with one TranslatableItem per text node
        """
        entry_list = self._get_list_value(gff_data, "EntryList")
        reply_list = self._get_list_value(gff_data, "ReplyList")

        items: List[TranslatableItem] = []
        stem = file_path.stem

        record_offsets = gff_data.get("_record_offsets", {})
        
        # Extract all entry texts
        for i, entry in enumerate(entry_list):
            if not isinstance(entry, dict):
                continue
            text = self._extract_text_from_local_string(entry.get("Text", {}))
            if not text:
                continue
            speaker = entry.get("Speaker", "")
            items.append(TranslatableItem(
                text=text,
                context=f"NPC line (speaker: '{speaker}')" if speaker else "NPC line",
                item_id=f"{stem}:entry:{i}",
                location=str(file_path),
                metadata={
                    "type": "entry",
                    "index": i,
                    "speaker": speaker,
                    "record_offset": entry.get("_record_offsets", {}).get("Text", 0) if isinstance(entry.get("_record_offsets"), dict) else 0,
                }
            ))

        # Extract all reply texts
        for i, reply in enumerate(reply_list):
            if not isinstance(reply, dict):
                continue
            text = self._extract_text_from_local_string(reply.get("Text", {}))
            if not text:
                continue
            items.append(TranslatableItem(
                text=text,
                context="Player reply",
                item_id=f"{stem}:reply:{i}",
                location=str(file_path),
                metadata={
                    "type": "reply",
                    "index": i,
                    "record_offset": reply.get("_record_offsets", {}).get("Text", 0) if isinstance(reply.get("_record_offsets"), dict) else 0,
                }
            ))

        return ExtractedContent(
            content_type="dialog",
            items=items,
            source_file=file_path,
            metadata={
                "entry_count": len(entry_list),
                "reply_count": len(reply_list),
                "text_node_count": len(items),
            }
        )

    def build_dialog_tree(self, gff_data: Dict[str, Any]) -> List[DialogNode]:
        """Build a hierarchical dialog tree from flat GFF data.

        Useful for generating a human-readable dialog preview, but extraction
        uses the flat approach (see extract()) which is safer for translation.

        Args:
            gff_data: Parsed GFF data

        Returns:
            List of root DialogNode objects reachable from StartingList
        """
        entry_list = self._get_list_value(gff_data, "EntryList")
        reply_list = self._get_list_value(gff_data, "ReplyList")
        starting_list = self._get_list_value(gff_data, "StartingList")

        # Index by position
        entries: Dict[int, Dict[str, Any]] = {i: e for i, e in enumerate(entry_list)}
        replies: Dict[int, Dict[str, Any]] = {i: r for i, r in enumerate(reply_list)}

        tree: List[DialogNode] = []
        visited_entries: Set[int] = set()

        for link in starting_list:
            if not isinstance(link, dict):
                continue
            entry_idx = link.get("Index")
            if entry_idx is None:
                # Try direct integer (some tool versions store index directly)
                continue
            if entry_idx in entries and entry_idx not in visited_entries:
                node = self._build_entry_node(entry_idx, entries, replies, visited_entries)
                tree.append(node)

        return tree

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_entry_node(
        self,
        entry_id: int,
        entries: Dict[int, Dict[str, Any]],
        replies: Dict[int, Dict[str, Any]],
        visited: Set[int],
    ) -> DialogNode:
        """Build a DialogNode for an NPC entry.

        Args:
            entry_id: Index into entries dict
            entries: All entry structs keyed by index
            replies: All reply structs keyed by index
            visited: Set of already-visited entry indices (cycle guard)

        Returns:
            DialogNode for this entry
        """
        visited.add(entry_id)
        entry_data = entries.get(entry_id, {})

        text = self._extract_text_from_local_string(entry_data.get("Text", {})) or ""
        speaker = entry_data.get("Speaker", "")

        node = DialogNode(
            node_id=entry_id,
            text=text,
            speaker=speaker,
            is_entry=True,
            metadata={"type": "entry"},
        )

        # Each entry has a RepliesList of link structs: {Index: <reply_index>, ...}
        for link in (entry_data.get("RepliesList") or []):
            if not isinstance(link, dict):
                continue
            reply_idx = link.get("Index")
            if reply_idx is not None and reply_idx in replies:
                reply_node = self._build_reply_node(reply_idx, replies, entries, visited)
                node.replies.append(reply_node)

        return node

    def _build_reply_node(
        self,
        reply_id: int,
        replies: Dict[int, Dict[str, Any]],
        entries: Dict[int, Dict[str, Any]],
        visited: Set[int],
    ) -> DialogNode:
        """Build a DialogNode for a player reply.

        Args:
            reply_id: Index into replies dict
            replies: All reply structs keyed by index
            entries: All entry structs keyed by index
            visited: Set of already-visited entry indices (cycle guard)

        Returns:
            DialogNode for this reply
        """
        reply_data = replies.get(reply_id, {})

        text = self._extract_text_from_local_string(reply_data.get("Text", {})) or ""

        node = DialogNode(
            node_id=reply_id,
            text=text,
            speaker="Player",
            is_entry=False,
            metadata={"type": "reply"},
        )

        # Each reply has an EntriesList of link structs: {Index: <entry_index>, ...}
        for link in (reply_data.get("EntriesList") or []):
            if not isinstance(link, dict):
                continue
            entry_idx = link.get("Index")
            if entry_idx is not None and entry_idx in entries and entry_idx not in visited:
                entry_node = self._build_entry_node(entry_idx, entries, replies, visited)
                node.replies.append(entry_node)

        return node
