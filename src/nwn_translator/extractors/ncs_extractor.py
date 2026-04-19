"""NCS script extractor for compiled NWScript bytecode.

Extracts player-visible string constants from ``.ncs`` files using a
combination of pattern heuristics and ACTION opcode context analysis.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .base import BaseExtractor, ExtractedContent, TranslatableItem
from ..file_handlers.ncs_parser import (
    NCSFile,
    NCSInstruction,
    OP_ACTION,
    OP_CONST,
    TYPE_STRING,
)

# ---------------------------------------------------------------------------
# Engine function numbers for context-based classification
# ---------------------------------------------------------------------------

# ACTION routines whose *string* argument is shown to the player.
PLAYER_FACING_ACTIONS: Set[int] = {
    39,  # SpeakString (classic NWN)
    40,  # ActionSpeakString
    169,  # ActionSpeakStringByStrRef (has string variant too)
    221,  # SpeakString (NWN:EE / some compilers — same role as 39)
    284,  # SetCustomToken
    374,  # SendMessageToPC
    417,  # SpeakOneLinerConversation
    468,  # FloatingTextStringOnCreature
    525,  # FloatingTextStringOnCreature (EE variant)
    761,  # SetDescription
}

# ACTION routines whose string argument is an internal identifier.
NON_PLAYER_ACTIONS: Set[int] = {
    1,  # PrintString (server log / DM console, not player screen)
    8,  # ExecuteScript
    13,  # SetLocalString (var name)
    14,  # SetLocalInt (var name)
    15,  # SetLocalFloat (var name)
    16,  # SetLocalObject (var name)
    17,  # SetLocalLocation (var name)
    29,  # GetLocalString (var name)
    30,  # GetLocalInt (var name)
    31,  # GetLocalFloat (var name)
    32,  # GetLocalObject (var name)
    33,  # GetLocalLocation (var name)
    45,  # PlaySound
    46,  # GetObjectByTag
    57,  # GetWaypointByTag
    165,  # CreateObject (resref)
    173,  # EffectVisualEffect  # not string, but sometimes confused
    200,  # GetObjectByTag (alt number)
    514,  # StartNewModule
    683,  # PlayAnimation (not string)
    755,  # SendMessageToAllDMs (DM-only, not player-visible)
    824,  # DeleteLocalString (var name)
    825,  # DeleteLocalInt (var name)
    826,  # DeleteLocalFloat (var name)
    827,  # DeleteLocalObject (var name)
    828,  # DeleteLocalLocation (var name)
}

# Max bytecode steps to scan after a CONST for a consuming ACTION.
# Random/if/assign patterns may place SpeakString many instructions later in linear order.
_ACTION_SCAN_WINDOW = 64

# ---------------------------------------------------------------------------
# Pattern-based heuristics
# ---------------------------------------------------------------------------

# Identifiers: snake_case, UPPER_CASE, CamelCase without spaces
_RE_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_RE_UPPER_CONST = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RE_RESREF = re.compile(r"^[a-zA-Z0-9_]{1,16}$")  # max 16 chars, no spaces

# Known non-translatable prefixes (script names, system identifiers)
_SKIP_PREFIXES = (
    "nw_",
    "x0_",
    "x2_",
    "x3_",
    "k_act_",
    "k_def_",
    "k_hb_",
    "nwnx_",
    "dmfi_",
    "aps_",
    "hc_",
    "zep_",
    "prc_",
)


def _is_definitely_not_translatable(text: str) -> bool:
    """Quick rejection test for obviously non-translatable strings."""
    stripped = text.strip()

    # Empty / whitespace
    if not stripped:
        return True

    # Very short strings (single char, two chars)
    if len(stripped) <= 2:
        return True

    # Pure numeric
    try:
        float(stripped)
        return True
    except ValueError:
        pass

    # Known non-translatable prefixes
    lower = stripped.lower()
    if any(lower.startswith(p) for p in _SKIP_PREFIXES):
        return True

    # snake_case identifiers: nw_c2_default1, my_var_name
    if _RE_SNAKE_CASE.match(stripped):
        return True

    # UPPER_CASE constants: MY_VARIABLE, NW_FLAG_HEARTBEAT
    if _RE_UPPER_CONST.match(stripped):
        return True

    # ResRef-like: short, no spaces, only alnum+underscore
    if " " not in stripped and _RE_RESREF.match(stripped):
        return True

    # Separator / decoration lines: ≥50% asterisks/hashes/dashes,
    # OR starts AND ends with decoration characters
    decoration_chars = sum(1 for ch in stripped if ch in "*#-=")
    if len(stripped) >= 3 and (
        decoration_chars / len(stripped) >= 0.5
        or (stripped[0] in "*#-=" and stripped[-1] in "*#-=")
    ):
        return True

    # Variable dump pattern: ends with " = " or "varName = "
    if stripped.endswith(" = ") or stripped.endswith("= ") or stripped.endswith(" ="):
        return True

    # Developer / debug error messages
    if any(
        phrase in lower
        for phrase in (
            "report as bug",
            "report this bug",
            "please report",
            "debug string",
            "error:",
        )
    ):
        return True

    # ALL-CAPS debug shouts — only letter-only tokens, so normal dialogue with
    # "okay," / "sir." is not mistaken for a shout (see "I'm okay, sir. I think.").
    alpha_tokens = re.findall(r"[A-Za-z]+", stripped)
    if len(alpha_tokens) >= 3 and all(t.isupper() for t in alpha_tokens):
        return True

    return False


_RE_CAMEL_CASE = re.compile(r"[a-z][a-zA-Z]*[A-Z][a-zA-Z]*")
_RE_FUNC_DOT = re.compile(r"\b\w+\.\w+")


def _contains_code_identifiers(text: str) -> bool:
    """True if text contains CamelCase identifiers or struct.field patterns."""
    return bool(_RE_CAMEL_CASE.search(text) or _RE_FUNC_DOT.search(text))


def _is_likely_translatable(text: str) -> bool:
    """Positive heuristic: looks like a player-visible sentence or short bark."""
    stripped = text.strip()
    words = stripped.split()
    has_punctuation = " " in text and any(ch in text for ch in ".!?,:;")
    has_enough_words = len(words) >= 3

    if has_punctuation and has_enough_words:
        return True

    # One-word / short barks: "Mommy." "Help!" "Sir?" — often SpeakString / floaty
    if len(stripped) >= 3 and stripped[-1] in ".!?":
        if len(words) <= 4 and any(any(c.isalpha() for c in w) for w in words):
            return True

    return False


def _classify_by_action_context(
    instr_index: int,
    instructions: List[NCSInstruction],
) -> Optional[str]:
    """Look ahead for an ACTION opcode to classify the string.

    Returns:
        - ``"player"`` if a player-facing ACTION consumes this string
        - ``"internal"`` if a non-player ACTION consumes it
        - ``None`` if no conclusive ACTION found nearby
    """
    # Scan forward for an ACTION that consumes this string (see _ACTION_SCAN_WINDOW).
    window = min(_ACTION_SCAN_WINDOW, len(instructions) - instr_index - 1)
    for i in range(1, window + 1):
        next_instr = instructions[instr_index + i]
        if next_instr.is_action and next_instr.action_routine is not None:
            routine = next_instr.action_routine
            if routine in PLAYER_FACING_ACTIONS:
                return "player"
            if routine in NON_PLAYER_ACTIONS:
                return "internal"
            # Unknown routine — inconclusive, keep looking
        # Several string CONSTS are often pushed as successive arguments before
        # one ACTION; keep scanning within the window instead of stopping here.

    return None


def _action_name(routine: int) -> str:
    """Human-readable name for common ACTION routine numbers."""
    names = {
        39: "SpeakString",
        221: "SpeakString",
        40: "ActionSpeakString",
        284: "SetCustomToken",
        374: "SendMessageToPC",
        417: "SpeakOneLinerConversation",
        468: "FloatingTextStringOnCreature",
        525: "FloatingTextStringOnCreature",
        761: "SetDescription",
    }
    return names.get(routine, f"ACTION #{routine}")


# ---------------------------------------------------------------------------
# Source-based classification (.nss)
# ---------------------------------------------------------------------------

# Functions whose string argument is player-visible
_NSS_PLAYER_FUNCS = (
    "SpeakString",
    "ActionSpeakString",
    "SendMessageToPC",
    "FloatingTextStringOnCreature",
    "SetCustomToken",
    "SpeakOneLinerConversation",
    "SetDescription",
)

# Functions whose string argument is debug / internal
_NSS_DEBUG_FUNCS = (
    "PrintString",
    "SendMessageToAllDMs",
)


def _classify_from_source(text: str, nss_content: str) -> Optional[str]:
    """Classify a string by searching the .nss source for its usage context.

    Returns:
        - ``"player"`` if the string is used in a player-facing function call
        - ``"debug"`` if the string is used in a debug/internal function call
        - ``None`` if the string is not found or context is ambiguous
    """
    # Escape the text for literal search
    escaped = re.escape(text)
    # Match: FunctionName ( ... "text" ... )  — possibly across args
    # We look for the string literal near a known function name
    for func in _NSS_PLAYER_FUNCS:
        pattern = rf'{func}\s*\(.*?"{escaped}"'
        if re.search(pattern, nss_content, re.DOTALL):
            return "player"

    for func in _NSS_DEBUG_FUNCS:
        pattern = rf'{func}\s*\(.*?"{escaped}"'
        if re.search(pattern, nss_content, re.DOTALL):
            return "debug"

    return None


class NcsExtractor(BaseExtractor):
    """Extractor for compiled NWScript (``.ncs``) files."""

    SUPPORTED_TYPES = [".ncs"]

    def can_extract(self, file_type: str) -> bool:
        return file_type.lower() in self.SUPPORTED_TYPES

    def extract(
        self,
        file_path: Path,
        parsed_data: Dict[str, Any],
    ) -> ExtractedContent:
        """Extract translatable string constants from an NCS file.

        Args:
            file_path: Path to the ``.ncs`` file.
            parsed_data: Dict with ``_ncs_file`` key containing parsed NCSFile
                      (NCS files are NOT GFF, so parsed_data is repurposed).

        Returns:
            ExtractedContent with translatable items.
        """
        ncs_file: Optional[NCSFile] = parsed_data.get("_ncs_file")
        if ncs_file is None:
            return ExtractedContent(
                content_type="ncs_script",
                items=[],
                source_file=file_path,
                metadata={"error": "No parsed NCS data"},
            )

        instructions = ncs_file.instructions
        items: List[TranslatableItem] = []

        # Try to load .nss source for better classification
        nss_content: Optional[str] = None
        nss_path = file_path.with_suffix(".nss")
        if nss_path.exists():
            try:
                nss_content = nss_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

        for idx, instr in enumerate(instructions):
            if not instr.is_string_const or instr.string_value is None:
                continue

            text = instr.string_value
            if not text.strip():
                continue

            # Quick rejection
            if _is_definitely_not_translatable(text):
                continue

            source_class: Optional[str] = None
            if nss_content is not None:
                source_class = _classify_from_source(text, nss_content)
                if source_class == "debug":
                    continue

            action_class = _classify_by_action_context(idx, instructions)

            if action_class == "internal":
                continue

            source_is_player = source_class == "player"
            bytecode_is_player = action_class == "player"

            # High-confidence player-facing: deterministic pass (no LLM gate)
            if source_is_player or bytecode_is_player:
                action_name = "script function"
                for i in range(1, min(_ACTION_SCAN_WINDOW + 1, len(instructions) - idx)):
                    next_i = instructions[idx + i]
                    if (
                        next_i.is_action
                        and next_i.action_routine is not None
                        and next_i.action_routine in PLAYER_FACING_ACTIONS
                    ):
                        action_name = _action_name(next_i.action_routine)
                        break
                context = (
                    f"Script text shown to player via {action_name} "
                    f"in {file_path.stem}.ncs. Translate naturally."
                )
                needs_llm_gate = False
                confidence = "high"
            elif action_class is None and (
                _is_likely_translatable(text)
                or (_contains_code_identifiers(text) and len(text.split()) >= 2)
            ):
                # Unclear bytecode context — require LLM gate before translate
                if _contains_code_identifiers(text):
                    context = (
                        f"Script string at offset {instr.offset:#x} in {file_path.stem}.ncs. "
                        f"Contains code-like tokens; may be debug or resref. "
                        f"Only translate if it is natural language shown to the player."
                    )
                    confidence = "low"
                else:
                    context = (
                        f"Script string at offset {instr.offset:#x} in {file_path.stem}.ncs. "
                        f"Possibly player-visible; confirm before translating."
                    )
                    confidence = "medium"
                needs_llm_gate = True
            else:
                continue

            ncs_hint = "unknown"
            if bytecode_is_player or source_is_player:
                for i in range(1, min(_ACTION_SCAN_WINDOW + 1, len(instructions) - idx)):
                    next_i = instructions[idx + i]
                    if (
                        next_i.is_action
                        and next_i.action_routine is not None
                        and next_i.action_routine in PLAYER_FACING_ACTIONS
                    ):
                        ncs_hint = _action_name(next_i.action_routine)
                        break
                if ncs_hint == "unknown" and source_is_player:
                    ncs_hint = "nss_player_func"
            if ncs_hint == "unknown":
                ncs_hint = "ambiguous_bytecode"

            items.append(
                TranslatableItem(
                    text=text,
                    context=context,
                    item_id=f"{file_path.stem}:off_{instr.offset:x}",
                    location=str(file_path),
                    metadata={
                        "type": "ncs_string",
                        "offset": instr.offset,
                        "confidence": confidence,
                        "needs_llm_gate": needs_llm_gate,
                        "ncs_hint": ncs_hint,
                    },
                )
            )

        return ExtractedContent(
            content_type="ncs_script",
            items=items,
            source_file=file_path,
            metadata={
                "total_strings": len(ncs_file.string_constants),
                "translatable_strings": len(items),
            },
        )
