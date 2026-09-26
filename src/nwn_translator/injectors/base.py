"""Injection result and the helpers shared by the injectors."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol, Sequence, Tuple

from ..extractors.base import TranslatableItem, Translations


@dataclass
class InjectedContent:
    """Result of writing translations into one resource.

    Attributes:
        source_file: Patched resource.
        modified: Whether the file was changed.
        items_updated: Number of patched occurrences.
        metadata: ``{"type": content type}`` plus failure details; it is
            recorded in the ``injection_result`` translation-log event.
    """

    source_file: Path
    modified: bool
    items_updated: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class Injector(Protocol):
    """Write the translations of extracted items back into a resource file."""

    def __call__(
        self,
        file_path: Path,
        items: Sequence[TranslatableItem],
        translations: Translations,
        *,
        content_type: str,
        text_encoding: str,
        source_encoding: Optional[str],
    ) -> InjectedContent:
        """Patches *file_path* in place.

        Args:
            file_path: Resource to patch.
            items: Items extracted from the file, in extraction order.
            translations: Translated text by occurrence.
            content_type: Content type of the extraction, reported back.
            text_encoding: Code page of the written strings.
            source_encoding: Code page used to decode the file at extraction
                (None when detected).

        Returns:
            The injection result.
        """


def changed_translations(
    items: Sequence[TranslatableItem], translations: Translations
) -> Iterator[Tuple[TranslatableItem, str]]:
    """Yields ``(item, translation)`` for items whose translation changes the text.

    Args:
        items: Extracted items, in extraction order.
        translations: Translated text by occurrence.

    Yields:
        Items with a translation that differs from their source text.
    """
    for item in items:
        translated = translations.get(item.key)
        if translated is not None and translated != item.text:
            yield item, translated
