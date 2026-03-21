"""Base extractor interface and data structures.

This module defines the abstract interface that all extractors must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ExtractedContent:
    """Container for extracted content from NWN files.

    Attributes:
        content_type: Type of content (dialog, journal, item, etc.)
        items: List of extracted translatable items
        source_file: Path to source file
        metadata: Additional metadata about the extraction
    """
    content_type: str
    items: List["TranslatableItem"]
    source_file: Path
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        """Return number of items extracted."""
        return len(self.items)

    def __iter__(self):
        """Iterate over extracted items."""
        return iter(self.items)


@dataclass
class TranslatableItem:
    """Represents a single translatable item extracted from a file.

    Attributes:
        text: The actual text to translate
        context: Context information for translation
        item_id: Unique identifier for the item
        location: Location information (e.g., dialog node ID)
        metadata: Additional metadata
    """
    text: str
    context: Optional[str] = None
    item_id: Optional[str] = None
    location: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def has_text(self) -> bool:
        """Check if item has translatable text."""
        return bool(self.text and isinstance(self.text, str) and self.text.strip())


@dataclass
class DialogNode:
    """Represents a node in a dialog tree.

    Attributes:
        node_id: Unique ID for this node
        text: The text content
        speaker: Speaker identifier
        is_entry: Whether this is a starting entry (True) or reply (False)
        replies: List of replies (for entries) or next entries (for replies)
        metadata: Additional metadata
    """
    node_id: int
    text: str
    speaker: Optional[str] = None
    is_entry: bool = True
    replies: List["DialogNode"] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def extract_local_string(text_data: Dict[str, Any]) -> Optional[str]:
    """Extract text from a LocalString (CExoLocString) structure.

    Returns the embedded Value when non-empty, regardless of whether
    a StrRef is also set. This matches NWN editor behaviour where text
    is embedded even when a TLK reference exists.

    Args:
        text_data: GFF LocalString dictionary with StrRef and Value keys

    Returns:
        Extracted text string, or None if no text is available
    """
    if not isinstance(text_data, dict):
        return None

    value = text_data.get("Value", "")
    return value if value else None


class BaseExtractor(ABC):
    """Abstract base class for content extractors.

    All extractors must implement this interface to ensure consistent behavior.
    """

    def __init__(self):
        """Initialize the extractor."""

    @abstractmethod
    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type.

        Args:
            file_type: File extension (e.g., ".dlg", ".uti")

        Returns:
            True if this extractor can handle this file type
        """
        pass

    @abstractmethod
    def extract(self, file_path: Path, gff_data: Dict[str, Any]) -> ExtractedContent:
        """Extract translatable content from a file.

        Args:
            file_path: Path to the file
            gff_data: Parsed GFF data dictionary

        Returns:
            ExtractedContent with translatable items
        """
        pass

    def _extract_text_from_local_string(
        self,
        text_data: Dict[str, Any]
    ) -> Optional[str]:
        """Delegate to module-level :func:`extract_local_string`."""
        return extract_local_string(text_data)

    def _safe_get(
        self,
        data: Dict[str, Any],
        key: str,
        default: Any = None
    ) -> Any:
        """Safely get a value from a dictionary.

        Args:
            data: Dictionary to get value from
            key: Key to retrieve
            default: Default value if key not found

        Returns:
            Value or default
        """
        return data.get(key, default) if isinstance(data, dict) else default

    def _get_list_value(
        self,
        data: Dict[str, Any],
        key: str
    ) -> List[Any]:
        """Get a list value from dictionary, returning empty list if not found.

        Args:
            data: Dictionary to get value from
            key: Key to retrieve

        Returns:
            List value or empty list
        """
        value = self._safe_get(data, key, [])
        return value if isinstance(value, list) else []

    def _make_name_item(
        self,
        gff_data: Dict[str, Any],
        file_path: Path,
        name_field: str,
        context_prefix: str,
        item_type: str,
    ) -> Optional["TranslatableItem"]:
        """Build a single TranslatableItem from a CExoLocString name field.

        This helper centralises the repeated pattern found across simple
        object extractors (trigger, placeable, door, store, …):
        read a localised-string field, extract text, wrap in a
        TranslatableItem.  Returns *None* when no text is found.

        Args:
            gff_data: Parsed GFF data dict.
            file_path: Source file path (used to derive tag fallback).
            name_field: GFF field key for the CExoLocString (e.g. "Name").
            context_prefix: Human-readable label for the context string
                (e.g. "Trigger", "Door").
            item_type: Metadata ``type`` value (e.g. "trigger_name").

        Returns:
            TranslatableItem or None if no text is available.
        """
        tag = gff_data.get("Tag", file_path.stem)
        text_obj = gff_data.get(name_field, {})
        text = self._extract_text_from_local_string(text_obj)
        if not text:
            return None
        return TranslatableItem(
            text=text,
            context=f"{context_prefix}: {tag}",
            item_id=f"{tag}_name",
            location=str(file_path),
            metadata={"type": item_type, "tag": tag},
        )
