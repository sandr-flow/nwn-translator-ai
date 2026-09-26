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
from typing import Callable, FrozenSet, List, Literal, Optional, Tuple

_PLACEHOLDER_RE = re.compile(r"^<[^<>\s]+>$")
_ANY_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")
_UNDERSCORE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")
_ALL_CAPS_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CAMEL_CASE_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z0-9][a-z0-9]*)+$")
_MIXED_CASE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*[A-Z][a-z][A-Za-z0-9]*$")
_ROUTE_LABEL_RE = re.compile(r"^[A-Za-z]*\d+[A-Za-z0-9]*$")
_RESREF_RE = re.compile(r"^[a-z]{1,4}_[a-z0-9_]+$", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_PUNCT_RE = re.compile(r"[,.!?;:\"'()[\]]")
_NUMBERED_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z' -]{1,48}\s+\d{1,4}$")
_QUEST_HIERARCHY_RE = re.compile(r"^[A-Z][\w'\- ]{1,40}\s+:\s+\S")
_BRACKETED_TAG_RE = re.compile(r"^\s*\[[A-Za-z0-9_]+\]\s+")

# Paired asterisks with at least one letter inside: the NWN emote convention
# (*gasp*, *whispers* ..., "SAY MY NAME! *WHIPCRACK*") — player-facing prose,
# unlike bare/unpaired wildcards ("Court of the Count *").
_EMOTE_MARKUP_RE = re.compile(r"\*[^*]*[A-Za-z][^*]*\*")

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

#: ``.git`` item types whose code-like labels are toolset names, not prose.
#: Every ``waypoint_*`` type counts as well.
_GIT_TECHNICAL_TYPES: FrozenSet[str] = frozenset({"trigger_name", "encounter_name", "item_name"})

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

# Reasons that make a string unfit for translation/entity seeding, as opposed
# to informational flags such as ``natural_language``.
_BLOCKING_REASONS: FrozenSet[str] = frozenset(
    {
        "empty",
        "placeholder",
        "system_term",
        "acronym_or_brand",
        "code_like_identifier",
        "wildcard_or_format_artifact",
        "script_comment",
    }
)

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

# "patron" also appears above: this set catches it with punctuation ("Patron.").
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

_GENERIC_REASONS: FrozenSet[str] = frozenset(
    {
        "generic_person_label",
        "generic_race_label",
        "generic_role_with_place_prefix",
    }
)


CandidateDecision = Literal["drop", "deprioritize", "keep"]


@dataclass(frozen=True)
class StringClassification:
    """Classification of a source string for entity/glossary filtering.

    Attributes:
        text: The stripped string.
        reasons: Every rule the string triggers (blocking reasons and the
            informational ``natural_language``).
        emote_markup: Paired asterisks are the only artifact (``*gasp*``).
    """

    text: str
    reasons: FrozenSet[str] = field(default_factory=frozenset)
    emote_markup: bool = False

    @property
    def empty(self) -> bool:
        """The string is empty after stripping."""
        return "empty" in self.reasons

    @property
    def acronym_or_brand(self) -> bool:
        """Upper-case acronym, ``&`` short form or system term."""
        return "acronym_or_brand" in self.reasons

    @property
    def code_like_identifier(self) -> bool:
        """Identifier-shaped: underscores, CamelCase, all caps, resref or route label."""
        return "code_like_identifier" in self.reasons

    @property
    def natural_language(self) -> bool:
        """Reads as words: two or more words, or one capitalized word."""
        return "natural_language" in self.reasons

    @property
    def blocked(self) -> bool:
        """Whether this string should be excluded from entity extraction."""
        return bool(self.reasons & _BLOCKING_REASONS)

    @property
    def primary_reason(self) -> str:
        """Alphabetically first reason, for logs and drop reasons."""
        return sorted(self.reasons)[0] if self.reasons else ""


@dataclass(frozen=True)
class CandidateFilterResult:
    """Deterministic entity/glossary candidate decision.

    Attributes:
        decision: ``drop``, ``deprioritize`` or ``keep``.
        reason: Reason tag of the decision.
        technical_score: How technical the name looks (0-100).
        reasons: Reason tags reported to the curator as technical flags.
    """

    decision: CandidateDecision
    reason: str = ""
    technical_score: int = 0
    reasons: FrozenSet[str] = field(default_factory=frozenset)


def classify_string(text: object) -> StringClassification:
    """Classify *text* for conservative entity/glossary candidate filtering.

    Args:
        text: Any value; ``None`` counts as empty.

    Returns:
        The classification of the stripped string.
    """
    value = "" if text is None else str(text)
    stripped = value.strip()
    lowered = stripped.casefold()
    # Scripter comments stored in name fields ("// * * * SCENE: ...").
    script_comment = stripped.startswith("//")
    checks = (
        ("empty", not stripped),
        ("placeholder", bool(stripped and _PLACEHOLDER_RE.fullmatch(stripped))),
        (
            "system_term",
            lowered in ENGINE_PLACEHOLDER_TAGS
            or lowered.startswith(ENGINE_TAG_PREFIXES)
            or any(term in lowered for term in _SYSTEM_TERMS),
        ),
        ("acronym_or_brand", _is_acronym_or_brand(stripped)),
        ("wildcard_or_format_artifact", _is_wildcard_or_format_artifact(stripped)),
        ("script_comment", script_comment),
        ("code_like_identifier", _is_code_like_identifier(stripped)),
        ("natural_language", _looks_natural_language(stripped)),
    )
    # Emote markup only when the asterisks are the sole artifact trigger:
    # with them stripped out, the rest must be clean of braces/placeholders.
    emote_markup = (
        not script_comment
        and bool(_EMOTE_MARKUP_RE.search(stripped))
        and not _is_wildcard_or_format_artifact(stripped.replace("*", ""))
    )
    return StringClassification(
        text=stripped,
        reasons=frozenset(reason for reason, hit in checks if hit),
        emote_markup=emote_markup,
    )


def is_valid_entity_name(name: object, category: Optional[str] = None) -> bool:
    """Return True if a model-extracted entity is safe to add to the glossary.

    Args:
        name: Name returned by entity extraction.
        category: Its category.

    Returns:
        Whether the name passes the candidate filter and looks like a name for
        its category (``unknown`` needs a natural-language multi-word name).
    """
    cls = classify_string(name)
    if _classify_candidate(cls, category).decision == "drop":
        return False
    if cls.acronym_or_brand:
        return True

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


def should_skip_entity_source_text(
    text: object,
    metadata: Optional[dict] = None,
    known_names: Optional[FrozenSet[str]] = None,
) -> bool:
    """Return True when a TranslatableItem text should not be sent to the LLM.

    Emote markup (``*gasp*``, ``*whispers* ...``) is player-facing prose and is
    allowed through when the wildcard artifact rule is the only objection.
    *known_names* (casefolded display names taken from the module's own
    blueprints) rescues strings whose only objection is the code-like shape:
    a CamelCase surname such as ``McGee`` or ``DeVir`` is legitimate when a
    creature in the same module carries it as a name — the .utc side already
    translates it unfiltered, so blocking the .git copy would desynchronize
    the two. Engine tags stay blocked (``system_term`` is a separate reason),
    and so do technical field types below. The glossary gates
    (:func:`is_valid_entity_name`, :func:`classify_entity_candidate`)
    deliberately stay strict — an asterisk-bearing string is never an entity
    name, and neither is a rescued CamelCase one.

    Args:
        text: Item text.
        metadata: Item metadata; its ``type`` selects the technical-type rule.
        known_names: Casefolded creature names of the module.

    Returns:
        Whether the text must be skipped.
    """
    cls = classify_string(text)
    if cls.blocked:
        blocking = cls.reasons & _BLOCKING_REASONS
        rescued = bool(
            known_names is not None
            and blocking <= {"code_like_identifier"}
            and cls.text.casefold() in known_names
        )
        if not rescued and not (cls.emote_markup and blocking <= {"wildcard_or_format_artifact"}):
            return True

    meta_type = str((metadata or {}).get("type", ""))
    if _is_git_technical_type(meta_type) and cls.code_like_identifier and not cls.natural_language:
        return True

    return False


def describe_rejection(name: object, category: Optional[str] = None) -> str:
    """Return a compact deterministic rejection reason for a log line."""
    cls = classify_string(name)
    if cls.primary_reason:
        return cls.primary_reason
    if (category or "").strip().lower() == "unknown":
        return "unknown_not_natural_multiword"
    return "invalid_entity_name"


def _is_race_gender_pair(text: str) -> bool:
    """``Human Female``-style label, optionally after a ``[TAG]`` prefix."""
    parts = _BRACKETED_TAG_RE.sub("", text).split()
    return (
        len(parts) == 2
        and parts[0].casefold() in _RACE_TOKENS
        and parts[1].casefold() in _GENDER_TOKENS
    )


#: Rule predicate: ``(text, casefolded text, words, lower-cased category) -> applies``.
_CandidateRule = Callable[[str, str, List[str], str], bool]

#: Candidate rules applied in order after the blocking checks:
#: ``(decision, reason, technical score, predicate)``; the first match decides.
_CANDIDATE_RULES: Tuple[Tuple[CandidateDecision, str, int, _CandidateRule], ...] = (
    (
        "drop",
        "numbered_generic_label",
        80,
        lambda text, _folded, _words, _cat: bool(_NUMBERED_LABEL_RE.fullmatch(text)),
    ),
    (
        "drop",
        "quest_hierarchy_label",
        85,
        lambda text, _folded, _words, _cat: bool(_QUEST_HIERARCHY_RE.match(text)),
    ),
    (
        "deprioritize",
        "generic_person_label",
        40,
        lambda _text, folded, _words, _cat: folded in _GENERIC_PERSON_LABELS,
    ),
    (
        "deprioritize",
        "single_common_noun",
        30,
        lambda _text, _folded, words, _cat: len(words) == 1
        and words[0].casefold() in _COMMON_SINGLE_NOUNS,
    ),
    (
        "deprioritize",
        "generic_race_label",
        35,
        lambda text, _folded, _words, _cat: _is_race_gender_pair(text),
    ),
    (
        "deprioritize",
        "generic_role_with_place_prefix",
        35,
        lambda _text, folded, _words, _cat: folded.endswith((" resident", " shopper", " sitter")),
    ),
    (
        "deprioritize",
        "unknown_single_word",
        20,
        lambda _text, _folded, words, cat: cat == "unknown" and len(words) == 1,
    ),
)


def classify_entity_candidate(
    name: object, category: Optional[str] = None
) -> CandidateFilterResult:
    """Classify whether *name* may become a glossary/world-context anchor.

    This does not decide whether the original string is translated.  It only
    controls whether the string is allowed to seed entity context.

    Args:
        name: Candidate name.
        category: Its category; ``unknown`` single words are deprioritized.

    Returns:
        The deterministic decision.
    """
    return _classify_candidate(classify_string(name), category)


def _classify_candidate(
    cls: StringClassification, category: Optional[str]
) -> CandidateFilterResult:
    """:func:`classify_entity_candidate` for an already classified string."""
    if cls.empty:
        return CandidateFilterResult("drop", "empty", 100, frozenset({"empty"}))
    # Acronyms in visible text need curation, not automatic removal from terminology.
    blocking = cls.reasons & _BLOCKING_REASONS
    if cls.acronym_or_brand and blocking <= {"acronym_or_brand", "code_like_identifier"}:
        return CandidateFilterResult("keep", "needs_acronym_curation", 0, cls.reasons)
    if cls.blocked:
        return CandidateFilterResult("drop", cls.primary_reason or "blocked", 90, cls.reasons)

    text = cls.text
    lowered = text.casefold()
    words = _words(text)
    category_key = (category or "").strip().lower()
    for decision, reason, score, applies in _CANDIDATE_RULES:
        if applies(text, lowered, words, category_key):
            return CandidateFilterResult(decision, reason, score, frozenset({reason}))
    return CandidateFilterResult("keep")


def is_generic_entity_label(name: object, category: Optional[str] = None) -> bool:
    """Return True if *name* is a non-disambiguating generic label.

    Generic labels (``Human Female``, ``Almraiven Resident``, ``dwarf merchant``)
    are shared by many distinct NPCs.  In prompt selection they must be
    admitted only by exact substring match on the name itself; tag/speaker
    matches are not evidence because every Human-Female NPC would otherwise
    pull in via a token co-occurrence.

    Args:
        name: Entity name.
        category: Its category.

    Returns:
        Whether the candidate filter deprioritizes the name as a generic
        person, race or role label.
    """
    result = classify_entity_candidate(name, category)
    return result.decision == "deprioritize" and result.reason in _GENERIC_REASONS


def _is_git_technical_type(meta_type: str) -> bool:
    """Whether *meta_type* names a toolset label field of a ``.git`` instance."""
    return meta_type in _GIT_TECHNICAL_TYPES or meta_type.startswith("waypoint_")


def _words(text: str) -> List[str]:
    """ASCII words of *text* (an inner apostrophe stays in the word)."""
    return _WORD_RE.findall(text)


def _is_acronym_or_brand(text: str) -> bool:
    """System term, short ``&`` form or upper-case text of at most two words."""
    if not text:
        return False
    if text.casefold() in _SYSTEM_TERMS:
        return True
    if "&" in text and len(text) <= 8:
        return True
    if text.isupper() and len(_words(text)) <= 2 and len(text) <= 16:
        return True
    return False


def _is_wildcard_or_format_artifact(text: str) -> bool:
    """Asterisk, brace or embedded placeholder (a whole-string placeholder is not one)."""
    if not text:
        return False
    if "*" in text or "{" in text or "}" in text:
        return True
    if _ANY_PLACEHOLDER_RE.search(text) and not _PLACEHOLDER_RE.fullmatch(text):
        return True
    return False


def _is_code_like_identifier(text: str) -> bool:
    """Single token shaped like an identifier, resref or route label."""
    if not text or " " in text:
        return False
    if _UNDERSCORE_IDENTIFIER_RE.fullmatch(text):
        return True
    if _ALL_CAPS_IDENTIFIER_RE.fullmatch(text) and len(text) > 1:
        return True
    if _RESREF_RE.fullmatch(text):
        return True
    if _ROUTE_LABEL_RE.fullmatch(text):
        return True
    if _CAMEL_CASE_RE.fullmatch(text) or _MIXED_CASE_IDENTIFIER_RE.fullmatch(text):
        return True
    return False


def _looks_natural_language(text: str) -> bool:
    """Two or more words not all identifier-shaped, or one ``Capitalized`` word."""
    if not text:
        return False
    words = _words(text)
    if len(words) >= 2 and not _all_identifier_tokens(words):
        return True
    if _SENTENCE_PUNCT_RE.search(text) and len(words) >= 2:
        return True
    if len(words) == 1:
        word = words[0]
        return bool(word[0].isupper() and word[1:].islower() and len(word) >= 3)
    return False


def _all_identifier_tokens(words: List[str]) -> bool:
    """Whether every word is CamelCase or all caps (``False`` for no words)."""
    if not words:
        return False
    return all(
        _CAMEL_CASE_RE.fullmatch(word) or _ALL_CAPS_IDENTIFIER_RE.fullmatch(word) for word in words
    )
