"""Helpers for extracting JSON objects from LLM responses.

Each reply parser keeps the tolerance its call site was built and tested with:
:func:`load_first_json_object` and :func:`json_extract_first_object` decode the
object at the first ``{``, :func:`load_brace_span` strictly decodes the greedy
``{ … }`` span, and :func:`scan_first_json_object` tries every ``{`` in turn.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, cast

_OPENING_FENCE = r"^```(?:json)?\s*"
_CLOSING_FENCE = re.compile(r"\s*```\s*$")
_BRACE_SPAN = re.compile(r"\{.*\}", re.DOTALL)


def strip_json_markdown_fences(raw: str, *, case_sensitive: bool = False) -> str:
    """Removes an optional markdown code fence (with or without a ``json`` tag).

    Args:
        raw: Model reply.
        case_sensitive: Recognise only a lower-case ``json`` tag, as
            :func:`load_first_json_object` does; ``JSON`` then stays in the text.

    Returns:
        The stripped reply.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    return _CLOSING_FENCE.sub("", re.sub(_OPENING_FENCE, "", raw.strip(), flags=flags))


def _decode_first_object(cleaned: str) -> Dict[str, Any]:
    """Decodes the JSON object that starts at the first ``{`` of *cleaned*."""
    idx = cleaned.find("{")
    if idx == -1:
        raise json.JSONDecodeError("No JSON object found", cleaned, 0)
    # strict=False accepts raw newlines inside strings, which models often emit.
    value, _end = json.JSONDecoder(strict=False).raw_decode(cleaned, idx)
    return cast(Dict[str, Any], value)


def load_first_json_object(raw: str) -> Dict[str, Any]:
    """Decodes the first JSON object of a reply, ignoring surrounding text.

    Args:
        raw: Model reply, optionally wrapped in a lower-case markdown fence.

    Returns:
        The decoded object.

    Raises:
        json.JSONDecodeError: If the reply has no ``{`` ("No JSON object found") or the
            object is malformed or truncated. Positions in the message refer to the
            fence-stripped text; they reach translation results verbatim.
    """
    return _decode_first_object(strip_json_markdown_fences(raw, case_sensitive=True))


def json_extract_first_object(raw: str) -> Optional[Dict[str, Any]]:
    """Decodes the first JSON object of *raw*, or returns ``None`` when there is none.

    Trailing text, further objects and markdown fences in any case are tolerated.

    Args:
        raw: Model reply.

    Returns:
        The decoded object, or ``None`` when *raw* holds no ``{`` or the object does not
        decode.
    """
    try:
        return _decode_first_object(strip_json_markdown_fences(raw))
    except json.JSONDecodeError:
        return None


def load_brace_span(raw: str) -> Any:
    """Strictly decodes the text from the first ``{`` to the last ``}`` of *raw*.

    The span is greedy, so prose around one object is ignored, but text between two
    objects makes it invalid; raw control characters inside strings are rejected.

    Args:
        raw: Model reply.

    Returns:
        The decoded value; *raw* is decoded whole when it has no ``{ … }`` span.

    Raises:
        json.JSONDecodeError: If the span (or *raw*) is not valid JSON.
    """
    match = _BRACE_SPAN.search(raw)
    return json.loads(match.group(0) if match else raw)


def scan_first_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Returns the first object that decodes at any ``{`` of *raw*, leniently.

    Each ``{`` is tried in turn with ``strict=False`` (raw newlines inside strings are
    accepted), so an unparsable fragment before a valid object is skipped; *raw* is
    decoded whole as a last resort.

    Args:
        raw: Model reply.

    Returns:
        The first decodable object, or ``None`` when *raw* has no ``{`` and decodes whole
        to a non-object value.

    Raises:
        json.JSONDecodeError: If no object decodes (the last decoding error).
    """
    decoder = json.JSONDecoder(strict=False)
    last_error: Optional[json.JSONDecodeError] = None
    for match in re.finditer(r"\{", raw):
        try:
            data, _end = decoder.raw_decode(raw[match.start() :])
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
    try:
        data = json.loads(raw, strict=False)
    except json.JSONDecodeError as exc:
        last_error = exc
    else:
        if isinstance(data, dict):
            return data
    if last_error is not None:
        raise last_error
    return None
