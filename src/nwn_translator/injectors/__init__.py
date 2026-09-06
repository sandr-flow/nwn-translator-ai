"""Content injectors for writing translated content back to NWN files.

This package contains injectors for various NWN file types.
"""

from .base import BaseInjector, InjectedContent
from .gff_injector import GffInjector
from .ncs_injector import NcsInjector

__all__ = [
    "BaseInjector",
    "InjectedContent",
    "GffInjector",
    "NcsInjector",
]

# Singleton registry: content_type -> injector instance
_INJECTOR_MAP: dict = {}
_INJECTOR_CLASSES: list = [GffInjector, NcsInjector]
for _cls in _INJECTOR_CLASSES:
    _inst = _cls()
    for _ct in getattr(_inst, "SUPPORTED_TYPES", []):
        _INJECTOR_MAP[_ct] = _inst


def get_injector_for_content(content_type: str):
    """Get appropriate injector for a given content type.

    Args:
        content_type: Type of content (dialog, journal, item, etc.)

    Returns:
        Injector instance or None if no injector found
    """
    return _INJECTOR_MAP.get(content_type)
