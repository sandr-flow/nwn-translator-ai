"""OpenRouter model catalog: reasoning metadata for the UI and request clamp.

The public ``GET /api/v1/models`` payload includes a ``reasoning`` object
(mandatory flag, default effort, supported efforts). Recommended models have a
static fallback so the UI works when the catalog fetch fails. Custom slugs are
looked up in the live catalog.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

from ..config import REASONING_EFFORTS

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CATALOG_TTL_SECONDS = 6 * 3600
_FETCH_TIMEOUT_SECONDS = 15.0

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")

_lock = threading.Lock()
_catalog: Optional[Dict[str, "ModelReasoning"]] = None
_catalog_at: float = 0.0
_catalog_live: bool = False


@dataclass(frozen=True)
class ModelReasoning:
    """Reasoning parameter metadata for one OpenRouter model slug.

    Attributes:
        supported: The model accepts a ``reasoning`` field.
        mandatory: Reasoning cannot be turned off (``none`` is not accepted).
        default_effort: Effort the model uses when the field is omitted.
        supported_efforts: Accepted efforts as listed by the catalog (highest
            first); ``None`` when the catalog sent no allowlist (all efforts).
    """

    supported: bool
    mandatory: bool = False
    default_effort: Optional[str] = None
    supported_efforts: Optional[Tuple[str, ...]] = None


#: Snapshot of the curated pool, offered by the web UI in this order. Used when
#: the live catalog is unavailable.
FALLBACK: Dict[str, ModelReasoning] = {
    "google/gemini-3.1-flash-lite": ModelReasoning(
        supported=True,
        mandatory=False,
        default_effort="minimal",
        supported_efforts=("high", "medium", "low", "minimal"),
    ),
    "google/gemini-3.5-flash-lite": ModelReasoning(
        supported=True,
        mandatory=True,
        default_effort="minimal",
        supported_efforts=("high", "medium", "low", "minimal"),
    ),
    "google/gemini-3.8-flash": ModelReasoning(
        supported=True,
        mandatory=True,
        default_effort="medium",
        supported_efforts=("high", "medium", "low"),
    ),
    "openai/gpt-5.6-luna": ModelReasoning(
        supported=True,
        mandatory=False,
        default_effort="medium",
        supported_efforts=("max", "xhigh", "high", "medium", "low", "none"),
    ),
}


def is_valid_model_slug(slug: str) -> bool:
    """Tells whether *slug* looks like an OpenRouter model id.

    Args:
        slug: Candidate ``author/model`` slug, optionally with a ``:variant`` suffix.

    Returns:
        ``True`` for a well-formed slug.
    """
    return bool(slug) and bool(_SLUG_RE.match(slug.strip()))


def _parse_entry(entry: dict) -> ModelReasoning:
    """Reads the ``reasoning`` object of one catalog entry."""
    raw = entry.get("reasoning")
    if not isinstance(raw, dict):
        return ModelReasoning(supported=False)
    efforts_raw = raw.get("supported_efforts")
    efforts: Optional[Tuple[str, ...]]
    if efforts_raw is None:
        efforts = None
    else:
        efforts = tuple(str(e) for e in efforts_raw)
    default = raw.get("default_effort")
    default_effort = str(default) if default else None
    return ModelReasoning(
        supported=True,
        mandatory=bool(raw.get("mandatory")),
        default_effort=default_effort,
        supported_efforts=efforts,
    )


def _parse_catalog(payload: dict) -> Dict[str, ModelReasoning]:
    """Maps every model id of a ``/models`` payload to its reasoning metadata."""
    parsed: Dict[str, ModelReasoning] = {}
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if isinstance(mid, str) and mid:
            parsed[mid] = _parse_entry(entry)
    return parsed


def reset_catalog_cache() -> None:
    """Drops the in-memory catalog (tests)."""
    global _catalog, _catalog_at, _catalog_live
    with _lock:
        _catalog = None
        _catalog_at = 0.0
        _catalog_live = False


def refresh_catalog(*, force: bool = False) -> Dict[str, ModelReasoning]:
    """Returns the model to reasoning map, fetching OpenRouter when the cache is stale.

    Args:
        force: Fetch even when a live catalog younger than the TTL is cached.

    Returns:
        The live catalog, or :data:`FALLBACK` when no fetch has succeeded yet.
    """
    global _catalog, _catalog_at, _catalog_live
    now = time.monotonic()
    with _lock:
        if (
            not force
            and _catalog_live
            and _catalog is not None
            and (now - _catalog_at) < _CATALOG_TTL_SECONDS
        ):
            return _catalog
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_SECONDS) as client:
            response = client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()
            parsed = _parse_catalog(response.json())
        if not parsed:
            raise ValueError("OpenRouter models payload had no entries")
        with _lock:
            _catalog = parsed
            _catalog_at = time.monotonic()
            _catalog_live = True
        return parsed
    except Exception:
        logger.warning("Failed to fetch OpenRouter models catalog", exc_info=True)
        with _lock:
            if _catalog is None:
                _catalog = dict(FALLBACK)
                _catalog_at = time.monotonic()
                _catalog_live = False
            return _catalog


def get_known_reasoning(slug: str) -> Optional[ModelReasoning]:
    """Returns reasoning metadata for an already known slug, without network access.

    Args:
        slug: Model slug.

    Returns:
        The live catalog entry once a fetch has succeeded, before that the
        :data:`FALLBACK` entry; ``None`` for unknown slugs.
    """
    key = (slug or "").strip()
    if not key:
        return None
    with _lock:
        if _catalog_live and _catalog is not None:
            return _catalog.get(key)
    return FALLBACK.get(key)


def lookup_model_reasoning(slug: str) -> Tuple[bool, Optional[ModelReasoning]]:
    """Looks up *slug* in the catalog, fetching it when needed.

    Args:
        slug: Model slug.

    Returns:
        ``(found, info)``; ``(False, None)`` when the slug is absent from the
        catalog (from :data:`FALLBACK` while OpenRouter is unreachable).
    """
    key = (slug or "").strip()
    if not key:
        return False, None
    info = refresh_catalog().get(key)
    return info is not None, info


def allowed_efforts(info: ModelReasoning) -> List[str]:
    """Returns the efforts a model accepts, lowest first.

    Args:
        info: The model's reasoning metadata.

    Returns:
        Known efforts from the model's allowlist (all when it has none), without
        ``none`` when reasoning is mandatory; empty when reasoning is unsupported.
    """
    if not info.supported:
        return []
    accepted = REASONING_EFFORTS if info.supported_efforts is None else info.supported_efforts
    return [e for e in REASONING_EFFORTS if e in accepted and not (info.mandatory and e == "none")]


def reasoning_payload(info: Optional[ModelReasoning]) -> dict:
    """Describes a model's reasoning options for the web API.

    Args:
        info: Reasoning metadata, or ``None`` for an unknown model.

    Returns:
        JSON-ready ``supported`` / ``mandatory`` / ``default_effort`` /
        ``supported_efforts`` mapping.
    """
    if info is None or not info.supported:
        return {
            "supported": False,
            "mandatory": False,
            "default_effort": None,
            "supported_efforts": [],
        }
    return {
        "supported": True,
        "mandatory": info.mandatory,
        "default_effort": info.default_effort,
        "supported_efforts": allowed_efforts(info),
    }


def resolve_reasoning_effort(model: str, requested: Optional[str]) -> Optional[str]:
    """Maps a requested effort onto a value the model accepts.

    A reasoning-capable model never gets the field omitted: omission enables the
    catalog default (e.g. medium on Gemini 3.8 Flash), so ``none``, a missing or a
    disallowed request becomes the lowest allowed effort.

    Args:
        model: Model slug.
        requested: Requested effort or ``None``.

    Returns:
        The effort to send, *requested* unchanged for slugs the catalog does not
        know, or ``None`` (omit the field) when the model has no reasoning.
    """
    info = get_known_reasoning(model)
    if info is None:
        return requested
    allowed = allowed_efforts(info)
    if not allowed:
        return None
    want = (requested or "").strip().lower() or "none"
    if want == "none" or want not in allowed:
        return allowed[0]
    return want
