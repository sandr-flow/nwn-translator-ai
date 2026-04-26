"""Run-level telemetry for LLM requests.

The translation JSONL log is item-oriented.  This module records request-level
metrics so pipeline changes can be compared by actual LLM calls and prompt
budget, not only by translated strings.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_CURRENT_PHASE: ContextVar[Optional[str]] = ContextVar("nwn_llm_phase", default=None)


def current_llm_phase(default: str) -> str:
    """Return the current request phase label."""
    return _CURRENT_PHASE.get() or default


@contextmanager
def llm_phase(phase: str) -> Iterator[None]:
    """Temporarily tag provider calls made in this context with *phase*."""
    token = _CURRENT_PHASE.set(phase)
    try:
        yield
    finally:
        _CURRENT_PHASE.reset(token)


@dataclass
class LLMRequestMetric:
    """One physical LLM request attempt."""

    request_id: str
    phase: str
    provider: str
    model: str
    batch_size: int = 1
    stable_chars: int = 0
    variable_chars: int = 0
    user_chars: int = 0
    world_context_chars: int = 0
    glossary_chars: int = 0
    prompt_chars: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    usage_input_tokens: Optional[int] = None
    usage_output_tokens: Optional[int] = None
    latency_ms: int = 0
    retry_count: int = 0
    timeout: bool = False
    parse_recovery: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class RunMetricsRecorder:
    """Thread-safe accumulator for request metrics and phase summaries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: List[LLMRequestMetric] = []
        self._counters: Dict[str, int] = {}

    def next_request_id(self) -> str:
        """Return an opaque request id."""
        return uuid.uuid4().hex

    def increment(self, key: str, by: int = 1) -> None:
        """Increment a named run-level counter."""
        with self._lock:
            self._counters[key] = int(self._counters.get(key, 0)) + by

    def record(self, metric: LLMRequestMetric) -> None:
        """Append a request metric."""
        with self._lock:
            self._requests.append(metric)

    @property
    def requests(self) -> List[LLMRequestMetric]:
        """Return a snapshot of recorded requests."""
        with self._lock:
            return list(self._requests)

    def summary(self) -> Dict[str, Any]:
        """Return aggregate metrics grouped by phase."""
        phases: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            requests = list(self._requests)
            counters = dict(self._counters)

        for metric in requests:
            phase = phases.setdefault(
                metric.phase,
                {
                    "requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "timeouts": 0,
                    "batch_items": 0,
                    "prompt_chars": 0,
                    "stable_chars": 0,
                    "variable_chars": 0,
                    "user_chars": 0,
                    "world_context_chars": 0,
                    "glossary_chars": 0,
                    "estimated_input_tokens": 0,
                    "estimated_output_tokens": 0,
                    "usage_input_tokens": 0,
                    "usage_output_tokens": 0,
                    "latency_ms": 0,
                },
            )
            phase["requests"] += 1
            phase["successful_requests"] += 1 if metric.success else 0
            phase["failed_requests"] += 0 if metric.success else 1
            phase["timeouts"] += 1 if metric.timeout else 0
            phase["batch_items"] += metric.batch_size
            phase["prompt_chars"] += metric.prompt_chars
            phase["stable_chars"] += metric.stable_chars
            phase["variable_chars"] += metric.variable_chars
            phase["user_chars"] += metric.user_chars
            phase["world_context_chars"] += metric.world_context_chars
            phase["glossary_chars"] += metric.glossary_chars
            phase["estimated_input_tokens"] += metric.estimated_input_tokens
            phase["estimated_output_tokens"] += metric.estimated_output_tokens
            phase["usage_input_tokens"] += metric.usage_input_tokens or 0
            phase["usage_output_tokens"] += metric.usage_output_tokens or 0
            phase["latency_ms"] += metric.latency_ms

        for phase in phases.values():
            requests_count = max(1, int(phase["requests"]))
            phase["avg_latency_ms"] = int(phase["latency_ms"] / requests_count)

        return {
            "total_requests": len(requests),
            "phases": phases,
            "counters": counters,
        }

    def to_json_dict(self) -> Dict[str, Any]:
        """Return a machine-readable metrics document."""
        return {
            "summary": self.summary(),
            "requests": [asdict(metric) for metric in self.requests],
        }

    def write_json(self, path: Path) -> None:
        """Write metrics as UTF-8 JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_json_dict(), f, ensure_ascii=False, indent=2)


def estimate_tokens(chars: int) -> int:
    """Cheap tokenizer-independent estimate used when provider usage is absent."""
    return max(0, (int(chars) + 3) // 4)


def split_system_prompt_chars(system_prompt: Any) -> tuple[int, int]:
    """Return ``(stable_chars, variable_chars)`` for a system message payload."""
    if isinstance(system_prompt, list):
        stable = 0
        variable = 0
        for part in system_prompt:
            if not isinstance(part, dict):
                variable += len(str(part))
                continue
            text = str(part.get("text", ""))
            if part.get("cache_control"):
                stable += len(text)
            else:
                variable += len(text)
        return stable, variable
    return len(str(system_prompt or "")), 0


def usage_tokens(response: Any) -> tuple[Optional[int], Optional[int]]:
    """Extract prompt/completion token usage from OpenAI-compatible responses."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", prompt)
        completion = usage.get("completion_tokens", completion)
    return (
        int(prompt) if prompt is not None else None,
        int(completion) if completion is not None else None,
    )
