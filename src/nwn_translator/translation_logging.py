"""Pluggable translation log output (file, null, or custom)."""

import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from uuid import uuid4
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, TypeVar

logger = logging.getLogger(__name__)


class TranslationLogWriter(Protocol):
    """Append one JSON-serializable log record per translation."""

    def write(self, entry: Dict[str, Any]) -> None:
        """Persist a single log entry (e.g. one line of JSONL)."""
        ...


class FileTranslationLogWriter:
    """Append JSONL lines to a file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        # Dialog files are translated from worker threads sharing one writer.
        self._lock = threading.Lock()

    def write(self, entry: Dict[str, Any]) -> None:
        """Serialize *entry* as JSON and append one line to the log file.

        Args:
            entry: JSON-serializable dict (e.g. original/translated pair).
        """
        try:
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to write translation log: %s", e)


class NullTranslationLogWriter:
    """No-op writer for when logging is disabled."""

    def write(self, entry: Dict[str, Any]) -> None:
        """Discard the entry (no-op).

        Args:
            entry: Ignored.
        """
        return None


def translation_log_writer_for_config(
    translation_log: Optional[Path],
    override: Optional[TranslationLogWriter] = None,
) -> TranslationLogWriter:
    """Resolve writer from optional path and optional injected override.

    If ``override`` is set, it wins. Else if ``translation_log`` is set, use file writer.
    Otherwise null writer.
    """
    if override is not None:
        return override
    if translation_log is not None:
        return FileTranslationLogWriter(translation_log)
    return NullTranslationLogWriter()


_Result = TypeVar("_Result")


def write_trace(writer: TranslationLogWriter, entry: Dict[str, Any]) -> None:
    """A diagnostic writer failure must not change a translation or patch result."""
    try:
        writer.write(entry)
    except Exception as exc:
        logger.debug("Failed to write translation trace: %s", exc)


def _trace_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _trace_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


async def logged_model_call(
    writer: TranslationLogWriter,
    method: Callable[..., Awaitable[_Result]],
    *,
    trace_context: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> _Result:
    """Trace a logical provider call and response, without provider credentials."""
    request_id = uuid4().hex
    write_trace(
        writer,
        {
            "event": "model_request",
            "request_id": request_id,
            "method": getattr(method, "__name__", type(method).__name__),
            "context": _trace_value(trace_context or {}),
            "arguments": _trace_value(kwargs),
        },
    )
    try:
        result = await method(**kwargs)
    except BaseException as exc:
        write_trace(
            writer,
            {"event": "model_response", "request_id": request_id, "error": type(exc).__name__},
        )
        raise
    write_trace(
        writer,
        {"event": "model_response", "request_id": request_id, "result": _trace_value(result)},
    )
    return result
