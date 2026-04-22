"""Base extractor interface and data structures.

This module defines the abstract interface that all extractors must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


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


ContextBuilder = Union[str, Callable[[str], str]]


class BaseExtractor(ABC):
    """Abstract base class for content extractors.

    All extractors must implement this interface to ensure consistent behavior.
    """

    def __init__(self):
        """Initialize the extractor."""

    def can_extract(self, file_type: str) -> bool:
        """Check if this extractor can handle the given file type.

        Args:
            file_type: File extension (e.g., ".dlg", ".uti")

        Returns:
            True if this extractor can handle this file type
        """
        return file_type.lower() in getattr(self, "SUPPORTED_TYPES", [])

    @abstractmethod
    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract translatable content from a file.

        Args:
            file_path: Path to the file
            parsed_data: Parsed GFF data dictionary

        Returns:
            ExtractedContent with translatable items
        """
        pass

    def _extract_text_from_local_string(self, text_data: Dict[str, Any]) -> Optional[str]:
        """Delegate to module-level :func:`extract_local_string`."""
        return extract_local_string(text_data)

    def _safe_get(self, data: Dict[str, Any], key: str, default: Any = None) -> Any:
        """Safely get a value from a dictionary.

        Args:
            data: Dictionary to get value from
            key: Key to retrieve
            default: Default value if key not found

        Returns:
            Value or default
        """
        return data.get(key, default) if isinstance(data, dict) else default

    def _get_list_value(self, data: Dict[str, Any], key: str) -> List[Any]:
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
        parsed_data: Dict[str, Any],
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
            parsed_data: Parsed GFF data dict.
            file_path: Source file path (used to derive tag fallback).
            name_field: GFF field key for the CExoLocString (e.g. "Name").
            context_prefix: Human-readable label for the context string
                (e.g. "Trigger", "Door").
            item_type: Metadata ``type`` value (e.g. "trigger_name").

        Returns:
            TranslatableItem or None if no text is available.
        """
        tag = parsed_data.get("Tag", file_path.stem)
        text_obj = parsed_data.get(name_field, {})
        text = self._extract_text_from_local_string(text_obj)
        if not text:
            return None
        return TranslatableItem(
            text=text,
            context=f"{context_prefix} name in game (tag: {tag}). Translate naturally.",
            item_id=f"{tag}_name",
            location=str(file_path),
            metadata={"type": item_type, "tag": tag},
        )

    def _first_localized_text(
        self,
        parsed_data: Dict[str, Any],
        field_names: Sequence[str],
    ) -> Optional[str]:
        """Return the first non-empty localized string among *field_names*."""
        for field_name in field_names:
            text = self._extract_text_from_local_string(parsed_data.get(field_name, {}))
            if text:
                return text
        return None


class SimpleLocalizedExtractor(BaseExtractor):
    """Declarative extractor for simple single-struct GFF resources."""

    CONTENT_TYPE = ""
    TAG_FIELD = "Tag"
    FIELD_SPECS: Sequence[Dict[str, Any]] = ()

    def _should_extract(self, parsed_data: Dict[str, Any]) -> bool:
        """Return whether extraction should proceed for *parsed_data*."""
        return True

    def _build_context(self, context: ContextBuilder, tag: str) -> str:
        return context(tag) if callable(context) else context

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract items described by :attr:`FIELD_SPECS`."""
        tag = parsed_data.get(self.TAG_FIELD, file_path.stem)
        items: List[TranslatableItem] = []
        extracted_by_key: Dict[str, str] = {}

        if not self._should_extract(parsed_data):
            return ExtractedContent(
                content_type=self.CONTENT_TYPE,
                items=items,
                source_file=file_path,
                metadata={"tag": tag, "item_count": 0},
            )

        for spec in self.FIELD_SPECS:
            key = str(spec.get("key") or spec["item_suffix"])
            fields_raw = spec.get("fields", ())
            fields = (fields_raw,) if isinstance(fields_raw, str) else tuple(fields_raw)
            text = self._first_localized_text(parsed_data, fields)
            if not text:
                continue

            skip_if_same_as = spec.get("skip_if_same_as")
            if skip_if_same_as and text == extracted_by_key.get(str(skip_if_same_as)):
                continue

            items.append(
                TranslatableItem(
                    text=text,
                    context=self._build_context(spec["context"], tag),
                    item_id=f"{tag}_{spec['item_suffix']}",
                    location=str(file_path),
                    metadata={
                        "type": spec["item_type"],
                        "tag": tag,
                    },
                )
            )
            extracted_by_key[key] = text

        return ExtractedContent(
            content_type=self.CONTENT_TYPE,
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )
