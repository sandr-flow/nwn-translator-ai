"""Deterministic filters for engine/toolset strings that are not prose.

Two callers rely on this module. The entity-extraction pass uses it to decide
what may seed world entities, and the extractors use it to decide what may be
sent to the translator at all. Both are intentionally conservative: missing one
inferred proper noun is cheaper than letting technical labels pollute the
run-wide glossary, and translating an engine tag silently breaks the scripts
that look it up by name.

This module is also the single source of truth for engine tag prefixes; the NCS
extractor imports :data:`ENGINE_TAG_PREFIXES` rather than keeping its own copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Literal, Optional, Tuple

_PLACEHOLDER_RE = re.compile(r"^<[^<>\s]+>$")
_ANY_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")
_UNDERSCORE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")
_ALL_CAPS_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CAMEL_CASE_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z0-9][a-z0-9]*)+$")
_MIXED_CASE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*[A-Z][a-z][A-Za-z0-9]*$")
_ROUTE_LABEL_RE = re.compile(r"^[A-Za-z]*\d+[A-Za-z0-9]*$")
_RESREF_RE = re.compile(r"^[a-z]{1,4}_[a-z0-9_]+$", re.IGNORECASE)
_LETTER_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_PUNCT_RE = re.compile(r"[,.!?;:\"'()[\]]")
_NUMBERED_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z' -]{1,48}\s+\d{1,4}$")
_ZERO_PADDED_NUMBER_RE = re.compile(r"\b\d{3,}\b")
_QUEST_HIERARCHY_RE = re.compile(r"^[A-Z][\w'\- ]{1,40}\s+:\s+\S")

_SYSTEM_TERMS: FrozenSet[str] = frozenset(
    {
        "ad&d",
        "d&d",
        "dmfi",
        "nwn",
        "nwn:ee",
        "bioware",
        "neverwinternights",
        "neverwinter nights",
    }
)

# Engine/toolset tag prefixes that must never be translated: waypoints (WP_),
# destinations (DST_), post markers (POST_), Bioware's NW_/ARCH_ families.
# Shared with the NCS extractor, so a prefix is never declared in two lists.
ENGINE_TAG_PREFIXES: Tuple[str, ...] = (
    "arch_",
    "nw_",
    "wp_",
    "dst_",
    "post_",
)

# Placeholder tags left in place by Bioware toolset templates. Matched whole and
# case-insensitively: the all-caps form already reads as code-like, but
# "yourtaghere" and "Yourtaghere" would otherwise pass as ordinary words.
ENGINE_PLACEHOLDER_TAGS: FrozenSet[str] = frozenset({"yourtaghere"})

_GIT_TECHNICAL_TYPES: FrozenSet[str] = frozenset(
    {
        "trigger_name",
        "encounter_name",
        "item_name",
        "waypoint_name",
        "waypoint_map_note",
    }
)

_RACE_TOKENS: FrozenSet[str] = frozenset(
    {
        "human",
        "elf",
        "elven",
        "elvish",
        "dwarf",
        "dwarven",
        "dwarvish",
        "halfling",
        "gnome",
        "gnomish",
        "tiefling",
        "aasimar",
        "orc",
        "orcish",
        "ogre",
        "goblin",
        "drow",
        "kobold",
        "githyanki",
        "githzerai",
        "half-elf",
        "halfelf",
        "half-orc",
        "halforc",
    }
)


_GENDER_TOKENS: FrozenSet[str] = frozenset(
    {"male", "female", "man", "woman", "boy", "girl", "child"}
)
_BRACKETED_TAG_RE = re.compile(r"^\s*\[[A-Za-z0-9_]+\]\s+")


def _is_race_token(word: str) -> bool:
    return word.casefold() in _RACE_TOKENS


def _is_gender_token(word: str) -> bool:
    return word.casefold() in _GENDER_TOKENS


_GENERIC_PERSON_LABELS: FrozenSet[str] = frozenset(
    {
        "human male",
        "human female",
        "commoner",
        "patron",
        "resident",
        "shopper",
        "sitter",
        "guard",
        "villager",
        "merchant",
    }
)

_COMMON_SINGLE_NOUNS: FrozenSet[str] = frozenset(
    {
        "armor",
        "boat",
        "candle",
        "chest",
        "display",
        "food",
        "kit",
        "patron",
        "rat",
        "tree",
    }
)


CandidateDecision = Literal["drop", "deprioritize", "keep"]


@dataclass(frozen=True)
class StringClassification:
    """Classification of a source string for entity/glossary filtering."""

    text: str
    empty: bool = False
    placeholder: bool = False
    system_term: bool = False
    acronym_or_brand: bool = False
    code_like_identifier: bool = False
    wildcard_or_format_artifact: bool = False
    natural_language: bool = False
    reasons: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def blocked(self) -> bool:
        """Whether this string should be excluded from entity extraction."""
        return bool(
            self.empty
            or self.placeholder
            or self.system_term
            or self.acronym_or_brand
            or self.code_like_identifier
            or self.wildcard_or_format_artifact
        )

    @property
    def primary_reason(self) -> str:
        """Stable first reason for logging/tests."""
        return sorted(self.reasons)[0] if self.reasons else ""


@dataclass(frozen=True)
class CandidateFilterResult:
    """Deterministic entity/glossary candidate decision."""

    decision: CandidateDecision
    reason: str = ""
    technical_score: int = 0
    reasons: FrozenSet[str] = field(default_factory=frozenset)


def classify_string(text: object) -> StringClassification:
    """Classify *text* for conservative entity/glossary candidate filtering."""
    value = "" if text is None else str(text)
    stripped = value.strip()
    reasons = set()

    empty = not stripped
    if empty:
        reasons.add("empty")

    placeholder = bool(stripped and _PLACEHOLDER_RE.fullmatch(stripped))
    if placeholder:
        reasons.add("placeholder")

    lowered = stripped.casefold()
    system_term = (
        lowered in _SYSTEM_TERMS
        or lowered in ENGINE_PLACEHOLDER_TAGS
        or lowered.startswith(ENGINE_TAG_PREFIXES)
        or _contains_system_term(stripped)
    )
    if system_term:
        reasons.add("system_term")

    acronym_or_brand = _is_acronym_or_brand(stripped)
    if acronym_or_brand:
        reasons.add("acronym_or_brand")

    wildcard_or_format_artifact = _is_wildcard_or_format_artifact(stripped)
    if wildcard_or_format_artifact:
        reasons.add("wildcard_or_format_artifact")

    code_like_identifier = _is_code_like_identifier(stripped)
    if code_like_identifier:
        reasons.add("code_like_identifier")

    natural_language = _looks_natural_language(stripped)
    if natural_language:
        reasons.add("natural_language")

    return StringClassification(
        text=stripped,
        empty=empty,
        placeholder=placeholder,
        system_term=system_term,
        acronym_or_brand=acronym_or_brand,
        code_like_identifier=code_like_identifier,
        wildcard_or_format_artifact=wildcard_or_format_artifact,
        natural_language=natural_language,
        reasons=frozenset(reasons),
    )


def is_valid_entity_name(name: object, category: Optional[str] = None) -> bool:
    """Return True if a model-extracted entity is safe to add to the glossary."""
    cls = classify_string(name)
    if cls.blocked:
        return False

    cat = (category or "").strip().lower()
    words = _words(cls.text)

    if cat == "unknown":
        return cls.natural_language and len(words) >= 2

    if cls.natural_language:
        return True

    # Allow single fantasy/personal-looking names such as "Barovia" or "Ireena".
    if len(words) == 1:
        word = words[0]
        return len(word) >= 3 and word[0].isupper()

    return False


def should_skip_entity_source_text(text: object, metadata: Optional[dict] = None) -> bool:
    """Return True when a TranslatableItem text should not be sent to the LLM."""
    cls = classify_string(text)
    if cls.blocked:
        return True

    meta_type = str((metadata or {}).get("type", ""))
    if _is_git_technical_type(meta_type) and cls.code_like_identifier and not cls.natural_language:
        return True

    return False


def describe_rejection(name: object, category: Optional[str] = None) -> str:
    """Return a compact deterministic rejection reason."""
    cls = classify_string(name)
    if cls.primary_reason:
        return cls.primary_reason
    if (category or "").strip().lower() == "unknown":
        return "unknown_not_natural_multiword"
    return "invalid_entity_name"


def classify_entity_candidate(
    name: object,
    category: Optional[str] = None,
    *,
    source: str = "",
) -> CandidateFilterResult:
    """Classify whether *name* may become a glossary/world-context anchor.

    This does not decide whether the original string is translated.  It only
    controls whether the string is allowed to seed entity context.
    """
    cls = classify_string(name)
    if cls.empty:
        return CandidateFilterResult("drop", "empty", 100, frozenset({"empty"}))
    if cls.blocked:
        return CandidateFilterResult(
            "drop",
            cls.primary_reason or "blocked",
            90,
            cls.reasons,
        )

    text = cls.text
    lowered = text.casefold()
    words = _words(text)
    reasons = set()
    technical_score = 0

    if _NUMBERED_LABEL_RE.fullmatch(text):
        reasons.add("numbered_generic_label")
        technical_score += 80
        return CandidateFilterResult(
            "drop",
            "numbered_generic_label",
            technical_score,
            frozenset(reasons),
        )

    if _QUEST_HIERARCHY_RE.match(text):
        reasons.add("quest_hierarchy_label")
        technical_score += 85
        return CandidateFilterResult(
            "drop",
            "quest_hierarchy_label",
            technical_score,
            frozenset(reasons),
        )

    if lowered in _GENERIC_PERSON_LABELS:
        return CandidateFilterResult(
            "deprioritize",
            "generic_person_label",
            40,
            frozenset({"generic_person_label"}),
        )

    if len(words) == 1 and words[0].casefold() in _COMMON_SINGLE_NOUNS:
        return CandidateFilterResult(
            "deprioritize",
            "single_common_noun",
            30,
            frozenset({"single_common_noun"}),
        )

    raw_text = _BRACKETED_TAG_RE.sub("", text)
    raw_parts = raw_text.split()
    if len(raw_parts) == 2 and _is_race_token(raw_parts[0]) and _is_gender_token(raw_parts[1]):
        return CandidateFilterResult(
            "deprioritize",
            "generic_race_label",
            35,
            frozenset({"generic_race_label"}),
        )

    if lowered.endswith((" resident", " shopper", " sitter")):
        return CandidateFilterResult(
            "deprioritize",
            "generic_role_with_place_prefix",
            35,
            frozenset({"generic_role_with_place_prefix"}),
        )

    if (category or "").strip().lower() == "unknown" and len(words) == 1:
        return CandidateFilterResult(
            "deprioritize",
            "unknown_single_word",
            20,
            frozenset({"unknown_single_word"}),
        )

    return CandidateFilterResult("keep", "", technical_score, frozenset())


_GENERIC_REASONS: FrozenSet[str] = frozenset(
    {
        "generic_person_label",
        "generic_race_label",
        "generic_role_with_place_prefix",
    }
)


def is_generic_entity_label(name: object, category: Optional[str] = None) -> bool:
    """Return True if *name* is a non-disambiguating generic label.

    Generic labels (``Human Female``, ``Almraiven Resident``, ``dwarf merchant``)
    are shared by many distinct NPCs.  In prompt selection they must be
    admitted only by exact substring match on the name itself; tag/speaker
    matches are not evidence because every Human-Female NPC would otherwise
    pull in via a token co-occurrence.
    """
    result = classify_entity_candidate(name, category)
    return result.decision == "deprioritize" and result.reason in _GENERIC_REASONS


def _is_git_technical_type(meta_type: str) -> bool:
    if meta_type in _GIT_TECHNICAL_TYPES:
        return True
    return meta_type.startswith("waypoint_")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _is_acronym_or_brand(text: str) -> bool:
    if not text:
        return False
    if text.casefold() in _SYSTEM_TERMS:
        return True
    if "&" in text and len(text) <= 8:
        return True
    if text.isupper() and len(_words(text)) <= 2 and len(text) <= 16:
        return True
    return False


def _contains_system_term(text: str) -> bool:
    if not text:
        return False
    lowered = text.casefold()
    return any(term in lowered for term in _SYSTEM_TERMS)


def _is_wildcard_or_format_artifact(text: str) -> bool:
    if not text:
        return False
    if "*" in text or "{" in text or "}" in text:
        return True
    if _ANY_PLACEHOLDER_RE.search(text) and not _PLACEHOLDER_RE.fullmatch(text):
        return True
    return False


def _is_code_like_identifier(text: str) -> bool:
    if not text or " " in text:
        return False
    if _UNDERSCORE_IDENTIFIER_RE.fullmatch(text):
        return True
    if _ALL_CAPS_IDENTIFIER_RE.fullmatch(text) and len(text) > 1:
        return True
    if _RESREF_RE.fullmatch(text):
        return True
    if _ROUTE_LABEL_RE.fullmatch(text) and any(ch.isdigit() for ch in text):
        return True
    if _CAMEL_CASE_RE.fullmatch(text) or _MIXED_CASE_IDENTIFIER_RE.fullmatch(text):
        return True
    return False


def _looks_natural_language(text: str) -> bool:
    if not text:
        return False
    words = _words(text)
    if len(words) >= 2 and not _is_mostly_identifier_tokens(words):
        return True
    if _SENTENCE_PUNCT_RE.search(text) and len(words) >= 2:
        return True
    if len(words) == 1:
        word = words[0]
        return bool(word[0].isupper() and word[1:].islower() and len(word) >= 3)
    return False


def _is_mostly_identifier_tokens(words: Iterable[str]) -> bool:
    word_list = list(words)
    if not word_list:
        return False
    identifierish = 0
    for word in word_list:
        if _CAMEL_CASE_RE.fullmatch(word) or _ALL_CAPS_IDENTIFIER_RE.fullmatch(word):
            identifierish += 1
    return identifierish == len(word_list)
