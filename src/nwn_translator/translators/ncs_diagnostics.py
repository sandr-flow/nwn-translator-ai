"""Counters and samples that explain what happened to the strings of compiled scripts.

A run keeps one diagnostics block (see :func:`new_ncs_diagnostics`): a counter per
outcome in :data:`NCS_COUNTERS` and the first :data:`SAMPLE_LIMIT` samples. Every
sample is also written to the translation log as an ``ncs_diagnostic`` event.
"""

from typing import Any, Dict, Optional, Tuple

from ..extractors.base import TranslatableItem
from ..translation_logging import TranslationLogWriter, write_trace

#: Outcome counters of a diagnostics block, in report order.
NCS_COUNTERS: Tuple[str, ...] = (
    "total",
    "extracted",
    "approved",
    "skipped_hard_veto",
    "skipped_fail_closed",
    "translated",
    "timeout",
    "retry_recovered",
    "failed",
    "patch_failed",
)

#: Samples kept per diagnostics block; later ones are only logged.
SAMPLE_LIMIT = 50

#: Source characters quoted in a sample.
_TEXT_PREFIX_CHARS = 120


def new_ncs_diagnostics() -> Dict[str, Any]:
    """Returns a diagnostics block with zero counters and no samples."""
    return {**{name: 0 for name in NCS_COUNTERS}, "samples": []}


def add_sample(
    diagnostics: Dict[str, Any],
    sample: Dict[str, Any],
    count_field: Optional[str] = None,
) -> None:
    """Counts an outcome and keep its sample while the block has room.

    Args:
        diagnostics: Block from :func:`new_ncs_diagnostics`.
        sample: Sample to keep.
        count_field: Counter to increment, if any.
    """
    if count_field:
        diagnostics[count_field] += 1
    if len(diagnostics["samples"]) < SAMPLE_LIMIT:
        diagnostics["samples"].append(sample)


class NcsDiagnostics:
    """Records the outcomes of script strings into one diagnostics block.

    Attributes:
        block: The diagnostics block being filled.
    """

    def __init__(self, block: Dict[str, Any], log_writer: TranslationLogWriter):
        """Records into *block* and log every sample to *log_writer*.

        Args:
            block: Block from :func:`new_ncs_diagnostics`.
            log_writer: Translation log of the run.
        """
        self.block = block
        self._log_writer = log_writer

    def count(self, field: str, by: int = 1) -> None:
        """Adds *by* to one counter without a sample.

        Args:
            field: Counter name from :data:`NCS_COUNTERS`.
            by: Amount to add.
        """
        self.block[field] += by

    def record(
        self,
        item: TranslatableItem,
        *,
        reason: str,
        count_field: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Records one outcome of a script string.

        Args:
            item: The script string.
            reason: Outcome label (``gate_rejected:…``, ``translation_timeout``, …).
            count_field: Counter to increment, if any.
            error: Error text, when the outcome is a failure.
        """
        sample: Dict[str, Any] = {
            "file": item.key[0],
            "item_id": item.item_id,
            "offset": item.metadata.get("offset"),
            "confidence": item.metadata.get("confidence"),
            "ncs_hint": item.metadata.get("ncs_hint"),
            "reason": reason,
            "text_prefix": item.text[:_TEXT_PREFIX_CHARS],
        }
        if error:
            sample["error"] = error
        add_sample(self.block, sample, count_field)
        write_trace(self._log_writer, {"event": "ncs_diagnostic", **sample})

    def timeout(self, item: TranslatableItem) -> None:
        """Records that the request of a script string timed out and will be retried.

        Args:
            item: The script string.
        """
        self.record(item, reason="translation_timeout", count_field="timeout")

    def retry_outcome(
        self, item: TranslatableItem, success: bool, error: Optional[str] = None
    ) -> None:
        """Records whether the retry after a :meth:`timeout` recovered a script string.

        Args:
            item: The script string.
            success: The retry produced an answer.
            error: Error of a failed retry.
        """
        if success:
            self.record(
                item, reason="translation_timeout_retry_recovered", count_field="retry_recovered"
            )
        else:
            self.record(item, reason="translation_timeout_retry_failed", error=error)
