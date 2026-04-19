"""Token-based relevance filter for context/glossary entries.

Used by :meth:`Glossary.to_prompt_block` and
:meth:`WorldContext.to_prompt_block` to keep only entries actually mentioned
in the source text of a translation batch.

Matching rules per token (entity vs. text):

* exact match on the normalized form;
* prefix match when both tokens share a common prefix of length >= 4;
* Damerau-Levenshtein <= 1 when both tokens are >= 6 characters long.

CJK is out of scope — see ``config.GAME_INCOMPATIBLE_TARGET_LANGS``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Set

# Unicode letters only (no digits, no underscore). Works for Latin, Cyrillic,
# Turkish, Polish, Czech and the like under Python 3's default re.UNICODE.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_PREFIX_MIN = 4
_FUZZY_MIN = 6


def tokenize(text: str) -> Set[str]:
    """Normalize *text* and return its set of letter-token strings."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return {m.group(0) for m in _TOKEN_RE.finditer(normalized)}


def is_relevant(entity_text: str, source_tokens: Set[str]) -> bool:
    """True if any token of *entity_text* matches one of *source_tokens*.

    *source_tokens* must already be the output of :func:`tokenize`.
    """
    if not source_tokens:
        return False
    entity_tokens = tokenize(entity_text)
    if not entity_tokens:
        return False
    for et in entity_tokens:
        for st in source_tokens:
            if _tokens_match(et, st):
                return True
    return False


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= _PREFIX_MIN and len(b) >= _PREFIX_MIN:
        if a.startswith(b) or b.startswith(a):
            return True
    if len(a) >= _FUZZY_MIN and len(b) >= _FUZZY_MIN:
        if _damerau_levenshtein_le_1(a, b):
            return True
    return False


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
