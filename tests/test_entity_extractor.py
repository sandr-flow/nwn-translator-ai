"""Tests for EntityExtractor (LLM-based proper noun extraction)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from src.nwn_translator.context.entity_extractor import (
    EntityExtractor,
    _batch_texts,
    _coerce_category,
    _format_user_prompt,
    _parse_entities_json,
    _select_texts,
)
from src.nwn_translator.extractors.base import TranslatableItem


def _item(text: str) -> TranslatableItem:
    return TranslatableItem(text=text)


class _FakeProvider:
    """Stub provider: records calls and returns scripted JSON payloads."""

    def __init__(self, payloads: List[str]):
        self._payloads = list(payloads)
        self.calls: List[tuple] = []

    async def complete_json_chat_async(
        self, system_prompt, user_prompt, *, max_tokens=0, temperature=0.0
    ):
        self.calls.append((system_prompt, user_prompt))
        if not self._payloads:
            return '{"entities": []}'
        return self._payloads.pop(0)

    async def close_async_client(self):  # pragma: no cover - trivial
        return None


def _config(source_lang: str = "English"):
    return SimpleNamespace(
        source_lang=source_lang,
        max_concurrent_requests=3,
    )


class TestTextSelection:
    def test_short_texts_filtered(self):
        items = [_item("Hi"), _item("x" * 60)]
        out = _select_texts(items)
        assert out == ["x" * 60]

    def test_duplicates_deduped(self):
        long = "This is a long enough sentence that passes the threshold."
        items = [_item(long), _item(long)]
        assert _select_texts(items) == [long]

    def test_empty_and_whitespace_skipped(self):
        items = [_item(""), _item("   "), _item("a" * 45)]
        assert _select_texts(items) == ["a" * 45]


class TestBatching:
    def test_exact_multiple(self):
        texts = [f"t{i}" for i in range(50)]
        batches = _batch_texts(texts, 25)
        assert len(batches) == 2
        assert len(batches[0]) == 25 and len(batches[1]) == 25

    def test_remainder(self):
        texts = [f"t{i}" for i in range(30)]
        batches = _batch_texts(texts, 25)
        assert [len(b) for b in batches] == [25, 5]

    def test_empty(self):
        assert _batch_texts([], 25) == []


class TestCategoryCoercion:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("character", "character"),
            ("LOCATION", "location"),
            ("Organization", "organization"),
            ("item", "item"),
            ("unknown", "unknown"),
            ("nonsense", "unknown"),
            (None, "unknown"),
            (42, "unknown"),
        ],
    )
    def test_coerce(self, value, expected):
        assert _coerce_category(value) == expected


class TestJsonParsing:
    def test_parses_standard_payload(self):
        raw = (
            '{"entities": ['
            '{"name": "Stout Village", "type": "location"},'
            '{"name": "Marvin", "type": "character"}'
            ']}'
        )
        out = _parse_entities_json(raw)
        assert out == [
            ("Stout Village", "location"),
            ("Marvin", "character"),
        ]

    def test_tolerates_leading_text(self):
        raw = 'here you go: {"entities": [{"name": "A", "type": "item"}]} done'
        assert _parse_entities_json(raw) == [("A", "item")]

    def test_invalid_type_becomes_unknown(self):
        raw = '{"entities": [{"name": "X", "type": "weapon"}]}'
        assert _parse_entities_json(raw) == [("X", "unknown")]

    def test_missing_name_skipped(self):
        raw = '{"entities": [{"type": "character"}, {"name": "Ok"}]}'
        assert _parse_entities_json(raw) == [("Ok", "unknown")]

    def test_garbage_returns_empty(self):
        assert _parse_entities_json("not json at all") == []
        assert _parse_entities_json("") == []

    def test_wrapper_key_fallback(self):
        raw = '{"results": [{"name": "Zed", "type": "character"}]}'
        assert _parse_entities_json(raw) == [("Zed", "character")]


class TestUserPrompt:
    def test_format(self):
        out = _format_user_prompt(["Hello world.", "Goodbye."])
        assert "[0]" in out and "[1]" in out
        assert "Hello world." in out
        assert "Goodbye." in out

    def test_newlines_flattened_and_quotes_escaped(self):
        out = _format_user_prompt(['line1\nline2 "quoted"'])
        assert "\n" in out  # between list items
        assert '"quoted"' not in out
        assert "'quoted'" in out


class TestExtractIntegration:
    """End-to-end extraction with fake provider."""

    def test_new_names_returned_known_filtered(self):
        long = (
            "Leading a coach to Stout Village with farming equipment to deliver."
        )
        items = [_item(long)]
        payload = (
            '{"entities": ['
            '{"name": "Stout Village", "type": "location"},'
            '{"name": "Glod Gloddson", "type": "character"}'
            ']}'
        )
        provider = _FakeProvider([payload])
        result = EntityExtractor().extract(
            items, provider, _config(), known_names={"Glod Gloddson"},
        )
        assert result == [("Stout Village", "location")]

    def test_case_insensitive_dedup_against_known(self):
        long = "Some long descriptive passage mentioning a named place in-world."
        items = [_item(long)]
        payload = '{"entities": [{"name": "stout village", "type": "location"}]}'
        provider = _FakeProvider([payload])
        result = EntityExtractor().extract(
            items, provider, _config(), known_names={"Stout Village"},
        )
        assert result == []

    def test_no_texts_returns_empty(self):
        items = [_item("hi"), _item("short")]
        provider = _FakeProvider([])
        result = EntityExtractor().extract(items, provider, _config(), known_names=set())
        assert result == []
        assert provider.calls == []

    def test_llm_failure_graceful(self):
        long = "This is a long sentence meant to trigger an LLM call in extraction."
        items = [_item(long)]

        class _FailingProvider(_FakeProvider):
            async def complete_json_chat_async(self, *a, **kw):
                raise RuntimeError("boom")

        provider = _FailingProvider([])
        result = EntityExtractor().extract(
            items, provider, _config(), known_names=set(),
        )
        assert result == []

    def test_auto_source_lang_becomes_english(self):
        long = "This is a long sentence meant to trigger an LLM call in extraction."
        items = [_item(long)]
        payload = '{"entities": [{"name": "Foo", "type": "location"}]}'
        provider = _FakeProvider([payload])
        EntityExtractor().extract(
            items, provider, _config(source_lang="auto"), known_names=set(),
        )
        system_prompt, _ = provider.calls[0]
        assert "English" in system_prompt

    def test_dedup_across_batches(self):
        texts = [
            f"Sentence number {i} that is well over forty chars long here." * 1
            for i in range(30)
        ]
        items = [_item(t) for t in texts]
        payload_a = '{"entities": [{"name": "Dup", "type": "location"}]}'
        payload_b = '{"entities": [{"name": "dup", "type": "character"}]}'
        provider = _FakeProvider([payload_a, payload_b])
        result = EntityExtractor().extract(
            items, provider, _config(), known_names=set(),
        )
        # Two batches (25 + 5), both return a name collapsing to same key.
        assert len(result) == 1
        assert result[0][0] == "Dup"
