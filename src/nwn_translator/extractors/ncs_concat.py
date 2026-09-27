"""NWScript string concatenation chains in compiled NCS bytecode.

NWScript ``"a" + name + "b"`` compiles as separate CONSTS instructions combined
with ``ADD`` (type ``0x23``, string+string). Translating those CONSTS in
isolation produces mixed-language sentences. This module finds each linear
concat expression so extractors can treat it as one unit with ``<VARn>``
placeholders for runtime values.

Known limitation: statements of the form ``s += "..."`` compile as separate
chains (store via CPDOWNSP, then a later ADD). Those are not merged.
"""

from __future__ import annotations

import itertools
import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..formats.ncs import (
    NCSFile,
    OP_ACTION,
    OP_ADD,
    OP_CONST,
    OP_CPTOPSP,
    OP_CPTOPBP,
    OP_RSADD,
    TYPE_STRING_STRING,
)
from .ncs_context import ACTION_SIGNATURES

_VAR_RE = re.compile(r"<VAR(\d+)>")


@dataclass(frozen=True)
class ConcatLit:
    """A string CONSTS operand in a concat expression.

    Attributes:
        offset: Byte offset of the CONSTS instruction.
        text: Decoded literal.
    """

    offset: int
    text: str


@dataclass(frozen=True)
class ConcatVar:
    """A runtime value in a concat expression, numbered as ``<VARn>``.

    Attributes:
        index: 1-based slot number ``n``.
    """

    index: int


#: One operand of a concat expression.
ConcatPart = Union[ConcatLit, ConcatVar]


@dataclass(frozen=True)
class ConcatChain:
    """One concat expression: literals plus runtime slots, left to right.

    Attributes:
        parts: Literals and numbered runtime slots in source order.
        first_offset: Byte offset of the first literal; the chain's key.
        last_instr_index: Index of the instruction that completed the chain.
    """

    parts: Tuple[ConcatPart, ...]
    first_offset: int
    last_instr_index: int

    def lits(self) -> List[ConcatLit]:
        """Returns the CONSTS operands, left to right."""
        return [p for p in self.parts if isinstance(p, ConcatLit)]

    def to_metadata(self) -> List[Dict[str, Any]]:
        """Serializes the parts for ``metadata["concat_parts"]`` (``offset``/``text`` or ``var``)."""
        return [
            {"offset": p.offset, "text": p.text} if isinstance(p, ConcatLit) else {"var": p.index}
            for p in self.parts
        ]


@dataclass
class _Cat:
    """A partial concat result on the simulated stack.

    Attributes:
        parts: Literals and runtime-value markers, left to right.
        end_index: Index of the ADD instruction that produced the result.
    """

    parts: List[Union[ConcatLit, object]]
    end_index: int


# Stack slot holding a runtime value.
_VAR = object()


def merged_text(chain: ConcatChain) -> str:
    """Joins the literals of a chain, with ``<VAR1>``, ``<VAR2>``, … for the runtime slots."""
    return "".join(p.text if isinstance(p, ConcatLit) else f"<VAR{p.index}>" for p in chain.parts)


def parts_from_metadata(raw: Sequence[Mapping[str, Any]]) -> List[ConcatPart]:
    """Rebuilds concat parts, in source order, from :meth:`ConcatChain.to_metadata` output."""
    return [
        (
            ConcatVar(int(cell["var"]))
            if "var" in cell
            else ConcatLit(int(cell["offset"]), str(cell.get("text", "")))
        )
        for cell in raw
    ]


def _finalize(parts: Sequence[Union[ConcatLit, object]], end_index: int) -> Optional[ConcatChain]:
    """Numbers the runtime slots of *parts*; ``None`` unless a literal and 2+ parts."""
    slots = itertools.count(1)
    numbered = tuple(p if isinstance(p, ConcatLit) else ConcatVar(next(slots)) for p in parts)
    lits = [p for p in numbered if isinstance(p, ConcatLit)]
    if not lits or len(numbered) < 2:
        return None
    return ConcatChain(numbered, lits[0].offset, end_index)


def find_concat_chains(ncs: NCSFile) -> Dict[int, ConcatChain]:
    """Returns concat chains keyed by the byte offset of the first CONSTS literal.

    Calls use the same signatures as consumer tracing. Unknown instructions
    end a chain; stack copies of literals must not become runtime placeholders.

    Args:
        ncs: The parsed script.

    Returns:
        First literal offset -> chain.
    """
    chains: Dict[int, ConcatChain] = {}
    stack: List[Union[_Cat, ConcatLit, object]] = []

    def emit(cat: _Cat) -> None:
        """Records *cat* as a chain when it qualifies."""
        chain = _finalize(cat.parts, cat.end_index)
        if chain is not None:
            chains[chain.first_offset] = chain

    def flush() -> None:
        """Emits every partial result on the stack and empties it."""
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

        if instr.opcode == OP_ADD and instr.type_byte == TYPE_STRING_STRING:
            right = stack.pop() if stack else _VAR
            left = stack.pop() if stack else _VAR
            parts = [
                part
                for node in (left, right)
                for part in (node.parts if isinstance(node, _Cat) else [node])
            ]
            stack.append(_Cat(parts, idx))
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
    """Splits a translated concat string back into per-CONSTS replacements.

    Each ``<VARn>`` must appear exactly once, in original order. Text between
    placeholders is assigned to the lit-run in that slot: the first CONSTS of
    the run gets the segment, the rest get ``""``. A slot with no CONSTS
    (leading/trailing/adjacent Vars) requires an empty segment.

    Args:
        parts: The chain's parts (see :func:`parts_from_metadata`).
        translated: Translation of :func:`merged_text` of the chain.

    Returns:
        ``(offset, original_text, new_text)`` per literal, or ``None`` when
        placeholders are missing, reordered or duplicated, or a slot without
        literals received non-empty text.
    """
    expected_vars = [p.index for p in parts if isinstance(p, ConcatVar)]
    if [int(m.group(1)) for m in _VAR_RE.finditer(translated)] != expected_vars:
        return None
    # The pattern's group puts each slot number between two segments.
    segments = _VAR_RE.split(translated)[::2]
    groups: List[List[ConcatLit]] = [[]]
    for part in parts:
        if isinstance(part, ConcatVar):
            groups.append([])
        else:
            groups[-1].append(part)
    replacements: List[Tuple[int, str, str]] = []
    for segment, lits in zip(segments, groups):
        if not lits:
            if segment:
                return None
            continue
        replacements.append((lits[0].offset, lits[0].text, segment))
        replacements.extend((lit.offset, lit.text, "") for lit in lits[1:])
    return replacements
