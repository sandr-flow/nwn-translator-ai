"""Deterministic filters for strings that should not seed world entities.

The entity-extraction pass is intentionally conservative: missing one inferred
proper noun is cheaper than letting technical labels pollute the run-wide
glossary and every later translation prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional


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

_TECHNICAL_PREFIXES = (
    "arch_",
    "nw_",
    "wp_",
    "dst_",
    "post_",
)

_GIT_TECHNICAL_TYPES: FrozenSet[str] = frozenset(
    {
        "trigger_name",
        "encounter_name",
        "item_name",
        "waypoint_name",
        "waypoint_map_note",
    }
)


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
        or lowered.startswith(_TECHNICAL_PREFIXES)
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
