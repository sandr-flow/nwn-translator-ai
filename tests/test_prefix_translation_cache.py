"""Parity tests for trie-backed prefix lookup vs naive scan."""

from __future__ import annotations

import random
import string

import pytest

from nwn_translator.translators.prefix_translation_cache import PrefixAwareTranslationCache
from nwn_translator.translators.translation_manager import _MIN_PREFIX_LEN


def _naive_longest_prefix(
    cache: PrefixAwareTranslationCache, sanitized: str, min_len: int
):
    best_key = None
    best_len = 0
    for key in cache:
        klen = len(key)
        if klen > best_len and klen >= min_len and sanitized.startswith(key):
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
    assert c.longest_prefix_match(k + "tail", _MIN_PREFIX_LEN) == (k, "new")
