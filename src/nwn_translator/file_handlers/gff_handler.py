"""GFF file handler for reading and writing NWN GFF format files.

This module provides a wrapper around our native GFF parser with a consistent interface
for reading and writing GFF (Generic File Format) files used by Neverwinter Nights.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .gff_parser import GFFFile, GFFParser, parse_gff, gff_to_dict, GFFParseError
from .gff_writer import GFFWriter, GFFWriteError, write_gff as _write_gff
from .tlk_reader import TLKFile, TLKEntry


class GFFHandlerError(Exception):
    """Exception raised for GFF handling errors."""
    pass


class GFFHandler:
    """Handler for reading and writing GFF files.

    This class provides methods to read GFF structures from files and write them back,
    with proper error handling and validation.
    """

    def __init__(self, file_path: Optional[Path] = None, tlk: Optional[TLKFile] = None):
        """Initialize GFF handler.

        Args:
            file_path: Optional path to GFF file
            tlk: Optional TLK file for resolving StrRefs
        """
        self.file_path = Path(file_path) if file_path else None
        self.tlk = tlk
        self._gff: Optional[GFFFile] = None
        self._data: Dict[str, Any] = {}

    @staticmethod
    def read(file_path: Path, tlk: Optional[TLKFile] = None) -> Dict[str, Any]:
        """Read a GFF file and return its data as a dictionary.

        Args:
            file_path: Path to the GFF file
            tlk: Optional TLK file for resolving StrRefs

        Returns:
            Dictionary containing GFF structure data

        Raises:
            GFFHandlerError: If file cannot be read
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise GFFHandlerError(f"File not found: {file_path}")

        try:
            parser = GFFParser(file_path)
            gff = parser.parse()

            # Convert to dictionary
            data = gff_to_dict(gff)

            # Resolve StrRefs if TLK provided
            if tlk:
                data = GFFHandler._resolve_strrefs(data, tlk)

            return data

        except GFFParseError as e:
            raise GFFHandlerError(f"Failed to parse GFF file {file_path}: {e}") from e
        except Exception as e:
            raise GFFHandlerError(f"Failed to read GFF file {file_path}: {e}") from e

    @staticmethod
    def _resolve_strrefs(data: Dict[str, Any], tlk: TLKFile) -> Dict[str, Any]:
        """Resolve StrRef IDs to actual text from TLK.

        Args:
            data: GFF data dictionary
            tlk: TLK file

        Returns:
            Data with StrRefs resolved to text
        """
        result = {}

        for key, value in data.items():
            # Preserve internal metadata keys intact — they must not be recursed into
            if key in ("_record_offsets", "_field_types"):
                result[key] = value
                continue

            if isinstance(value, dict) and "StrRef" in value:
                # This is a CExoLocString
                str_ref = value["StrRef"]
                text = value.get("Value", "")

                # If no embedded text, try to get from TLK
                if not text and str_ref >= 0:
                    tlk_text = tlk.get_string(str_ref)
                    if tlk_text:
                        text = tlk_text

                result[key] = {
                    "StrRef": str_ref,
                    "Value": text,
                }
            elif isinstance(value, dict):
                # Recursively handle nested dicts
                result[key] = GFFHandler._resolve_strrefs(value, tlk)
            elif isinstance(value, list):
                # Handle lists
                result[key] = [
                    GFFHandler._resolve_strrefs(item, tlk) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    @staticmethod
    def write(file_path: Path, data: Dict[str, Any]) -> None:
        """Write data to a GFF file.

        Args:
            file_path: Path where the GFF file should be written
            data: Dictionary containing GFF structure data

        Raises:
            GFFHandlerError: If file cannot be written
        """
        try:
            _write_gff(Path(file_path), data)
        except GFFWriteError as e:
            raise GFFHandlerError(f"Failed to write GFF file {file_path}: {e}") from e
        except Exception as e:
            raise GFFHandlerError(f"Failed to write GFF file {file_path}: {e}") from e

    @staticmethod
    def get_field(data: Dict[str, Any], field_path: str, default: Any = None) -> Any:
        """Get a field value from GFF data using dot notation.

        Args:
            data: GFF data dictionary
            field_path: Path to field using dot notation (e.g., "Dialog.EntryList")
            default: Default value if field not found

        Returns:
            Field value or default
        """
        keys = field_path.split(".")
        value = data

        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            elif isinstance(value, list) and key.isdigit():
                index = int(key)
                if 0 <= index < len(value):
                    value = value[index]
                else:
                    return default
            else:
                return default

        return value

    @staticmethod
    def set_field(data: Dict[str, Any], field_path: str, value: Any) -> None:
        """Set a field value in GFF data using dot notation.

        Args:
            data: GFF data dictionary
            field_path: Path to field using dot notation (e.g., "Dialog.EntryList")
            value: Value to set
        """
        keys = field_path.split(".")
        current = data

        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    @staticmethod
    def get_local_string(data: Dict[str, Any]) -> Optional[str]:
        """Extract text from a LocalString structure.

        LocalString in GFF format contains:
        - StrRef: Integer reference to string table (or -1 if not used)
        - Value: Actual string text (if StrRef is -1)

        Args:
            data: Dictionary containing StrRef and Value fields

        Returns:
            The string value, or None if not available
        """
        if not isinstance(data, dict):
            return None

        # Check if StrRef is -1 (meaning Value contains the actual text)
        str_ref = data.get("StrRef", -1)
        value = data.get("Value", "")

        if str_ref == -1:
            return value
        else:
            # Return value if present (may be resolved from TLK)
            return value if value else None

    @staticmethod
    def create_local_string(text: str, str_ref: int = -1) -> Dict[str, Any]:
        """Create a LocalString structure with the given text.

        Args:
            text: String text to store
            str_ref: StrRef ID (default: -1 for embedded text)

        Returns:
            Dictionary with StrRef and Value fields
        """
        return {
            "StrRef": str_ref,
            "Value": text,
        }

    @staticmethod
    def validate_gff_structure(data: Dict[str, Any], expected_type: str) -> bool:
        """Validate that GFF data has the expected structure type.

        Args:
            data: GFF data dictionary
            expected_type: Expected GFF file type (e.g., "UTI", "DLG")

        Returns:
            True if structure type matches, False otherwise
        """
        if not isinstance(data, dict):
            return False

        actual_type = data.get("StructType")
        return actual_type == expected_type


# Convenience functions for common operations
def read_gff(
    file_path: Path,
    tlk: Optional[TLKFile] = None,
    cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Read a GFF file and return its data.

    Args:
        file_path: Path to the GFF file
        tlk: Optional TLK file for resolving StrRefs
        cache: Optional session cache keyed by ``(resolved_path, id(tlk) or 0)``

    Returns:
        Dictionary containing GFF data
    """
    path = Path(file_path).resolve()
    tlk_key = id(tlk) if tlk is not None else 0
    cache_key = (path, tlk_key)
    if cache is not None:
        if cache_key in cache:
            return cache[cache_key]
        data = GFFHandler.read(path, tlk)
        cache[cache_key] = data
        return data
    return GFFHandler.read(path, tlk)


def write_gff(file_path: Path, data: Dict[str, Any]) -> None:
    """Write data to a GFF file.

    Args:
        file_path: Path where the GFF file should be written
        data: Dictionary containing GFF data
    """
    GFFHandler.write(file_path, data)
