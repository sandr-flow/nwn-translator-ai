"""Tests for json_utils.json_extract_first_object."""

from __future__ import annotations

from nwn_translator.json_utils import json_extract_first_object, strip_json_markdown_fences


def test_strip_fences() -> None:
    assert strip_json_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_markdown_fences("```\n{x:1}") == "{x:1}"


def test_extra_data_trailing_text() -> None:
    raw = '{"E0": "hello", "R1": "world"}\n\nSome trailing notes'
    out = json_extract_first_object(raw)
    assert out == {"E0": "hello", "R1": "world"}


def test_two_objects_first_wins() -> None:
    raw = '{"a": 1}{"b": 2}'
    out = json_extract_first_object(raw)
    assert out == {"a": 1}


def test_brace_inside_string_value() -> None:
    raw = r'{"E0": "Use } carefully", "R1": "ok"}'
    out = json_extract_first_object(raw)
    assert out == {"E0": "Use } carefully", "R1": "ok"}


def test_invalid_returns_none() -> None:
    assert json_extract_first_object("") is None
    assert json_extract_first_object("no brace") is None
    assert json_extract_first_object('{"unclosed": "') is None


def test_array_root_returns_none() -> None:
    assert json_extract_first_object("[1, 2]") is None
