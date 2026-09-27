"""Token-based relevance filter for world-context entries.

:meth:`~nwn_translator.context.world_context.WorldContext.to_prompt_block` uses it
to keep only the entities a dialog batch mentions. Matching is conservative:

* a single-token name matches an exact source token; a distinctive (long,
  non-magnet) token also matches a plural/possessive variant or a token within
  Damerau-Levenshtein distance 1;
* a multi-token name matches when all its tokens occur, with two strong hits
  (exact, variant or fuzzy) of meaningful tokens, with one exact hit of a
  non-magnet token of at least 4 letters (``Smith``), or with a plural/possessive
  variant of one distinctive token (``Winters`` vs. ``Winter's``);
* common prompt, routing and game words (magnets: ``player``, ``reply``,
  ``ravenloft`` …) never count as a strong hit of a multi-token name and are
  never distinctive for variant or fuzzy matching; an exact match of the whole
  name still counts.

CJK is out of scope (see ``config.GAME_INCOMPATIBLE_TARGET_LANGS``).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

#: Runs of Unicode letters: digits and ``_`` split tokens.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_PREFIX_MIN = 4
_FUZZY_MIN = 6
_DISTINCTIVE_MIN = 6
_MAGNET_TOKENS = frozenset(
    "action area chapter class conversation creature dialog dm encounter entry item level module "
    "narrator npc object player quest ravenloft reply script sound tag trigger vampire weapon".split()
)
#: Separator of hierarchical ``A - B - C`` names.
_HIERARCHY_SPLIT_RE = re.compile(r"\s+(?:[-|/>])\s+")
#: A component shared by this many hierarchical names is common.
_COMMON_COMPONENT_MIN = 3


def tokenize(text: str) -> Set[str]:
    """Returns the letter tokens of *text*.

    Args:
        text: Any text.

    Returns:
        The runs of letters of the NFKC-normalized, casefolded text.
    """
    if not text:
        return set()
    return set(_TOKEN_RE.findall(unicodedata.normalize("NFKC", str(text)).casefold()))


def tokenize_corpus(texts: Iterable[str]) -> Set[str]:
    """Returns the tokens of a source corpus.

    Args:
        texts: Source texts; empty items are skipped.

    Returns:
        The union of the :func:`tokenize` tokens of *texts*.
    """
    return set().union(*(tokenize(text) for text in texts if text))


@lru_cache(maxsize=16384)
def _entity_tokens(text: str) -> FrozenSet[str]:
    """Returns the tokens of an entity name, cached across filter calls."""
    return frozenset(tokenize(text))


class SourceTokenIndex:
    """Source tokens with the fuzzy-eligible ones bucketed by length.

    Attributes:
        tokens: The source tokens.
        by_len: Tokens of at least 6 letters by length, so a distance-1 probe
            scans only the lengths within one.
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


def is_relevant(entity_text: str, index: SourceTokenIndex) -> bool:
    """Tells whether *entity_text* is strongly evidenced by the source tokens.

    Args:
        entity_text: Entity name (and tag) to look for.
        index: The source tokens; build it once per corpus.

    Returns:
        ``True`` when the entity passes the matching rules of this module.
    """
    tokens = index.tokens
    entity_tokens = _entity_tokens(entity_text) if entity_text else frozenset()
    if not tokens or not entity_tokens:
        return False
    if entity_tokens <= tokens:
        return True
    if len(entity_tokens) == 1:
        (token,) = entity_tokens
        return _is_distinctive_token(token) and (
            _variant_in_tokens(token, tokens) or _fuzzy_in_index(token, index)
        )
    strong = [
        token
        for token in entity_tokens
        if token not in _MAGNET_TOKENS
        and (token in tokens or _variant_in_tokens(token, tokens) or _fuzzy_in_index(token, index))
    ]
    if len(strong) != 1:
        return len(strong) >= 2
    # One surname or title hit counts ("Merrick Winters" for "Mr. Winter's house"),
    # but one common token must not pull in every entity that shares it.
    (only,) = strong
    if only in tokens:
        return len(only) >= _PREFIX_MIN
    return _is_distinctive_token(only) and _variant_in_tokens(only, tokens)


def _is_distinctive_token(token: str) -> bool:
    """Tells whether *token* is long and not a magnet, so a near match counts as evidence."""
    return len(token) >= _DISTINCTIVE_MIN and token not in _MAGNET_TOKENS


def _variant_in_tokens(token: str, tokens: Set[str]) -> bool:
    """Tells whether a plural/possessive variant (+s, +es, -s, -es) of *token* is a source token."""
    if len(token) < _PREFIX_MIN:
        return False
    return (
        token + "s" in tokens
        or token + "es" in tokens
        or (len(token) > _PREFIX_MIN and token.endswith("s") and token[:-1] in tokens)
        or (len(token) > _PREFIX_MIN + 1 and token.endswith("es") and token[:-2] in tokens)
    )


def _fuzzy_in_index(token: str, index: SourceTokenIndex) -> bool:
    """Tells whether a source token is within Damerau-Levenshtein distance 1 of a long *token*."""
    return len(token) >= _FUZZY_MIN and any(
        _damerau_levenshtein_le_1(token, candidate)
        for length in (len(token) - 1, len(token), len(token) + 1)
        for candidate in index.by_len.get(length, ())
    )


def _damerau_levenshtein_le_1(a: str, b: str) -> bool:
    """Tells whether *a* and *b* differ by at most one edit or one adjacent transposition."""
    if len(a) < len(b):
        a, b = b, a
    if len(a) - len(b) > 1:
        return False
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), len(b))
    if len(a) > len(b):
        return a[i + 1 :] == b[i:]
    swapped = a[i : i + 2] == b[i : i + 2][::-1] and a[i + 2 :] == b[i + 2 :]
    return a[i + 1 :] == b[i + 1 :] or swapped


@lru_cache(maxsize=16384)
def _split_hierarchical(name: str) -> Optional[Tuple[str, ...]]:
    """Splits ``A - B - C`` into its parts; ``None`` unless there are several, all capitalized."""
    parts = tuple(p.strip() for p in _HIERARCHY_SPLIT_RE.split(name))
    if len(parts) < 2 or not all(p and p[0].isupper() for p in parts):
        return None
    return parts


def common_hierarchy_components(names: Iterable[str]) -> Set[str]:
    """Returns the casefolded components shared by at least three hierarchical names.

    Such a component (a city name, say) is a common prefix or suffix and on its
    own does not make a hierarchical entry relevant.

    Args:
        names: Candidate names; ``A - B - C`` style ones count.

    Returns:
        The common components.
    """
    counts = Counter(
        part.casefold() for name in names if name for part in _split_hierarchical(str(name)) or ()
    )
    return {key for key, count in counts.items() if count >= _COMMON_COMPONENT_MIN}


def hierarchical_entry_passes(name: str, source_joined: str, common: Set[str]) -> bool:
    """Tells whether a hierarchical *name* is evidenced by the source corpus.

    The whole name as a substring always is; otherwise a component that is not
    common must occur as a literal substring. Token relevance alone would let
    ``Loom Avenue`` in through the ``ward``/``gates`` of ``Dock Ward gates``.

    Args:
        name: Entity name; non-hierarchical names always pass.
        source_joined: Casefolded source corpus.
        common: Output of :func:`common_hierarchy_components`.

    Returns:
        ``True`` when the name is evidenced.
    """
    parts = _split_hierarchical(str(name)) if name else None
    if not parts or name.casefold() in source_joined:
        return True
    return any(p.casefold() in source_joined for p in parts if p.casefold() not in common)
