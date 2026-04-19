"""ERF archive reader for extracting Neverwinter Nights module files.

This module handles reading and extracting .mod files, which are ERF (Encapsulated
Resource File) archives used by Neverwinter Nights to package game content.
"""

import logging
import struct
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from tqdm import tqdm

# phase, current (0-based), total, optional message (e.g. resource name)
ProgressCallback = Optional[Callable[[str, int, int, Optional[str]], None]]

logger = logging.getLogger(__name__)


class ERFReaderError(Exception):
    """Exception raised for ERF reading errors."""

    pass


class ERFHeader:
    """ERF file header structure."""

    # ERF file type identifiers
    ERF_TYPE = b"ERF "
    MOD_TYPE = b"MOD "  # NWN: Enhanced Edition uses MOD type
    ERF_VERSION_V1 = b"V1.0"
    ERF_VERSION_V2 = b"V2.0"

    # Valid file types
    VALID_TYPES = (ERF_TYPE, MOD_TYPE, b"HAK ", b"ERF")

    def __init__(self, data: bytes):
        """Parse ERF header from binary data.

        Args:
            data: First 160 bytes of ERF file

        Raises:
            ERFReaderError: If header is invalid
        """
        if len(data) < 160:
            raise ERFReaderError("Invalid ERF header: too short")

        # Parse header fields
        self.file_type = data[0:4]
        self.version = data[4:8]
        self.language_count = struct.unpack("<I", data[8:12])[0]
        self.localized_string_size = struct.unpack("<I", data[12:16])[0]
        self.entry_count = struct.unpack("<I", data[16:20])[0]

        # Offset to Key List (variable names in docs can be confusing)
        self.offset_to_key_list = struct.unpack("<I", data[24:28])[0]

        # Offset to Resource List
        self.offset_to_resource_list = struct.unpack("<I", data[28:32])[0]
        self.build_day = struct.unpack("<I", data[32:36])[0]

        # Validate (support both ERF and MOD types)
        if self.file_type not in self.VALID_TYPES:
            raise ERFReaderError(f"Invalid file type: {self.file_type!r}")

    def is_valid(self) -> bool:
        """Check if header is valid."""
        return self.file_type in self.VALID_TYPES

    def is_mod(self) -> bool:
        """Check if this is a NWN:EE MOD file."""
        return self.file_type == self.MOD_TYPE


class ERFEntry:
    """ERF resource entry."""

    def __init__(self, res_ref: str, res_id: int, res_type: int, offset: int, size: int):
        """Initialize ERF entry.

        Args:
            res_ref: Resource name
            res_id: Resource ID (index)
            res_type: Resource type ID
            offset: Offset to data
            size: Size of data
        """
        self.res_ref = res_ref
        self.res_id = res_id
        self.res_type = res_type
        self.offset = offset
        self.size = size

    def __repr__(self) -> str:
        """String representation."""
        return f"ERFEntry({self.res_ref}, type={self.res_type}, size={self.size})"


class ERFReader:
    """Reader for ERF archive files (.mod, .erf, .hak)."""

    # Resource type mappings (common ones)
    RESOURCE_TYPES = {
        0: ".bmp",  # Texture
        1: ".tga",  # Texture
        2: ".wav",  # Sound
        3: ".plt",  # Color palette
        4: ".ini",  # Configuration
        5: ".txt",  # Text
        6: ".mdl",  # Model
        7: ".thg",  # Tileset generic
        8: ".fxt",  # Font texture
        9: ".txi",  # Texture info
        10: ".git",  # Instance file
        11: ".uti",  # Item
        12: ".ptc",  # Particle
        13: ".sst",  # Store state
        14: ".ncs",  # Script
        15: ".mod",  # Module
        16: ".are",  # Area
        17: ".set",  # Special effect
        18: ".ifo",  # Module info
        19: ".bic",  # Character
        20: ".wok",  # Walk mesh
        21: ".2da",  # 2D array
        22: ".tlk",  # Talk table
        23: ".txi",  # Texture info
        24: ".git",  # Instance
        25: ".bti",  # Item blueprint
        26: ".utc",  # Creature
        27: ".dlg",  # Dialog
        28: ".itp",  # Tool palette
        29: ".btt",  # Trigger blueprint
        30: ".utt",  # Trigger / Placeable
        31: ".btc",  # Creature blueprint
        32: ".uts",  # Sound blueprint
        33: ".utr",  # Trap blueprint
        34: ".btd",  # Door blueprint
        35: ".btp",  # Placeable blueprint
        36: ".ptm",  # Plot manager
        37: ".ptt",  # Plot instance
        38: ".ncs",  # Script
        39: ".bfx",  # Effect
        40: ".bte",  # Effect blueprint
        41: ".css",  # Client script
        42: ".fs",  # Faction
        43: ".jrl",  # Journal
        44: ".sec",  # Secret
        45: ".ifo",  # Module info
        46: ".bio",  # Player info
        47: ".spe",  # Spawn point
        48: ".sem",  # Trigger
        49: ".lus",  # Locomotion
        50: ".gor",  # Gore
        51: ".fxs",  # FX script
        52: ".wmp",  # Map
        53: ".fac",  # Faction
        54: ".gff",  # Generic GFF
        55: ".gam",  # Game
        56: ".gui",  # GUI
        57: ".ute",  # Encounter
        58: ".utp",  # Placeable
        59: ".utm",  # Store
        60: ".utw",  # Waypoint
        61: ".uts",  # Sound
        62: ".utr",  # Trap
        63: ".utf",  # Layout
        64: ".utd",  # Door
        65: ".utn",  # Waypoint
        66: ".pal",  # Palette
        67: ".pdf",  # Palette data
        68: ".gic",  # Instance
        69: ".fxe",  # Effect
        70: ".ptx",  # Plot
        71: ".png",  # Texture
        72: ".ltx",  # Lexicon
        73: ".utx",  # Tileset
        74: ".gff",  # GFF
        75: ".xml",  # XML
        76: ".xba",  # Animation
        77: ".ids",  # Identifier
        78: ".bwd",  # Data
        79: ".bwm",  # Walkmesh
        2009: ".nss",  # Script source
        2010: ".ncs",  # Compiled script
        2012: ".are",  # Area static
        2013: ".set",  # Tileset info
        2014: ".ifo",  # Module info
        2015: ".bic",  # Character
        2016: ".wok",  # Walkmesh
        2017: ".2da",  # 2D Array
        2022: ".txi",  # Texture info
        2023: ".git",  # Area instance
        2025: ".uti",  # Item
        2027: ".utc",  # Creature
        2029: ".dlg",  # Dialogue
        2032: ".uts",  # Sound
        2035: ".uts",  # Sound (alt)
        2038: ".fac",  # Faction
        2040: ".ute",  # Encounter
        2042: ".utm",  # Store
        2044: ".utp",  # Placeable
        2045: ".ncs",  # Script (alt)
        2047: ".gui",  # GUI
        2052: ".css",  # Client script
        2056: ".jrl",  # Journal
        2058: ".utw",  # Waypoint
    }

    def __init__(self, file_path: Path, progress_callback: ProgressCallback = None):
        """Initialize ERF reader.

        Args:
            file_path: Path to ERF file (.mod, .erf, .hak)
            progress_callback: If set, tqdm is not used; called per entry during extract.

        Raises:
            ERFReaderError: If file cannot be read
        """
        self.file_path = Path(file_path)
        self.header: Optional[ERFHeader] = None
        self.entries: List[ERFEntry] = []
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.progress_callback = progress_callback
        #: Filled in :meth:`read_entries` — ``res_id`` -> extension from GFF signature
        self._header_type_by_res_id: Dict[int, str] = {}

        if not self.file_path.exists():
            raise ERFReaderError(f"File not found: {file_path}")

    def read_header(self) -> ERFHeader:
        """Read ERF header.

        Returns:
            ERFHeader object

        Raises:
            ERFReaderError: If header cannot be read
        """
        with open(self.file_path, "rb") as f:
            header_data = f.read(160)
            self.header = ERFHeader(header_data)

        if not self.header.is_valid():
            raise ERFReaderError("Invalid ERF header")

        return self.header

    def read_entries(self) -> List[ERFEntry]:
        """Read ERF entry table.

        Returns:
            List of ERFEntry objects

        Raises:
            ERFReaderError: If entries cannot be read
        """
        if not self.header:
            self.read_header()
        assert self.header is not None

        entry_count = self.header.entry_count

        # 1. Read Key List
        keys = []
        with open(self.file_path, "rb") as f:
            f.seek(self.header.offset_to_key_list)
            # Key entry is 24 bytes in the actual file structure (Ref 16 + ID 4 + Type 4)
            # despite offset delta suggesting 32. The gap is likely padding.
            key_stride = 24
            key_data_block = f.read(entry_count * key_stride)

            for i in range(entry_count):
                base = i * key_stride
                res_ref = (
                    key_data_block[base : base + 16]
                    .split(b"\x00")[0]
                    .decode("ascii", errors="ignore")
                )
                res_id = struct.unpack("<I", key_data_block[base + 16 : base + 20])[0]
                res_type = struct.unpack("<I", key_data_block[base + 20 : base + 24])[0]
                keys.append((res_ref, res_id, res_type))

            # 2. Read Resource List
            f.seek(self.header.offset_to_resource_list)
            # Resource entry is 8 bytes
            # [Offset 4b] [Size 4b]
            res_data_block = f.read(entry_count * 8)

            resources = []
            for i in range(entry_count):
                base = i * 8
                offset = struct.unpack("<I", res_data_block[base : base + 4])[0]
                size = struct.unpack("<I", res_data_block[base + 4 : base + 8])[0]
                resources.append((offset, size))

        # 3. Combine
        self.entries = []
        for res_ref, res_id, res_type in keys:
            if res_id < len(resources):
                offset, size = resources[res_id]
                entry = ERFEntry(res_ref, res_id, res_type, offset, size)
                self.entries.append(entry)
            else:
                logger.warning(f"Invalid resource ID {res_id} for {res_ref}")

        self._fill_header_type_cache()
        return self.entries

    # Signature-based detection for resources whose numeric Type ID may be custom
    # or non-standard in third-party modules. This allows unknown extensions like
    # ".2051" to be mapped into a known pipeline type by inspecting file headers.
    _GFF_SIG_MAP = {
        b"ARE ": ".are",
        b"DLG ": ".dlg",
        b"FAC ": ".fac",
        b"GFF ": ".gff",
        b"GIC ": ".gic",
        b"GIT ": ".git",
        b"IFO ": ".ifo",
        b"JRL ": ".jrl",
        b"NCS ": ".ncs",
        b"UTE ": ".ute",
        b"UTD ": ".utd",
        b"UTI ": ".uti",
        b"UTM ": ".utm",
        b"UTP ": ".utp",
        b"UTR ": ".utr",
        b"UTS ": ".uts",
        b"UTT ": ".utt",
        b"UTW ": ".utw",
        b"UTC ": ".utc",
    }

    def _fill_header_type_cache(self) -> None:
        """Read each entry's 4-byte GFF signature once (single file open)."""
        self._header_type_by_res_id.clear()
        if not self.entries:
            return
        with open(self.file_path, "rb") as f:
            for entry in self.entries:
                if entry.offset == 0xFFFFFFFF:
                    self._header_type_by_res_id[entry.res_id] = self.get_resource_type(
                        entry.res_type
                    )
                    continue
                f.seek(entry.offset)
                sig = f.read(4)
                if sig in self._GFF_SIG_MAP:
                    self._header_type_by_res_id[entry.res_id] = self._GFF_SIG_MAP[sig]
                else:
                    self._header_type_by_res_id[entry.res_id] = self.get_resource_type(
                        entry.res_type
                    )

    def get_resource_type(self, res_id: int) -> str:
        """Get file extension for a resource type ID.

        Args:
            res_id: Resource type ID

        Returns:
            File extension (e.g., ".dlg", ".uti")
        """
        return self.RESOURCE_TYPES.get(res_id, f".{res_id}")

    def detect_type_from_header(self, entry: ERFEntry) -> str:
        """Detect file type from signature, fallback to Type ID.

        Args:
            entry: ERFEntry to check

        Returns:
            Computed extension
        """
        cached = self._header_type_by_res_id.get(entry.res_id)
        if cached is not None:
            return cached

        # Lazy path if entries were not loaded via read_entries
        if entry.offset == 0xFFFFFFFF:
            ext = self.get_resource_type(entry.res_type)
            self._header_type_by_res_id[entry.res_id] = ext
            return ext
        with open(self.file_path, "rb") as f:
            f.seek(entry.offset)
            sig = f.read(4)
        if sig in self._GFF_SIG_MAP:
            ext = self._GFF_SIG_MAP[sig]
        else:
            ext = self.get_resource_type(entry.res_type)
        self._header_type_by_res_id[entry.res_id] = ext
        return ext

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for Windows filesystem.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename safe for filesystem
        """
        # Replace invalid characters with underscore
        # Invalid for Windows: < > : " / \ | ? * and control chars (0-31)
        invalid_chars = '<>:"/\\|?*'
        result = filename

        # Replace control characters (0-31) with underscore
        result = "".join("_" if (0 <= ord(c) <= 31) else c for c in result)

        # Replace other invalid characters
        for char in invalid_chars:
            result = result.replace(char, "_")

        return result

    def _write_entry_to_dir(self, fp, entry: ERFEntry, output_dir: Path) -> None:
        """Read one entry from open file handle and write to output_dir."""
        if entry.offset == 0xFFFFFFFF:  # Skip empty entries
            return
        res_type = self.detect_type_from_header(entry)
        raw_filename = f"{entry.res_ref}{res_type}"
        filename = self._sanitize_filename(raw_filename)
        output_path = output_dir / filename
        fp.seek(entry.offset)
        resource_data = fp.read(entry.size)
        output_path.write_bytes(resource_data)

    def extract_all(self, output_dir: Optional[Path] = None) -> Path:
        """Extract all resources to a temporary directory.

        Args:
            output_dir: Optional output directory (uses temp dir if None)

        Returns:
            Path to extraction directory

        Raises:
            ERFReaderError: If extraction fails
        """
        if not self.entries:
            self.read_entries()

        # Create temp directory if not specified
        if output_dir is None:
            self.temp_dir = tempfile.TemporaryDirectory(prefix="nwn_extract_")
            output_dir = Path(self.temp_dir.name)
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        total = len(self.entries)
        with open(self.file_path, "rb") as f:
            if self.progress_callback is None:
                for entry in tqdm(
                    self.entries,
                    desc="Extracting ERF",
                    disable=None,
                ):
                    self._write_entry_to_dir(f, entry, output_dir)
            else:
                for i, entry in enumerate(self.entries):
                    self.progress_callback("extracting", i, total, entry.res_ref)
                    self._write_entry_to_dir(f, entry, output_dir)

        return output_dir

    def extract_resource(self, res_ref: str, res_type: str, output_path: Path) -> bool:
        """Extract a single resource.

        Args:
            res_ref: Resource reference (filename without extension)
            res_type: Resource type (e.g., ".dlg", ".uti")
            output_path: Where to write the extracted file

        Returns:
            True if extraction successful
        """
        if not self.entries:
            self.read_entries()

        # Find the entry
        for entry in self.entries:
            if entry.res_ref.lower() == res_ref.lower():
                # Check if detected type matches requested type
                # OR if requested type matches the raw ID type (fallback)
                detected_type = self.detect_type_from_header(entry)
                raw_type = self.get_resource_type(entry.res_type)

                if (
                    detected_type.lower() == res_type.lower()
                    or raw_type.lower() == res_type.lower()
                ):
                    # Extract it
                    with open(self.file_path, "rb") as f:
                        f.seek(entry.offset)
                        resource_data = f.read(entry.size)
                        output_path.write_bytes(resource_data)
                    return True

        return False

    def get_translatable_files(self) -> List[Tuple[str, str]]:
        """Get list of translatable files in the archive.

        Returns:
            List of (res_ref, res_type) tuples
        """
        if not self.entries:
            self.read_entries()

        # Translatable file types
        translatable_types = {
            ".dlg",
            ".jrl",
            ".uti",
            ".utc",
            ".are",
            ".utt",
            ".utp",
            ".utd",
            ".utm",
        }

        translatable = []
        for entry in self.entries:
            # Use detection!
            res_type = self.detect_type_from_header(entry)
            if res_type.lower() in translatable_types:
                translatable.append((entry.res_ref, res_type))

        return translatable

    def cleanup(self) -> None:
        """Clean up temporary files."""
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()
