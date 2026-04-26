"""Token-based relevance filter for context/glossary entries.

Used by :meth:`Glossary.to_prompt_block` and
:meth:`WorldContext.to_prompt_block` to keep only entries actually mentioned
in the source text of a translation batch.

Matching is deliberately conservative:

* single-token names match exact source tokens, simple plural/possessive
  variants, or Damerau-Levenshtein <= 1 for long tokens;
* multi-token names need at least two meaningful token hits, except for a
  distinctive long surname/title token such as ``Winters`` vs. ``Winter's``;
* common prompt/routing/game tokens (``player``, ``reply``, ``ravenloft``,
  ``module``, etc.) never count as meaningful multi-token evidence.

CJK is out of scope — see ``config.GAME_INCOMPATIBLE_TARGET_LANGS``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Literal, Optional, Set

# Unicode letters only (no digits, no underscore). Works for Latin, Cyrillic,
# Turkish, Polish, Czech and the like under Python 3's default re.UNICODE.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_PREFIX_MIN = 4
_FUZZY_MIN = 6
_DISTINCTIVE_MIN = 6

_MAGNET_TOKENS = frozenset(
    {
        "action",
        "area",
        "chapter",
        "class",
        "conversation",
        "creature",
        "dialog",
        "dm",
        "encounter",
        "entry",
        "item",
        "level",
        "module",
        "narrator",
        "npc",
        "object",
        "player",
        "quest",
        "ravenloft",
        "reply",
        "script",
        "sound",
        "tag",
        "trigger",
        "vampire",
        "weapon",
    }
)

_MatchKind = Literal["none", "exact", "variant", "fuzzy"]


def tokenize(text: str) -> Set[str]:
    """Normalize *text* and return its set of letter-token strings."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return {m.group(0) for m in _TOKEN_RE.finditer(normalized)}


def is_relevant(entity_text: str, source_tokens: Set[str]) -> bool:
    """True if *entity_text* is strongly evidenced by *source_tokens*.

    *source_tokens* must already be the output of :func:`tokenize`.
    """
    if not source_tokens:
        return False
    entity_tokens = tokenize(entity_text)
    if not entity_tokens:
        return False

    if len(entity_tokens) == 1:
        token = next(iter(entity_tokens))
        return _single_token_relevant(token, source_tokens)

    if entity_tokens.issubset(source_tokens):
        return True

    meaningful_tokens = {t for t in entity_tokens if not _is_magnet_token(t)}
    if not meaningful_tokens:
        return False

    exact_hits: Set[str] = set()
    variant_hits: Set[str] = set()
    fuzzy_hits: Set[str] = set()

    for et in meaningful_tokens:
        for st in source_tokens:
            kind = _token_match_kind(et, st)
            if kind == "exact":
                exact_hits.add(et)
            elif kind == "variant":
                variant_hits.add(et)
            elif kind == "fuzzy":
                fuzzy_hits.add(et)

    strong_hits = exact_hits | variant_hits | fuzzy_hits
    if len(strong_hits) >= 2:
        return True

    if len(strong_hits) == 1:
        only = next(iter(strong_hits))
        # Keep useful surname/title matches (e.g. "Merrick Winters" for
        # "Mr. Winter's house") without letting one common token pull in
        # every entity that happens to share it.
        if only in exact_hits:
            return len(only) >= _PREFIX_MIN and not _is_magnet_token(only)
        return _is_distinctive_token(only) and only in variant_hits

    return False


def _tokens_match(a: str, b: str) -> bool:
    return _token_match_kind(a, b) != "none"


def _single_token_relevant(entity_token: str, source_tokens: Set[str]) -> bool:
    for source_token in source_tokens:
        kind = _token_match_kind(entity_token, source_token)
        if kind == "exact":
            return True
        if kind == "variant" and _is_distinctive_token(entity_token):
            return True
        if kind == "fuzzy" and _is_distinctive_token(entity_token):
            return True
    return False


def _token_match_kind(a: str, b: str) -> _MatchKind:
    if a == b:
        return "exact"
    if _simple_plural_or_possessive_variant(a, b):
        return "variant"
    if len(a) >= _FUZZY_MIN and len(b) >= _FUZZY_MIN:
        if _damerau_levenshtein_le_1(a, b):
            return "fuzzy"
    return "none"


def _simple_plural_or_possessive_variant(a: str, b: str) -> bool:
    if len(a) < _PREFIX_MIN or len(b) < _PREFIX_MIN:
        return False
    if a.endswith("s") and a[:-1] == b:
        return True
    if b.endswith("s") and b[:-1] == a:
        return True
    if a.endswith("es") and a[:-2] == b:
        return True
    if b.endswith("es") and b[:-2] == a:
        return True
    return False


def _is_distinctive_token(token: str) -> bool:
    return len(token) >= _DISTINCTIVE_MIN and not _is_magnet_token(token)


def _is_magnet_token(token: str) -> bool:
    return token in _MAGNET_TOKENS


def _damerau_levenshtein_le_1(a: str, b: str) -> bool:
    """True iff Damerau-Levenshtein distance between *a* and *b* is <= 1.

    Cheaper than computing the full distance: we only need a yes/no for
    distance in {0, 1}, so we walk the strings once and bail on the second
    discrepancy.
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        # Substitution or single transposition.
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2:
            i, j = diffs
            if j == i + 1 and a[i] == b[j] and a[j] == b[i]:
                return True
        return False
    # One insertion / deletion. Make `a` the longer one.
    if la < lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        i += 1  # skip one char in the longer string
    return True


def tokenize_corpus(texts: Iterable[str]) -> Set[str]:
    """Tokenize each text and return the union of all tokens."""
    out: Set[str] = set()
    for t in texts:
        if t:
            out.update(tokenize(t))
    return out


_HIERARCHY_SPLIT_RE = re.compile(r"\s+(?:[-|/>])\s+")
_COMPOUND_FREQUENCY_THRESHOLD = 3


def split_hierarchical(name: str) -> Optional[List[str]]:
    """Return component parts of a hierarchical name like ``A - B - C``.

    Returns ``None`` for names that are not hierarchical: single-component
    names, compound nouns joined without surrounding spaces (``street-side``,
    ``cul-de-sac``), or names whose components do not all start with an
    uppercase letter.
    """
    if not name:
        return None
    parts = [p.strip() for p in _HIERARCHY_SPLIT_RE.split(str(name))]
    if len(parts) < 2:
        return None
    if not all(p and p[0].isupper() for p in parts):
        return None
    return parts


def common_hierarchy_components(
    names: Iterable[str], threshold: int = _COMPOUND_FREQUENCY_THRESHOLD
) -> Set[str]:
    """Return casefolded components shared by many hierarchical names.

    A component appearing in *threshold* or more hierarchical names is
    classified as a common prefix/suffix and on its own is not enough to
    consider a hierarchical entry relevant to a source corpus.
    """
    counts: Dict[str, int] = {}
    for name in names:
        parts = split_hierarchical(name)
        if not parts:
            continue
        for part in parts:
            key = part.casefold()
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= threshold}


def hierarchical_entry_passes(
    name: str,
    source_joined: str,
    source_tokens: Set[str],
    common: Set[str],
) -> bool:
    """Return True if a hierarchical *name* is evidenced by the source corpus.

    The full name string winning by exact substring is always sufficient.
    Otherwise at least one non-common component must appear as a literal
    substring in the source corpus.  Names whose only matching component is
    a common prefix shared with many other entries (e.g. the city name) never
    pass; substring match on the whole component avoids the
    ``Loom Avenue`` ↔ ``Dock Ward gates`` false positive that token-level
    relevance would let through via the shared ``ward``/``gates`` magnet.
    """
    parts = split_hierarchical(name)
    if not parts:
        return True
    folded_name = (name or "").casefold()
    if folded_name and folded_name in source_joined:
        return True
    significant = [p for p in parts if p.casefold() not in common]
    if not significant:
        return False
    return any(p.casefold() in source_joined for p in significant)
