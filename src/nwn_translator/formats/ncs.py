"""NCS compiled NWScript: bytecode parsing and string-constant patching.

Layout: the 8-byte banner ``NCS V1.0``, then the program-size preamble
(opcode ``0x42`` followed by the big-endian uint32 file length), then the
instructions. Every NWN and NWN:EE compiler emits the preamble; a file without
it is parsed from offset 8. All multi-byte values are big-endian. Each
instruction is a 1-byte opcode, a 1-byte type qualifier and opcode-specific
argument bytes.

Patching replaces string constants (CONSTS). A new length shifts every later
instruction, so relative jump offsets that cross a patched instruction are
adjusted and the preamble's size field is rewritten.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .text_codec import MODULE_ENCODINGS, decode_module_text, encode_module_text

logger = logging.getLogger(__name__)


class NCSParseError(Exception):
    """Raised when an NCS file cannot be parsed."""


class NCSPatchError(Exception):
    """Raised when NCS patching fails."""


# ---------------------------------------------------------------------------
# Opcodes and type qualifiers
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

# CONST type qualifiers.
TYPE_INT = 0x03
TYPE_FLOAT = 0x04
TYPE_STRING = 0x05
TYPE_OBJECT = 0x06

#: Opcodes whose argument is a signed relative jump offset.
JUMP_OPCODES = frozenset({OP_JMP, OP_JSR, OP_JZ, OP_JNZ})

# Argument bytes after the opcode and type byte; unlisted opcodes have none.
_OPCODE_ARG_SIZES: Dict[int, int] = {
    OP_CPDOWNSP: 6,  # int32 stack offset + uint16 size
    OP_CPTOPSP: 6,  # int32 stack offset + uint16 size
    OP_ACTION: 3,  # uint16 routine number + uint8 argument count
    OP_MOVSP: 4,
    OP_STORE_STATEALL: 4,
    OP_JMP: 4,
    OP_JSR: 4,
    OP_JZ: 4,
    OP_DESTRUCT: 6,  # three int16
    OP_DECISP: 4,
    OP_INCISP: 4,
    OP_JNZ: 4,
    OP_CPDOWNBP: 6,  # int32 stack offset + uint16 size
    OP_CPTOPBP: 6,  # int32 stack offset + uint16 size
    OP_DECIBP: 4,
    OP_INCIBP: 4,
    OP_STORE_STATE: 8,  # int32 BP size + int32 stack size
}
# EQUAL/NEQUAL of two structures (type 0x24) carry a trailing uint16 size;
# missing it desyncs the instruction stream.
_STRUCT_COMPARE_TYPE = 0x24
_STRUCT_COMPARE_OPCODES = frozenset({OP_EQUAL, OP_NEQUAL})
# CONST argument sizes by type; strings are a uint16 length plus the bytes, and
# an unknown type is assumed to be 4 bytes like the others.
_CONST_ARG_SIZES: Dict[int, int] = {TYPE_INT: 4, TYPE_FLOAT: 4, TYPE_OBJECT: 4}

NCS_HEADER = b"NCS V1.0"
NCS_HEADER_SIZE = 8
# Program-size preamble after the banner: opcode, then the BE uint32 file size.
_PREAMBLE_OPCODE = 0x42
_PREAMBLE_SIZE = 5
_PREAMBLE_SIZE_FIELD = NCS_HEADER_SIZE + 1


@dataclass
class NCSInstruction:
    """One parsed instruction.

    Attributes:
        offset: Absolute byte offset in the file.
        opcode: Opcode byte.
        type_byte: Type qualifier byte.
        size: Total instruction size in bytes.
        args: Argument bytes after the opcode and type byte.
        string_value: Decoded text of a string constant.
        jump_offset: Signed relative offset of a jump.
        action_routine: Engine routine number of an ACTION.
        action_arg_count: Argument count of an ACTION.
    """

    offset: int
    opcode: int
    type_byte: int
    size: int
    args: bytes
    string_value: Optional[str] = None
    jump_offset: Optional[int] = None
    action_routine: Optional[int] = None
    action_arg_count: Optional[int] = None

    @property
    def is_string_const(self) -> bool:
        """Whether this instruction pushes a string constant."""
        return self.opcode == OP_CONST and self.type_byte == TYPE_STRING

    @property
    def is_jump(self) -> bool:
        """Whether this instruction carries a relative jump offset."""
        return self.opcode in JUMP_OPCODES

    @property
    def is_action(self) -> bool:
        """Whether this instruction calls an engine routine."""
        return self.opcode == OP_ACTION


@dataclass
class NCSFile:
    """A parsed script.

    Attributes:
        header: The 8-byte banner.
        instructions: All instructions in file order.
        raw_bytes: The complete file.
    """

    header: bytes
    instructions: List[NCSInstruction]
    raw_bytes: bytearray

    @property
    def string_constants(self) -> List[NCSInstruction]:
        """All string constant (CONSTS) instructions."""
        return [i for i in self.instructions if i.is_string_const]


def _parse_instruction(
    data: Union[bytes, bytearray], offset: int, source_encoding: Optional[str] = None
) -> NCSInstruction:
    """Parses the instruction at *offset*.

    Args:
        data: Complete file bytes.
        offset: Byte offset of the instruction.
        source_encoding: Declared code page of CONSTS bytes; ``None`` uses the
            cascade of :func:`~.text_codec.decode_module_text`.

    Returns:
        The instruction.

    Raises:
        NCSParseError: If the instruction runs past the end of the data.
    """
    if offset + 2 > len(data):
        raise NCSParseError(
            f"Unexpected end of file at offset {offset:#x}: "
            f"need 2 bytes for opcode+type, have {len(data) - offset}"
        )
    opcode = data[offset]
    type_byte = data[offset + 1]

    string_length = None
    if opcode == OP_CONST:
        if type_byte == TYPE_STRING:
            if offset + 4 > len(data):
                raise NCSParseError(
                    f"Unexpected end of file at offset {offset:#x}: "
                    f"CONSTS needs at least 4 bytes"
                )
            string_length = struct.unpack_from(">H", data, offset + 2)[0]
            arg_size = 2 + string_length
        else:
            arg_size = _CONST_ARG_SIZES.get(type_byte, 4)
    elif opcode in _STRUCT_COMPARE_OPCODES and type_byte == _STRUCT_COMPARE_TYPE:
        arg_size = 2
    else:
        arg_size = _OPCODE_ARG_SIZES.get(opcode, 0)

    size = 2 + arg_size
    if offset + size > len(data):
        raise NCSParseError(
            f"Unexpected end of file at offset {offset:#x}: "
            f"instruction (opcode {opcode:#04x}) needs {size} bytes, "
            f"have {len(data) - offset}"
        )
    instruction = NCSInstruction(
        offset=offset,
        opcode=opcode,
        type_byte=type_byte,
        size=size,
        args=bytes(data[offset + 2 : offset + size]),
    )
    if string_length is not None:
        instruction.string_value = decode_module_text(
            bytes(data[offset + 4 : offset + 4 + string_length]), source_encoding
        )
    elif opcode in JUMP_OPCODES:
        instruction.jump_offset = struct.unpack_from(">i", data, offset + 2)[0]
    elif opcode == OP_ACTION:
        instruction.action_routine = struct.unpack_from(">H", data, offset + 2)[0]
        instruction.action_arg_count = data[offset + 4]
    return instruction


def parse_ncs(file_path: Path, source_encoding: Optional[str] = None) -> NCSFile:
    """Parses an NCS file.

    Args:
        file_path: The ``.ncs`` file.
        source_encoding: Declared code page of CONSTS bytes.

    Returns:
        The parsed script.

    Raises:
        NCSParseError: If the file is missing or invalid.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise NCSParseError(f"File not found: {file_path}")
    return parse_ncs_bytes(file_path.read_bytes(), source_encoding=source_encoding)


def parse_ncs_bytes(raw: bytes, source_encoding: Optional[str] = None) -> NCSFile:
    """Parses NCS bytecode.

    Args:
        raw: Complete file contents.
        source_encoding: Declared code page of CONSTS bytes.

    Returns:
        The parsed script.

    Raises:
        NCSParseError: If the banner is wrong or an instruction is truncated.
    """
    if len(raw) < NCS_HEADER_SIZE:
        raise NCSParseError(
            f"File too small ({len(raw)} bytes): expected at least {NCS_HEADER_SIZE}"
        )
    header = raw[:NCS_HEADER_SIZE]
    if header != NCS_HEADER:
        raise NCSParseError(f"Invalid NCS header: expected {NCS_HEADER!r}, got {header!r}")

    data = bytearray(raw)
    cursor = NCS_HEADER_SIZE
    if len(data) >= NCS_HEADER_SIZE + _PREAMBLE_SIZE and data[cursor] == _PREAMBLE_OPCODE:
        cursor += _PREAMBLE_SIZE
    instructions: List[NCSInstruction] = []
    while cursor < len(data):
        instruction = _parse_instruction(data, cursor, source_encoding)
        instructions.append(instruction)
        cursor += instruction.size
    return NCSFile(header=bytes(header), instructions=instructions, raw_bytes=data)


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------


def patch_ncs_string_replacements(
    file_path: Path,
    replacements: Sequence[Tuple[int, str, str]],
    text_encoding: str = "cp1251",
    source_encoding: Optional[str] = None,
) -> int:
    """Replaces listed string constants, addressed by offset.

    Each ``(byte_offset, original_text, translated_text)`` must name a CONSTS
    instruction whose decoded text equals ``original_text``; the same literal
    at other offsets is left alone. The patched file is re-parsed and its
    jump targets checked before it is written.

    Args:
        file_path: The ``.ncs`` file.
        replacements: Replacement specs; entries with unchanged text are skipped.
        text_encoding: Code page of the written string bytes, one of
            :data:`~.text_codec.MODULE_ENCODINGS`.
        source_encoding: Code page for decoding the existing strings; must match
            the one used at extraction or the original-text checks fail.

    Returns:
        Number of CONSTS instructions patched.

    Raises:
        NCSPatchError: On an unsupported encoding, a duplicate or wrong
            offset, a text mismatch, an overlong string, or a patched file that
            no longer parses or jumps outside instruction boundaries; the file
            is then left unchanged.
    """
    file_path = Path(file_path)
    if not replacements:
        return 0
    if text_encoding not in MODULE_ENCODINGS:
        raise NCSPatchError(f"Unsupported module text encoding: {text_encoding!r}")

    ncs = parse_ncs(file_path, source_encoding=source_encoding)
    by_offset = {instruction.offset: instruction for instruction in ncs.instructions}
    patches: List[Tuple[NCSInstruction, str]] = []
    seen_offsets = set()
    for offset, original_text, translated_text in replacements:
        if offset in seen_offsets:
            raise NCSPatchError(f"Duplicate replacement at offset {offset:#x}")
        seen_offsets.add(offset)
        if translated_text == original_text:
            continue
        instruction = by_offset.get(offset)
        if instruction is None:
            raise NCSPatchError(f"No instruction at offset {offset:#x} in {file_path.name}")
        if not instruction.is_string_const or instruction.string_value is None:
            raise NCSPatchError(f"Instruction at offset {offset:#x} is not a string constant")
        if instruction.string_value != original_text:
            raise NCSPatchError(
                f"String mismatch at offset {offset:#x} in {file_path.name}: "
                f"expected {original_text!r}, found {instruction.string_value!r}"
            )
        patches.append((instruction, translated_text))
    if not patches:
        return 0

    data = _patched_bytes(ncs, patches, text_encoding)
    try:
        if not _jumps_land_on_instructions(parse_ncs_bytes(bytes(data))):
            logger.error("Jump validation failed for %s — reverting to original", file_path.name)
            raise NCSPatchError(f"Jump validation failed after patching {file_path.name}")
    except NCSParseError as e:
        logger.error("Re-parse failed for %s — reverting to original: %s", file_path.name, e)
        raise NCSPatchError(f"Patched file failed re-parse: {file_path.name}: {e}") from e

    file_path.write_bytes(data)
    logger.info("Patched %d string(s) in %s", len(patches), file_path.name)
    return len(patches)


def _patched_bytes(
    ncs: NCSFile, patches: List[Tuple[NCSInstruction, str]], text_encoding: str
) -> bytearray:
    """Splices new CONSTS into ``ncs.raw_bytes`` and fix jumps and the size field.

    Patches are applied from the highest offset down, so an instruction's
    offset only changes after every patch below it is done. The instructions
    of *ncs* are updated in place to track the shifts.
    """
    data = ncs.raw_bytes
    original_size = len(data)
    instructions = ncs.instructions
    for instruction, translated_text in sorted(patches, key=lambda p: p[0].offset, reverse=True):
        new_bytes = _consts_bytes(encode_module_text(translated_text, text_encoding))
        start = instruction.offset
        end = start + instruction.size
        delta = len(new_bytes) - instruction.size
        data[start:end] = new_bytes
        if delta:
            _shift(instructions, end, delta)

    for instruction in instructions:
        if instruction.jump_offset is not None:
            struct.pack_into(">i", data, instruction.offset + 2, instruction.jump_offset)
    if (
        len(data) != original_size
        and len(data) >= _PREAMBLE_SIZE_FIELD + 4
        and data[NCS_HEADER_SIZE] == _PREAMBLE_OPCODE
    ):
        struct.pack_into(">I", data, _PREAMBLE_SIZE_FIELD, len(data))
    return data


def _consts_bytes(encoded: bytes) -> bytes:
    """Builds a CONSTS instruction: ``04 05``, BE uint16 length, string bytes."""
    if len(encoded) > 0xFFFF:
        raise NCSPatchError(
            f"Translated string too long ({len(encoded)} bytes): "
            f"NCS CONSTS supports max 65535 bytes"
        )
    return struct.pack(">BBH", OP_CONST, TYPE_STRING, len(encoded)) + encoded


def _shift(instructions: List[NCSInstruction], patch_end: int, delta: int) -> None:
    """Accounts for *delta* bytes inserted (or removed) at *patch_end*.

    A jump whose source is before *patch_end* and target at or after it grows
    by *delta*; one crossing the other way shrinks. Then every instruction at
    or after *patch_end* moves by *delta*.
    """
    for instruction in instructions:
        if instruction.jump_offset is None:
            continue
        source = instruction.offset
        target = source + instruction.jump_offset
        if source < patch_end <= target:
            instruction.jump_offset += delta
        elif target < patch_end <= source:
            instruction.jump_offset -= delta
    for instruction in instructions:
        if instruction.offset >= patch_end:
            instruction.offset += delta


def _jumps_land_on_instructions(ncs: NCSFile) -> bool:
    """Checks that every jump targets an instruction start or the end of the code."""
    valid_targets = {i.offset for i in ncs.instructions}
    if ncs.instructions:
        last = ncs.instructions[-1]
        valid_targets.add(last.offset + last.size)
    for instruction in ncs.instructions:
        if instruction.jump_offset is None:
            continue
        target = instruction.offset + instruction.jump_offset
        if target not in valid_targets:
            logger.error(
                "Jump at offset %#x targets %#x which is not a valid instruction boundary",
                instruction.offset,
                target,
            )
            return False
    return True
