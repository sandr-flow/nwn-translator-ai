"""Pluggable translation log output (file, null, or custom)."""

import json
import logging
import threading
import weakref
from dataclasses import asdict, is_dataclass
from uuid import uuid4
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, TextIO, TypeVar

logger = logging.getLogger(__name__)

_Result = TypeVar("_Result")


class TranslationLogWriter(Protocol):
    """Append one JSON-serializable log record per translation."""

    def write(self, entry: Dict[str, Any]) -> None:
        """Persists a single log entry (e.g. one line of JSONL)."""


class FileTranslationLogWriter:
    """Append JSONL lines to a file through one handle kept open.

    Opening the file for every entry costs milliseconds on Windows, and a run
    writes tens of thousands of entries. Each entry is flushed at once, so the
    file is complete while the run goes on.

    Attributes:
        path: The log file.
    """

    def __init__(self, path: Path) -> None:
        """Creates a writer; the file is opened by the first entry.

        Args:
            path: Log file; entries are appended to its current content.
        """
        self.path = Path(path)
        # Dialog files are translated from worker threads sharing one writer.
        self._lock = threading.Lock()
        self._file: Optional[TextIO] = None
        self._finalizer: Optional[weakref.finalize] = None

    def write(self, entry: Dict[str, Any]) -> None:
        """Serializes *entry* as JSON and append one line to the log file.

        Args:
            entry: JSON-serializable dict (e.g. original/translated pair).
        """
        try:
            with self._lock:
                if self._file is None:
                    self._file = open(self.path, "a", encoding="utf-8")
                    # A writer that is never closed still releases its handle.
                    self._finalizer = weakref.finalize(self, self._file.close)
                self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._file.flush()
        except OSError as e:
            logger.debug("Failed to write translation log: %s", e)

    def close(self) -> None:
        """Closes the file; a later entry opens it again."""
        with self._lock:
            if self._finalizer is not None:
                self._finalizer()
            self._file = None
            self._finalizer = None


class NullTranslationLogWriter:
    """No-op writer for when logging is disabled."""

    def write(self, entry: Dict[str, Any]) -> None:
        """Discards the entry (no-op).

        Args:
            entry: Ignored.
        """
        return None


def translation_log_writer_for_config(
    translation_log: Optional[Path],
    override: Optional[TranslationLogWriter] = None,
) -> TranslationLogWriter:
    """Resolves the log writer of a run.

    Args:
        translation_log: JSONL log path, or ``None``.
        override: Injected writer (web database); wins over *translation_log*.

    Returns:
        *override*, else a file writer for *translation_log*, else a null writer.
    """
    if override is not None:
        return override
    if translation_log is not None:
        return FileTranslationLogWriter(translation_log)
    return NullTranslationLogWriter()


def write_trace(writer: TranslationLogWriter, entry: Dict[str, Any]) -> None:
    """Writes a diagnostic log entry; a writer failure never changes a result.

    Args:
        writer: Log writer.
        entry: JSON-serializable log entry.
    """
    try:
        writer.write(entry)
    except Exception as exc:
        logger.debug("Failed to write translation trace: %s", exc)


def _trace_value(value: Any) -> Any:
    """Converts dataclasses, tuples and paths into JSON-ready values."""
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
    """Calls a provider task and log the request and its response.

    The request entry records ``method.__name__`` and the call arguments, never
    provider credentials; the response entry records the result or the error type.

    Args:
        writer: Log writer.
        method: Bound provider task method (e.g. ``provider.translate_batch_async``).
        trace_context: Caller context stored with the request entry.
        **kwargs: Arguments of *method*.

    Returns:
        The method's result.

    Raises:
        BaseException: Whatever *method* raises, after logging its type.
    """
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
