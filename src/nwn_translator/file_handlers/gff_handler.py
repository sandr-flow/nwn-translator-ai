"""GFF file handler for reading and writing NWN GFF format files.

This module provides a wrapper around our native GFF parser with a consistent interface
for reading and writing GFF (Generic File Format) files used by Neverwinter Nights.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from .gff_parser import GFFFile, GFFParser, parse_gff, gff_to_dict, GFFParseError
from .gff_writer import GFFWriteError, write_gff as _write_gff


class GFFHandlerError(Exception):
    """Exception raised for GFF handling errors."""

    pass


class GFFHandler:
    """Handler for reading and writing GFF files.

    This class provides methods to read GFF structures from files and write them back,
    with proper error handling and validation.
    """

    def __init__(self, file_path: Optional[Path] = None):
        """Initialize GFF handler.

        Args:
            file_path: Optional path to GFF file
        """
        self.file_path = Path(file_path) if file_path else None
        self._gff: Optional[GFFFile] = None
        self._data: Dict[str, Any] = {}

    @staticmethod
    def read(file_path: Path, source_encoding: Optional[str] = None) -> Dict[str, Any]:
        """Read a GFF file and return its data as a dictionary.

        Args:
            file_path: Path to the GFF file
            source_encoding: Declared code page for string bytes; ``None`` uses
                the detection cascade

        Returns:
            Dictionary containing GFF structure data

        Raises:
            GFFHandlerError: If file cannot be read
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise GFFHandlerError(f"File not found: {file_path}")

        try:
            parser = GFFParser(file_path, source_encoding=source_encoding)
            gff = parser.parse()
            return gff_to_dict(gff)

        except GFFParseError as e:
            raise GFFHandlerError(f"Failed to parse GFF file {file_path}: {e}") from e
        except Exception as e:
            raise GFFHandlerError(f"Failed to read GFF file {file_path}: {e}") from e

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
    cache: Optional[Dict[Path, Dict[str, Any]]] = None,
    source_encoding: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a GFF file and return its data.

    Args:
        file_path: Path to the GFF file
        cache: Optional session cache keyed by resolved path. Callers sharing a
            cache must pass the same *source_encoding* for every read.
        source_encoding: Declared code page for string bytes

    Returns:
        Dictionary containing GFF data
    """
    path = Path(file_path).resolve()
    if cache is not None:
        if path in cache:
            return cache[path]
        data = GFFHandler.read(path, source_encoding=source_encoding)
        cache[path] = data
        return data
    return GFFHandler.read(path, source_encoding=source_encoding)


def write_gff(file_path: Path, data: Dict[str, Any]) -> None:
    """Write data to a GFF file.

    Args:
        file_path: Path where the GFF file should be written
        data: Dictionary containing GFF data
    """
    GFFHandler.write(file_path, data)
