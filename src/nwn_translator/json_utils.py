"""Helpers for extracting JSON objects from LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, cast

_OPENING_FENCE = r"^```(?:json)?\s*"
_CLOSING_FENCE = re.compile(r"\s*```\s*$")


def strip_json_markdown_fences(raw: str, *, case_sensitive: bool = False) -> str:
    """Remove an optional markdown code fence (with or without a ``json`` tag).

    Args:
        raw: Model reply.
        case_sensitive: Recognise the ``json`` tag only in lower case, as the
            provider parsers do; an upper-case ``JSON`` tag then stays in the text.

    Returns:
        The stripped reply.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    return _CLOSING_FENCE.sub("", re.sub(_OPENING_FENCE, "", raw.strip(), flags=flags))


def _decode_first_object(cleaned: str) -> Dict[str, Any]:
    """Decode the JSON object that starts at the first ``{`` of *cleaned*."""
    idx = cleaned.find("{")
    if idx == -1:
        raise json.JSONDecodeError("No JSON object found", cleaned, 0)
    # strict=False accepts raw newlines inside strings, which models often emit.
    value, _end = json.JSONDecoder(strict=False).raw_decode(cleaned, idx)
    return cast(Dict[str, Any], value)


def load_first_json_object(raw: str) -> Dict[str, Any]:
    """Decode the first JSON object of a provider reply, ignoring surrounding text.

    Args:
        raw: Model reply, optionally wrapped in a lower-case markdown fence.

    Returns:
        The decoded object.

    Raises:
        json.JSONDecodeError: When the reply has no ``{`` ("No JSON object found")
            or the object is malformed or truncated. Positions in the message refer
            to the fence-stripped text; they reach translation results verbatim.
    """
    return _decode_first_object(strip_json_markdown_fences(raw, case_sensitive=True))


def json_extract_first_object(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the first JSON object from *raw*, tolerating fences and trailing text.

    Handles trailing text after the object (``Extra data`` from :func:`json.loads`),
    multiple objects (only the first is returned) and markdown fences in any case.

    Args:
        raw: Model reply.

    Returns:
        The decoded object, or ``None`` when *raw* holds no ``{`` or the object
        does not decode.
    """
    try:
        return _decode_first_object(strip_json_markdown_fences(raw))
    except json.JSONDecodeError:
        return None
