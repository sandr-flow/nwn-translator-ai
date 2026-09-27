"""Deterministic filters for engine and toolset strings that are not prose.

Entity extraction uses them to decide what may seed world entities, and the
extractors to decide what may be sent to the translator at all. Both are
conservative: missing one inferred proper noun is cheaper than technical labels
in the run-wide glossary, and translating an engine tag silently breaks the
scripts that look it up by name. :data:`ENGINE_TAG_PREFIXES` is the one list of
engine tag prefixes; the NCS extractor imports it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Literal, Optional, Tuple

_PLACEHOLDER_RE = re.compile(r"^<[^<>\s]+>$")
_ANY_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")
_ALL_CAPS_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CAMEL_CASE_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z0-9][a-z0-9]*)+$")
#: Other identifier shapes: underscores, resref, route label, CamelCase, mixed case.
_IDENTIFIER_RES = (
    re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$"),
    re.compile(r"^[a-z]{1,4}_[a-z0-9_]+$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]*\d+[A-Za-z0-9]*$"),
    _CAMEL_CASE_RE,
    re.compile(r"^[A-Za-z][A-Za-z0-9]*[A-Z][a-z][A-Za-z0-9]*$"),
)
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_PUNCT_RE = re.compile(r"[,.!?;:\"'()[\]]")
_NUMBERED_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z' -]{1,48}\s+\d{1,4}$")
_QUEST_HIERARCHY_RE = re.compile(r"^[A-Z][\w'\- ]{1,40}\s+:\s+\S")
_BRACKETED_TAG_RE = re.compile(r"^\s*\[[A-Za-z0-9_]+\]\s+")
#: Paired asterisks around a letter: the NWN emote convention (``*gasp*``), which is
#: player-facing prose, unlike a bare wildcard (``Court of the Count *``).
_EMOTE_MARKUP_RE = re.compile(r"\*[^*]*[A-Za-z][^*]*\*")

_SYSTEM_TERMS = frozenset(
    {"ad&d", "d&d", "dmfi", "nwn", "nwn:ee", "bioware", "neverwinternights", "neverwinter nights"}
)
#: Engine and toolset tag prefixes that are never translated: waypoints, destinations,
#: post markers and Bioware's ``NW_``/``ARCH_`` families.
ENGINE_TAG_PREFIXES: Tuple[str, ...] = ("arch_", "nw_", "wp_", "dst_", "post_")
#: Toolset template placeholder tags, matched whole and case-insensitively.
ENGINE_PLACEHOLDER_TAGS: FrozenSet[str] = frozenset({"yourtaghere"})
#: ``.git`` item types whose code-like labels are toolset names; so is every ``waypoint_*``.
_GIT_TECHNICAL_TYPES = frozenset({"trigger_name", "encounter_name", "item_name"})
_RACE_TOKENS = frozenset(
    "human elf elven elvish dwarf dwarven dwarvish halfling gnome gnomish tiefling aasimar orc "
    "orcish ogre goblin drow kobold githyanki githzerai half-elf halfelf half-orc halforc".split()
)
_GENDER_TOKENS = frozenset({"male", "female", "man", "woman", "boy", "girl", "child"})
_GENERIC_PERSON_LABELS = frozenset(
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
#: Single common nouns; "patron" is here too, to catch it with punctuation ("Patron.").
_COMMON_SINGLE_NOUNS = frozenset("armor boat candle chest display food kit patron rat tree".split())
#: Reasons that make a string unfit for translation and entity seeding, unlike the
#: informational ``natural_language``.
_BLOCKING_REASONS = frozenset(
    "empty placeholder system_term acronym_or_brand code_like_identifier "
    "wildcard_or_format_artifact script_comment".split()
)
_GENERIC_REASONS = frozenset(
    {"generic_person_label", "generic_race_label", "generic_role_with_place_prefix"}
)

#: Decision of the deterministic candidate filter.
CandidateDecision = Literal["drop", "deprioritize", "keep"]


@dataclass(frozen=True)
class StringClassification:
    """Classification of a source string for entity and glossary filtering.

    Attributes:
        text: The stripped string.
        reasons: Every rule the string triggers: the blocking reasons and the
            informational ``natural_language``.
        emote_markup: Paired asterisks are the only artifact (``*gasp*``).
    """

    text: str
    reasons: FrozenSet[str] = field(default_factory=frozenset)
    emote_markup: bool = False

    @property
    def blocking(self) -> FrozenSet[str]:
        """The reasons that make the string unfit for translation and entity seeding."""
        return self.reasons & _BLOCKING_REASONS

    @property
    def blocked(self) -> bool:
        """Whether a blocking reason applies."""
        return bool(self.blocking)

    @property
    def natural_language(self) -> bool:
        """Reads as words: two or more words, or one capitalized word."""
        return "natural_language" in self.reasons

    @property
    def primary_reason(self) -> str:
        """Alphabetically first reason, for logs and drop reasons."""
        return min(self.reasons, default="")


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
    """Classifies *text* for conservative entity and glossary filtering.

    Args:
        text: Any value; ``None`` counts as empty.

    Returns:
        The classification of the stripped string.
    """
    stripped = ("" if text is None else str(text)).strip()
    lowered = stripped.casefold()
    # Scripter comments stored in name fields ("// * * * SCENE: ...").
    script_comment = stripped.startswith("//")
    checks = {
        "empty": not stripped,
        "placeholder": bool(_PLACEHOLDER_RE.fullmatch(stripped)),
        "system_term": lowered in ENGINE_PLACEHOLDER_TAGS
        or lowered.startswith(ENGINE_TAG_PREFIXES)
        or any(term in lowered for term in _SYSTEM_TERMS),
        "acronym_or_brand": _is_acronym_or_brand(stripped),
        "wildcard_or_format_artifact": _is_wildcard_or_format_artifact(stripped),
        "script_comment": script_comment,
        "code_like_identifier": _is_code_like_identifier(stripped),
        "natural_language": _looks_natural_language(stripped),
    }
    # Emote markup only when, without the asterisks, nothing else is an artifact.
    emote_markup = (
        not script_comment
        and bool(_EMOTE_MARKUP_RE.search(stripped))
        and not _is_wildcard_or_format_artifact(stripped.replace("*", ""))
    )
    reasons = frozenset(reason for reason, hit in checks.items() if hit)
    return StringClassification(stripped, reasons, emote_markup)


def is_valid_entity_name(name: object, category: Optional[str] = None) -> bool:
    """Tells whether a model-extracted entity is safe to add to the glossary.

    Args:
        name: Name returned by entity extraction.
        category: Its category.

    Returns:
        ``True`` when the name passes the candidate filter and looks like a name
        for its category (``unknown`` needs a natural-language multi-word name).
    """
    cls = classify_string(name)
    if _classify_candidate(cls, category).decision == "drop":
        return False
    if "acronym_or_brand" in cls.reasons:
        return True
    words = _WORD_RE.findall(cls.text)
    if (category or "").strip().lower() == "unknown":
        return cls.natural_language and len(words) >= 2
    if cls.natural_language:
        return True
    # A single fantasy or personal name such as "Barovia" or "Ireena".
    return len(words) == 1 and len(words[0]) >= 3 and words[0][0].isupper()


def should_skip_entity_source_text(
    text: object,
    metadata: Optional[dict] = None,
    known_names: Optional[FrozenSet[str]] = None,
) -> bool:
    """Tells whether an item text must not be sent to the model.

    Emote markup (``*gasp*``) is prose and passes when the wildcard rule is its
    only objection. *known_names* rescues a string whose only objection is its
    code-like shape: a CamelCase surname (``McGee``) that a creature of the
    module carries is translated on the ``.utc`` side anyway, and blocking the
    ``.git`` copy would desynchronize the two. Engine tags (``system_term``) and
    technical ``.git`` field types stay blocked, and the glossary gates stay
    strict: neither an emote nor a rescued name ever becomes an entity name.

    Args:
        text: Item text.
        metadata: Item metadata; its ``type`` selects the technical-type rule.
        known_names: Casefolded creature names of the module.

    Returns:
        ``True`` when the text must be skipped.
    """
    cls = classify_string(text)
    blocking = cls.blocking
    if blocking:
        rescued = (
            known_names is not None
            and blocking <= {"code_like_identifier"}
            and cls.text.casefold() in known_names
        )
        emote = cls.emote_markup and blocking <= {"wildcard_or_format_artifact"}
        if not (rescued or emote):
            return True
    meta_type = str((metadata or {}).get("type", ""))
    technical = meta_type in _GIT_TECHNICAL_TYPES or meta_type.startswith("waypoint_")
    return technical and "code_like_identifier" in cls.reasons and not cls.natural_language


def describe_rejection(name: object, category: Optional[str] = None) -> str:
    """Returns a compact rejection reason for a log line.

    Args:
        name: Rejected entity name.
        category: Its category.

    Returns:
        The primary classification reason, else a reason for the category rule.
    """
    reason = classify_string(name).primary_reason
    if reason:
        return reason
    if (category or "").strip().lower() == "unknown":
        return "unknown_not_natural_multiword"
    return "invalid_entity_name"


def classify_entity_candidate(
    name: object, category: Optional[str] = None
) -> CandidateFilterResult:
    """Classifies whether *name* may seed the glossary and world context.

    It does not decide whether the string itself is translated.

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
    """Returns the :func:`classify_entity_candidate` decision of a classified string."""
    if "empty" in cls.reasons:
        return CandidateFilterResult("drop", "empty", 100, frozenset({"empty"}))
    # Acronyms in visible text need curation, not automatic removal from terminology.
    acronym_only = cls.blocking <= {"acronym_or_brand", "code_like_identifier"}
    if "acronym_or_brand" in cls.reasons and acronym_only:
        return CandidateFilterResult("keep", "needs_acronym_curation", 0, cls.reasons)
    if cls.blocked:
        return CandidateFilterResult("drop", cls.primary_reason, 90, cls.reasons)
    rule = _generic_label_rule(cls.text, (category or "").strip().lower())
    if rule is None:
        return CandidateFilterResult("keep")
    decision, reason, score = rule
    return CandidateFilterResult(decision, reason, score, frozenset({reason}))


def _generic_label_rule(text: str, category: str) -> Optional[Tuple[CandidateDecision, str, int]]:
    """Returns ``(decision, reason, technical score)`` of the first generic-label rule met."""
    folded = text.casefold()
    words = _WORD_RE.findall(text)
    if _NUMBERED_LABEL_RE.fullmatch(text):
        return "drop", "numbered_generic_label", 80
    if _QUEST_HIERARCHY_RE.match(text):
        return "drop", "quest_hierarchy_label", 85
    if folded in _GENERIC_PERSON_LABELS:
        return "deprioritize", "generic_person_label", 40
    if len(words) == 1 and words[0].casefold() in _COMMON_SINGLE_NOUNS:
        return "deprioritize", "single_common_noun", 30
    pair = _BRACKETED_TAG_RE.sub("", text).casefold().split()  # "[TAG] Human Female" too
    if len(pair) == 2 and pair[0] in _RACE_TOKENS and pair[1] in _GENDER_TOKENS:
        return "deprioritize", "generic_race_label", 35
    if folded.endswith((" resident", " shopper", " sitter")):
        return "deprioritize", "generic_role_with_place_prefix", 35
    if category == "unknown" and len(words) == 1:
        return "deprioritize", "unknown_single_word", 20
    return None


def is_generic_entity_label(name: object, category: Optional[str] = None) -> bool:
    """Tells whether *name* is a generic label shared by many distinct NPCs.

    Prompt selection never admits such a label (``Human Female``, ``Almraiven
    Resident``) by a tag or token match.

    Args:
        name: Entity name.
        category: Its category.

    Returns:
        ``True`` when the candidate filter deprioritizes the name as a generic
        person, race or role label.
    """
    result = classify_entity_candidate(name, category)
    return result.decision == "deprioritize" and result.reason in _GENERIC_REASONS


def _is_acronym_or_brand(text: str) -> bool:
    """Tells whether *text* is a system term, a short ``&`` form or short upper-case text."""
    return bool(text) and (
        text.casefold() in _SYSTEM_TERMS
        or ("&" in text and len(text) <= 8)
        or (text.isupper() and len(_WORD_RE.findall(text)) <= 2 and len(text) <= 16)
    )


def _is_wildcard_or_format_artifact(text: str) -> bool:
    """Tells whether *text* holds an asterisk, a brace or a placeholder that is not all of it."""
    if any(char in text for char in "*{}"):
        return True
    return bool(_ANY_PLACEHOLDER_RE.search(text) and not _PLACEHOLDER_RE.fullmatch(text))


def _is_code_like_identifier(text: str) -> bool:
    """Tells whether *text* is one token shaped like an identifier, resref or route label."""
    if " " in text:
        return False
    if _ALL_CAPS_IDENTIFIER_RE.fullmatch(text) and len(text) > 1:
        return True
    return any(pattern.fullmatch(text) for pattern in _IDENTIFIER_RES)


def _looks_natural_language(text: str) -> bool:
    """Tells whether *text* reads as words.

    That is two or more words that are not all identifier-shaped or that come
    with sentence punctuation, or one ``Capitalized`` word of 3+ letters.
    """
    words: List[str] = _WORD_RE.findall(text)
    if len(words) >= 2:
        return bool(_SENTENCE_PUNCT_RE.search(text)) or not all(
            _CAMEL_CASE_RE.fullmatch(word) or _ALL_CAPS_IDENTIFIER_RE.fullmatch(word)
            for word in words
        )
    return (
        len(words) == 1 and len(words[0]) >= 3 and words[0][0].isupper() and words[0][1:].islower()
    )
