"""TLK file reader for Neverwinter Nights: Enhanced Edition.

This module reads TLK (Talk Table) files which contain localized strings
used by Neverwinter Modules. dialog.tlk contains most module text.
"""

import struct
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TLKEntry:
    """Represents a single entry in a TLK file."""

    def __init__(self, text: str, sound_resref: str = "", volume: int = 0):
        """Initialize a TLK entry.

        Args:
            text: The string text
            sound_resref: Sound resource reference (optional)
            volume: Sound volume (0-127)
        """
        self.text = text
        self.sound_resref = sound_resref
        self.volume = volume

    def __repr__(self) -> str:
        text_preview = self.text[:30] + "..." if len(self.text) > 30 else self.text
        return f"TLKEntry('{text_preview}')"


class TLKFile:
    """Parsed TLK file with all string entries."""

    def __init__(self):
        """Initialize empty TLK file."""
        self.language: int = 0
        self.entry_count: int = 0
        self.entries: List[TLKEntry] = []
        self.entry_map: Dict[int, TLKEntry] = {}

    def get_string(self, str_ref: int) -> Optional[str]:
        """Get string by StrRef ID.

        Args:
            str_ref: String reference ID

        Returns:
            String text or None if not found
        """
        if str_ref < 0 or str_ref >= len(self.entries):
            return None
        return self.entries[str_ref].text

    def get_entry(self, str_ref: int) -> Optional[TLKEntry]:
        """Get full entry by StrRef ID.

        Args:
            str_ref: String reference ID

        Returns:
            TLKEntry or None if not found
        """
        if str_ref < 0 or str_ref >= len(self.entries):
            return None
        return self.entries[str_ref]

    def __len__(self) -> int:
        """Return number of entries."""
        return len(self.entries)

    def __repr__(self) -> str:
        return f"TLKFile(lang={self.language}, entries={len(self.entries)})"


class TLKParseError(Exception):
    """Exception raised for TLK parsing errors."""
    pass


class TLKReader:
    """Reader for TLK files used by Neverwinter Nights."""

    # Language codes
    LANGUAGES = {
        0: "English",
        1: "French",
        2: "German",
        3: "Italian",
        4: "Spanish",
        5: "Polish",
        6: "Korean",
        7: "Chinese Traditional",
        8: "Japanese",
        9: "Russian",
        10: "Portuguese",
    }

    def __init__(self, file_path: Path):
        """Initialize TLK reader.

        Args:
            file_path: Path to TLK file
        """
        self.file_path = Path(file_path)

    def parse(self) -> TLKFile:
        """Parse the TLK file.

        Returns:
            Parsed TLKFile object

        Raises:
            TLKParseError: If parsing fails
        """
        with open(self.file_path, "rb") as f:
            data = f.read()

        if len(data) < 20:
            raise TLKParseError("File too small to be valid TLK")

        tlk = TLKFile()

        # Parse header
        file_type = data[0:4]
        if file_type != b"TLK ":
            raise TLKParseError(f"Invalid TLK file type: {file_type}")

        version = data[4:8]
        tlk.language = struct.unpack("<I", data[8:12])[0]
        tlk.entry_count = struct.unpack("<I", data[12:16])[0]
        string_data_offset = struct.unpack("<I", data[16:20])[0]

        logger.info(f"TLK: {tlk.entry_count} entries, language={self.LANGUAGES.get(tlk.language, tlk.language)}")

        # Parse entry data
        # Each entry has: [offset (4)] + [size (4)] + [sound_resref (16)] + [volume (4)]
        entry_size = 28  # 4 + 4 + 16 + 4
        entries_offset = 20

        entries = []
        for i in range(tlk.entry_count):
            offset = entries_offset + (i * entry_size)

            if offset + entry_size > len(data):
                logger.warning(f"Entry {i} exceeds file size")
                break

            string_offset = struct.unpack("<I", data[offset:offset+4])[0]
            string_size = struct.unpack("<I", data[offset+4:offset+8])[0]

            # Sound resref (16 chars, null-terminated)
            sound_data = data[offset+8:offset+24]
            sound_resref = sound_data.split(b'\x00')[0].decode('ascii', errors='ignore').strip()

            # Volume
            volume = struct.unpack("<I", data[offset+24:offset+28])[0]

            # Extract string text
            text = ""
            if string_size > 0 and string_offset > 0:
                abs_offset = string_data_offset + string_offset
                if abs_offset + string_size <= len(data):
                    try:
                        # NWN:EE uses UTF-8
                        text_data = data[abs_offset:abs_offset+string_size-1]  # -1 for null terminator
                        text = text_data.decode('utf-8')
                    except Exception as e:
                        # Fallback to latin-1 for very old files
                        try:
                            text = text_data.decode('latin-1')
                        except:
                            text = text_data.decode('utf-8', errors='ignore')

            entry = TLKEntry(text, sound_resref, volume)
            entries.append(entry)

        tlk.entries = entries
        return tlk


def parse_tlk(file_path: Path) -> TLKFile:
    """Parse a TLK file.

    Args:
        file_path: Path to TLK file

    Returns:
        Parsed TLKFile object

    Raises:
        TLKParseError: If parsing fails
    """
    reader = TLKReader(file_path)
    return reader.parse()


def find_dialog_tlk(module_dir: Path) -> Optional[Path]:
    """Find dialog.tlk file in module directory or game installation.

    Args:
        module_dir: Module directory to search

    Returns:
        Path to dialog.tlk or None
    """
    # Check module directory first
    tlk_path = module_dir / "dialog.tlk"
    if tlk_path.exists():
        return tlk_path

    # Common NWN:EE installation paths
    nwn_paths = [
        Path("C:/GOG Games/NWN Diamond Edition"),  # GOG
        Path("C:/Program Files (x86)/GOG Galaxy/Games/NWN Diamond Edition"),
        Path("C:/Program Files (x86)/Steam/steamapps/common/Neverwinter Nights"),
        Path("C:/Neverwinter Nights"),
    ]

    for base_path in nwn_paths:
        tlk = base_path / "dialog.tlk"
        if tlk.exists():
            return tlk

        tlk = base_path / "lang" / "en_US" / "dialog.tlk"
        if tlk.exists():
            return tlk

    return None
