"""NCS bytecode parser for Neverwinter Nights compiled scripts.

NCS files contain compiled NWScript bytecode executed by the game's
stack-based virtual machine.  This module parses the binary format to
extract individual instructions, with special focus on string constants
(CONSTS) that may contain player-visible text.

Binary format reference:
  - Header: ``NCS V1.0`` (8 bytes)
  - **Classic:** bytecode starts at offset 8
  - **NWN:EE (Beamdog / xoreos):** bytes 8–12 are ``0x42`` + uint32 BE declared script
    length; real opcodes start at offset **13**
  - Byte order: **big-endian** for all multi-byte values
  - Each instruction: 1-byte opcode + 1-byte type qualifier + variable args
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class NCSParseError(Exception):
    """Raised when an NCS file cannot be parsed."""


# ---------------------------------------------------------------------------
# Opcode constants
# ---------------------------------------------------------------------------

OP_CPDOWNSP = 0x01
OP_RSADD = 0x02
OP_CPTOPSP = 0x03
OP_CONST = 0x04
OP_ACTION = 0x05
OP_LOGAND = 0x06
OP_LOGOR = 0x07
OP_INCOR = 0x08
OP_EXCOR = 0x09
OP_BOOLAND = 0x0A
OP_EQUAL = 0x0B
OP_NEQUAL = 0x0C
OP_GEQ = 0x0D
OP_GT = 0x0E
OP_LT = 0x0F
OP_LEQ = 0x10
OP_SHLEFT = 0x11
OP_SHRIGHT = 0x12
# Unsigned/signed shift (Torlack / xoreos; stack op,2-byte instruction)
OP_USHRIGHT = 0x13
OP_ADD = 0x14
OP_SUB = 0x15
OP_MUL = 0x16
OP_DIV = 0x17
OP_MOD = 0x18
OP_NEG = 0x19
OP_COMP = 0x1A
OP_MOVSP = 0x1B
OP_STORE_STATEALL = 0x1C
OP_JMP = 0x1D
OP_JSR = 0x1E
OP_JZ = 0x1F
OP_RETN = 0x20
OP_DESTRUCT = 0x21
OP_NOT = 0x22
OP_DECISP = 0x23
OP_INCISP = 0x24
OP_JNZ = 0x25
OP_CPDOWNBP = 0x26
OP_CPTOPBP = 0x27
OP_DECIBP = 0x28
OP_INCIBP = 0x29
OP_SAVEBP = 0x2A
OP_RESTOREBP = 0x2B
OP_STORE_STATE = 0x2C
OP_NOP = 0x2D

# Type qualifiers for CONST instruction
TYPE_INT = 0x03
TYPE_FLOAT = 0x04
TYPE_STRING = 0x05
TYPE_OBJECT = 0x06

# Jump opcodes (instructions whose args contain a relative offset)
JUMP_OPCODES = frozenset({OP_JMP, OP_JSR, OP_JZ, OP_JNZ})

# ---------------------------------------------------------------------------
# Opcode argument sizes (bytes AFTER the 2-byte opcode+type header)
# ---------------------------------------------------------------------------

# Most opcodes have 0 extra bytes (header-only = 2 bytes total).
# This dict lists exceptions.
_OPCODE_ARG_SIZES: Dict[int, int] = {
    OP_CPDOWNSP: 4,       # int32: stack offset + size
    OP_CPTOPSP: 4,        # int32: stack offset + size
    OP_ACTION: 3,         # uint16 routine number + uint8 arg count
    OP_MOVSP: 4,          # int32: displacement
    OP_STORE_STATEALL: 4, # int32 (obsolete, but may appear)
    OP_JMP: 4,            # int32: relative offset
    OP_JSR: 4,            # int32: relative offset
    OP_JZ: 4,             # int32: relative offset
    OP_DESTRUCT: 6,       # int16 + int16 + int16
    OP_DECISP: 4,         # int32
    OP_INCISP: 4,         # int32
    OP_JNZ: 4,            # int32: relative offset
    OP_CPDOWNBP: 4,       # int32
    OP_CPTOPBP: 4,        # int32
    OP_DECIBP: 4,         # int32
    OP_INCIBP: 4,         # int32
    OP_STORE_STATE: 8,    # int32 BP size + int32 stack size
}

# CONST arg sizes by type qualifier (TYPE_STRING is variable-length)
_CONST_ARG_SIZES: Dict[int, int] = {
    TYPE_INT: 4,
    TYPE_FLOAT: 4,
    TYPE_OBJECT: 4,
    # TYPE_STRING handled separately (2-byte length prefix + N bytes)
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class NCSInstruction:
    """A single parsed NCS bytecode instruction."""

    offset: int         # absolute byte offset from file start
    opcode: int         # 1-byte opcode
    type_byte: int      # 1-byte type qualifier
    size: int           # total instruction size in bytes
    args: bytes         # raw argument bytes (after opcode+type)

    # Convenience fields populated during parsing:
    string_value: Optional[str] = None   # decoded string for CONSTS
    jump_offset: Optional[int] = None    # signed int32 for jump opcodes
    action_routine: Optional[int] = None # routine number for ACTION opcode
    action_arg_count: Optional[int] = None

    @property
    def is_string_const(self) -> bool:
        """True if this instruction pushes a string constant."""
        return self.opcode == OP_CONST and self.type_byte == TYPE_STRING

    @property
    def is_jump(self) -> bool:
        """True if this instruction contains a relative jump offset."""
        return self.opcode in JUMP_OPCODES

    @property
    def is_action(self) -> bool:
        """True if this instruction is an ACTION (engine function call)."""
        return self.opcode == OP_ACTION


@dataclass
class NCSFile:
    """Parsed NCS script file."""

    header: bytes                       # raw 8-byte header
    instructions: List[NCSInstruction]  # ordered list of all instructions
    raw_bytes: bytearray                # complete file contents

    @property
    def string_constants(self) -> List[NCSInstruction]:
        """All CONSTS (string) instructions."""
        return [i for i in self.instructions if i.is_string_const]

    @property
    def jump_instructions(self) -> List[NCSInstruction]:
        """All jump instructions (JMP/JSR/JZ/JNZ)."""
        return [i for i in self.instructions if i.is_jump]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

NCS_HEADER = b"NCS V1.0"
NCS_HEADER_SIZE = 8

# NWN:EE preamble after the 8-byte banner (see xoreos ``NCSFile::load``).
_NCS_EE_SCRIPT_SIZE_OPCODE = 0x42
_NCS_EE_SCRIPT_SIZE_PREFIX_LEN = 5  # opcode + 4-byte BE length


def _decode_string(raw: bytes) -> str:
    """Decode a string from NCS bytecode (CP1252 with fallback)."""
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _parse_instruction(data: bytes, offset: int) -> NCSInstruction:
    """Parse a single instruction starting at *offset* in *data*.

    Args:
        data: Complete file bytes.
        offset: Byte offset of the instruction.

    Returns:
        Parsed NCSInstruction.

    Raises:
        NCSParseError: If the instruction cannot be parsed.
    """
    if offset + 2 > len(data):
        raise NCSParseError(
            f"Unexpected end of file at offset {offset:#x}: "
            f"need 2 bytes for opcode+type, have {len(data) - offset}"
        )

    opcode = data[offset]
    type_byte = data[offset + 1]

    # Determine argument size
    if opcode == OP_CONST:
        if type_byte == TYPE_STRING:
            # Variable-length: 2-byte BE length prefix + string bytes
            if offset + 4 > len(data):
                raise NCSParseError(
                    f"Unexpected end of file at offset {offset:#x}: "
                    f"CONSTS needs at least 4 bytes"
                )
            str_len = struct.unpack_from(">H", data, offset + 2)[0]
            arg_size = 2 + str_len  # length prefix + string data
        elif type_byte in _CONST_ARG_SIZES:
            arg_size = _CONST_ARG_SIZES[type_byte]
        else:
            # Unknown CONST type -- try 4 bytes (most common)
            arg_size = 4
    elif opcode in _OPCODE_ARG_SIZES:
        arg_size = _OPCODE_ARG_SIZES[opcode]
    else:
        arg_size = 0  # header-only instruction

    total_size = 2 + arg_size
    if offset + total_size > len(data):
        raise NCSParseError(
            f"Unexpected end of file at offset {offset:#x}: "
            f"instruction (opcode {opcode:#04x}) needs {total_size} bytes, "
            f"have {len(data) - offset}"
        )

    args = data[offset + 2 : offset + total_size]

    # Build instruction
    instr = NCSInstruction(
        offset=offset,
        opcode=opcode,
        type_byte=type_byte,
        size=total_size,
        args=bytes(args),
    )

    # Populate convenience fields
    if opcode == OP_CONST and type_byte == TYPE_STRING:
        str_len = struct.unpack_from(">H", data, offset + 2)[0]
        raw_str = data[offset + 4 : offset + 4 + str_len]
        instr.string_value = _decode_string(raw_str)

    if opcode in JUMP_OPCODES:
        instr.jump_offset = struct.unpack_from(">i", data, offset + 2)[0]

    if opcode == OP_ACTION:
        instr.action_routine = struct.unpack_from(">H", data, offset + 2)[0]
        instr.action_arg_count = data[offset + 4]

    return instr


def parse_ncs(file_path: Path) -> NCSFile:
    """Parse an NCS bytecode file.

    Args:
        file_path: Path to the ``.ncs`` file.

    Returns:
        Parsed NCSFile with all instructions.

    Raises:
        NCSParseError: If the file is invalid or cannot be parsed.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise NCSParseError(f"File not found: {file_path}")

    raw = file_path.read_bytes()
    return parse_ncs_bytes(raw)


def parse_ncs_bytes(raw: bytes) -> NCSFile:
    """Parse NCS bytecode from raw bytes.

    Args:
        raw: Complete file contents.

    Returns:
        Parsed NCSFile.

    Raises:
        NCSParseError: If the data is invalid.
    """
    if len(raw) < NCS_HEADER_SIZE:
        raise NCSParseError(
            f"File too small ({len(raw)} bytes): expected at least {NCS_HEADER_SIZE}"
        )

    header = raw[:NCS_HEADER_SIZE]
    if header != NCS_HEADER:
        raise NCSParseError(
            f"Invalid NCS header: expected {NCS_HEADER!r}, got {header!r}"
        )

    data = bytearray(raw)
    instructions: List[NCSInstruction] = []
    cursor = NCS_HEADER_SIZE
    if (
        len(data) >= NCS_HEADER_SIZE + _NCS_EE_SCRIPT_SIZE_PREFIX_LEN
        and data[cursor] == _NCS_EE_SCRIPT_SIZE_OPCODE
    ):
        cursor = NCS_HEADER_SIZE + _NCS_EE_SCRIPT_SIZE_PREFIX_LEN

    while cursor < len(data):
        instr = _parse_instruction(data, cursor)
        instructions.append(instr)
        cursor += instr.size

    return NCSFile(
        header=bytes(header),
        instructions=instructions,
        raw_bytes=data,
    )
