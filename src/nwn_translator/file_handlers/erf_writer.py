"""ERF archive writer for creating Neverwinter Nights module files.

This module handles writing and packaging .mod files from extracted resources.

ERF v1.0 binary layout (header = 160 bytes):
    [0:4]   FileType    (b"MOD ", b"ERF ", b"HAK ")
    [4:8]   Version     (b"V1.0")
    [8:12]  LanguageCount
    [12:16] LocalizedStringSize
    [16:20] EntryCount
    [20:24] OffsetToLocalizedString ← byte offset from start of file
    [24:28] OffsetToKeyList   ← byte offset from start of file
    [28:32] OffsetToResourceList ← byte offset from start of file
    [32:36] BuildYear (years since 1900)
    [36:40] BuildDay  (day of year, 0-based)
    [40:44] DescriptionStrRef (0xFFFFFFFF when LanguageCount = 0)
    bytes [44:160] are unused/zero

The Localized String List (the module description shown by the toolset)
sits between the header and the Key List.

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
import os
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
        # Module description (Localized String List) carried from a source
        # archive; defaults mean "no description".
        self._loc_language_count = 0
        self._loc_strings_block = b""
        self._description_strref = 0xFFFFFFFF

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

    def set_localized_strings(
        self, language_count: int, raw_block: bytes, description_strref: int
    ) -> None:
        """Carry the module description from a source archive.

        Args:
            language_count: LanguageCount header value of the source archive.
            raw_block: Raw Localized String List bytes (may be empty).
            description_strref: DescriptionStrRef header value of the source.
        """
        self._loc_language_count = language_count
        self._loc_strings_block = raw_block
        self._description_strref = description_strref

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

        # Write to a sibling temp file and swap it in atomically: rebuild
        # overwrites an existing .mod in place, and a failure mid-write must
        # not destroy the previous artifact. os.replace is atomic only
        # within one volume, hence the same directory.
        tmp_path = self.output_path.with_name(self.output_path.name + ".tmp")
        try:
            tmp_path.write_bytes(binary)
            os.replace(tmp_path, self.output_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
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
        # The Localized String List (module description) sits right after
        # the header, pushing the Key List back by its size.
        key_list_offset = _HEADER_SIZE + len(self._loc_strings_block)
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
                self._loc_strings_block,
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

        Raises:
            ERFWriterError: If *res_ref* does not fit the 16-byte field.
        """
        name_bytes = res_ref.encode("ascii", errors="replace")
        if len(name_bytes) > 16:
            raise ERFWriterError(
                f"Resource name {res_ref!r} is {len(name_bytes)} bytes; "
                "ERF resrefs are limited to 16"
            )
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
        struct.pack_into("<I", header, 8, self._loc_language_count)  # LanguageCount
        struct.pack_into("<I", header, 12, len(self._loc_strings_block))  # LocalizedStringSize
        struct.pack_into("<I", header, 16, entry_count)  # EntryCount
        if self._loc_strings_block:
            struct.pack_into("<I", header, 20, _HEADER_SIZE)  # OffsetToLocalizedString
        struct.pack_into("<I", header, 24, key_list_offset)  # OffsetToKeyList
        struct.pack_into("<I", header, 28, resource_list_offset)  # OffsetToResourceList
        struct.pack_into("<I", header, 32, build_year)  # BuildYear
        struct.pack_into("<I", header, 36, build_day)  # BuildDay

        # [40:44] DescriptionStrRef. MUST be 0xFFFFFFFF if LanguageCount=0,
        # otherwise NWN looks up string 0 in dialog.tlk, which is "Bad Strref"!
        struct.pack_into("<I", header, 40, self._description_strref)

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
    description_carry: Optional[Tuple[int, bytes, int]] = None

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
            assert reader.header is not None
            loc_block = reader.read_localized_strings_block()
            description_carry = (
                # A corrupt block reads back empty; LanguageCount > 0 with
                # zero LocalizedStringSize would make an invalid header.
                reader.header.language_count if loc_block else 0,
                loc_block,
                reader.header.description_strref,
            )
            reader.cleanup()
        except Exception as exc:
            logger.error("Could not read original mod for metadata: %s", exc)
            raise ERFWriterError(
                f"Failed to read resource types from original module {original_mod}: {exc}"
            ) from exc

    writer = ERFWriter(output_path, type_overrides=type_overrides)
    if description_carry is not None:
        writer.set_localized_strings(*description_carry)
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
