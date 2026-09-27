"""Builders of compiled NWScript (NCS) bytecode for hand-made test scripts."""

import struct
from pathlib import Path

from nwn_translator.extractors.base import ExtractedContent
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.formats.ncs import (
    NCS_HEADER,
    OP_ACTION,
    OP_ADD,
    OP_CONST,
    OP_CPTOPSP,
    OP_JMP,
    OP_JSR,
    OP_JZ,
    OP_MOVSP,
    OP_RETN,
    TYPE_INT,
    TYPE_STRING,
    TYPE_STRING_STRING,
    parse_ncs_bytes,
)


def consts(text: str, encoding: str = "cp1252") -> bytes:
    """CONSTS: push a string constant (big-endian length prefix).

    Args:
        text: The string.
        encoding: Codec of the string bytes.

    Returns:
        The instruction bytes.
    """
    encoded = text.encode(encoding)
    return struct.pack(">BBH", OP_CONST, TYPE_STRING, len(encoded)) + encoded


def consti(value: int) -> bytes:
    """CONSTI: push an integer constant.

    Args:
        value: The integer.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBi", OP_CONST, TYPE_INT, value)


def consto(value: int = 0) -> bytes:
    """CONSTO: push an object constant.

    Args:
        value: The object id.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBI", OP_CONST, 0x06, value)


def jmp(offset: int) -> bytes:
    """JMP by a relative offset.

    Args:
        offset: Distance from the start of this instruction.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBi", OP_JMP, 0x00, offset)


def jz(offset: int) -> bytes:
    """JZ by a relative offset.

    Args:
        offset: Distance from the start of this instruction.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBi", OP_JZ, 0x00, offset)


def jsr(offset: int) -> bytes:
    """JSR to a relative offset.

    Args:
        offset: Distance from the start of this instruction.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBi", OP_JSR, 0x00, offset)


def retn() -> bytes:
    """RETN.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BB", OP_RETN, 0x00)


def action(routine: int, arg_count: int = 1) -> bytes:
    """ACTION: call an engine routine.

    Args:
        routine: Number of the engine routine.
        arg_count: Number of arguments the routine takes from the stack.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBHB", OP_ACTION, 0x00, routine, arg_count)


def add_ss() -> bytes:
    """ADD of two strings.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BB", OP_ADD, TYPE_STRING_STRING)


def cptopsp(stack_offset: int = -4, size: int = 4) -> bytes:
    """CPTOPSP: copy a stack slot to the top.

    Args:
        stack_offset: Offset of the slot from the stack top, in bytes.
        size: Number of bytes copied.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBiH", OP_CPTOPSP, 0x01, stack_offset, size)


def movsp(displacement: int) -> bytes:
    """MOVSP: move the stack pointer.

    Args:
        displacement: Change of the stack pointer in bytes; negative pops.

    Returns:
        The instruction bytes.
    """
    return struct.pack(">BBi", OP_MOVSP, 0x00, displacement)


def script(*parts: bytes) -> bytes:
    """A complete script: the NCS header followed by *parts*.

    Args:
        *parts: Instruction bytes, in order.

    Returns:
        The script bytes.
    """
    return NCS_HEADER + b"".join(parts)


def write_ncs(directory: Path, name: str, *parts: bytes) -> Path:
    """Write ``script(*parts)`` to ``directory / name``.

    Args:
        directory: Directory of the script.
        name: File name of the script.
        *parts: Instruction bytes, in order.

    Returns:
        The path of the written script.
    """
    path = directory / name
    path.write_bytes(script(*parts))
    return path


def extract_script(directory: Path, *parts: bytes, name: str = "scene.ncs") -> ExtractedContent:
    """Extract ``script(*parts)`` as the script ``directory / name``.

    Matching ``.nss`` sources in *directory* are read as for a module script.

    Args:
        directory: Directory the script is taken to live in.
        *parts: Instruction bytes, in order.
        name: File name of the script.

    Returns:
        What :class:`NcsExtractor` extracts from the script.
    """
    ncs = parse_ncs_bytes(script(*parts))
    return NcsExtractor().extract(directory / name, {"_ncs_file": ncs})
