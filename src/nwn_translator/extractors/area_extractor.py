"""Simple extractors for NWN area-like GFF resources."""

from typing import Any, Dict

from .base import SimpleLocalizedExtractor


class AreaExtractor(SimpleLocalizedExtractor):
    """Extractor for area (.are) files."""

    SUPPORTED_TYPES = [".are"]
    CONTENT_TYPE = "area"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("Name",),
            "item_suffix": "name",
            "item_type": "area_name",
            "context": "Location/area name in game world",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "area_description",
            "context": lambda tag: f"Area description: {tag}",
        },
    )


class TriggerExtractor(SimpleLocalizedExtractor):
    """Extractor for trigger (.utt) files."""

    SUPPORTED_TYPES = [".utt"]
    CONTENT_TYPE = "trigger"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("LocalizedName",),
            "item_suffix": "name",
            "item_type": "trigger_name",
            "context": lambda tag: f"Trigger name in game (tag: {tag}). Translate naturally.",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "trigger_description",
            "context": lambda tag: f"Trigger description: {tag}",
        },
    )

    def _should_extract(self, parsed_data: Dict[str, Any]) -> bool:
        # Only trap triggers have player-visible names; scripting triggers
        # (invisible regions that fire scripts) should not be translated.
        return bool(parsed_data.get("TrapFlag"))


class PlaceableExtractor(SimpleLocalizedExtractor):
    """Extractor for placeable (.utp) files."""

    SUPPORTED_TYPES = [".utp"]
    CONTENT_TYPE = "placeable"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("LocName", "LocalizedName", "Name"),
            "item_suffix": "name",
            "item_type": "placeable_name",
            "context": lambda tag: f"Placeable name in game (tag: {tag}). Translate naturally.",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "placeable_description",
            "context": lambda tag: f"Placeable description: {tag}",
        },
        {
            "key": "identified",
            "fields": ("DescIdentified",),
            "item_suffix": "desc_identified",
            "item_type": "placeable_desc_identified",
            "context": lambda tag: f"Placeable identified description: {tag}",
            "skip_if_same_as": "description",
        },
    )


class DoorExtractor(SimpleLocalizedExtractor):
    """Extractor for door (.utd) files."""

    SUPPORTED_TYPES = [".utd"]
    CONTENT_TYPE = "door"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("LocalizedName",),
            "item_suffix": "name",
            "item_type": "door_name",
            "context": lambda tag: f"Door name in game (tag: {tag}). Translate naturally.",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "door_description",
            "context": lambda tag: f"Door description: {tag}",
        },
    )


class EncounterExtractor(SimpleLocalizedExtractor):
    """Extractor for encounter blueprint (.ute) files."""

    SUPPORTED_TYPES = [".ute"]
    CONTENT_TYPE = "encounter"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("LocalizedName",),
            "item_suffix": "name",
            "item_type": "encounter_name",
            "context": lambda tag: f"Encounter name in game (tag: {tag}). Translate naturally.",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "encounter_description",
            "context": lambda tag: f"Encounter description: {tag}",
        },
    )


class StoreExtractor(SimpleLocalizedExtractor):
    """Extractor for store (.utm) files."""

    SUPPORTED_TYPES = [".utm"]
    CONTENT_TYPE = "store"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("LocName", "LocalizedName"),
            "item_suffix": "name",
            "item_type": "store_name",
            "context": lambda tag: f"Store name in game (tag: {tag}). Translate naturally.",
        },
        {
            "key": "description",
            "fields": ("Description",),
            "item_suffix": "description",
            "item_type": "store_description",
            "context": lambda tag: f"Store description: {tag}",
        },
    )
