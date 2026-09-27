"""Token-based relevance filter for world-context entries.

:meth:`~nwn_translator.context.world_context.WorldContext.to_prompt_block` uses
it to keep only the entities a dialog batch actually mentions.

Matching is deliberately conservative:

* single-token names match exact source tokens; distinctive (long, non-magnet)
  tokens also match simple plural/possessive variants or Damerau-Levenshtein <= 1;
* multi-token names match when all their tokens occur, with two strong hits
  (exact, variant or fuzzy) of meaningful tokens, with one exact hit of a
  non-magnet token of at least 4 letters (``Smith``), or with a
  plural/possessive variant of one distinctive token (``Winters`` vs.
  ``Winter's``);
* common prompt/routing/game tokens (``player``, ``reply``, ``ravenloft``,
  ``module``, etc.) never count as meaningful multi-token evidence.

CJK is out of scope — see ``config.GAME_INCOMPATIBLE_TARGET_LANGS``.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple, Union

# Unicode letters only (no digits, no underscore). Works for Latin, Cyrillic,
# Turkish, Polish, Czech and the like under Python 3's default re.UNICODE.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_PREFIX_MIN = 4
_FUZZY_MIN = 6
_DISTINCTIVE_MIN = 6

#: Common prompt, routing and game words that never count as evidence on their own.
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


def tokenize(text: str) -> Set[str]:
    """Returns the letter tokens of *text*.

    Args:
        text: Any text.

    Returns:
        The runs of letters of the NFKC-normalized, casefolded text; digits
        and ``_`` split tokens.
    """
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return {m.group(0) for m in _TOKEN_RE.finditer(normalized)}


@lru_cache(maxsize=16384)
def _entity_tokens_cached(text: str) -> FrozenSet[str]:
    """Returns the :func:`tokenize` tokens of an entity name, cached across filter calls."""
    return frozenset(tokenize(text))


class SourceTokenIndex:
    """Lookup structures for one source-token set (fast :func:`is_relevant` matching).

    Attributes:
        tokens: The source tokens.
        by_len: Fuzzy-eligible tokens (length >= 6) bucketed by length, so a
            Damerau-Levenshtein <= 1 probe only scans lengths within +/- 1.
    """

    __slots__ = ("tokens", "by_len")

    def __init__(self, tokens: Set[str]):
        """Buckets the fuzzy-eligible tokens by length.

        Args:
            tokens: Source tokens (see :func:`tokenize_corpus`).
        """
        self.tokens = tokens
        self.by_len: Dict[int, List[str]] = {}
        for token in tokens:
            if len(token) >= _FUZZY_MIN:
                self.by_len.setdefault(len(token), []).append(token)


def _variant_in_tokens(token: str, tokens: Set[str]) -> bool:
    """Tells whether a plural/possessive variant (+s, +es, -s, -es) of *token* is a source token."""
    if len(token) < _PREFIX_MIN:
        return False
    if token + "s" in tokens or token + "es" in tokens:
        return True
    if token.endswith("s") and len(token) >= _PREFIX_MIN + 1 and token[:-1] in tokens:
        return True
    if token.endswith("es") and len(token) >= _PREFIX_MIN + 2 and token[:-2] in tokens:
        return True
    return False


def _fuzzy_in_index(token: str, index: SourceTokenIndex) -> bool:
    """Tells whether a source token is within Damerau-Levenshtein distance 1 of a long *token*."""
    if len(token) < _FUZZY_MIN:
        return False
    for length in (len(token) - 1, len(token), len(token) + 1):
        for candidate in index.by_len.get(length, ()):
            if _damerau_levenshtein_le_1(token, candidate):
                return True
    return False


def is_relevant(entity_text: str, source_tokens: Union[Set[str], SourceTokenIndex]) -> bool:
    """Tells whether *entity_text* is strongly evidenced by *source_tokens*.

    Args:
        entity_text: Entity name (and tag) to look for.
        source_tokens: Output of :func:`tokenize_corpus`, or a prebuilt
            :class:`SourceTokenIndex`; callers filtering many entities against
            the same corpus should build the index once.

    Returns:
        ``True`` when the entity passes the conservative matching rules of this module.
    """
    if isinstance(source_tokens, SourceTokenIndex):
        index = source_tokens
    else:
        index = SourceTokenIndex(source_tokens)
    tokens = index.tokens
    if not tokens:
        return False
    entity_tokens = _entity_tokens_cached(entity_text) if entity_text else frozenset()
    if not entity_tokens:
        return False

    if len(entity_tokens) == 1:
        token = next(iter(entity_tokens))
        if token in tokens:
            return True
        if not _is_distinctive_token(token):
            return False
        return _variant_in_tokens(token, tokens) or _fuzzy_in_index(token, index)

    if entity_tokens.issubset(tokens):
        return True

    meaningful_tokens = {t for t in entity_tokens if not _is_magnet_token(t)}
    if not meaningful_tokens:
        return False

    exact_hits: Set[str] = set()
    variant_hits: Set[str] = set()
    strong_hits: Set[str] = set()

    for et in meaningful_tokens:
        exact = et in tokens
        variant = _variant_in_tokens(et, tokens)
        if exact:
            exact_hits.add(et)
        if variant:
            variant_hits.add(et)
        if exact or variant or _fuzzy_in_index(et, index):
            strong_hits.add(et)

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


def _is_distinctive_token(token: str) -> bool:
    """Tells whether *token* is long and not a magnet, so a near match counts as evidence."""
    return len(token) >= _DISTINCTIVE_MIN and not _is_magnet_token(token)


def _is_magnet_token(token: str) -> bool:
    """Tells whether *token* is a common word that never counts as evidence on its own."""
    return token in _MAGNET_TOKENS


def _damerau_levenshtein_le_1(a: str, b: str) -> bool:
    """Tells whether the Damerau-Levenshtein distance between *a* and *b* is at most 1.

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
    # One insertion / deletion. Make ``a`` the longer one.
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
    """Returns the tokens of a source corpus.

    Args:
        texts: Source texts; empty items are skipped.

    Returns:
        The union of the :func:`tokenize` tokens of *texts*.
    """
    out: Set[str] = set()
    for t in texts:
        if t:
            out.update(tokenize(t))
    return out


_HIERARCHY_SPLIT_RE = re.compile(r"\s+(?:[-|/>])\s+")
_COMPOUND_FREQUENCY_THRESHOLD = 3


@lru_cache(maxsize=16384)
def _split_hierarchical_cached(name: str) -> Optional[Tuple[str, ...]]:
    """Splits ``A - B - C`` into its parts; ``None`` unless every part starts upper-case."""
    parts = tuple(p.strip() for p in _HIERARCHY_SPLIT_RE.split(name))
    if len(parts) < 2:
        return None
    if not all(p and p[0].isupper() for p in parts):
        return None
    return parts


def common_hierarchy_components(
    names: Iterable[str], threshold: int = _COMPOUND_FREQUENCY_THRESHOLD
) -> Set[str]:
    """Returns casefolded components shared by many hierarchical names.

    A component appearing in *threshold* or more hierarchical names is
    classified as a common prefix/suffix and on its own is not enough to
    consider a hierarchical entry relevant to a source corpus.

    Args:
        names: Candidate names (``A - B - C`` style ones count).
        threshold: Minimum number of names sharing a component.

    Returns:
        The common components.
    """
    counts: Dict[str, int] = {}
    for name in names:
        parts = _split_hierarchical_cached(str(name)) if name else None
        if not parts:
            continue
        for part in parts:
            key = part.casefold()
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= threshold}


def hierarchical_entry_passes(
    name: str,
    source_joined: str,
    common: Set[str],
) -> bool:
    """Tells whether a hierarchical *name* is evidenced by the source corpus.

    The full name occurring as a substring is always sufficient. Otherwise at
    least one non-common component must appear as a literal substring in the
    source corpus. Names whose only matching component is
    a common prefix shared with many other entries (e.g. the city name) never
    pass; substring match on the whole component avoids the
    ``Loom Avenue`` ↔ ``Dock Ward gates`` false positive that token-level
    relevance would let through via the shared ``ward``/``gates`` magnet.

    Args:
        name: Entity name; non-hierarchical names always pass.
        source_joined: Casefolded source corpus.
        common: Output of :func:`common_hierarchy_components`.

    Returns:
        ``True`` when the name is evidenced.
    """
    parts = _split_hierarchical_cached(str(name)) if name else None
    if not parts:
        return True
    folded_name = (name or "").casefold()
    if folded_name and folded_name in source_joined:
        return True
    significant = [p for p in parts if p.casefold() not in common]
    if not significant:
        return False
    return any(p.casefold() in source_joined for p in significant)
