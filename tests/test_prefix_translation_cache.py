"""Parity tests for trie-backed prefix lookup vs naive scan."""

from __future__ import annotations

import random
import string

import pytest

from nwn_translator.translators.prefix_translation_cache import PrefixAwareTranslationCache
from nwn_translator.translators.translation_manager import _MIN_PREFIX_LEN


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _naive_longest_prefix(cache: PrefixAwareTranslationCache, sanitized: str, min_len: int):
    best_key = None
    best_len = 0
    for key in cache:
        klen = len(key)
        if klen <= best_len or klen < min_len or not sanitized.startswith(key):
            continue
        # Mirror the word-boundary rule: a prefix may not split a word.
        if (
            klen < len(sanitized)
            and _is_word_char(sanitized[klen - 1])
            and _is_word_char(sanitized[klen])
        ):
            continue
        best_key = key
        best_len = klen
    if best_key is None:
        return None
    return best_key, cache[best_key]


def test_longest_prefix_matches_naive_random() -> None:
    rng = random.Random(42)
    cache = PrefixAwareTranslationCache()
    alphabet = string.ascii_letters + string.digits + " "

    for _ in range(80):
        n = rng.randint(_MIN_PREFIX_LEN, _MIN_PREFIX_LEN + 40)
        key = "".join(rng.choice(alphabet) for _ in range(n))
        cache[key] = f"tr_{key[:8]}"

    for _ in range(200):
        base_key = rng.choice(list(cache.keys()))
        extra = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        query = base_key + extra
        assert cache.longest_prefix_match(query, _MIN_PREFIX_LEN) == _naive_longest_prefix(
            cache, query, _MIN_PREFIX_LEN
        )


def test_prefix_update_overwrites_terminal() -> None:
    c = PrefixAwareTranslationCache()
    c["x" * _MIN_PREFIX_LEN] = "old"
    k = "x" * _MIN_PREFIX_LEN
    c[k] = "new"
    assert c[k] == "new"
    # A space after the key is a word boundary, so the prefix match is valid.
    assert c.longest_prefix_match(k + " tail", _MIN_PREFIX_LEN) == (k, "new")


def test_prefix_match_requires_word_boundary() -> None:
    c = PrefixAwareTranslationCache()
    key = "Welcome to the town of Brel"  # >= _MIN_PREFIX_LEN
    c[key] = "tr"
    # Mid-word: "Brel" inside "Brelnum" must not match.
    assert (
        c.longest_prefix_match("Welcome to the town of Brelnum the great", _MIN_PREFIX_LEN) is None
    )
    # Boundary (punctuation) right after the key is a valid match.
    assert c.longest_prefix_match("Welcome to the town of Brel, friend", _MIN_PREFIX_LEN) == (
        key,
        "tr",
    )
    # Boundary (space) is also valid.
    assert c.longest_prefix_match("Welcome to the town of Brel and beyond", _MIN_PREFIX_LEN) == (
        key,
        "tr",
    )


def test_prefix_match_falls_back_to_shorter_boundary_key() -> None:
    c = PrefixAwareTranslationCache()
    short = "The dragon flew over"  # ends at a word boundary in the query
    longer = "The dragon flew over the"  # would split "there" mid-word
    c[short] = "a"
    c[longer] = "b"
    # Longer key splits "there"; the shorter boundary-valid key wins.
    assert c.longest_prefix_match("The dragon flew over there", _MIN_PREFIX_LEN) == (short, "a")


def test_set_exact_excluded_from_prefix_matches() -> None:
    c = PrefixAwareTranslationCache()
    term = "Crimson Brotherhood of Bane"  # >= _MIN_PREFIX_LEN, glossary-style seed
    c.set_exact(term, "tr")
    # Exact lookup still works.
    assert c[term] == "tr"
    assert term in c
    # But it never seeds a prefix match.
    assert c.longest_prefix_match(term + ", the cult", _MIN_PREFIX_LEN) is None
