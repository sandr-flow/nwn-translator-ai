"""ERF archives (``.mod``, ``.erf``, ``.hak``): reading, extraction and repacking.

ERF V1.0 layout (integers are little-endian DWORDs):

- header, 160 bytes: FileType, Version, LanguageCount, LocalizedStringSize,
  EntryCount, OffsetToLocalizedString, OffsetToKeyList, OffsetToResourceList,
  BuildYear (years since 1900), BuildDay (0-based), DescriptionStrRef and 116
  reserved bytes;
- localized string list (the module description), LocalizedStringSize bytes;
- key list: EntryCount x (ResRef[16], ResID, ResType);
- resource list: EntryCount x (OffsetToResource, ResourceSize);
- resource data.

ResType is a WORD followed by an unused WORD; both are read and written as one
DWORD, which round-trips the bytes unchanged.

Extraction writes every resource to ``<resref><extension>`` (see
:meth:`ERFReader.filename_for`); repacking rebuilds the archive from such a
directory and takes the original type ids from the source archive by the same
file names.
"""

import datetime
import logging
import os
import struct
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Union

from tqdm import tqdm

from ..config import ProgressCallback
from .text_codec import decode_fixed_ascii

logger = logging.getLogger(__name__)

#: Header: FileType, Version, 9 DWORDs, reserved bytes.
HEADER = struct.Struct("<4s4s9I116s")
#: Key list entry: ResRef, ResID (index into the resource list), ResType.
KEY = struct.Struct("<16sII")
#: Resource list entry: data offset, data size.
RESOURCE = struct.Struct("<II")
#: Resource-list offset of an entry that has no data.
UNUSED_OFFSET = 0xFFFFFFFF
#: DescriptionStrRef of an archive without a talk-table description.
NO_STRREF = 0xFFFFFFFF
VERSION = b"V1.0"
#: Output suffix -> FileType written into the header.
FILE_TYPES = {".mod": b"MOD ", ".erf": b"ERF ", ".hak": b"HAK "}

_COPY_CHUNK = 1024 * 1024

#: Resource type id -> file extension.
RESOURCE_TYPES: Dict[int, str] = {
    0: ".bmp",
    1: ".tga",
    2: ".wav",
    3: ".plt",
    4: ".ini",
    5: ".txt",
    6: ".mdl",
    7: ".thg",
    8: ".fxt",
    9: ".txi",
    10: ".git",
    11: ".uti",
    12: ".ptc",
    13: ".sst",
    14: ".ncs",
    15: ".mod",
    16: ".are",
    17: ".set",
    18: ".ifo",
    19: ".bic",
    20: ".wok",
    21: ".2da",
    22: ".tlk",
    23: ".txi",
    24: ".git",
    25: ".bti",
    26: ".utc",
    27: ".dlg",
    28: ".itp",
    29: ".btt",
    30: ".utt",
    31: ".btc",
    32: ".uts",
    33: ".utr",
    34: ".btd",
    35: ".btp",
    36: ".ptm",
    37: ".ptt",
    38: ".ncs",
    39: ".bfx",
    40: ".bte",
    41: ".css",
    42: ".fs",
    43: ".jrl",
    44: ".sec",
    45: ".ifo",
    46: ".bio",
    47: ".spe",
    48: ".sem",
    49: ".lus",
    50: ".gor",
    51: ".fxs",
    52: ".wmp",
    53: ".fac",
    54: ".gff",
    55: ".gam",
    56: ".gui",
    57: ".ute",
    58: ".utp",
    59: ".utm",
    60: ".utw",
    61: ".uts",
    62: ".utr",
    63: ".utf",
    64: ".utd",
    65: ".utn",
    66: ".pal",
    67: ".pdf",
    68: ".gic",
    69: ".fxe",
    70: ".ptx",
    71: ".png",
    72: ".ltx",
    73: ".utx",
    74: ".gff",
    75: ".xml",
    76: ".xba",
    77: ".ids",
    78: ".bwd",
    79: ".bwm",
    2002: ".res",
    2009: ".nss",
    2010: ".ncs",
    2011: ".mod",
    2012: ".are",
    2013: ".set",
    2014: ".ifo",
    2015: ".bic",
    2016: ".wok",
    2017: ".2da",
    2018: ".tlk",
    2022: ".txi",
    2023: ".git",
    2024: ".bti",
    2025: ".uti",
    2026: ".btc",
    2027: ".utc",
    2029: ".dlg",
    2030: ".itp",
    2031: ".btt",
    2032: ".utt",
    2033: ".dds",
    2034: ".bts",
    2035: ".uts",
    2036: ".ltr",
    2037: ".gff",
    2038: ".fac",
    2039: ".bte",
    2040: ".ute",
    2041: ".btd",
    2042: ".utd",
    2043: ".btp",
    2044: ".utp",
    2045: ".dft",
    2046: ".gic",
    2047: ".gui",
    2048: ".css",
    2049: ".ccs",
    2050: ".btm",
    2051: ".utm",
    2052: ".dwk",
    2053: ".pwk",
    2054: ".btg",
    2055: ".utg",
    2056: ".jrl",
    2057: ".sav",
    2058: ".utw",
    2059: ".4pc",
    2060: ".ssf",
    2064: ".ndb",
    2065: ".ptm",
    2066: ".ptt",
}


def _type_ids_by_extension() -> Dict[str, int]:
    """Invert :data:`RESOURCE_TYPES`, preferring the 20xx id of a repeated extension."""
    ids: Dict[str, int] = {}
    for type_id, ext in sorted(RESOURCE_TYPES.items()):
        if ext not in ids or (type_id >= 2000 and ids[ext] < 2000):
            ids[ext] = type_id
    return ids


#: File extension -> resource type id written for files without a source type.
TYPE_ID_BY_EXTENSION = _type_ids_by_extension()

# Third-party archives sometimes store resources under custom type ids; the
# 4-byte signature at the start of a GFF (or NCS) resource names its real type.
SIGNATURE_EXTENSIONS: Dict[bytes, str] = {
    f"{ext.upper()} ".encode("ascii"): f".{ext}"
    for ext in "are dlg fac gff gic git ifo jrl ncs ute utd uti utm utp utr uts utt utw utc".split()
}

# Characters Windows does not allow in file names.
_UNSAFE_FILENAME_CHARS = str.maketrans({c: "_" for c in [*map(chr, range(32)), *'<>:"/\\|?*']})


class ERFError(Exception):
    """Raised when an ERF archive cannot be read or written."""


def extension_for_type(type_id: int) -> str:
    """Return the file extension of a resource type id.

    Args:
        type_id: ERF resource type id.

    Returns:
        The extension with a leading dot; ``".<id>"`` for unknown ids.
    """
    return RESOURCE_TYPES.get(type_id, f".{type_id}")


@dataclass(frozen=True)
class ERFHeader:
    """ERF V1.0 header.

    Attributes:
        file_type: FileType tag (``b"MOD "``, ``b"ERF "`` or ``b"HAK "``).
        version: Version tag, always ``b"V1.0"``.
        language_count: Number of localized description strings.
        localized_string_size: Byte size of the localized string list.
        entry_count: Number of resources.
        offset_to_localized_string: File offset of the localized string list.
        offset_to_key_list: File offset of the key list.
        offset_to_resource_list: File offset of the resource list.
        build_year: Build year minus 1900.
        build_day: 0-based day of the build year.
        description_strref: Talk-table StrRef of the description.
    """

    file_type: bytes
    version: bytes
    language_count: int
    localized_string_size: int
    entry_count: int
    offset_to_localized_string: int
    offset_to_key_list: int
    offset_to_resource_list: int
    build_year: int
    build_day: int
    description_strref: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "ERFHeader":
        """Parse and validate the first 160 bytes of an archive.

        Args:
            data: Archive bytes starting at offset 0.

        Returns:
            The parsed header.

        Raises:
            ERFError: If the data is too short, the file type is unknown or the
                version is not V1.0.
        """
        if len(data) < HEADER.size:
            raise ERFError("Invalid ERF header: too short")
        header = cls(*HEADER.unpack_from(data)[:-1])
        if header.file_type not in FILE_TYPES.values():
            raise ERFError(f"Invalid file type: {header.file_type!r}")
        if header.version != VERSION:
            raise ERFError(
                f"Unsupported ERF version {header.version!r}; "
                "only V1.0 (NWN / NWN:EE) is supported"
            )
        return header

    def pack(self) -> bytes:
        """Return the 160-byte header with zeroed reserved bytes."""
        return HEADER.pack(*astuple(self), b"")


@dataclass(frozen=True)
class ERFEntry:
    """One resource of an archive.

    Attributes:
        res_ref: Resource name without extension.
        res_id: Index into the resource list.
        res_type: Resource type id as stored in the key list.
        offset: File offset of the data (:data:`UNUSED_OFFSET` if none).
        size: Data size in bytes.
    """

    res_ref: str
    res_id: int
    res_type: int
    offset: int
    size: int


class ERFReader:
    """Reader for one ERF archive.

    Attributes:
        file_path: The archive.
        progress_callback: Called as ``("extracting", index, total, res_ref)``
            before each entry during :meth:`extract_all`; without it a tqdm
            bar is shown.
        header: The header, once read.
        entries: The entries, once read by :meth:`read_entries`.
    """

    def __init__(self, file_path: Path, progress_callback: Optional[ProgressCallback] = None):
        """Open an archive for reading.

        Args:
            file_path: Path of the ``.mod``, ``.erf`` or ``.hak`` file.
            progress_callback: Extraction progress callback (see the class).

        Raises:
            ERFError: If the file does not exist.
        """
        self.file_path = Path(file_path)
        self.progress_callback = progress_callback
        self.header: Optional[ERFHeader] = None
        self.entries: List[ERFEntry] = []
        # res_id -> extension, filled by read_entries().
        self._extensions: Dict[int, str] = {}
        if not self.file_path.exists():
            raise ERFError(f"File not found: {file_path}")

    def read_header(self) -> ERFHeader:
        """Read the header and check that the declared tables fit the file.

        The size check runs before anything is allocated per entry, so a
        crafted header with a huge entry count fails fast.

        Returns:
            The header.

        Raises:
            ERFError: If the header is invalid or its tables exceed the file.
        """
        with open(self.file_path, "rb") as f:
            header = ERFHeader.from_bytes(f.read(HEADER.size))
        file_size = self.file_path.stat().st_size
        key_list_end = header.offset_to_key_list + header.entry_count * KEY.size
        res_list_end = header.offset_to_resource_list + header.entry_count * RESOURCE.size
        if key_list_end > file_size or res_list_end > file_size:
            raise ERFError(
                f"Corrupt ERF header: {header.entry_count} entries do not fit "
                f"in a {file_size}-byte file"
            )
        self.header = header
        return header

    def read_localized_strings_block(self) -> bytes:
        """Read the raw localized string list (the module description).

        Returns:
            The block as stored, or ``b""`` when the header declares none or
            the declared region does not fit the file.

        Raises:
            ERFError: If the header is invalid.
        """
        header = self.header or self.read_header()
        size = header.localized_string_size
        if size == 0:
            return b""
        offset = header.offset_to_localized_string
        file_size = self.file_path.stat().st_size
        if offset + size > file_size:
            logger.warning(
                "Localized string block %d+%d exceeds file size %d; treating as absent",
                offset,
                size,
                file_size,
            )
            return b""
        with open(self.file_path, "rb") as f:
            f.seek(offset)
            return f.read(size)

    def read_entries(self) -> List[ERFEntry]:
        """Read the key and resource lists and detect each entry's extension.

        Returns:
            The entries in key-list order (also stored in :attr:`entries`).

        Raises:
            ERFError: If an entry's data lies outside the file, or the entries
                declare more data than the file holds (overlapping entries).
        """
        header = self.header or self.read_header()
        count = header.entry_count
        file_size = self.file_path.stat().st_size
        with open(self.file_path, "rb") as f:
            f.seek(header.offset_to_key_list)
            keys = list(KEY.iter_unpack(f.read(count * KEY.size)))
            # Toolset archives leave an unused gap after the key list, so the
            # resource list is found by its own offset.
            f.seek(header.offset_to_resource_list)
            resources = list(RESOURCE.iter_unpack(f.read(count * RESOURCE.size)))

            # ERF stores resources uncompressed, so in a well-formed archive the
            # data regions are disjoint and cannot add up to more than the file;
            # crafted overlapping entries would otherwise let a small upload
            # extract to an unbounded volume on disk.
            entries: List[ERFEntry] = []
            total_data_size = 0
            for raw_ref, res_id, res_type in keys:
                res_ref = decode_fixed_ascii(raw_ref)
                if res_id >= len(resources):
                    logger.warning("Invalid resource ID %s for %s", res_id, res_ref)
                    continue
                offset, size = resources[res_id]
                if offset != UNUSED_OFFSET:
                    if offset + size > file_size:
                        raise ERFError(
                            f"Corrupt ERF entry {res_ref!r}: data region "
                            f"{offset}+{size} exceeds file size {file_size}"
                        )
                    total_data_size += size
                entries.append(ERFEntry(res_ref, res_id, res_type, offset, size))
            if total_data_size > file_size:
                raise ERFError(
                    f"Corrupt ERF: entries declare {total_data_size} bytes of data "
                    f"in a {file_size}-byte file (overlapping entries)"
                )

            self._extensions.clear()
            for entry in entries:
                ext = None
                if entry.offset != UNUSED_OFFSET:
                    f.seek(entry.offset)
                    ext = SIGNATURE_EXTENSIONS.get(f.read(4))
                self._extensions[entry.res_id] = ext or extension_for_type(entry.res_type)
        self.entries = entries
        return entries

    def extension_for(self, entry: ERFEntry) -> str:
        """Return the extension of *entry*: from its signature if known, else its type id.

        Args:
            entry: An entry returned by :meth:`read_entries` of this reader.

        Returns:
            The extension with a leading dot, e.g. ``".dlg"``.
        """
        return self._extensions[entry.res_id]

    def filename_for(self, entry: ERFEntry) -> str:
        """Return the file name :meth:`extract_all` writes *entry* to.

        Characters Windows forbids in file names become ``_``.

        Args:
            entry: An entry returned by :meth:`read_entries` of this reader.

        Returns:
            ``<resref><extension>``, e.g. ``"guard.dlg"``.
        """
        return (entry.res_ref + self.extension_for(entry)).translate(_UNSAFE_FILENAME_CHARS)

    def extract_all(self, output_dir: Path) -> Path:
        """Write every resource with data to *output_dir*.

        Args:
            output_dir: Target directory, created if missing.

        Returns:
            *output_dir* as a :class:`~pathlib.Path`.

        Raises:
            ERFError: If the archive is invalid.
        """
        entries = self.entries or self.read_entries()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        callback = self.progress_callback
        total = len(entries)
        with open(self.file_path, "rb") as f:
            progress = (
                tqdm(entries, desc="Extracting ERF", disable=None) if callback is None else entries
            )
            for index, entry in enumerate(progress):
                if callback is not None:
                    callback("extracting", index, total, entry.res_ref)
                if entry.offset == UNUSED_OFFSET:
                    continue
                f.seek(entry.offset)
                (output_dir / self.filename_for(entry)).write_bytes(f.read(entry.size))
        return output_dir


class ERFWriter:
    """Writer for one ERF archive.

    Resources are kept as bytes or as paths read at :meth:`write` time, so a
    module is never held in memory whole. They are stored in code-point
    order of their file names; ``res_id`` is the position in that order.

    Attributes:
        output_path: Archive to write.
        type_overrides: File name -> resource type id to write instead of the
            id derived from the extension.
        file_type: FileType tag, from the output suffix.
    """

    def __init__(self, output_path: Path, type_overrides: Optional[Dict[str, int]] = None):
        """Start an empty archive.

        Args:
            output_path: Where :meth:`write` puts the archive.
            type_overrides: File name -> exact resource type id.
        """
        self.output_path = Path(output_path)
        self.type_overrides: Dict[str, int] = type_overrides or {}
        self.file_type = FILE_TYPES.get(self.output_path.suffix.lower(), b"ERF ")
        # File name (stem + lower-case extension) -> data or source file.
        self._resources: Dict[str, Union[bytes, Path]] = {}
        self._language_count = 0
        self._localized_strings = b""
        self._description_strref = NO_STRREF

    def add_resource(self, res_ref: str, res_type: str, data: bytes) -> None:
        """Add a resource from memory.

        Args:
            res_ref: Resource name without extension (at most 16 ASCII chars).
            res_type: Extension with the dot, e.g. ``".dlg"``.
            data: Resource bytes.
        """
        self._resources[f"{res_ref}{res_type.lower()}"] = data

    def add_file(self, file_path: Path) -> None:
        """Add a file; its content is read only by :meth:`write`.

        Args:
            file_path: File whose stem is the resref and suffix the type.
        """
        file_path = Path(file_path)
        self._resources[f"{file_path.stem}{file_path.suffix.lower()}"] = file_path

    def add_directory(self, directory: Path) -> None:
        """Add every file under *directory*, recursively.

        Args:
            directory: Root directory; sub-directories are flattened.
        """
        for file_path in Path(directory).rglob("*"):
            if file_path.is_file():
                self.add_file(file_path)

    def set_localized_strings(
        self, language_count: int, raw_block: bytes, description_strref: int
    ) -> None:
        """Carry the module description from a source archive.

        Args:
            language_count: LanguageCount of the source archive.
            raw_block: Raw localized string list (may be empty).
            description_strref: DescriptionStrRef of the source archive.
        """
        self._language_count = language_count
        self._localized_strings = raw_block
        self._description_strref = description_strref

    def write(self) -> None:
        """Write the archive to :attr:`output_path`.

        Tables are built in memory; resource data is streamed into a
        ``.tmp`` file next to the output, which then replaces the output, so
        a failed write never destroys a previous archive.

        Raises:
            ERFError: If a source file cannot be read or changes size, or a
                resref exceeds 16 bytes.
        """
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        resources = sorted(self._resources.items())
        try:
            sizes = [
                len(src) if isinstance(src, bytes) else src.stat().st_size for _, src in resources
            ]
        except OSError as exc:
            raise ERFError(f"Failed to stat resource file: {exc}") from exc

        key_list_offset = HEADER.size + len(self._localized_strings)
        resource_list_offset = key_list_offset + len(resources) * KEY.size
        data_offset = resource_list_offset + len(resources) * RESOURCE.size
        key_list = bytearray()
        resource_list = bytearray()
        for res_id, ((filename, _src), size) in enumerate(zip(resources, sizes)):
            name = Path(filename)
            type_id = self.type_overrides.get(
                filename, TYPE_ID_BY_EXTENSION.get(name.suffix.lower(), 0)
            )
            key_list += KEY.pack(_resref_bytes(name.stem), res_id, type_id)
            resource_list += RESOURCE.pack(data_offset, size)
            data_offset += size

        now = datetime.datetime.now()
        header = ERFHeader(
            file_type=self.file_type,
            version=VERSION,
            language_count=self._language_count,
            localized_string_size=len(self._localized_strings),
            entry_count=len(resources),
            offset_to_localized_string=HEADER.size if self._localized_strings else 0,
            offset_to_key_list=key_list_offset,
            offset_to_resource_list=resource_list_offset,
            build_year=now.year - 1900,
            build_day=now.timetuple().tm_yday - 1,
            description_strref=self._description_strref,
        )

        # os.replace is atomic only within one volume, hence the same directory.
        tmp_path = self.output_path.with_name(self.output_path.name + ".tmp")
        try:
            with open(tmp_path, "wb") as out:
                out.write(header.pack())
                out.write(self._localized_strings)
                out.write(key_list)
                out.write(resource_list)
                for (filename, src), size in zip(resources, sizes):
                    written = _copy_into(out, src)
                    if written != size:
                        # The resource list already holds the declared size.
                        raise ERFError(
                            f"Resource {filename} changed size during write "
                            f"(declared {size}, wrote {written})"
                        )
            os.replace(tmp_path, self.output_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        logger.info(
            "ERF archive written: %s (%d bytes, %d resources)",
            self.output_path,
            data_offset,
            len(resources),
        )


def _resref_bytes(res_ref: str) -> bytes:
    """Encode a resref for the key list; non-ASCII characters become ``?``."""
    raw = res_ref.encode("ascii", errors="replace")
    if len(raw) > 16:
        raise ERFError(
            f"Resource name {res_ref!r} is {len(raw)} bytes; ERF resrefs are limited to 16"
        )
    return raw


def _copy_into(out: BinaryIO, src: Union[bytes, Path]) -> int:
    """Append one resource's data to *out* and return the number of bytes written."""
    if isinstance(src, bytes):
        out.write(src)
        return len(src)
    written = 0
    with open(src, "rb") as f:
        while chunk := f.read(_COPY_CHUNK):
            out.write(chunk)
            written += len(chunk)
    return written


def create_mod_from_directory(
    input_dir: Path, output_path: Path, original_mod: Optional[Path] = None
) -> None:
    """Pack a directory of extracted resources into an archive.

    With *original_mod*, every file keeps the type id its resource had in
    that archive (matched by :meth:`ERFReader.filename_for`), and the module
    description is carried over.

    Args:
        input_dir: Directory written by :meth:`ERFReader.extract_all`.
        output_path: Archive to write; its suffix picks the FileType.
        original_mod: The archive *input_dir* was extracted from.

    Raises:
        ERFError: If *original_mod* cannot be read or the write fails.
    """
    writer = ERFWriter(output_path)
    if original_mod and original_mod.exists():
        try:
            reader = ERFReader(original_mod)
            entries = reader.read_entries()
            writer.type_overrides = {
                reader.filename_for(entry): entry.res_type for entry in entries
            }
            header = reader.header or reader.read_header()
            localized_strings = reader.read_localized_strings_block()
            writer.set_localized_strings(
                # A corrupt block reads back empty; LanguageCount > 0 with a zero
                # LocalizedStringSize would make an invalid header.
                header.language_count if localized_strings else 0,
                localized_strings,
                header.description_strref,
            )
        except Exception as exc:
            logger.error("Could not read original mod for metadata: %s", exc)
            raise ERFError(
                f"Failed to read resource types from original module {original_mod}: {exc}"
            ) from exc
    writer.add_directory(input_dir)
    writer.write()
