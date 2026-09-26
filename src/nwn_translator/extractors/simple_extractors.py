"""Declarative extractors for single-struct GFF resources.

Areas, trigger/placeable/door/encounter/store blueprints and the module info
each hold a few top-level CExoLocStrings; a table of :class:`FieldSpec` rows
per resource kind describes them.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Sequence, Tuple

from .base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    record_offset,
)


@dataclass(frozen=True)
class FieldSpec:
    """One translatable CExoLocString of a single-struct resource.

    Attributes:
        fields: Candidate GFF labels; the first one with embedded text is used
            for both the text and the patched field record.
        item_suffix: ``item_id`` suffix after ``{tag}_``.
        item_type: Metadata ``type`` of the item.
        context: Prompt context; ``{tag}`` is replaced by the resource tag.
    """

    fields: Tuple[str, ...]
    item_suffix: str
    item_type: str
    context: str


def _name_and_description(
    label: str, kind: str, name_fields: Tuple[str, ...] = ("LocalizedName",)
) -> Tuple[FieldSpec, FieldSpec]:
    """Returns the name and description specs shared by the blueprint kinds."""
    return (
        FieldSpec(
            name_fields,
            "name",
            f"{kind}_name",
            f"{label} name in game (tag: {{tag}}). Translate naturally.",
        ),
        FieldSpec(
            ("Description",), "description", f"{kind}_description", f"{label} description: {{tag}}"
        ),
    )


class SimpleLocalizedExtractor(BaseExtractor):
    """Extractor of the fields listed in :attr:`FIELD_SPECS` from the root struct.

    Attributes:
        CONTENT_TYPE: ``ExtractedContent.content_type`` of the resource kind.
        TAG_FIELD: Root field holding the tag used in item ids and contexts; the
            file stem stands in when the field is absent.
        FIELD_SPECS: Translatable fields in extraction order.
    """

    CONTENT_TYPE: ClassVar[str]
    TAG_FIELD: ClassVar[str] = "Tag"
    FIELD_SPECS: ClassVar[Sequence[FieldSpec]]

    def _should_extract(self, parsed_data: Dict[str, Any]) -> bool:
        """Tells whether the resource carries player-visible text at all."""
        return True

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extracts one item per spec whose field holds embedded text.

        Args:
            file_path: Path of the resource.
            parsed_data: Parsed GFF root struct.

        Returns:
            The extracted items, in :attr:`FIELD_SPECS` order.
        """
        tag = parsed_data.get(self.TAG_FIELD, file_path.stem)
        items: List[TranslatableItem] = []
        specs = self.FIELD_SPECS if self._should_extract(parsed_data) else ()
        for spec in specs:
            for field_name in spec.fields:
                text = extract_local_string(parsed_data.get(field_name, {}))
                if text:
                    items.append(
                        TranslatableItem(
                            text=text,
                            context=spec.context.format(tag=tag),
                            item_id=f"{tag}_{spec.item_suffix}",
                            metadata={
                                "type": spec.item_type,
                                "tag": tag,
                                "record_offset": record_offset(parsed_data, field_name),
                            },
                        )
                    )
                    break
        return ExtractedContent(
            content_type=self.CONTENT_TYPE,
            items=items,
            source_file=file_path,
            metadata={"tag": tag, "item_count": len(items)},
        )


class AreaExtractor(SimpleLocalizedExtractor):
    """Area (``.are``): name and description."""

    CONTENT_TYPE = "area"
    FIELD_SPECS = (
        FieldSpec(("Name",), "name", "area_name", "Location/area name in game world"),
        FieldSpec(("Description",), "description", "area_description", "Area description: {tag}"),
    )


class TriggerExtractor(SimpleLocalizedExtractor):
    """Trigger blueprint (``.utt``): name and description of trap triggers."""

    CONTENT_TYPE = "trigger"
    FIELD_SPECS = _name_and_description("Trigger", "trigger")

    def _should_extract(self, parsed_data: Dict[str, Any]) -> bool:
        """Tells whether the trigger is a trap: scripting triggers are invisible."""
        return bool(parsed_data.get("TrapFlag"))


class PlaceableExtractor(SimpleLocalizedExtractor):
    """Placeable blueprint (``.utp``): name and both descriptions."""

    CONTENT_TYPE = "placeable"
    FIELD_SPECS = (
        *_name_and_description("Placeable", "placeable", ("LocName", "LocalizedName", "Name")),
        FieldSpec(
            ("DescIdentified",),
            "desc_identified",
            "placeable_desc_identified",
            "Placeable identified description: {tag}",
        ),
    )


class DoorExtractor(SimpleLocalizedExtractor):
    """Door blueprint (``.utd``): name and description."""

    CONTENT_TYPE = "door"
    FIELD_SPECS = _name_and_description("Door", "door")


class EncounterExtractor(SimpleLocalizedExtractor):
    """Encounter blueprint (``.ute``): name and description."""

    CONTENT_TYPE = "encounter"
    FIELD_SPECS = _name_and_description("Encounter", "encounter")


class StoreExtractor(SimpleLocalizedExtractor):
    """Store blueprint (``.utm``): name and description."""

    CONTENT_TYPE = "store"
    FIELD_SPECS = _name_and_description("Store", "store", ("LocName", "LocalizedName"))


class ModuleExtractor(SimpleLocalizedExtractor):
    """Module info (``.ifo``): module name and description, tagged by ``Mod_Tag``."""

    CONTENT_TYPE = "module"
    TAG_FIELD = "Mod_Tag"
    FIELD_SPECS = (
        FieldSpec(("Mod_Name",), "mod_name", "module_name", "Module name: {tag}"),
        FieldSpec(
            ("Mod_Description",),
            "mod_description",
            "module_description",
            "Module description: {tag}",
        ),
    )
