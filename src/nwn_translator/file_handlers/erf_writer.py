"""ERF archive writer for creating Neverwinter Nights module files.

This module handles writing and packaging .mod files from extracted resources.

ERF v1.0 binary layout (header = 160 bytes):
    [0:4]   FileType    (b"MOD ", b"ERF ", b"HAK ")
    [4:8]   Version     (b"V1.0")
    [8:12]  LanguageCount    = 0
    [12:16] LocalizedStringSize = 0
    [16:20] EntryCount
    [24:28] OffsetToKeyList   ← byte offset from start of file
    [28:32] OffsetToResourceList ← byte offset from start of file
    [32:36] BuildYear (years since 1900)
    [36:40] BuildDay  (day of year, 0-based)
    bytes [20:24] and [40:160] are unused/zero

Key List entry — 24 bytes each (per entry):
    ResRef[16]  null-padded ASCII name (no extension)
    ResID[4]    DWORD — index into the Resource List (0-based)
    ResType[4]  DWORD — resource type ID (e.g. 27 = .dlg)

Resource List entry — 8 bytes each (per entry):
    OffsetToResource[4]  DWORD — byte offset of resource data from start of file
    ResourceSize[4]      DWORD — size in bytes
"""

import datetime
import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional

from .erf_reader import ERFReader, ERFHeader

logger = logging.getLogger(__name__)

# Header is always 160 bytes for ERF v1.0
_HEADER_SIZE = 160
# Key List entry is 24 bytes
_KEY_ENTRY_SIZE = 24
# Resource List entry is 8 bytes
_RES_ENTRY_SIZE = 8


class ERFWriterError(Exception):
    """Exception raised for ERF writing errors."""

    pass


class ERFWriter:
    """Writer for ERF archive files (.mod, .erf, .hak).

    Produces a standards-compliant ERF v1.0 binary with separate
    Key List and Resource List sections, as expected by NWN:EE.
    """

    # NWN:EE standard modules use IDs >= 2000 for game entities (.ifo, .utc, etc).
    RESOURCE_TYPE_IDS: Dict[str, int] = {}
    for _res_id, _ext in sorted(ERFReader.RESOURCE_TYPES.items()):
        _ext_lower = _ext.lower()
        if _ext_lower not in RESOURCE_TYPE_IDS:
            RESOURCE_TYPE_IDS[_ext_lower] = _res_id
        # Prefer the first ID >= 2000 over IDs < 2000
        elif _res_id >= 2000 and RESOURCE_TYPE_IDS[_ext_lower] < 2000:
            RESOURCE_TYPE_IDS[_ext_lower] = _res_id

    FILE_TYPES = {
        ".mod": b"MOD ",
        ".erf": b"ERF ",
        ".hak": b"HAK ",
    }

    def __init__(
        self,
        output_path: Path,
        version: str = "V1.0",
        type_overrides: Optional[Dict[str, int]] = None,
    ):
        """Initialize ERF writer.

        Args:
            output_path: Path where ERF file should be written.
            version: ERF version string (only V1.0 is fully supported).
            type_overrides: Optional mapping of filename to exact res_type ID.
        """
        self.output_path = Path(output_path)
        self.version: bytes = version.encode("ascii") if isinstance(version, str) else version
        self.type_overrides: Dict[str, int] = type_overrides or {}
        # Ordered mapping: filename (stem + ext) → raw bytes
        self._resources: Dict[str, bytes] = {}

        ext = self.output_path.suffix.lower()
        self.file_type: bytes = self.FILE_TYPES.get(ext, b"ERF ")

    # ------------------------------------------------------------------
    # Public methods for adding resources
    # ------------------------------------------------------------------

    def add_resource(self, res_ref: str, res_type: str, data: bytes) -> None:
        """Add a resource to the archive.

        Args:
            res_ref: Resource name without extension (max 16 chars, ASCII).
            res_type: File extension including dot (e.g. ``".dlg"``).
            data: Raw resource bytes.
        """
        filename = f"{res_ref}{res_type.lower()}"
        self._resources[filename] = data

    def add_file(self, file_path: Path) -> None:
        """Add a file from disk to the archive.

        Args:
            file_path: Path to file on disk.
        """
        file_path = Path(file_path)
        data = file_path.read_bytes()
        stem = file_path.stem
        suffix = file_path.suffix.lower()
        self.add_resource(stem, suffix, data)

    def add_directory(self, directory: Path) -> None:
        """Add all files from *directory* recursively.

        Args:
            directory: Root directory to scan.
        """
        for file_path in Path(directory).rglob("*"):
            if file_path.is_file():
                self.add_file(file_path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self) -> None:
        """Write the ERF archive to ``self.output_path``.

        Raises:
            ERFWriterError: If writing fails.
        """
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            binary = self._build()
        except Exception as exc:
            raise ERFWriterError(f"Failed to build ERF: {exc}") from exc

        self.output_path.write_bytes(binary)
        logger.info(
            "ERF archive written: %s (%d bytes, %d resources)",
            self.output_path,
            len(binary),
            len(self._resources),
        )

    # ------------------------------------------------------------------
    # Internal build
    # ------------------------------------------------------------------

    def _build(self) -> bytes:
        """Build and return the complete ERF binary.

        Returns:
            Complete ERF binary data.
        """
        sorted_resources = sorted(self._resources.items())
        entry_count = len(sorted_resources)

        # ── 1. Calculate section offsets ──────────────────────────────
        key_list_offset = _HEADER_SIZE
        resource_list_offset = key_list_offset + entry_count * _KEY_ENTRY_SIZE
        resource_data_offset = resource_list_offset + entry_count * _RES_ENTRY_SIZE

        # ── 2. Build Key List and Resource List in parallel ────────────
        key_list_bytes = bytearray()
        res_list_bytes = bytearray()
        res_data_parts: List[bytes] = []

        current_data_offset = resource_data_offset

        for res_id, (filename, data) in enumerate(sorted_resources):
            stem = Path(filename).stem
            suffix = Path(filename).suffix.lower()

            # Prefer the explicit override if we have it from the original mod
            if filename in self.type_overrides:
                res_type_id = self.type_overrides[filename]
            else:
                res_type_id = self.RESOURCE_TYPE_IDS.get(suffix, 0)

            # Key List entry (24 bytes)
            key_list_bytes += self._pack_key_entry(stem, res_id, res_type_id)

            # Resource List entry (8 bytes)
            res_list_bytes += struct.pack("<II", current_data_offset, len(data))

            res_data_parts.append(data)
            current_data_offset += len(data)

        # ── 3. Build header ───────────────────────────────────────────
        header = self._build_header(
            entry_count,
            key_list_offset,
            resource_list_offset,
        )

        # ── 4. Assemble ───────────────────────────────────────────────
        return b"".join(
            [
                header,
                bytes(key_list_bytes),
                bytes(res_list_bytes),
                *res_data_parts,
            ]
        )

    @staticmethod
    def _pack_key_entry(res_ref: str, res_id: int, res_type_id: int) -> bytes:
        """Pack one 24-byte Key List entry.

        Args:
            res_ref: Resource name (max 16 chars ASCII, null-padded).
            res_id: 0-based index (used as index into Resource List).
            res_type_id: ERF resource type ID.

        Returns:
            24-byte packed bytes.
        """
        name_bytes = res_ref.encode("ascii", errors="replace")[:16]
        name_bytes = name_bytes.ljust(16, b"\x00")
        return name_bytes + struct.pack("<II", res_id, res_type_id)

    def _build_header(
        self,
        entry_count: int,
        key_list_offset: int,
        resource_list_offset: int,
    ) -> bytes:
        """Build the 160-byte ERF v1.0 header.

        Args:
            entry_count: Total number of resources.
            key_list_offset: Byte offset to Key List from start of file.
            resource_list_offset: Byte offset to Resource List from start of file.

        Returns:
            160-byte header.
        """
        now = datetime.datetime.now()
        build_year = now.year - 1900
        build_day = now.timetuple().tm_yday - 1  # 0-based

        header = bytearray(_HEADER_SIZE)
        header[0:4] = self.file_type  # FileType
        header[4:8] = self.version  # Version
        # [8:12]  LanguageCount = 0 (already zero)
        # [12:16] LocalizedStringSize = 0 (already zero)
        struct.pack_into("<I", header, 16, entry_count)  # EntryCount
        # [20:24] unused — keep zero
        struct.pack_into("<I", header, 24, key_list_offset)  # OffsetToKeyList
        struct.pack_into("<I", header, 28, resource_list_offset)  # OffsetToResourceList
        struct.pack_into("<I", header, 32, build_year)  # BuildYear
        struct.pack_into("<I", header, 36, build_day)  # BuildDay

        # [40:44] DescriptionStrRef. MUST be 0xFFFFFFFF if LanguageCount=0,
        # otherwise NWN looks up string 0 in dialog.tlk, which is "Bad Strref"!
        struct.pack_into("<I", header, 40, 0xFFFFFFFF)

        # Bytes [44:160] remain zero
        return bytes(header)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def create_mod_from_directory(
    input_dir: Path,
    output_path: Path,
    original_mod: Optional[Path] = None,
) -> None:
    """Create a .mod file from a directory of extracted files.

    Args:
        input_dir: Directory containing extracted resources.
        output_path: Path for the output .mod file.
        original_mod: Original .mod for reference metadata (res_type IDs).
    """
    type_overrides: Dict[str, int] = {}

    if original_mod and original_mod.exists():
        try:
            reader = ERFReader(original_mod)
            entries = reader.read_entries()
            # Map the exact filenames that extract_all() generates to the original res_type
            for entry in entries:
                res_type_ext = reader.detect_type_from_header(entry)
                raw_filename = f"{entry.res_ref}{res_type_ext}"
                filename = reader._sanitize_filename(raw_filename)
                type_overrides[filename] = entry.res_type
            reader.cleanup()
        except Exception as exc:
            logger.warning("Could not read original mod for metadata: %s", exc)

    writer = ERFWriter(output_path, type_overrides=type_overrides)
    writer.add_directory(input_dir)
    writer.write()


def create_mod_from_files(
    files: List[Path],
    output_path: Path,
) -> None:
    """Create a .mod file from a list of files.

    Args:
        files: List of file paths to include.
        output_path: Path for the output .mod file.
    """
    writer = ERFWriter(output_path)
    for file_path in files:
        writer.add_file(file_path)
    writer.write()
