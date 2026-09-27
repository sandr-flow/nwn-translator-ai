"""Tests for the JSON reply helpers in json_utils."""

from __future__ import annotations

import json

import pytest

from nwn_translator.json_utils import (
    json_extract_first_object,
    load_brace_span,
    load_first_json_object,
    scan_first_json_object,
    strip_json_markdown_fences,
)


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


def test_raw_newlines_inside_string_values() -> None:
    raw = '{"E0": "hello\nworld", "R1": "ok"}'
    out = json_extract_first_object(raw)
    assert out == {"E0": "hello\nworld", "R1": "ok"}


def test_array_root_returns_none() -> None:
    assert json_extract_first_object("[1, 2]") is None


def test_case_sensitive_fence_keeps_upper_case_tag() -> None:
    assert strip_json_markdown_fences("```JSON\n{}\n```") == "{}"
    assert strip_json_markdown_fences("```JSON\n{}\n```", case_sensitive=True) == "JSON\n{}"


def test_load_first_json_object_ignores_surrounding_text() -> None:
    assert load_first_json_object('```json\n{"a": "x\ny"} trailing') == {"a": "x\ny"}
    assert load_first_json_object('Sure! {"a": 1}{"b": 2}') == {"a": 1}


@pytest.mark.parametrize(
    "raw,message",
    [
        ("no brace", "No JSON object found: line 1 column 1 (char 0)"),
        ('```JSON\n{"a": ', "Expecting value: line 2 column 6 (char 10)"),
        ('```json\n{"a": ', "Expecting value: line 1 column 6 (char 5)"),
    ],
)
def test_load_first_json_object_error_positions_refer_to_stripped_text(raw, message) -> None:
    with pytest.raises(json.JSONDecodeError) as exc_info:
        load_first_json_object(raw)
    assert str(exc_info.value) == message


def test_load_brace_span_is_greedy_and_strict() -> None:
    assert load_brace_span('Sure: {"a": {"b": 1}} done') == {"a": {"b": 1}}
    assert load_brace_span("[1, 2]") == [1, 2]
    for raw in ('{"a": 1} and {"b": 2}', '{"a": "x\ny"}', "no json"):
        with pytest.raises(json.JSONDecodeError):
            load_brace_span(raw)


def test_scan_first_json_object_skips_broken_fragments() -> None:
    raw = 'Example: {broken} then {"a": "x\ny"} and {"b": 2}'
    assert scan_first_json_object(raw) == {"a": "x\ny"}
    assert scan_first_json_object("[1, 2]") is None
    with pytest.raises(json.JSONDecodeError):
        scan_first_json_object('{"unclosed": ')
    # None needs a text without "{": a failed "{" is raised even when the whole text
    # decodes to a non-object.
    with pytest.raises(json.JSONDecodeError):
        scan_first_json_object('"{x"')
