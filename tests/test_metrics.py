"""Tests for run-level LLM telemetry."""

from pathlib import Path

from src.nwn_translator.telemetry import LLMRequestMetric, RunMetricsRecorder


def test_metrics_summary_and_json_output(tmp_path: Path):
    recorder = RunMetricsRecorder()
    recorder.record(
        LLMRequestMetric(
            request_id=recorder.next_request_id(),
            phase="glossary",
            provider="fake",
            model="fake/model",
            batch_size=3,
            prompt_chars=120,
            stable_chars=40,
            variable_chars=50,
            user_chars=30,
            glossary_chars=50,
            estimated_input_tokens=30,
            estimated_output_tokens=4,
            latency_ms=25,
        )
    )
    recorder.record(
        LLMRequestMetric(
            request_id=recorder.next_request_id(),
            phase="dialog",
            provider="fake",
            model="fake/model",
            batch_size=2,
            prompt_chars=80,
            estimated_input_tokens=20,
            timeout=True,
            success=False,
            error="timeout",
        )
    )

    summary = recorder.summary()
    assert summary["total_requests"] == 2
    assert summary["phases"]["glossary"]["batch_items"] == 3
    assert summary["phases"]["dialog"]["timeouts"] == 1

    out = tmp_path / "run.metrics.json"
    recorder.write_json(out)
    assert out.exists()
    assert '"glossary"' in out.read_text(encoding="utf-8")
