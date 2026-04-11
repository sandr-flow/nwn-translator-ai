"""File handlers for NWN file formats."""

from .gff_handler import (
    GFFHandler,
    GFFHandlerError,
    read_gff,
    write_gff,
)
from .gff_writer import GFFWriter, GFFWriteError, write_gff_bytes
from .erf_reader import ERFReader, ERFReaderError, ERFHeader, ERFEntry
from .erf_writer import ERFWriter, ERFWriterError, create_mod_from_directory
from .ncs_parser import NCSFile, NCSInstruction, NCSParseError, parse_ncs
from .ncs_patcher import NCSPatchError, patch_ncs_string_replacements, patch_ncs_strings

__all__ = [
    "GFFHandler",
    "GFFHandlerError",
    "read_gff",
    "write_gff",
    "GFFWriter",
    "GFFWriteError",
    "write_gff_bytes",
    "ERFReader",
    "ERFReaderError",
    "ERFHeader",
    "ERFEntry",
    "ERFWriter",
    "ERFWriterError",
    "create_mod_from_directory",
    "NCSFile",
    "NCSInstruction",
    "NCSParseError",
    "parse_ncs",
    "NCSPatchError",
    "patch_ncs_string_replacements",
    "patch_ncs_strings",
]
