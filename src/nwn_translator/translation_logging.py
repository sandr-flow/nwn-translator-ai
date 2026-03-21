"""Pluggable translation log output (file, null, or custom)."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

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

    def write(self, entry: Dict[str, Any]) -> None:
        """Serialize *entry* as JSON and append one line to the log file.

        Args:
            entry: JSON-serializable dict (e.g. original/translated pair).
        """
        try:
            with open(self.path, "a", encoding="utf-8") as f:
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
