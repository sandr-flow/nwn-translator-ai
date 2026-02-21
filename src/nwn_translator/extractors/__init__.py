"""Content extractors for NWN file formats.

This package contains extractors for various NWN file types that need translation.
"""

from .base import BaseExtractor, ExtractedContent, TranslatableItem, DialogNode
from .dialog_extractor import DialogExtractor
from .journal_extractor import JournalExtractor
from .item_extractor import ItemExtractor
from .creature_extractor import CreatureExtractor
from .area_extractor import (
    AreaExtractor,
    TriggerExtractor,
    PlaceableExtractor,
    DoorExtractor,
    StoreExtractor,
)

__all__ = [
    "BaseExtractor",
    "ExtractedContent",
    "TranslatableItem",
    "DialogNode",
    "DialogExtractor",
    "JournalExtractor",
    "ItemExtractor",
    "CreatureExtractor",
    "AreaExtractor",
    "TriggerExtractor",
    "PlaceableExtractor",
    "DoorExtractor",
    "StoreExtractor",
]

# Registry of all available extractors
EXTRACTOR_CLASSES = [
    DialogExtractor,
    JournalExtractor,
    ItemExtractor,
    CreatureExtractor,
    AreaExtractor,
    TriggerExtractor,
    PlaceableExtractor,
    DoorExtractor,
    StoreExtractor,
]


def get_extractor_for_file(file_extension: str):
    """Get appropriate extractor for a given file extension.

    Args:
        file_extension: File extension (e.g., ".dlg", ".uti")

    Returns:
        Extractor instance or None if no extractor found
    """
    for extractor_class in EXTRACTOR_CLASSES:
        extractor = extractor_class()
        if extractor.can_extract(file_extension):
            return extractor
    return None
