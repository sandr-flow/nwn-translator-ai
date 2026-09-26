"""Extractor for dialogs (``.dlg``): flat node items and the conversation tree.

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

from .base import (
    BaseExtractor,
    DialogNode,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    list_field,
    record_offset,
)

logger = logging.getLogger(__name__)


def dialog_item_id(stem: str, is_entry: bool, index: object) -> str:
    """Returns the item id of a dialog node.

    Args:
        stem: Dialog resource name without extension.
        is_entry: ``True`` for an ``EntryList`` node, ``False`` for a ``ReplyList`` node.
        index: Position of the node in its list.

    Returns:
        ``{stem}:entry:{index}`` or ``{stem}:reply:{index}``.
    """
    return f"{stem}:{'entry' if is_entry else 'reply'}:{index}"


class DialogExtractor(BaseExtractor):
    """Dialog (``.dlg``): every NPC entry and player reply with text."""

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts one item per dialog node with embedded text.

        Args:
            file_path: Path of the ``.dlg`` resource.
            parsed_data: Parsed GFF root struct.

        Returns:
            All entry items in list order, then all reply items.
        """
        stem = file_path.stem
        entry_list = list_field(parsed_data, "EntryList")
        reply_list = list_field(parsed_data, "ReplyList")
        items: List[TranslatableItem] = []

        for is_entry, nodes in ((True, entry_list), (False, reply_list)):
            for i, node in enumerate(nodes):
                if not isinstance(node, dict):
                    continue
                text = extract_local_string(node.get("Text", {}))
                if not text:
                    continue
                if is_entry:
                    speaker = node.get("Speaker", "")
                    context = (
                        f"Dialog line in {stem}.dlg (speaker: {speaker})"
                        if speaker
                        else f"NPC dialog line in {stem}.dlg"
                    )
                    metadata = {"type": "entry", "index": i, "speaker": speaker}
                else:
                    context = f"Player reply in {stem}.dlg"
                    metadata = {"type": "reply", "index": i}
                metadata["record_offset"] = record_offset(node, "Text")
                items.append(
                    TranslatableItem(
                        text=text,
                        context=context,
                        item_id=dialog_item_id(stem, is_entry, i),
                        metadata=metadata,
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
        """Builds the conversation tree reachable from ``StartingList``.

        This tree is the input of contextual dialog translation. Each entry is
        attached once, on the first path that reaches it (depth-first, in link
        order); later links to it, including back-edges of loops, are dropped.
        The walk is iterative because long cutscene chains would exceed
        Python's recursion limit. Non-struct nodes are skipped with a warning.

        Args:
            parsed_data: Parsed GFF root struct.

        Returns:
            Root nodes in ``StartingList`` order.
        """
        nodes_by_kind: Dict[bool, Dict[Any, Any]] = {
            True: dict(enumerate(list_field(parsed_data, "EntryList"))),
            False: dict(enumerate(list_field(parsed_data, "ReplyList"))),
        }
        tree: List[DialogNode] = []
        visited_entries: Set[Any] = set()

        # Work items: (is_entry, node_id, parent); parent None = root of the tree.
        # A LIFO stack with children pushed in reverse order walks depth-first
        # in link order, and the visited check fires only after the previous
        # sibling's subtree is fully built.
        stack: List[Tuple[bool, Any, Optional[DialogNode]]] = [
            (True, link["Index"], None)
            for link in reversed(list_field(parsed_data, "StartingList"))
            if isinstance(link, dict) and link.get("Index") is not None
        ]
        while stack:
            is_entry, node_id, parent = stack.pop()
            nodes = nodes_by_kind[is_entry]
            if node_id not in nodes:
                continue
            if is_entry:
                if node_id in visited_entries:
                    continue
                visited_entries.add(node_id)
            data = nodes[node_id]
            if not isinstance(data, dict):
                logger.warning(
                    "Dialog %s %s is not a struct (%s); skipping node",
                    "entry" if is_entry else "reply",
                    node_id,
                    type(data).__name__,
                )
                continue
            node = DialogNode(
                node_id=node_id,
                text=extract_local_string(data.get("Text") or {}) or "",
                speaker=data.get("Speaker", "") if is_entry else "Player",
                is_entry=is_entry,
            )
            if parent is None:
                tree.append(node)
            else:
                parent.replies.append(node)

            # Entries link to replies through RepliesList, replies to entries
            # through EntriesList; each link struct carries the target Index.
            links = data.get("RepliesList" if is_entry else "EntriesList") or []
            for link in reversed(links):
                if isinstance(link, dict) and link.get("Index") is not None:
                    stack.append((not is_entry, link["Index"], node))

        return tree
