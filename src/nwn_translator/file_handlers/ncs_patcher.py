"""NCS bytecode patcher for translating string constants.

Replaces string constants in compiled NWScript bytecode (``.ncs`` files)
and recalculates all jump offsets affected by size changes.

The core challenge: changing a string's length shifts everything after it,
so every jump instruction that "crosses" the modification point must have
its relative offset adjusted.

Safe patching uses explicit ``(offset, original_text, new_text)`` triples so
only intended CONSTS sites are modified (same literal at multiple offsets
can differ in whether they should be translated).
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .ncs_parser import (
    NCSFile,
    NCSInstruction,
    NCSParseError,
    OP_CONST,
    TYPE_STRING,
    parse_ncs,
    parse_ncs_bytes,
)
from .gff_patcher import _sanitize_for_cp1251

logger = logging.getLogger(__name__)


class NCSPatchError(Exception):
    """Raised when NCS patching fails."""


def _encode_string(text: str) -> bytes:
    """Encode a translated string for NCS bytecode (CP1251)."""
    sanitized = _sanitize_for_cp1251(text)
    return sanitized.encode("cp1251", errors="replace")


def _build_consts_bytes(encoded_string: bytes) -> bytes:
    """Build the raw bytes for a CONSTS instruction.

    Returns:
        Bytes: opcode(0x04) + type(0x05) + BE uint16 length + string data.
    """
    str_len = len(encoded_string)
    if str_len > 0xFFFF:
        raise NCSPatchError(
            f"Translated string too long ({str_len} bytes): "
            f"NCS CONSTS supports max 65535 bytes"
        )
    return (
        struct.pack(">BB", OP_CONST, TYPE_STRING)
        + struct.pack(">H", str_len)
        + encoded_string
    )


def _adjust_jumps(
    instructions: List[NCSInstruction],
    patch_offset: int,
    old_size: int,
    delta: int,
) -> None:
    """Adjust jump offsets for instructions that cross the patch point."""
    patch_end = patch_offset + old_size

    for instr in instructions:
        if instr.jump_offset is None:
            continue

        source = instr.offset
        target = source + instr.jump_offset

        if source < patch_end and target >= patch_end:
            instr.jump_offset += delta
        elif source >= patch_end and target < patch_end:
            instr.jump_offset -= delta


def _update_offsets(
    instructions: List[NCSInstruction],
    after_offset: int,
    delta: int,
) -> None:
    """Shift instruction offsets for all instructions after a splice point."""
    for instr in instructions:
        if instr.offset >= after_offset:
            instr.offset += delta


def _validate_jumps(ncs: NCSFile) -> bool:
    """Verify all jump targets land on valid instruction boundaries."""
    valid_offsets = {i.offset for i in ncs.instructions}
    if ncs.instructions:
        last = ncs.instructions[-1]
        valid_offsets.add(last.offset + last.size)

    for instr in ncs.instructions:
        if instr.jump_offset is None:
            continue
        target = instr.offset + instr.jump_offset
        if target not in valid_offsets:
            logger.error(
                "Jump at offset %#x targets %#x which is not a valid "
                "instruction boundary",
                instr.offset,
                target,
            )
            return False
    return True


def _instruction_at_offset(
    ncs: NCSFile,
    offset: int,
) -> Optional[NCSInstruction]:
    for instr in ncs.instructions:
        if instr.offset == offset:
            return instr
    return None


def _apply_instruction_patches(
    file_path: Path,
    ncs: NCSFile,
    patches: List[Tuple[NCSInstruction, str]],
) -> int:
    """Apply validated (instruction, new_text) patches; write file or rollback."""
    if not patches:
        return 0

    original_bytes = bytes(ncs.raw_bytes)
    patches.sort(key=lambda p: p[0].offset, reverse=True)

    data = ncs.raw_bytes
    instructions = ncs.instructions

    for instr, translated_text in patches:
        encoded = _encode_string(translated_text)
        new_bytes = _build_consts_bytes(encoded)
        new_size = len(new_bytes)
        old_size = instr.size
        delta = new_size - old_size

        if delta != 0:
            _adjust_jumps(instructions, instr.offset, old_size, delta)

        start = instr.offset
        end = start + old_size
        data[start:end] = new_bytes

        instr.size = new_size
        instr.args = bytes(new_bytes[2:])
        instr.string_value = translated_text

        if delta != 0:
            _update_offsets(instructions, start + old_size, delta)

    for instr in instructions:
        if instr.jump_offset is not None:
            struct.pack_into(">i", data, instr.offset + 2, instr.jump_offset)

    try:
        validated = parse_ncs_bytes(bytes(data))
        if not _validate_jumps(validated):
            logger.error(
                "Jump validation failed for %s — reverting to original",
                file_path.name,
            )
            file_path.write_bytes(original_bytes)
            raise NCSPatchError(
                f"Jump validation failed after patching {file_path.name}"
            )
    except NCSParseError as e:
        logger.error(
            "Re-parse failed for %s — reverting to original: %s",
            file_path.name,
            e,
        )
        file_path.write_bytes(original_bytes)
        raise NCSPatchError(
            f"Patched file failed re-parse: {file_path.name}: {e}"
        ) from e

    file_path.write_bytes(data)
    logger.info(
        "Patched %d string(s) in %s",
        len(patches),
        file_path.name,
    )
    return len(patches)


def patch_ncs_string_replacements(
    file_path: Path,
    replacements: Sequence[Tuple[int, str, str]],
) -> int:
    """Patch only listed string CONSTS (by offset), with original-text checks.

    Each tuple is ``(byte_offset, original_text, translated_text)``.
    The instruction at *byte_offset* must be a string CONSTS whose decoded
    value equals *original_text*. Other occurrences of the same literal at
    different offsets are left untouched.

    Args:
        file_path: Path to the ``.ncs`` file.
        replacements: Non-empty sequence of explicit replacement specs.

    Returns:
        Number of CONSTS instructions patched.

    Raises:
        NCSPatchError: On validation, parse, or jump errors.
    """
    file_path = Path(file_path)
    if not replacements:
        return 0

    ncs = parse_ncs(file_path)
    patches: List[Tuple[NCSInstruction, str]] = []

    for offset, original_text, translated_text in replacements:
        if translated_text == original_text:
            continue
        instr = _instruction_at_offset(ncs, offset)
        if instr is None:
            raise NCSPatchError(
                f"No instruction at offset {offset:#x} in {file_path.name}"
            )
        if not instr.is_string_const or instr.string_value is None:
            raise NCSPatchError(
                f"Instruction at offset {offset:#x} is not a string constant"
            )
        if instr.string_value != original_text:
            raise NCSPatchError(
                f"String mismatch at offset {offset:#x} in {file_path.name}: "
                f"expected {original_text!r}, found {instr.string_value!r}"
            )
        patches.append((instr, translated_text))

    if not patches:
        return 0

    return _apply_instruction_patches(file_path, ncs, patches)


def patch_ncs_strings(
    file_path: Path,
    translations: Dict[str, str],
) -> int:
    """Patch every string CONSTS whose value is a key in *translations*.

    .. warning::
        This replaces **all** occurrences of each key in the file. Prefer
        :func:`patch_ncs_string_replacements` for module-safe patching.

    Kept for tests and backward compatibility.
    """
    file_path = Path(file_path)
    ncs = parse_ncs(file_path)
    patches: List[Tuple[NCSInstruction, str]] = []
    for instr in ncs.string_constants:
        if instr.string_value in translations:
            translated = translations[instr.string_value]
            if translated != instr.string_value:
                patches.append((instr, translated))

    if not patches:
        return 0
    return _apply_instruction_patches(file_path, ncs, patches)
