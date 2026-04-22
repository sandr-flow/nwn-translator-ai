"""Extractor for NWN module info (.ifo) files."""

from .base import SimpleLocalizedExtractor


class ModuleExtractor(SimpleLocalizedExtractor):
    """Extractor for module info (.ifo) files."""

    SUPPORTED_TYPES = [".ifo"]
    CONTENT_TYPE = "module"
    TAG_FIELD = "Mod_Tag"
    FIELD_SPECS = (
        {
            "key": "name",
            "fields": ("Mod_Name",),
            "item_suffix": "mod_name",
            "item_type": "module_name",
            "context": lambda tag: f"Module name: {tag}",
        },
        {
            "key": "description",
            "fields": ("Mod_Description",),
            "item_suffix": "mod_description",
            "item_type": "module_description",
            "context": lambda tag: f"Module description: {tag}",
        },
    )
