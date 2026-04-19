"""Tests for the relevance filter (token-based name matcher)."""

import pytest

from src.nwn_translator.context.relevance import (
    is_relevant,
    tokenize,
    tokenize_corpus,
    _damerau_levenshtein_le_1,
)


class TestTokenize:
    def test_empty(self):
        assert tokenize("") == set()

    def test_basic_latin(self):
        assert tokenize("Hello, world!") == {"hello", "world"}

    def test_casefold(self):
        assert tokenize("MERRICK") == tokenize("Merrick") == {"merrick"}

    def test_strip_punctuation_and_digits(self):
        assert tokenize("Inmate2 said: 'hi'") == {"inmate", "said", "hi"}

    def test_cyrillic(self):
        assert tokenize("Привет Мир") == {"привет", "мир"}

    def test_polish_diacritics_preserved(self):
        # casefold keeps the diacritic; tokens should match on the same form
        toks = tokenize("Łódź jest piękne")
        assert "łódź" in toks

    def test_turkish_capital_i(self):
        # Turkish dotted/dotless I: NFKC + casefold normalizes consistently
        toks = tokenize("İstanbul")
        assert any("stanbul" in t for t in toks)


class TestDamerauLevenshtein:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("merrick", "merrick", True),
            ("merrick", "merric", True),  # deletion
            ("merrick", "merrik", True),  # substitution at end
            ("merrick", "errick", True),  # leading deletion
            ("abcdef", "abdcef", True),  # transposition
            ("merrick", "marrack", False),  # 2 substitutions
            ("merrick", "mer", False),  # too short delta
            ("abc", "xyz", False),
        ],
    )
    def test_cases(self, a, b, expected):
        assert _damerau_levenshtein_le_1(a, b) is expected


class TestIsRelevant:
    def test_exact_match(self):
        toks = tokenize("Meet Perin at the tavern")
        assert is_relevant("Perin", toks)

    def test_no_match(self):
        toks = tokenize("Meet Perin at the tavern")
        assert not is_relevant("Drazek", toks)

    def test_prefix_match_both_min_4(self):
        # "Winters" (entity) vs "Winter's" → tokenizes to "winter" (6 chars)
        # entity token: "winters" (7). Both >=4, common prefix "winter" → match.
        toks = tokenize("Mr. Winter's house")
        assert is_relevant("Winters", toks)

    def test_prefix_too_short(self):
        # "Iri" (3) — below prefix-min 4
        toks = tokenize("ancient irises bloom")
        assert not is_relevant("Iri", toks)

    def test_levenshtein_match(self):
        toks = tokenize("I met Merric today")  # typo
        assert is_relevant("Merrick", toks)

    def test_no_fuzzy_on_short(self):
        # both 4 chars — prefix may fire but Levenshtein won't
        toks = tokenize("Iris was here")
        assert is_relevant("Iris", toks)
        # one diff in 4 chars: prefix fails (no common prefix>=4),
        # fuzzy fails (both must be >=6).  No match.
        assert not is_relevant("Aris", tokenize("Iris was here"))

    def test_multi_token_entity_any_match(self):
        toks = tokenize("Meet Winters tomorrow")
        assert is_relevant("Merrick Winters", toks)

    def test_empty_corpus_no_match(self):
        assert not is_relevant("Anything", set())

    def test_empty_entity_no_match(self):
        assert not is_relevant("", tokenize("anything"))


class TestTokenizeCorpus:
    def test_unions_tokens(self):
        toks = tokenize_corpus(["Hello world", "Привет"])
        assert toks == {"hello", "world", "привет"}

    def test_skips_empty(self):
        toks = tokenize_corpus(["", None, "x"])
        assert toks == {"x"}
