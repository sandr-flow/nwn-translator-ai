"""GFF file handler for reading and writing NWN GFF format files.

This module provides a wrapper around our native GFF parser with a consistent interface
for reading and writing GFF (Generic File Format) files used by Neverwinter Nights.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .gff_parser import GFFFile, GFFParser, parse_gff, gff_to_dict, GFFParseError
from .gff_writer import GFFWriteError, write_gff as _write_gff
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
