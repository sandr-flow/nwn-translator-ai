"""User prompts of contextual dialog translation.

The system prompt comes from
:func:`~nwn_translator.prompts._builder.build_dialog_system_prompt_parts`;
this module holds the user messages: the script of one dialog or of a group
of small dialogs, the repair requests after an unparseable answer, the retry
of lines whose NWN tokens or tags came back broken, and the context of a
per-line retry.
"""

from typing import TYPE_CHECKING, List, Mapping, Optional, Sequence, Tuple

from .token_retry import (
    PRESERVE_INLINE_MARKUP,
    PRESERVE_PLACEHOLDERS,
    expected_artifacts_line,
    previous_mismatch_lines,
)

if TYPE_CHECKING:
    from ..translators.token_handler import TokenMismatchReport

#: Characters of an unparseable answer quoted back in a repair request.
_BAD_ANSWER_CHARS = 1200

_NO_TEXT_AROUND_JSON = "No markdown, no comments, no text before or after the object."

#: The single per-line retry calls itself the second of two attempts. The text
#: is part of the request, so it is kept as is.
_LINE_RETRY_ATTEMPT = "Retry attempt 2 of 2."


def _keys_exactly(keys: Sequence[str]) -> str:
    """Return the sentence asking for exactly *keys* (sorted) as string values."""
    keys_csv = ", ".join(sorted(keys))
    return (
        f"Return ONLY one JSON object: keys exactly {keys_csv} "
        f"(same IDs as in the script), each value a string translation."
    )


def _bad_answer(bad_response: str) -> str:
    """Return the quoted start of an unparseable answer."""
    return "Invalid previous output (truncated for context):\n" + (
        (bad_response or "").strip()[:_BAD_ANSWER_CHARS]
    )


def speakers_block(lines: Sequence[str]) -> str:
    """Wrap speaker lines in the ``DIALOG SPEAKERS`` block of the system prompt.

    Args:
        lines: Lines from :func:`~nwn_translator.context.dialog_speakers.speaker_lines`.

    Returns:
        The block, or ``""`` without lines.
    """
    if not lines:
        return ""
    closing = (
        "Use each speaker's gender for their grammatical forms (verb endings, "
        "adjectives, self-references) in the lines they speak."
    )
    return "DIALOG SPEAKERS:\n" + "\n".join([*lines, closing])


def dialog_user_prompt(filename: str, script: str) -> str:
    """Ask for the translation of one dialog script.

    Args:
        filename: Dialog file name.
        script: Formatted script (or one chunk of it).

    Returns:
        The user message.
    """
    return (
        f"Translate the following dialog script from {filename}:\n\n"
        f"{script}\n\n"
        f"Return ONLY a JSON object: map each line ID (e.g. E0, R1) to the "
        f"translated string. No markdown fences, no extra keys, no text outside JSON."
    )


def repair_prompt(filename: str, script: str, keys: Sequence[str], bad_response: str) -> str:
    """Ask again for valid JSON after an unparseable dialog answer.

    Args:
        filename: Dialog file name.
        script: The script of the failed request.
        keys: Keys the request asked for.
        bad_response: The unparseable answer; its start is quoted back.

    Returns:
        The user message.
    """
    return (
        f"The previous answer for {filename} was not valid JSON or was truncated.\n"
        f"{_keys_exactly(keys)}\n"
        f"{_NO_TEXT_AROUND_JSON}\n\n"
        f"Dialog script:\n\n{script}\n\n"
        f"{_bad_answer(bad_response)}"
    )


def token_retry_prompt(
    filename: str,
    script: str,
    keys: Sequence[str],
    expected: Mapping[str, Sequence[str]],
    reports: Mapping[str, Optional["TokenMismatchReport"]],
) -> str:
    """Ask again for lines that were missing or whose tokens or tags broke.

    Args:
        filename: Dialog file name.
        script: Script of the lines to retry.
        keys: Keys of those lines.
        expected: Artifacts each line must keep, by key.
        reports: How each line's previous answer broke its artifacts, if known.

    Returns:
        The user message; lines are described in sorted key order.
    """
    keys_csv = ", ".join(sorted(keys))
    lines = [
        f"The previous answer for {filename} changed, dropped, or omitted preserved NWN "
        f"tags/tokens for keys: {keys_csv}.",
        _keys_exactly(keys),
        "Preserve every placeholder and helper token surrogate EXACTLY as it appears in the script.",
        "Do not rename, reorder, delete, duplicate, or replace any placeholder.",
        "Translate only the normal prose and the text inside square brackets.",
        "",
        "Expected preserved artifacts after restoration:",
    ]
    for key in sorted(keys):
        if expected[key]:
            lines.append(f"- {key}: " + " | ".join(expected[key]))
        report = reports.get(key)
        if report is not None and not report.is_exact_match and report.actual_sequence:
            lines.append("  previous restored sequence: " + " | ".join(report.actual_sequence))
    lines.extend(["", "Dialog script:", "", script])
    return "\n".join(lines)


def line_retry_context(
    key: str,
    filename: str,
    speaker: str,
    expected: Sequence[str],
    report: Optional["TokenMismatchReport"],
) -> str:
    """Return the context of a single-line retry of one dialog node.

    Args:
        key: Script key of the node.
        filename: Dialog file name.
        speaker: Speaker label of the node.
        expected: Artifacts the line must keep.
        report: How the previous answer broke them, if known.

    Returns:
        The context passed to the single-line translation.
    """
    return "\n".join(
        [
            f"Dialog node {key} in {filename}.",
            f"Speaker: {speaker}.",
            PRESERVE_PLACEHOLDERS,
            PRESERVE_INLINE_MARKUP,
            *expected_artifacts_line(expected),
            *previous_mismatch_lines(report),
            _LINE_RETRY_ATTEMPT,
        ]
    )


def group_script(scripts: Sequence[Tuple[str, str]]) -> str:
    """Join dialog scripts under ``=== FILE: <name> ===`` headers.

    Args:
        scripts: ``(file name, script)`` pairs in request order.

    Returns:
        The combined script.
    """
    return "\n\n".join(f"=== FILE: {name} ===\n{script}" for name, script in scripts)


def group_user_prompt(names: List[str], combined_script: str) -> str:
    """Ask for the translation of several small dialogs in one request.

    Args:
        names: File names in request order.
        combined_script: Output of :func:`group_script`.

    Returns:
        The user message.
    """
    names_csv = ", ".join(names)
    return (
        f"Translate the following {len(names)} unrelated dialog scripts ({names_csv}). "
        f"Each script starts with a '=== FILE: <name> ===' header. The conversations "
        f"are independent: do not let tone, wording, or context leak from one file "
        f"into another.\n\n"
        f"{combined_script}\n\n"
        f"Return ONLY one JSON object. Each top-level key is a file name exactly as "
        f"written in its header; each value is an object mapping that file's line IDs "
        f"(e.g. E0, R1) to the translated string. "
        f'Example: {{"a.dlg": {{"E0": "...", "R1": "..."}}, "b.dlg": {{"E0": "..."}}}}. '
        f"No markdown fences, no extra keys, no text outside JSON."
    )


def group_repair_prompt(names: List[str], combined_script: str, bad_response: str) -> str:
    """Ask again for valid nested JSON after an unparseable group answer.

    Args:
        names: File names in request order.
        combined_script: Output of :func:`group_script`.
        bad_response: The unparseable answer; its start is quoted back.

    Returns:
        The user message.
    """
    return (
        f"The previous answer was not valid JSON or was truncated.\n"
        f"Return ONLY one JSON object with exactly these top-level keys: {', '.join(names)}. "
        f"Each value is an object mapping that file's line IDs (same IDs as in the "
        f"script) to the translated string.\n"
        f"{_NO_TEXT_AROUND_JSON}\n\n"
        f"Dialog scripts:\n\n{combined_script}\n\n"
        f"{_bad_answer(bad_response)}"
    )
