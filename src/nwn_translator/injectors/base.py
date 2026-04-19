"""Base injector interface and data structures.

This module defines the abstract interface that all injectors must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class InjectedContent:
    """Result of content injection.

    Attributes:
        source_file: Path to source file
        modified: Whether any changes were made
        items_updated: Number of items updated
        metadata: Additional metadata
    """

    source_file: Path
    modified: bool
    items_updated: int
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseInjector(ABC):
    """Abstract base class for content injectors.

    All injectors must implement this interface to ensure consistent behavior.
    """

    def __init__(self):
        """Initialize the injector."""

    @abstractmethod
    def can_inject(self, content_type: str) -> bool:
        """Check if this injector can handle the given content type.

        Args:
            content_type: Type of content (dialog, journal, item, etc.)

        Returns:
            True if this injector can handle this content type
        """
        pass

    @abstractmethod
    def inject(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        translations: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InjectedContent:
        """Inject translated content back into GFF data.

        Args:
            file_path: Path to the file
            parsed_data: Original GFF data dictionary
            translations: Dictionary mapping original text to translated text
            metadata: Additional metadata about the translation

        Returns:
            InjectedContent with injection results
        """
        pass
