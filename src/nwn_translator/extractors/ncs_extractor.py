"""Select NCS string occurrences using bytecode consumers and source context.

Extraction produces candidates. Every candidate still needs the translation
manager's safety gate; sentence shape and source snippets are not proof.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseExtractor, ExtractedContent, TranslatableItem
from .ncs_context import trace_string_consumer
from .nss_index import read_script_source, snippet_for_text
from ..context.string_filters import ENGINE_TAG_PREFIXES
from ..file_handlers.ncs_parser import NCSFile
from ..file_handlers.ncs_concat import find_concat_chains, merged_text

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


def _is_definitely_not_translatable(
    text: str, proven_player: bool = False, player_candidate: bool = False
) -> bool:
    """Apply the shared veto, then cheap candidate heuristics without context."""
    if ncs_hard_veto_reason(
        text, proven_player=proven_player, player_candidate=player_candidate, is_concat=True
    ):
        return True
    if proven_player or player_candidate:
        return False
    stripped = text.strip()

    # --- soft rules: heuristics for strings with no proven consumer ---

    # Very short strings (single char, two chars)
    if len(stripped) <= 2:
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
    player_candidate: bool = False,
) -> Optional[str]:
    """Return a deterministic reason why an NCS string must never be translated.

    This is stricter than extraction filtering and is used as a final safety
    net before translation. NCS bytecode can contain script identifiers and
    technical literals that become invalid if localized.

    ``proven_player=True`` permits natural words, including lowercase and
    uppercase barks, instead of treating them as resrefs/constants when
    a player-facing ACTION provably consumes it. Identifier shapes with digits,
    underscores, or known prefixes stay vetoed regardless.

    ``is_concat=True`` skips the sentence-fragment rule: concat units are
    merged literals whose edges often carry spaces around ``<VARn>`` slots.

    ``player_candidate=True`` allows a natural single word to reach the LLM
    gate without claiming proof. Only use it when that gate is enabled.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"
    if not any(ch.isalpha() for ch in stripped):
        return "no_letters"
    if stripped.endswith(" ="):
        return "debug_or_developer_text"

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

    if _RE_UPPER_CONST.match(stripped) and (
        "_" in stripped or not (proven_player or player_candidate)
    ):
        return "upper_case_constant"

    if " " not in stripped and "_" in stripped:
        return "underscore_identifier"

    if not proven_player and " " not in stripped and _RE_MIXED_CASE_WORD.match(stripped):
        return "code_identifier"

    if " " not in stripped and _RE_RESREF.match(stripped):
        if not ((proven_player or player_candidate) and stripped.isalpha()):
            return "resref_like_identifier"

    if not proven_player and _contains_code_identifiers(stripped):
        return "code_identifier"

    if not proven_player and any(phrase in lower for phrase in _DEBUG_PHRASES):
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
        const_index_by_offset = {
            instr.offset: i for i, instr in enumerate(ncs_file.string_constants)
        }
        selection_trace = parsed_data.get("_ncs_selection_trace")

        def record(stage: str, kept: bool, reason: str, offsets: List[int]) -> None:
            if selection_trace is not None:
                selection_trace.append(
                    {
                        "stage": stage,
                        "kept": kept,
                        "reason": reason,
                        "const_indices": [const_index_by_offset[offset] for offset in offsets],
                    }
                )

        encoding = parsed_data.get("_source_encoding") or "cp1252"
        source = read_script_source(file_path, encoding)

        for idx, instr in enumerate(instructions):
            if not instr.is_string_const or instr.string_value is None:
                continue

            chain = chains.get(instr.offset)
            extra_meta: Dict[str, Any] = {}
            if chain is not None:
                offsets = [lit.offset for lit in chain.lits()]
                text = merged_text(chain)
                scan_idx = chain.last_instr_index
                lookup_text = max((lit.text for lit in chain.lits()), key=len)
                extra_meta["concat_parts"] = chain.to_metadata()
            elif instr.offset in chain_lit_offsets:
                continue
            else:
                offsets = [instr.offset]
                text = instr.string_value
                if not text.strip():
                    record("units", False, "empty_literal", offsets)
                    continue
                scan_idx = idx
                lookup_text = text

            record("units", True, "concat" if chain is not None else "literal", offsets)

            bytecode_ctx = trace_string_consumer(scan_idx, instructions)
            action_class = bytecode_ctx["role"]
            record(
                "consumer",
                action_class not in ("internal", "compare"),
                action_class or "unresolved",
                offsets,
            )
            if action_class in ("internal", "compare"):
                continue

            bytecode_is_player = action_class == "player"
            nss_snippet = snippet_for_text(lookup_text, source)
            player_candidate = (
                bytecode_ctx["player_action_nearby"] or bytecode_ctx["player_use_seen"]
            )
            if _is_definitely_not_translatable(
                text, proven_player=bytecode_is_player, player_candidate=player_candidate
            ):
                record("text_filter", False, "technical_or_shape_veto", offsets)
                continue
            record("text_filter", True, "passed", offsets)
            if not (bytecode_is_player or player_candidate or _is_likely_translatable(text)):
                record("candidate", False, "insufficient_context_or_sentence_shape", offsets)
                continue
            record("candidate", True, "passed", offsets)

            ncs_hint = bytecode_ctx["next_action_name"] or "ambiguous_bytecode"
            confidence = "high" if bytecode_is_player else "medium"
            context = (
                f"Script string at offset {instr.offset:#x} in {file_path.stem}.ncs. "
                f"Consumer: {ncs_hint}. Translate only player-visible natural language."
            )

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
                        "proven_player": bytecode_is_player,
                        "player_candidate": player_candidate,
                        "needs_llm_gate": True,
                        "ncs_hint": ncs_hint,
                        "nss_snippet": nss_snippet,
                        "bytecode_context": bytecode_ctx,
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
