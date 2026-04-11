"""Dialog injector for NWN dialog files.

This module handles injection of translated dialog content back into .dlg GFF files.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

from .base import BaseInjector, InjectedContent
from ..file_handlers.gff_patcher import GFFPatcher, GFFPatchError
from ..extractors.base import DialogNode

logger = logging.getLogger(__name__)


def _module_text_encoding(metadata: Optional[Dict[str, Any]]) -> str:
    return (metadata or {}).get("module_text_encoding") or "cp1251"


class DialogInjector(BaseInjector):
    """Injector for dialog (.dlg) files."""

    def can_inject(self, content_type: str) -> bool:
        """Check if this injector can handle the given content type."""
        return content_type == "dialog"

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
            patcher = GFFPatcher(
                file_path, text_encoding=_module_text_encoding(metadata)
            )
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
            }
        )


class JournalInjector(BaseInjector):
    """Injector for journal (.jrl) files."""

    def can_inject(self, content_type: str) -> bool:
        """Check if this injector can handle the given content type."""
        return content_type == "journal"

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
            patcher = GFFPatcher(
                file_path, text_encoding=_module_text_encoding(metadata)
            )
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
            }
        )


class GenericInjector(BaseInjector):
    """Generic injector for simple item files.

    This handles items, creatures, areas, placeables, doors, and stores.
    """

    SUPPORTED_TYPES = ["item", "creature", "area", "trigger", "placeable", "door", "store", "module"]

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
        "placeable": {
            "name": "Name",
            "description": "Description",
            "identified": "DescIdentified",
        },
        "door": {
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

    def can_inject(self, content_type: str) -> bool:
        """Check if this injector can handle the given content type."""
        return content_type in self.SUPPORTED_TYPES

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
            patcher = GFFPatcher(
                file_path, text_encoding=_module_text_encoding(metadata)
            )
        except Exception as e:
            logger.error(f"Failed to load GFFPatcher for {file_path}: {e}")
            return InjectedContent(source_file=file_path, modified=False, items_updated=0)

        record_offsets = parsed_data.get("_record_offsets", {})
        patches: List[Tuple[int, str]] = []

        # Store templates use LocName; fall back to LocalizedName when absent.
        if content_type == "store":
            for name_field in ("LocName", "LocalizedName"):
                if name_field not in parsed_data:
                    continue
                field_obj = parsed_data[name_field]
                if not isinstance(field_obj, dict):
                    continue
                original_text = field_obj.get("Value", "")
                if not original_text or original_text not in translations:
                    continue
                translated_text = translations[original_text]
                if translated_text == original_text:
                    continue
                rec_offset = record_offsets.get(name_field, 0)
                if rec_offset > 0:
                    patches.append((rec_offset, translated_text))
                    items_updated += 1
                    modified = True
                    break

        # For creatures, we need to handle first and last name specially
        if content_type == "creature":
            first_name_obj = parsed_data.get("FirstName", {})
            last_name_obj = parsed_data.get("LastName", {})

            if isinstance(first_name_obj, dict):
                original_first = first_name_obj.get("Value", "")
                if original_first and original_first in translations:
                    rec_offset = record_offsets.get("FirstName", 0)
                    if rec_offset > 0:
                        patches.append((rec_offset, translations[original_first]))
                        items_updated += 1
                        modified = True

            if isinstance(last_name_obj, dict):
                original_last = last_name_obj.get("Value", "")
                if original_last and original_last in translations:
                    rec_offset = record_offsets.get("LastName", 0)
                    if rec_offset > 0:
                        patches.append((rec_offset, translations[original_last]))
                        items_updated += 1
                        modified = True

        # Handle other fields
        for key, field_name in fields.items():
            if key == "first_name" or key == "last_name":
                continue  # Already handled above

            if field_name in parsed_data:
                field_obj = parsed_data[field_name]
                if isinstance(field_obj, dict):
                    original_text = field_obj.get("Value", "")
                    if original_text and original_text in translations:
                        translated_text = translations[original_text]
                        if translated_text != original_text:
                            rec_offset = record_offsets.get(field_name, 0)
                            if rec_offset > 0:
                                patches.append((rec_offset, translated_text))
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
            }
        )
