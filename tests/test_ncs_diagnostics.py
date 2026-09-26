"""NCS diagnostics blocks: counters, capped samples and log events."""

from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.translators.ncs_diagnostics import (
    NCS_COUNTERS,
    SAMPLE_LIMIT,
    NcsDiagnostics,
    new_ncs_diagnostics,
)


class _Writer:
    def __init__(self, fail: bool = False) -> None:
        self.entries: list = []
        self.fail = fail

    def write(self, entry) -> None:
        if self.fail:
            raise OSError("disk full")
        self.entries.append(entry)


def _item(index: int) -> TranslatableItem:
    return TranslatableItem(
        "x" * 200,
        None,
        f"s:{index}",
        "dir/s.ncs",
        {"type": "ncs_string", "offset": index, "confidence": "high", "ncs_hint": "SpeakString"},
    )


def test_new_block_lists_every_counter_then_samples():
    block = new_ncs_diagnostics()
    assert list(block) == [*NCS_COUNTERS, "samples"]
    assert all(block[name] == 0 for name in NCS_COUNTERS) and block["samples"] == []


def test_samples_are_capped_but_every_outcome_is_counted_and_logged():
    writer = _Writer()
    diagnostics = NcsDiagnostics(new_ncs_diagnostics(), writer)

    for index in range(SAMPLE_LIMIT + 10):
        diagnostics.record(
            _item(index), reason="translation_failed", count_field="failed", error="e"
        )
    diagnostics.count("translated", 3)

    block = diagnostics.block
    assert block["failed"] == SAMPLE_LIMIT + 10 and block["translated"] == 3
    assert len(block["samples"]) == SAMPLE_LIMIT
    assert block["samples"][0] == {
        "file": "s.ncs",
        "item_id": "s:0",
        "offset": 0,
        "confidence": "high",
        "ncs_hint": "SpeakString",
        "reason": "translation_failed",
        "text_prefix": "x" * 120,
        "error": "e",
    }
    assert len(writer.entries) == SAMPLE_LIMIT + 10
    assert writer.entries[0] == {"event": "ncs_diagnostic", **block["samples"][0]}


def test_log_failure_does_not_lose_the_sample():
    diagnostics = NcsDiagnostics(new_ncs_diagnostics(), _Writer(fail=True))
    diagnostics.record(_item(1), reason="gate_rejected:no")
    assert diagnostics.block["samples"][0]["reason"] == "gate_rejected:no"
    assert "error" not in diagnostics.block["samples"][0]
