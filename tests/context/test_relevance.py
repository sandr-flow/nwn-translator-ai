"""The token-based name matcher that selects relevant context for a batch."""

import pytest

from nwn_translator.context.relevance import (
    _damerau_levenshtein_le_1,
    is_relevant,
    tokenize,
    tokenize_corpus,
)


@pytest.mark.parametrize(
    "text, tokens",
    [
        ("", set()),
        ("Hello, world!", {"hello", "world"}),
        ("MERRICK", {"merrick"}),
        ("Merrick", {"merrick"}),
        ("Inmate2 said: 'hi'", {"inmate", "said", "hi"}),
        ("Привет Мир", {"привет", "мир"}),
    ],
)
def test_tokenize(text, tokens):
    assert tokenize(text) == tokens


def test_tokenize_keeps_diacritics_and_normalizes_turkish_capital_i():
    assert "łódź" in tokenize("Łódź jest piękne")
    assert any("stanbul" in token for token in tokenize("İstanbul"))


def test_tokenize_corpus_unions_non_empty_texts():
    assert tokenize_corpus(["Hello world", "Привет"]) == {"hello", "world", "привет"}
    assert tokenize_corpus(["", None, "x"]) == {"x"}


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("merrick", "merrick", True),
        ("merrick", "merric", True),  # deletion
        ("merrick", "merrik", True),  # substitution at the end
        ("merrick", "errick", True),  # leading deletion
        ("abcdef", "abdcef", True),  # transposition
        ("merrick", "marrack", False),  # two substitutions
        ("merrick", "mer", False),
        ("abc", "xyz", False),
    ],
)
def test_damerau_levenshtein_distance_at_most_one(a, b, expected):
    assert _damerau_levenshtein_le_1(a, b) is expected


@pytest.mark.parametrize(
    "entity, text, relevant",
    [
        ("Perin", "Meet Perin at the tavern", True),
        ("Drazek", "Meet Perin at the tavern", False),
        # "Winter's" tokenizes to "winter": a common prefix of at least 4 matches "Winters".
        ("Winters", "Mr. Winter's house", True),
        # Arbitrary prefixes below the minimum do not match.
        ("Iri", "ancient irises bloom", False),
        ("Iris", "The Irish warrior approached.", False),
        ("Merrick", "I met Merric today", True),  # a typo
        # Short tokens get no fuzzy match: "Aris" vs "Iris".
        ("Iris", "Iris was here", True),
        ("Aris", "Iris was here", False),
        # Multi-token names need one distinctive token, not a shared magnet token.
        ("Merrick Winters", "Meet Winters tomorrow", True),
        ("Ravenloft Vampire", "Welcome to Ravenloft.", False),
        ("Ravenloft Shadow", "Welcome to Ravenloft.", False),
        ("Ravenloft Vampire", "The Ravenloft Vampire attacks.", True),
        ("", "anything", False),
    ],
)
def test_is_relevant(entity, text, relevant):
    assert is_relevant(entity, tokenize(text)) is relevant


def test_nothing_is_relevant_to_an_empty_corpus():
    assert not is_relevant("Anything", set())
