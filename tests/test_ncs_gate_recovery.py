"""NCS gate requests: payload shape and recovery from replies that do not parse."""

import json

from nwn_translator.ai_providers.ncs_gate import classify_with_recovery, gate_user_prompt
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
    entries = [{"key": "7", "text": "Hello"}]
    verdicts = run_async(classify_with_recovery(gate, entries, "english"), timeout=5.0)
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
