"""Helpers for extracting JSON objects from LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def strip_json_markdown_fences(raw: str) -> str:
    """Remove optional ``` / ```json wrappers."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned


def json_extract_first_object(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the first JSON object from *raw* using :meth:`json.JSONDecoder.raw_decode`.

    Handles trailing text after the object (``Extra data`` from :func:`json.loads`),
    multiple objects (only the first is returned), and markdown fences.

    Returns:
        A dict if a JSON object was decoded, else ``None`` (no ``{``, not an object,
        or :exc:`json.JSONDecodeError`).
    """
    cleaned = strip_json_markdown_fences(raw)
    decoder = json.JSONDecoder()
    idx = cleaned.find("{")
    if idx == -1:
        return None
    try:
        value, _end = decoder.raw_decode(cleaned, idx)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value
