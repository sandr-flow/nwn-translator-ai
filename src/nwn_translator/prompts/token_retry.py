"""Text shared by the retries that ask the model to keep NWN tokens and tags intact."""

from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:
    from ..translators.token_handler import TokenMismatchReport

#: Asks for placeholders to come back exactly as sent.
PRESERVE_PLACEHOLDERS = (
    "TOKEN/TAG PRESERVATION RETRY: preserve every placeholder and helper token "
    "surrogate exactly as it appears in the source text. Do not rename, reorder, "
    "delete, duplicate, or replace any placeholder."
)

#: Asks for NWN inline markup to survive restoration unchanged.
PRESERVE_INLINE_MARKUP = (
    "If the line contains NWN inline markup such as StartAction, StartCheck, "
    "StartHighlight, or </Start>, preserve that markup exactly after restoration. "
    "Translate only normal prose and text inside square brackets."
)


def expected_artifacts_line(expected: Sequence[str]) -> List[str]:
    """Returns the line listing the artifacts the restored text must contain.

    Args:
        expected: Artifacts in source order.

    Returns:
        One line, or no line when the source has no artifacts.
    """
    if not expected:
        return []
    return ["Expected preserved artifacts after restoration: " + " | ".join(expected)]


def previous_mismatch_lines(report: Optional["TokenMismatchReport"]) -> List[str]:
    """Describes how the previous answer broke the artifacts.

    Args:
        report: Validation report of the previous answer, if any.

    Returns:
        The mismatch type and, when known, the restored artifact sequence; no
        lines when there was no report or the answer matched exactly.
    """
    if report is None or report.is_exact_match:
        return []
    lines = [f"Previous mismatch type: {report.mismatch_type}."]
    if report.actual_sequence:
        lines.append("Previous restored artifact sequence: " + " | ".join(report.actual_sequence))
    return lines
