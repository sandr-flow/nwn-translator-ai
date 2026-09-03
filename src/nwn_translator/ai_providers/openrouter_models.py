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

from ..config import OPENROUTER_REASONING_EFFORT_VALUES

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CATALOG_TTL_SECONDS = 6 * 3600

#: OpenRouter lists efforts highest-first; UI and Off-clamp use lowest-first.
EFFORT_RANK: Tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")

_lock = threading.Lock()
_catalog: Optional[Dict[str, "ModelReasoning"]] = None
_catalog_at: float = 0.0
_catalog_live: bool = False


@dataclass(frozen=True)
class ModelReasoning:
    """Reasoning parameter metadata for one OpenRouter model slug."""

    supported: bool
    mandatory: bool = False
    default_effort: Optional[str] = None
    #: ``None`` means the catalog did not send an allowlist (all efforts ok).
    supported_efforts: Optional[Tuple[str, ...]] = None


#: Snapshot of the curated pool. Used when the live catalog is unavailable.
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
    """True for OpenRouter-style ``author/slug`` (optional ``:variant`` suffix)."""
    return bool(slug) and bool(_SLUG_RE.match(slug.strip()))


def _parse_entry(entry: dict) -> ModelReasoning:
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
    parsed: Dict[str, ModelReasoning] = {}
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if isinstance(mid, str) and mid:
            parsed[mid] = _parse_entry(entry)
    return parsed


def reset_catalog_cache() -> None:
    """Drop the in-memory catalog (tests)."""
    global _catalog, _catalog_at, _catalog_live
    with _lock:
        _catalog = None
        _catalog_at = 0.0
        _catalog_live = False


def refresh_catalog(*, force: bool = False) -> Dict[str, ModelReasoning]:
    """Return the model→reasoning map, fetching OpenRouter when the cache is stale."""
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
        with httpx.Client(timeout=15.0) as client:
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
    """Reasoning metadata if the slug is already known; no network.

    Live catalog hits return ``None`` for unknown slugs. Before a successful
    fetch, only :data:`FALLBACK` (the curated pool) is known.
    """
    key = (slug or "").strip()
    if not key:
        return None
    with _lock:
        if _catalog_live and _catalog is not None:
            return _catalog.get(key)
    return FALLBACK.get(key)


def lookup_model_reasoning(slug: str) -> Tuple[bool, Optional[ModelReasoning]]:
    """Look up *slug* in the catalog, fetching if needed.

    Returns ``(found, info)``. ``found`` is False when the slug is absent.
    """
    key = (slug or "").strip()
    if not key:
        return False, None
    catalog = refresh_catalog()
    if key in catalog:
        return True, catalog[key]
    with _lock:
        live = _catalog_live
    if not live:
        catalog = refresh_catalog(force=True)
        if key in catalog:
            return True, catalog[key]
    return False, None


def allowed_efforts(info: ModelReasoning) -> List[str]:
    """Effort values the UI may offer, lowest first. Empty if unsupported."""
    if not info.supported:
        return []
    if info.supported_efforts is None:
        ranked = [e for e in EFFORT_RANK if e in OPENROUTER_REASONING_EFFORT_VALUES]
    else:
        known = {str(e) for e in info.supported_efforts}
        ranked = [e for e in EFFORT_RANK if e in known]
        ranked.extend(
            e
            for e in info.supported_efforts
            if e not in ranked and e in OPENROUTER_REASONING_EFFORT_VALUES
        )
    if info.mandatory:
        ranked = [e for e in ranked if e != "none"]
    return ranked


def reasoning_payload(info: Optional[ModelReasoning]) -> dict:
    """JSON-ready dict for the web API."""
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
    """Map a requested effort onto a value the model accepts.

    Unknown slugs pass *requested* through. Unsupported models omit the field
    (``None``). ``none`` / missing request becomes the lowest allowed effort —
    never omit on a reasoning-capable model (omit enables the catalog default,
    e.g. medium on Gemini 3.8 Flash).
    """
    info = get_known_reasoning(model)
    if info is None:
        return requested
    if not info.supported:
        return None
    allowed = allowed_efforts(info)
    if not allowed:
        return None
    want = (requested or "").strip().lower() or "none"
    if want == "none" or want not in allowed:
        return allowed[0]
    return want
