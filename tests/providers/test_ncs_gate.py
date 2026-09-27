"""The NCS gate request: payload, verdict parsing and recovery from bad replies."""

import json

import pytest

from nwn_translator.ai_providers.ncs_gate import (
    classify_with_recovery,
    gate_user_prompt,
    parse_gate_verdicts,
)
from nwn_translator.async_utils import run_async


def _entries(n):
    return [{"key": str(i), "text": f"Line {i}", "file": "a.ncs", "offset": i} for i in range(n)]


class _Gate:
    """Scripted gate endpoint: answers per batch size; records every request."""

    def __init__(self, parseable_up_to):
        self.parseable_up_to = parseable_up_to
        self.requests = []

    async def __call__(self, user_prompt, max_tokens, batch_size):
        payload = json.loads(user_prompt.split("\n\n", 1)[1])
        self.requests.append((sorted(payload), max_tokens, batch_size))
        if batch_size > self.parseable_up_to:
            return '{"0": {"translate": tru'
        return json.dumps(
            {key: {"translate": True, "reason": cell["text"]} for key, cell in payload.items()}
        )


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}])
def test_only_a_json_boolean_true_approves(value):
    result = parse_gate_verdicts(json.dumps({"0": {"translate": value}}), [{"key": "0"}])
    assert result["0"]["translate"] is False


@pytest.mark.parametrize("raw", ["[]", 'prefix {"0": {"translate": true}}', "{} {}"])
def test_non_object_or_extra_output_is_rejected(raw):
    with pytest.raises(json.JSONDecodeError):
        parse_gate_verdicts(raw, [{"key": "0"}])


def test_explicit_approval_counts_and_missing_entries_are_rejected():
    result = parse_gate_verdicts(
        '```json\n{"0": {"translate": true, "reason": "speech"}}\n```',
        [{"key": "0"}, {"key": "1"}],
    )
    assert (result["0"]["translate"], result["1"]["translate"]) == (True, False)


def test_budget_is_doubled_before_the_batch_is_split():
    gate = _Gate(parseable_up_to=2)
    verdicts = run_async(classify_with_recovery(gate, _entries(4), "english"), timeout=5.0)
    assert gate.requests == [
        (["0", "1", "2", "3"], 8192, 4),
        (["0", "1", "2", "3"], 16384, 4),
        (["0", "1"], 8192, 2),
        (["0", "1"], 8192, 2),
    ]
    assert list(verdicts) == ["0", "1", "2", "3"]
    assert [v["reason"] for v in verdicts.values()] == ["Line 0", "Line 1", "Line 2", "Line 3"]


def test_single_entry_that_never_parses_is_rejected():
    gate = _Gate(parseable_up_to=0)
    verdicts = run_async(
        classify_with_recovery(gate, [{"key": "7", "text": "Hello"}], "english"), timeout=5.0
    )
    assert verdicts == {"7": {"translate": False, "reason": "gate_parse_failed"}}
    assert [r[1] for r in gate.requests] == [8192, 16384]


def test_no_entries_make_no_request():
    gate = _Gate(parseable_up_to=10)
    assert run_async(classify_with_recovery(gate, [], "english"), timeout=5.0) == {}
    assert gate.requests == []


def test_payload_without_sources_uses_default_separators_and_string_fields():
    prompt = gate_user_prompt([{"key": 0, "text": "Hi", "offset": None}], "auto")
    assert prompt == (
        "Source language label: auto. Classify each entry.\n\n"
        '{"0": {"text": "Hi", "file": "", "offset": "None", "hint": ""}}'
    )


def test_shared_sources_keep_the_consumer_of_each_occurrence():
    entries = [
        {
            "key": str(i),
            "file": "a.ncs",
            "text": "Hello",
            "offset": i,
            "nss_snippet": "abcdef"[i:],
            "nss_start": i,
            "bytecode_context": {"consumer": consumer},
        }
        for i, consumer in enumerate(["SpeakString:0", "SetLocalString:1"])
    ]
    payload = json.loads(gate_user_prompt(entries, "en").split("\n\n", 1)[1])
    assert len(payload["sources"]["a.ncs"]) == 1
    first, second = payload["entries"]["0"], payload["entries"]["1"]
    assert first["bytecode_context"] != second["bytecode_context"]
    assert parse_gate_verdicts('{"0":{"translate":true}}', entries)["1"]["translate"] is False
