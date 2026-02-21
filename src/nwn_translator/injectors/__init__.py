"""Content injectors for writing translated content back to NWN files.

This package contains injectors for various NWN file types.
"""

from .base import BaseInjector, InjectedContent
from .dialog_injector import (
    DialogInjector,
    JournalInjector,
    GenericInjector,
)

__all__ = [
    "BaseInjector",
    "InjectedContent",
    "DialogInjector",
    "JournalInjector",
    "GenericInjector",
]

# Registry of all available injectors
INJECTOR_CLASSES = [
    DialogInjector,
    JournalInjector,
    GenericInjector,
]


def get_injector_for_content(content_type: str):
    """Get appropriate injector for a given content type.

    Args:
        content_type: Type of content (dialog, journal, item, etc.)

    Returns:
        Injector instance or None if no injector found
    """
    for injector_class in INJECTOR_CLASSES:
        injector = injector_class()
        if injector.can_inject(content_type):
            return injector
    return None
