"""Dialog injector for NWN dialog files.

This module handles injection of translated dialog content back into .dlg GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

from .base import BaseInjector, InjectedContent
from ..file_handlers.gff_patcher import GFFPatcher, GFFPatchError

logger = logging.getLogger(__name__)


def _module_text_encoding(metadata: Optional[Dict[str, Any]]) -> str:
    return (metadata or {}).get("module_text_encoding") or "cp1251"


class DialogInjector(BaseInjector):
    """Injector for dialog (.dlg) files."""

    SUPPORTED_TYPES = ["dialog"]

    def inject(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        translations: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InjectedContent:
        """Inject translated dialog content back into GFF data.

        Args:
            file_path: Path to the .dlg file
            parsed_data: Original GFF data
            translations: Dictionary mapping original text to translated text
            metadata: Optional metadata (may contain dialog tree)

        Returns:
            InjectedContent with injection results
        """
        items_updated = 0
        modified = False

        # Get entry and reply lists
        entry_list = parsed_data.get("EntryList", [])
        reply_list = parsed_data.get("ReplyList", [])

        try:
            patcher = GFFPatcher(file_path, text_encoding=_module_text_encoding(metadata))
        except Exception as e:
            logger.error(f"Failed to initialize GFFPatcher for {file_path}: {e}")
            return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        patches: List[Tuple[int, str]] = []

        # Update entries
        for entry in entry_list:
            if isinstance(entry, dict):
                text_obj = entry.get("Text", {})
                if isinstance(text_obj, dict):
                    original_text = text_obj.get("Value", "")
                    if original_text and original_text in translations:
                        translated_text = translations[original_text]
                        if translated_text != original_text:
                            rec_offset = entry.get("_record_offsets", {}).get("Text", 0)
                            if rec_offset > 0:
                                patches.append((rec_offset, translated_text))
                                items_updated += 1
                                modified = True

        # Update replies
        for reply in reply_list:
            if isinstance(reply, dict):
                text_obj = reply.get("Text", {})
                if isinstance(text_obj, dict):
                    original_text = text_obj.get("Value", "")
                    if original_text and original_text in translations:
                        translated_text = translations[original_text]
                        if translated_text != original_text:
                            rec_offset = reply.get("_record_offsets", {}).get("Text", 0)
                            if rec_offset > 0:
                                patches.append((rec_offset, translated_text))
                                items_updated += 1
                                modified = True

        if patches:
            try:
                patcher.patch_multiple(patches)
            except GFFPatchError as e:
                logger.error(f"Failed to patch dialog strings in {file_path}: {e}")
                return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        return InjectedContent(
            source_file=file_path,
            modified=modified,
            items_updated=items_updated,
            metadata={
                "type": "dialog",
                "entry_count": len(entry_list),
                "reply_count": len(reply_list),
            },
        )


class JournalInjector(BaseInjector):
    """Injector for journal (.jrl) files."""

    SUPPORTED_TYPES = ["journal"]

    def inject(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
        translations: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InjectedContent:
        """Inject translated journal content back into GFF data.

        Args:
            file_path: Path to the .jrl file
            parsed_data: Original GFF data
            translations: Dictionary mapping original text to translated text
            metadata: Optional metadata

        Returns:
            InjectedContent with injection results
        """
        items_updated = 0
        modified = False

        try:
            patcher = GFFPatcher(file_path, text_encoding=_module_text_encoding(metadata))
        except Exception as e:
            logger.error(f"Failed to load GFFPatcher for {file_path}: {e}")
            return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        patches: List[Tuple[int, str]] = []

        # Update categories (GFF field is "Categories", not "CategoriesList")
        categories = parsed_data.get("Categories", [])
        for category in categories:
            if isinstance(category, dict):
                # Update category name
                name_obj = category.get("Name", {})
                if isinstance(name_obj, dict):
                    original_text = name_obj.get("Value", "")
                    if original_text and original_text in translations:
                        translated_text = translations[original_text]
                        if translated_text != original_text:
                            rec_offset = category.get("_record_offsets", {}).get("Name", 0)
                            if rec_offset > 0:
                                patches.append((rec_offset, translated_text))
                                items_updated += 1
                                modified = True

                # Update entries nested inside each category
                entries = category.get("EntryList", [])
                for entry in entries:
                    if isinstance(entry, dict):
                        text_obj = entry.get("Text", {})
                        if isinstance(text_obj, dict):
                            original_text = text_obj.get("Value", "")
                            if original_text and original_text in translations:
                                translated_text = translations[original_text]
                                if translated_text != original_text:
                                    rec_offset = entry.get("_record_offsets", {}).get("Text", 0)
                                    if rec_offset > 0:
                                        patches.append((rec_offset, translated_text))
                                        items_updated += 1
                                        modified = True

        if patches:
            try:
                patcher.patch_multiple(patches)
            except GFFPatchError as e:
                logger.error(f"Failed to patch journal in {file_path}: {e}")
                return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        return InjectedContent(
            source_file=file_path,
            modified=modified,
            items_updated=items_updated,
            metadata={
                "type": "journal",
                "category_count": len(categories),
            },
        )


class GenericInjector(BaseInjector):
    """Generic injector for simple item files.

    This handles items, creatures, areas, placeables, doors, and stores.
    """

    SUPPORTED_TYPES = [
        "item",
        "creature",
        "area",
        "trigger",
        "placeable",
        "door",
        "encounter",
        "store",
        "module",
    ]

    # Mapping of content types to GFF field names
    FIELD_MAP = {
        "item": {
            "name": "LocalizedName",
            "description": "Description",
            "identified": "DescIdentified",
        },
        "creature": {
            "first_name": "FirstName",
            "last_name": "LastName",
            "description": "Description",
        },
        "area": {
            "name": "Name",
            "description": "Description",
        },
        "trigger": {
            "name": "LocalizedName",
            "description": "Description",
        },
        # Placeable name is handled by the multi-candidate block (LocName /
        # LocalizedName / Name fallback); only Description + DescIdentified
        # go through the generic loop.
        "placeable": {
            "description": "Description",
            "identified": "DescIdentified",
        },
        "door": {
            "name": "LocalizedName",
            "description": "Description",
        },
        "encounter": {
            "name": "LocalizedName",
            "description": "Description",
        },
        "store": {
            "description": "Description",
        },
        "module": {
            "name": "Mod_Name",
            "description": "Mod_Description",
        },
    }

    @staticmethod
    def _append_patch_for_field(
        patches: List[Tuple[int, str]],
        record_offsets: Dict[str, Any],
        field_name: str,
        field_obj: Any,
        translations: Dict[str, str],
    ) -> bool:
        """Append a translated patch for one localized field when possible."""
        if not isinstance(field_obj, dict):
            return False

        original_text = field_obj.get("Value", "")
        if not original_text or original_text not in translations:
            return False

        translated_text = translations[original_text]
        if translated_text == original_text:
            return False

        rec_offset = record_offsets.get(field_name, 0)
        if rec_offset <= 0:
            return False

        patches.append((rec_offset, translated_text))
        return True

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
            parsed_data: Original GFF data
            translations: Dictionary mapping original text to translated text
            metadata: Optional metadata with content_type

        Returns:
            InjectedContent with injection results
        """
        content_type = metadata.get("type", "") if metadata else ""

        if content_type not in self.FIELD_MAP:
            return InjectedContent(
                source_file=file_path,
                modified=False,
                items_updated=0,
            )

        items_updated = 0
        modified = False
        fields = self.FIELD_MAP[content_type]

        try:
            patcher = GFFPatcher(file_path, text_encoding=_module_text_encoding(metadata))
        except Exception as e:
            logger.error(f"Failed to load GFFPatcher for {file_path}: {e}")
            return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        record_offsets = parsed_data.get("_record_offsets", {})
        patches: List[Tuple[int, str]] = []

        # Store templates use LocName; fall back to LocalizedName when absent.
        # Placeable templates use LocName in stock NWN/NWN:EE; a few third-party
        # toolset exports emit LocalizedName or Name instead, so fall back.
        _NAME_CANDIDATES = {
            "store": ("LocName", "LocalizedName"),
            "placeable": ("LocName", "LocalizedName", "Name"),
        }
        if content_type in _NAME_CANDIDATES:
            for name_field in _NAME_CANDIDATES[content_type]:
                if self._append_patch_for_field(
                    patches,
                    record_offsets,
                    name_field,
                    parsed_data.get(name_field),
                    translations,
                ):
                    items_updated += 1
                    modified = True
                    break

        # For creatures, we need to handle first and last name specially
        if content_type == "creature":
            if self._append_patch_for_field(
                patches,
                record_offsets,
                "FirstName",
                parsed_data.get("FirstName"),
                translations,
            ):
                items_updated += 1
                modified = True

            if self._append_patch_for_field(
                patches,
                record_offsets,
                "LastName",
                parsed_data.get("LastName"),
                translations,
            ):
                items_updated += 1
                modified = True

        # Handle other fields
        for key, field_name in fields.items():
            if key == "first_name" or key == "last_name":
                continue  # Already handled above

            if self._append_patch_for_field(
                patches,
                record_offsets,
                field_name,
                parsed_data.get(field_name),
                translations,
            ):
                items_updated += 1
                modified = True

        if patches:
            try:
                patcher.patch_multiple(patches)
            except GFFPatchError as e:
                logger.error(f"Failed to patch generic content in {file_path}: {e}")
                return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        return InjectedContent(
            source_file=file_path,
            modified=modified,
            items_updated=items_updated,
            metadata={
                "type": content_type,
            },
        )
