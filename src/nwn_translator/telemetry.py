"""Request-level metrics of the LLM calls of a run (prompt budget, tokens, latency).

The translation log is item-oriented; these metrics let pipeline changes be
compared by the actual model calls.
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
    """Returns the phase set by the enclosing :func:`llm_phase`, else *default*.

    Args:
        default: Phase of the calling task.

    Returns:
        The phase label for request metrics.
    """
    return _CURRENT_PHASE.get() or default


@contextmanager
def llm_phase(phase: str) -> Iterator[None]:
    """Tags the provider requests made inside the ``with`` block with *phase*.

    Args:
        phase: Phase label for request metrics.

    Yields:
        Nothing; the label applies until the block exits.
    """
    token = _CURRENT_PHASE.set(phase)
    try:
        yield
    finally:
        _CURRENT_PHASE.reset(token)


@dataclass
class LLMRequestMetric:
    """One LLM request attempt.

    A resend without a ``reasoning`` field the model rejected belongs to the same
    attempt: one metric covers both HTTP requests and their combined latency.

    Attributes:
        request_id: Opaque unique id.
        phase: Pipeline phase (``llm_phase`` label or the task's default).
        provider: Provider name.
        model: Model slug.
        batch_size: Items answered by the request.
        stable_chars: Characters of the cacheable system prompt half.
        variable_chars: Characters of the per-call system prompt half.
        user_chars: Characters of the user message.
        world_context_chars: Reserved; always 0.
        glossary_chars: Characters of the glossary block in the system prompt.
        prompt_chars: ``stable_chars + variable_chars + user_chars``.
        estimated_input_tokens: Reported prompt tokens, else ``ceil(prompt_chars / 4)``.
        estimated_output_tokens: Reported completion tokens, else ``ceil(reply / 4)``.
        usage_input_tokens: Prompt tokens reported by the API.
        usage_output_tokens: Completion tokens reported by the API.
        latency_ms: Wall time of the attempt.
        retry_count: Reserved; always 0.
        timeout: The attempt timed out.
        parse_recovery: Reserved; always ``None``.
        success: The API returned a reply with a message.
        error: Error text of a failed attempt.
        created_at: Unix time of recording.
    """

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


#: Metric fields summed per phase, in report order.
_SUMMED_FIELDS = (
    "prompt_chars",
    "stable_chars",
    "variable_chars",
    "user_chars",
    "world_context_chars",
    "glossary_chars",
    "estimated_input_tokens",
    "estimated_output_tokens",
    "usage_input_tokens",
    "usage_output_tokens",
    "latency_ms",
)
#: Per-phase summary keys in report order; ``avg_latency_ms`` is appended last.
_PHASE_KEYS = (
    "requests",
    "successful_requests",
    "failed_requests",
    "timeouts",
    "batch_items",
    *_SUMMED_FIELDS,
)


class RunMetricsRecorder:
    """Thread-safe accumulator for request metrics and run counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: List[LLMRequestMetric] = []
        self._counters: Dict[str, int] = {}

    def next_request_id(self) -> str:
        """Returns a new opaque request id.

        Returns:
            A random 32-character hex string.
        """
        return uuid.uuid4().hex

    def increment(self, key: str, by: int = 1) -> None:
        """Adds *by* to the run counter *key*.

        Args:
            key: Counter name.
            by: Increment.
        """
        with self._lock:
            self._counters[key] = int(self._counters.get(key, 0)) + by

    def record(self, metric: LLMRequestMetric) -> None:
        """Appends one request metric.

        Args:
            metric: The metric to store.
        """
        with self._lock:
            self._requests.append(metric)

    @property
    def requests(self) -> List[LLMRequestMetric]:
        """A snapshot of the recorded request metrics."""
        with self._lock:
            return list(self._requests)

    def summary(self) -> Dict[str, Any]:
        """Aggregates the recorded requests by phase.

        Returns:
            ``total_requests``, ``phases`` (per phase: request counts, summed sizes,
            tokens and latency, and ``avg_latency_ms``) and the run ``counters``.
        """
        phases: Dict[str, Dict[str, int]] = {}
        with self._lock:
            requests = list(self._requests)
            counters = dict(self._counters)

        for metric in requests:
            phase = phases.setdefault(metric.phase, dict.fromkeys(_PHASE_KEYS, 0))
            phase["requests"] += 1
            phase["successful_requests"] += int(metric.success)
            phase["failed_requests"] += int(not metric.success)
            phase["timeouts"] += int(metric.timeout)
            phase["batch_items"] += metric.batch_size
            for name in _SUMMED_FIELDS:
                phase[name] += getattr(metric, name) or 0

        for phase in phases.values():
            phase["avg_latency_ms"] = int(phase["latency_ms"] / max(1, phase["requests"]))

        return {
            "total_requests": len(requests),
            "phases": phases,
            "counters": counters,
        }

    def write_json(self, path: Path) -> None:
        """Writes ``{"summary": ..., "requests": [...]}`` as indented UTF-8 JSON.

        Args:
            path: Output file; its parent directories are created.
        """
        document = {
            "summary": self.summary(),
            "requests": [asdict(metric) for metric in self.requests],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False, indent=2)


def estimate_tokens(chars: int) -> int:
    """Estimates tokens from characters when the API reports no usage.

    Args:
        chars: Character count.

    Returns:
        ``ceil(chars / 4)``, at least 0.
    """
    return max(0, (int(chars) + 3) // 4)


def split_system_prompt_chars(system_prompt: Any) -> tuple[int, int]:
    """Measures the cacheable and per-call parts of a system message.

    Args:
        system_prompt: Plain text, or content parts in which ``cache_control`` marks
            the cacheable part.

    Returns:
        ``(stable_chars, variable_chars)``; plain text counts as stable.
    """
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
    """Reads token usage from an OpenAI-compatible response.

    Args:
        response: Chat completion (``usage`` as object or dict), or ``None``.

    Returns:
        ``(prompt_tokens, completion_tokens)``, each ``None`` when not reported.
    """
    usage = getattr(response, "usage", None)

    def count(name: str) -> Optional[int]:
        """Returns one reported count of *usage*, if any."""
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(value) if value is not None else None

    return count("prompt_tokens"), count("completion_tokens")
