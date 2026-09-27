"""The script gate decides every script literal and records each decision."""

from unittest.mock import AsyncMock, Mock

from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.translators.ncs_diagnostics import NcsDiagnostics, new_ncs_diagnostics
from nwn_translator.translators.script_gate import ScriptGate, add_script_context
from tests.support.fakes import RecordingWriter


def _line(index: int, **meta) -> TranslatableItem:
    return TranslatableItem(
        f"Line {index} is spoken aloud.",
        "NCS string",
        f"s:{index}",
        "s.ncs",
        {"type": "ncs_string", "offset": index, "ncs_hint": "SpeakString", **meta},
    )


def _gate(provider, **config):
    writer = RecordingWriter()
    diagnostics = NcsDiagnostics(new_ncs_diagnostics(), writer)
    gate = ScriptGate(TranslationConfig(api_key="k", **config), provider, writer, diagnostics)
    return gate, diagnostics, writer


def test_candidates_are_asked_in_chunks_with_local_keys():
    items = [_line(i) for i in range(45)]
    asked = []

    async def classify(entries, *, source_lang):
        asked.append(entries)
        return {
            e["key"]: {"translate": int(e["key"]) % 2 == 0, "reason": "checked"}
            for e in entries
            if e["key"] != "3"
        }

    provider = Mock()
    provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=classify)
    gate, diagnostics, writer = _gate(provider, max_concurrent_requests=1)

    approvals = gate.decide(
        [TranslatableItem("Not a script", item_id="x", location="a.uti")] + items
    )

    assert [len(entries) for entries in asked] == [20, 20, 5]
    assert [e["key"] for e in asked[1]] == [str(i) for i in range(20)]
    assert asked[1][0] == {
        "key": "0",
        "text": items[20].text,
        "file": "s.ncs",
        "offset": 20,
        "hint": "SpeakString",
        "nss_snippet": None,
        "nss_start": None,
        "bytecode_context": None,
        "confidence": None,
    }
    assert set(approvals) == {item.key for item in items}
    assert approvals[items[20].key] is True
    assert approvals[items[21].key] is False
    assert approvals[items[23].key] is False
    samples = diagnostics.block["samples"]
    assert samples[23]["reason"] == "gate_rejected:gate_missing_key"
    assert samples[20]["reason"] == "gate_approved:checked"
    assert (diagnostics.block["approved"], diagnostics.block["skipped_fail_closed"]) == (23, 22)
    requests = [e for e in writer.entries if e.get("event") == "model_request"]
    assert requests[1]["context"] == {"occurrences": [list(item.key) for item in items[20:40]]}


def test_failed_chunk_rejects_only_its_own_candidates():
    items = [_line(i) for i in range(45)]

    async def classify(entries, *, source_lang):
        if entries[0]["text"] == items[20].text:
            raise RuntimeError("provider down")
        return {e["key"]: {"translate": True, "reason": "ok"} for e in entries}

    provider = Mock()
    provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=classify)
    gate, diagnostics, _writer = _gate(provider, max_concurrent_requests=1)

    approvals = gate.decide(items)

    assert [approvals[item.key] for item in items] == [True] * 20 + [False] * 20 + [True] * 5
    reasons = [sample["reason"] for sample in diagnostics.block["samples"]]
    assert reasons[19:21] == ["gate_approved:ok", "gate_rejected:gate_unavailable"]
    assert reasons[40] == "gate_approved:ok"
    assert diagnostics.block["skipped_fail_closed"] == 20


def test_vetoed_and_bypassed_literals_never_reach_the_model():
    vetoed = _line(0)
    vetoed.text = "DetermineClassToUse: This character is invalid."
    proven = _line(1, proven_player=True)
    unproven = _line(2, proven_player=False, player_candidate=True)
    provider = Mock()
    gate, diagnostics, _writer = _gate(provider, skip_ncs_llm_gate=True)

    approvals = gate.decide([vetoed, proven, unproven])

    assert approvals == {vetoed.key: False, proven.key: True, unproven.key: False}
    provider.classify_ncs_translate_gate_batch_async.assert_not_called()
    assert [s["reason"] for s in diagnostics.block["samples"]] == [
        "code_identifier",
        "gate_bypassed_proven",
        "gate_disabled_unproven",
    ]
    assert diagnostics.block["skipped_hard_veto"] == 1


def test_script_context_quotes_nearest_approved_lines_by_offset():
    items = [_line(offset) for offset in (50, 10, 40, 20, 30)]
    items[1].metadata["nss_snippet"] = "x" * 2500

    add_script_context(items)

    by_offset = {item.metadata["offset"]: item for item in items}
    first = by_offset[10]
    assert first.metadata["translation_group"] == "script"
    assert first.metadata["batch_resource"] == "s.ncs"
    assert first.metadata["batch_context"] == "NCS string"
    assert first.metadata["approved_neighbors"] == [by_offset[o].text for o in (20, 30, 40)]
    assert first.context.split("\n")[:2] == [
        "NCS string",
        "Matching source excerpt (context only):",
    ]
    assert "x" * 2000 + "\n" in first.context and "x" * 2001 not in first.context
    middle = by_offset[30]
    assert middle.metadata["approved_neighbors"] == [by_offset[o].text for o in (10, 20, 40, 50)]
    assert middle.context.endswith(
        "do not translate these as extra outputs):\n"
        + "\n".join(by_offset[o].text for o in (10, 20, 40, 50))
    )
