"""Content extractors for NWN file formats.

This package contains extractors for various NWN file types that need translation.
"""

from .base import BaseExtractor, ExtractedContent, TranslatableItem, DialogNode, extract_local_string
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
from .module_extractor import ModuleExtractor
from .ncs_extractor import NcsExtractor

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
    "ModuleExtractor",
    "NcsExtractor",
]

# Singleton registry: file extension -> extractor instance
_EXTRACTOR_MAP = {}
for _cls in [
    DialogExtractor,
    JournalExtractor,
    ItemExtractor,
    CreatureExtractor,
    AreaExtractor,
    TriggerExtractor,
    PlaceableExtractor,
    DoorExtractor,
    StoreExtractor,
    ModuleExtractor,
    NcsExtractor,
]:
    _inst = _cls()
    for _ext in _inst.SUPPORTED_TYPES:
        _EXTRACTOR_MAP[_ext] = _inst


def get_extractor_for_file(file_extension: str):
    """Get appropriate extractor for a given file extension.

    Args:
        file_extension: File extension (e.g., ".dlg", ".uti")

    Returns:
        Extractor instance or None if no extractor found
    """
    return _EXTRACTOR_MAP.get(file_extension.lower())
