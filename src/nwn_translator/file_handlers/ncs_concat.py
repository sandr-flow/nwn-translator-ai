"""Detect NWScript string concatenation chains in compiled NCS bytecode.

NWScript ``"a" + name + "b"`` compiles as separate CONSTS instructions combined
with ``ADD`` (type ``0x23``, string+string). Translating those CONSTS in
isolation produces mixed-language sentences. This module finds each linear
concat expression so extractors can treat it as one unit with ``<VARn>``
placeholders for runtime values.

Known limitation: statements of the form ``s += "..."`` compile as separate
chains (store via CPDOWNSP, then a later ADD). Those are not merged.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .ncs_parser import (
    NCSFile,
    OP_ACTION,
    OP_ADD,
    OP_CONST,
    OP_CPTOPSP,
    OP_CPTOPBP,
    OP_RSADD,
)

# ADD type qualifier for string+string (community / Torlack SS).
TYPE_ADD_STRING_STRING = 0x23

_VAR_RE = re.compile(r"<VAR(\d+)>")


@dataclass(frozen=True)
class ConcatLit:
    """A string CONSTS operand in a concat expression."""

    offset: int
    text: str


@dataclass(frozen=True)
class ConcatVar:
    """A runtime value in a concat expression, numbered as ``<VARn>``."""

    index: int


ConcatPart = Union[ConcatLit, ConcatVar]


@dataclass(frozen=True)
class ConcatChain:
    """One concat expression: literals plus runtime slots, left to right."""

    parts: Tuple[ConcatPart, ...]
    first_offset: int
    last_instr_index: int

    def lits(self) -> List[ConcatLit]:
        """Return the CONSTS operands in left-to-right order."""
        return [p for p in self.parts if isinstance(p, ConcatLit)]

    def to_metadata(self) -> List[Dict[str, Any]]:
        """Serialize parts for ``TranslatableItem.metadata['concat_parts']``."""
        out: List[Dict[str, Any]] = []
        for part in self.parts:
            if isinstance(part, ConcatLit):
                out.append({"offset": part.offset, "text": part.text})
            else:
                out.append({"var": part.index})
        return out


@dataclass
class _Cat:
    parts: List[Union[ConcatLit, object]]
    end_index: int


_VAR = object()


def merged_text(chain: ConcatChain) -> str:
    """Join chain parts, replacing runtime slots with ``<VAR1>``, ``<VAR2>``, …."""
    bits: List[str] = []
    for part in chain.parts:
        if isinstance(part, ConcatLit):
            bits.append(part.text)
        else:
            bits.append(f"<VAR{part.index}>")
    return "".join(bits)


def parts_from_metadata(raw: Sequence[Mapping[str, Any]]) -> List[ConcatPart]:
    """Rebuild concat parts from extractor metadata."""
    parts: List[ConcatPart] = []
    for cell in raw:
        if "var" in cell:
            parts.append(ConcatVar(int(cell["var"])))
        else:
            parts.append(ConcatLit(int(cell["offset"]), str(cell.get("text", ""))))
    return parts


def _finalize(parts: Sequence[Union[ConcatLit, object]], end_index: int) -> Optional[ConcatChain]:
    numbered: List[ConcatPart] = []
    var_n = 0
    has_lit = False
    for part in parts:
        if isinstance(part, ConcatLit):
            numbered.append(part)
            has_lit = True
        else:
            var_n += 1
            numbered.append(ConcatVar(var_n))
    if not has_lit or len(numbered) < 2:
        return None
    first_offset = next(p.offset for p in numbered if isinstance(p, ConcatLit))
    return ConcatChain(parts=tuple(numbered), first_offset=first_offset, last_instr_index=end_index)


def _flatten(node: Union[_Cat, ConcatLit, object]) -> List[Union[ConcatLit, object]]:
    if isinstance(node, _Cat):
        return list(node.parts)
    return [node]


def find_concat_chains(ncs: NCSFile) -> Dict[int, ConcatChain]:
    """Return concat chains keyed by the byte offset of the first CONSTS literal.

    Calls use the same signatures as consumer tracing. Unknown instructions
    end a chain; stack copies of literals must not become runtime placeholders.
    """
    from ..extractors.ncs_context import ACTION_SIGNATURES

    chains: Dict[int, ConcatChain] = {}
    stack: List[Union[_Cat, ConcatLit, object]] = []

    def emit(cat: _Cat) -> None:
        chain = _finalize(cat.parts, cat.end_index)
        if chain is not None:
            chains[chain.first_offset] = chain

    def flush() -> None:
        for node in stack:
            if isinstance(node, _Cat):
                emit(node)
        stack.clear()

    for idx, instr in enumerate(ncs.instructions):
        if instr.is_string_const and instr.string_value is not None:
            stack.append(ConcatLit(instr.offset, instr.string_value))
            continue

        if instr.opcode in (OP_CPTOPSP, OP_CPTOPBP):
            offset, size = struct.unpack(">iH", instr.args)
            if size == 0 or size % 4 or offset % 4:
                flush()
                continue
            if instr.opcode == OP_CPTOPSP:
                copied = [
                    stack[pos]
                    for pos in range(len(stack) + offset // 4, len(stack) + (offset + size) // 4)
                    if 0 <= pos < len(stack)
                ]
                if any(node is not _VAR for node in copied):
                    flush()
            stack.extend([_VAR] * (size // 4))
            continue

        if instr.opcode == OP_ADD and instr.type_byte == TYPE_ADD_STRING_STRING:
            right = stack.pop() if stack else _VAR
            left = stack.pop() if stack else _VAR
            stack.append(_Cat(_flatten(left) + _flatten(right), idx))
            continue

        if instr.opcode == OP_ACTION:
            signature = ACTION_SIGNATURES.get(instr.action_routine or -1)
            argc = instr.action_arg_count
            if signature is None or argc is None or argc > len(signature[1]):
                flush()
                continue
            _, params, return_slots = signature
            consumed = sum(
                0 if param == "a" else 3 if param == "v" else 1 for param in params[:argc]
            )
            for _ in range(min(consumed, len(stack))):
                node = stack.pop()
                if isinstance(node, _Cat):
                    emit(node)
            stack.extend([_VAR] * return_slots)
            continue

        if instr.opcode in (OP_CONST, OP_RSADD):
            stack.append(_VAR)
            continue

        flush()

    flush()
    return chains


def split_concat_translation(
    parts: Sequence[ConcatPart],
    translated: str,
) -> Optional[List[Tuple[int, str, str]]]:
    """Split a translated concat string back into per-CONSTS replacements.

    Each ``<VARn>`` must appear exactly once, in original order. Text between
    placeholders is assigned to the lit-run in that slot: the first CONSTS of
    the run gets the segment, the rest get ``""``. A slot with no CONSTS
    (leading/trailing/adjacent Vars) requires an empty segment.

    Returns ``None`` when placeholders are missing, reordered, duplicated, or
    a no-lit slot received non-empty text.
    """
    expected_vars = [p.index for p in parts if isinstance(p, ConcatVar)]
    found = list(_VAR_RE.finditer(translated))
    found_idxs = [int(m.group(1)) for m in found]
    if found_idxs != expected_vars:
        return None

    segs: List[str] = []
    pos = 0
    for match in found:
        segs.append(translated[pos : match.start()])
        pos = match.end()
    segs.append(translated[pos:])

    groups: List[List[ConcatLit]] = []
    current: List[ConcatLit] = []
    for part in parts:
        if isinstance(part, ConcatVar):
            groups.append(current)
            current = []
        else:
            current.append(part)
    groups.append(current)

    if len(segs) != len(groups):
        return None

    replacements: List[Tuple[int, str, str]] = []
    for segment, lits in zip(segs, groups):
        if not lits:
            if segment != "":
                return None
            continue
        replacements.append((lits[0].offset, lits[0].text, segment))
        for lit in lits[1:]:
            replacements.append((lit.offset, lit.text, ""))
    return replacements
