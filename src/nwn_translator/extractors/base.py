"""Data types shared by extraction, translation and injection.

Extractors turn a parsed resource into :class:`TranslatableItem` rows grouped in
an :class:`ExtractedContent`. Each item is addressed by its
:data:`Occurrence` — the archive resource name plus the extractor's stable
``item_id`` — which keys translations, failures, persistence and injection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

#: Address of one extracted string: ``(resource file name, item_id)``.
Occurrence = tuple[str, str]
#: Translated text by occurrence.
Translations = Dict[Occurrence, str]


def occurrence_key(resource: Union[str, Path], item_id: str) -> Occurrence:
    """Returns ``(file name, item_id)``: an occurrence address independent of text or Tag."""
    return Path(resource).name, item_id


@dataclass
class ExtractedContent:
    """Translatable items extracted from one resource.

    Attributes:
        content_type: Resource kind label (``dialog``, ``item``, …), reported on injection.
        items: Extracted items in resource order, which is also the GFF patch order.
        source_file: Path of the extracted resource.
        metadata: Resource-level details (counts, tags), kept for artifacts.
    """

    content_type: str
    items: List["TranslatableItem"]
    source_file: Path
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fills in the location of every item that has none."""
        for item in self.items:
            if not item.location:
                item.location = str(self.source_file)

    def __len__(self) -> int:
        """Returns the number of extracted items."""
        return len(self.items)

    def __iter__(self) -> Iterator["TranslatableItem"]:
        """Iterates over the extracted items."""
        return iter(self.items)


@dataclass
class TranslatableItem:
    """One translatable string occurrence.

    Attributes:
        text: Source text.
        context: Prompt context describing where the text appears.
        item_id: Stable id, unique within the resource.
        location: Resource path; :class:`ExtractedContent` fills it in.
        metadata: Item details for batching, prompts and injection (``type``,
            ``record_offset`` for GFF fields, ``offset`` for scripts, …).
    """

    text: str
    context: Optional[str] = None
    item_id: Optional[str] = None
    location: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Replaces a ``None`` metadata with an empty dict."""
        if self.metadata is None:
            self.metadata = {}

    def has_text(self) -> bool:
        """Tells whether :attr:`text` is a string with a non-whitespace character."""
        return bool(self.text and isinstance(self.text, str) and self.text.strip())

    @property
    def key(self) -> Occurrence:
        """Stable address used for results, failures, persistence, and injection.

        Raises:
            ValueError: If the item has no location or no item_id.
        """
        if not self.location or not self.item_id:
            raise ValueError("Translation occurrences require a resource and item_id")
        return occurrence_key(self.location, self.item_id)


@dataclass
class DialogNode:
    """A node of a dialog tree.

    Attributes:
        node_id: Index of the node in ``EntryList`` or ``ReplyList``.
        text: Node text (empty when the node has none).
        speaker: Speaker tag for entries (empty for the dialog owner),
            ``"Player"`` for replies.
        is_entry: ``True`` for NPC entries, ``False`` for player replies.
        replies: Child nodes (replies of an entry, entries following a reply).
    """

    node_id: int
    text: str
    speaker: Optional[str] = None
    is_entry: bool = True
    replies: List["DialogNode"] = field(default_factory=list)


def extract_local_string(text_data: Any) -> Optional[str]:
    """Returns the embedded ``Value`` of a parsed CExoLocString, or ``None`` when empty.

    The embedded text wins even when a StrRef is also set, as in the NWN toolset;
    StrRef-only strings are left to the player's ``dialog.tlk``.
    """
    if not isinstance(text_data, dict):
        return None
    return text_data.get("Value", "") or None


def record_offset(struct: Dict[str, Any], field_name: str) -> int:
    """Returns the file offset of *field_name*'s field record in *struct*, 0 when unknown."""
    offset: int = struct.get("_record_offsets", {}).get(field_name, 0)
    return offset


def list_field(struct: Any, key: str) -> List[Any]:
    """Returns the list under *key* of a parsed struct; ``[]`` for anything else."""
    value = struct.get(key, []) if isinstance(struct, dict) else []
    return value if isinstance(value, list) else []


class BaseExtractor(ABC):
    """Base class of the extractors: selects the translatable strings of one resource kind."""

    @abstractmethod
    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts the translatable items of a resource.

        Args:
            file_path: Path of the resource.
            parsed_data: The parsed GFF dict, or for scripts the dict of the NCS loader.
        """
