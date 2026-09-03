"""NCS script extractor for compiled NWScript bytecode.

Extracts player-visible string constants from ``.ncs`` files. The primary
translatability oracle is the module's own ``.nss`` sources (see
:mod:`.nss_index`): the toolset packs them next to the bytecode, and they
name the consumer of each literal explicitly. Bytecode ACTION-context
analysis and pattern heuristics remain as the fallback for modules shipped
without sources and for literals the sources do not resolve.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .base import BaseExtractor, ExtractedContent, TranslatableItem
from .nss_index import NssModuleIndex, get_module_index
from ..context.string_filters import ENGINE_TAG_PREFIXES
from ..file_handlers.ncs_parser import (
    NCSFile,
    NCSInstruction,
    OP_ACTION,
    OP_CONST,
    OP_EQUAL,
    OP_NEQUAL,
    TYPE_STRING,
)
from ..file_handlers.ncs_concat import find_concat_chains, merged_text

# ---------------------------------------------------------------------------
# Engine function numbers for context-based classification
# ---------------------------------------------------------------------------
# A routine number is the zero-based index of the function prototype in the
# game's nwscript.nss. The tables below are verified against the NWN:EE copy
# (ovr/nwscript.nss); tests/test_ncs.py pins the key ids.

# ACTION routines whose *string* argument is shown to the player.
PLAYER_FACING_ACTIONS: Set[int] = {
    39,  # ActionSpeakString
    221,  # SpeakString
    284,  # SetCustomToken (token text is spliced into dialog shown to player)
    374,  # SendMessageToPC
    526,  # FloatingTextStringOnCreature
    830,  # SetName
    837,  # SetDescription
    901,  # PostString (EE on-screen text)
}

# ACTION routines whose string argument is an internal identifier
# (variable/tag/resref/database key) and must never be translated.
NON_PLAYER_ACTIONS: Set[int] = {
    1,  # PrintString (server log / DM console, not player screen)
    8,  # ExecuteScript (script resref)
    30,  # GetItemPossessedBy (item tag)
    31,  # CreateItemOnObject (item resref)
    46,  # PlaySound
    51,  # GetLocalInt (var name)
    52,  # GetLocalFloat (var name)
    53,  # GetLocalString (var name)
    54,  # GetLocalObject (var name)
    55,  # SetLocalInt (var name)
    56,  # SetLocalFloat (var name)
    57,  # SetLocalString (var name)
    58,  # SetLocalObject (var name)
    152,  # SetLocalLocation (var name)
    153,  # GetLocalLocation (var name)
    197,  # GetWaypointByTag
    200,  # GetObjectByTag
    229,  # GetNearestObjectByTag
    243,  # CreateObject (resref)
    265,  # DeleteLocalInt (var name)
    266,  # DeleteLocalFloat (var name)
    267,  # DeleteLocalString (var name)
    268,  # DeleteLocalObject (var name)
    269,  # DeleteLocalLocation (var name)
    417,  # SpeakOneLinerConversation (dialog resref, not display text)
    509,  # StartNewModule (module resref)
    563,  # SendMessageToAllDMs (DM-only, not player-visible)
    589,  # SetCampaignFloat (db/var name)
    590,  # SetCampaignInt (db/var name)
    591,  # SetCampaignVector (db/var name)
    592,  # SetCampaignLocation (db/var name)
    593,  # SetCampaignString (db/var name)
    594,  # DestroyCampaignDatabase (db name)
    595,  # GetCampaignFloat (db/var name)
    596,  # GetCampaignInt (db/var name)
    597,  # GetCampaignVector (db/var name)
    598,  # GetCampaignLocation (db/var name)
    599,  # GetCampaignString (db/var name)
    601,  # DeleteCampaignVariable (db/var name)
    602,  # StoreCampaignObject (db/var name)
    603,  # RetrieveCampaignObject (db/var name)
    848,  # SetTag (EE)
}

# Max bytecode steps to scan after a CONST for a consuming ACTION.
# Random/if/assign patterns may place SpeakString many instructions later in linear order.
_ACTION_SCAN_WINDOW = 64

# An ACTION this close to the push, with no other call in between, consumes the
# string: only argument pushes (CONST/CPTOPSP) fit in the gap.
_ADJACENT_CONSUMER_DISTANCE = 4

# ---------------------------------------------------------------------------
# Pattern-based heuristics
# ---------------------------------------------------------------------------

# Identifiers: snake_case, UPPER_CASE, CamelCase without spaces
_RE_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_RE_UPPER_CONST = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RE_RESREF = re.compile(r"^[a-zA-Z0-9_]{1,16}$")  # max 16 chars, no spaces
# Single word with an interior [A-Z][a-z] hump (BJArrested, oAssignedHorse):
# identifier convention — spoken one-worders are Titlecase or lowercase.
_RE_MIXED_CASE_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9]*[A-Z][a-z][A-Za-z0-9]*$")

# Known non-translatable prefixes: the shared engine tag families plus script
# resref prefixes that only ever name compiled scripts.
_SKIP_PREFIXES = ENGINE_TAG_PREFIXES + (
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


_DEBUG_PHRASES = (
    "report as bug",
    "report this bug",
    "please report",
    "debug string",
    "error:",
)


def _is_definitely_not_translatable(text: str, proven_player: bool = False) -> bool:
    """Quick rejection test for obviously non-translatable strings.

    With ``proven_player=True`` (a player-facing ACTION provably consumes the
    string) only the hard rules apply: identifier shapes, debug phrases, and
    letterless strings stay fatal, but the soft "looks odd" rules — short
    length, resref-like single words, ``*...*`` decoration, all-caps shouts —
    are waived, because ``*sniff*`` and ``Goodbye`` are then real speech.
    """
    stripped = text.strip()

    # --- hard rules: fatal regardless of consumption context ---

    # Empty / whitespace
    if not stripped:
        return True

    # No letters at all (pure numbers, '+', '***', ' = '): nothing to translate.
    if not any(ch.isalpha() for ch in stripped):
        return True

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

    # Mixed-case single-word identifiers: BJArrested, oAssignedHorse
    if " " not in stripped and _RE_MIXED_CASE_WORD.match(stripped):
        return True

    # Variable dump pattern: ends with " = " or "varName = "
    if stripped.endswith(" = ") or stripped.endswith("= ") or stripped.endswith(" ="):
        return True

    # Developer / debug error messages
    if any(phrase in lower for phrase in _DEBUG_PHRASES):
        return True

    if proven_player:
        # A bare all-lowercase single word ("triggered", "caravanrun") is an
        # identifier even when a speech call appears in the scan window — the
        # anywhere-in-window proof is too weak for it. Spoken one-worders are
        # Titlecase ("Goodbye") or carry punctuation ("merci!", "*sniff*").
        if " " not in stripped and stripped.isalpha() and stripped.islower():
            return True
        return False

    # --- soft rules: heuristics for strings with no proven consumer ---

    # Very short strings (single char, two chars)
    if len(stripped) <= 2:
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

    # ALL-CAPS debug shouts — only letter-only tokens, so normal dialogue with
    # "okay," / "sir." is not mistaken for a shout (see "I'm okay, sir. I think.").
    alpha_tokens = re.findall(r"[A-Za-z]+", stripped)
    if len(alpha_tokens) >= 3 and all(t.isupper() for t in alpha_tokens):
        return True

    return False


_RE_CAMEL_CASE = re.compile(r"[a-z][a-zA-Z]*[A-Z][a-zA-Z]*")
_RE_FUNC_DOT = re.compile(r"\b\w+\.\w+")
_RE_ALPHABET_DUMP = re.compile(
    r"^(?:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ|"
    r"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz)$"
)


def _contains_code_identifiers(text: str) -> bool:
    """True if text contains CamelCase identifiers or struct.field patterns."""
    return bool(_RE_CAMEL_CASE.search(text) or _RE_FUNC_DOT.search(text))


def ncs_hard_veto_reason(
    text: str,
    proven_player: bool = False,
    *,
    is_concat: bool = False,
) -> Optional[str]:
    """Return a deterministic reason why an NCS string must never be translated.

    This is stricter than extraction filtering and is used as a final safety
    net before translation. NCS bytecode can contain script identifiers and
    technical literals that become invalid if localized.

    ``proven_player=True`` relaxes exactly one rule: a single natural word with
    letters only ("Goodbye", "Farewell") is no longer treated as a resref when
    a player-facing ACTION provably consumes it. Identifier shapes with digits,
    underscores, or known prefixes stay vetoed regardless.

    ``is_concat=True`` skips the sentence-fragment rule: concat units are
    merged literals whose edges often carry spaces around ``<VARn>`` slots.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"

    # Concatenation fragments keep a leading space (" hour(s)…") or a trailing
    # space on an unfinished prefix ("You must wait "). A finished bark that
    # merely has a stray trailing space after ".!?" is left alone.
    # Merged concat chains skip this: the fragments are one translation unit.
    if not is_concat:
        if text[:1].isspace():
            return "sentence_fragment"
        if text[-1:].isspace() and stripped[-1] not in ".!?;:…":
            return "sentence_fragment"

    if _RE_ALPHABET_DUMP.match(stripped):
        return "alphabet_dump"

    lower = stripped.lower()
    if any(lower.startswith(p) for p in _SKIP_PREFIXES):
        return "known_internal_prefix"

    if _RE_SNAKE_CASE.match(stripped):
        return "snake_case_identifier"

    if _RE_UPPER_CONST.match(stripped):
        return "upper_case_constant"

    if " " not in stripped and "_" in stripped:
        return "underscore_identifier"

    if " " not in stripped and _RE_MIXED_CASE_WORD.match(stripped):
        return "code_identifier"

    if " " not in stripped and _RE_RESREF.match(stripped):
        if not (proven_player and stripped.isalpha() and not stripped.islower()):
            return "resref_like_identifier"

    if _contains_code_identifiers(stripped):
        return "code_identifier"

    if any(phrase in lower for phrase in _DEBUG_PHRASES):
        return "debug_or_developer_text"

    return None


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
        - ``"compare"`` if a string comparison (``OP_EQUAL`` / ``OP_NEQUAL``)
          appears before any player-facing ACTION — the string is a dispatch
          key (e.g. ``sChat == "animal empathy"`` in DMFI voice commands) and
          translating it silently breaks the script
        - ``"player"`` if a player-facing ACTION appears within the window
        - ``"internal"`` if an internal ACTION is the first call after the
          push and sits within arg-push distance — it is the consumer
        - ``"internal_weak"`` if internal consumers appear only farther away
        - ``None`` if nothing conclusive appears nearby

    The stack is not simulated, so a distant ACTION is not reliably the
    consumer: real speech regularly reaches its call through intermediate
    instructions — variable assignment chains (``sBill = "..."`` spoken after
    a switch) or nested argument calls (``DelayCommand(1.0,
    ActionSpeakString(...))`` compiles the inner call away from the push).
    A player-facing ACTION therefore wins over non-adjacent internal
    consumers, and ``"internal_weak"`` is advisory: the caller drops
    identifier-shaped strings on it but routes sentence-shaped ones to the
    LLM gate, because the true speech call may simply sit beyond the window.
    """
    window = min(_ACTION_SCAN_WINDOW, len(instructions) - instr_index - 1)
    action_seen = False
    internal_seen = False
    for i in range(1, window + 1):
        next_instr = instructions[instr_index + i]
        if next_instr.opcode in (OP_EQUAL, OP_NEQUAL) and next_instr.type_byte == TYPE_STRING:
            return "compare"
        if next_instr.is_action and next_instr.action_routine is not None:
            routine = next_instr.action_routine
            if routine in PLAYER_FACING_ACTIONS:
                return "player"
            if routine in NON_PLAYER_ACTIONS:
                # First call after the push, close enough that only argument
                # pushes fit in between: this ACTION consumes the string.
                if not action_seen and i <= _ADJACENT_CONSUMER_DISTANCE:
                    return "internal"
                internal_seen = True
            action_seen = True
            # Unknown routine — inconclusive, keep looking
        # Several string CONSTS are often pushed as successive arguments before
        # one ACTION; keep scanning within the window instead of stopping here.

    return "internal_weak" if internal_seen else None


def _action_name(routine: int) -> str:
    """Human-readable name for common ACTION routine numbers."""
    names = {
        39: "ActionSpeakString",
        221: "SpeakString",
        284: "SetCustomToken",
        374: "SendMessageToPC",
        526: "FloatingTextStringOnCreature",
        830: "SetName",
        837: "SetDescription",
        901: "PostString",
    }
    return names.get(routine, f"ACTION #{routine}")


def _bytecode_context(
    instr_index: int,
    instructions: List[NCSInstruction],
) -> Dict[str, Any]:
    """Summarize the bytecode neighbourhood of a string CONST instruction.

    Produces a structured record the LLM gate can reason over:
    * ``next_action`` — routine number and human name if an ACTION consumes it
    * ``compare_nearby`` — True if OP_EQUAL / OP_NEQUAL appears before any ACTION
    * ``distance`` — instructions between the CONST and the first consumer
    """
    context: Dict[str, Any] = {
        "next_action": None,
        "next_action_name": None,
        "compare_nearby": False,
        "distance": None,
    }
    window = min(_ACTION_SCAN_WINDOW, len(instructions) - instr_index - 1)
    for i in range(1, window + 1):
        next_instr = instructions[instr_index + i]
        # Only string-typed comparisons matter — int/float compares (e.g. loop
        # counters) downstream of the literal don't make it a dispatch key.
        if next_instr.opcode in (OP_EQUAL, OP_NEQUAL) and next_instr.type_byte == TYPE_STRING:
            context["compare_nearby"] = True
            context["distance"] = i
            return context
        if next_instr.is_action and next_instr.action_routine is not None:
            context["next_action"] = next_instr.action_routine
            context["next_action_name"] = _action_name(next_instr.action_routine)
            context["distance"] = i
            return context
    return context


class NcsExtractor(BaseExtractor):
    """Extractor for compiled NWScript (``.ncs``) files."""

    SUPPORTED_TYPES = [".ncs"]

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
        chains = find_concat_chains(ncs_file)
        chain_lit_offsets = {part.offset for chain in chains.values() for part in chain.lits()}
        emitted_chain_offsets: Set[int] = set()
        const_index_by_offset = {
            instr.offset: i for i, instr in enumerate(ncs_file.string_constants)
        }

        # The module-wide .nss index is the primary oracle; the bytecode
        # heuristics below are the fallback for modules without sources.
        encoding = parsed_data.get("_source_encoding") or "cp1252"
        try:
            index: Optional[NssModuleIndex] = get_module_index(file_path.parent, encoding)
        except OSError:
            index = None
        has_sources = index is not None and index.source_count > 0

        for idx, instr in enumerate(instructions):
            if not instr.is_string_const or instr.string_value is None:
                continue

            chain = chains.get(instr.offset)
            extra_meta: Dict[str, Any] = {}
            if chain is not None:
                if instr.offset in emitted_chain_offsets:
                    continue
                emitted_chain_offsets.add(instr.offset)
                text = merged_text(chain)
                scan_idx = chain.last_instr_index
                lookup_text = max((lit.text for lit in chain.lits()), key=len)
                extra_meta["concat_parts"] = chain.to_metadata()
            elif instr.offset in chain_lit_offsets:
                continue
            else:
                text = instr.string_value
                if not text.strip():
                    continue
                scan_idx = idx
                lookup_text = text

            verdict = index.verdict(lookup_text) if has_sources and index is not None else "absent"
            action_class = _classify_by_action_context(scan_idx, instructions)

            # Dispatch keys must never be translated: the per-occurrence
            # bytecode compare and the module-wide source compare are both
            # authoritative (e.g. DMFI `sChat == "animal empathy"`).
            if action_class == "compare" or verdict == "compare":
                continue
            if verdict == "internal":
                continue

            source_is_player = verdict == "player"
            bytecode_is_player = action_class == "player"

            # An adjacent internal consumer in bytecode is trusted only when
            # the sources do not overrule it: DelayCommand/AssignCommand
            # compile into stored state that hides the real speech call from
            # the linear scan, while the .nss names it explicitly.
            if action_class == "internal" and not source_is_player:
                continue

            # Quick rejection runs after classification: a proven player-facing
            # consumer waives the soft rules, so emotes (*sniff*) and one-word
            # barks (Goodbye) survive to translation.
            if _is_definitely_not_translatable(
                text, proven_player=source_is_player or bytecode_is_player
            ):
                continue

            bytecode_ctx = _bytecode_context(scan_idx, instructions)
            nss_snippet = (
                index.snippet(lookup_text, encoding, prefer_stem=file_path.stem)
                if has_sources and index is not None
                else None
            )

            # High-confidence player-facing: deterministic pass (no LLM gate)
            if source_is_player or bytecode_is_player:
                action_name = "script function"
                for i in range(1, min(_ACTION_SCAN_WINDOW + 1, len(instructions) - scan_idx)):
                    next_i = instructions[scan_idx + i]
                    if (
                        next_i.is_action
                        and next_i.action_routine is not None
                        and next_i.action_routine in PLAYER_FACING_ACTIONS
                    ):
                        action_name = _action_name(next_i.action_routine)
                        break
                if action_name == "script function" and source_is_player and index is not None:
                    action_name = index.player_consumer(lookup_text) or action_name
                context = (
                    f"Script text shown to player via {action_name} "
                    f"in {file_path.stem}.ncs. Translate naturally."
                )
                needs_llm_gate = False
                confidence = "high"
            elif verdict == "mixed" and _is_likely_translatable(text):
                # The module sources use this exact text both as speech and as
                # an internal identifier. Translating it may be right for this
                # occurrence, but only the LLM gate can tell.
                context = (
                    f"Script string at offset {instr.offset:#x} in {file_path.stem}.ncs. "
                    f"Module sources use this text both as player-visible speech and "
                    f"as an internal identifier. Only translate if this occurrence is "
                    f"natural language shown to the player."
                )
                confidence = "low"
                needs_llm_gate = True
            elif action_class == "internal_weak" and _is_likely_translatable(text):
                # A nearby internal consumer is a weak verdict: the true speech
                # call may sit beyond the scan window (assignment chains,
                # DelayCommand-wrapped calls). Sentence-shaped strings go to
                # the LLM gate instead of being dropped outright.
                context = (
                    f"Script string at offset {instr.offset:#x} in {file_path.stem}.ncs. "
                    f"Nearest bytecode consumer is an internal function (variable/tag), "
                    f"but the true consumer may be a later speech call. Only translate "
                    f"if it is natural language shown to the player."
                )
                confidence = "low"
                needs_llm_gate = True
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
                for i in range(1, min(_ACTION_SCAN_WINDOW + 1, len(instructions) - scan_idx)):
                    next_i = instructions[scan_idx + i]
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
                    item_id=f"{file_path.stem}:c{const_index_by_offset[instr.offset]}",
                    location=str(file_path),
                    metadata={
                        "type": "ncs_string",
                        "offset": instr.offset,
                        "confidence": confidence,
                        "proven_player": source_is_player or bytecode_is_player,
                        "needs_llm_gate": needs_llm_gate,
                        "ncs_hint": ncs_hint,
                        "nss_snippet": nss_snippet,
                        "bytecode_context": bytecode_ctx,
                        "source_class": verdict,
                        **extra_meta,
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
