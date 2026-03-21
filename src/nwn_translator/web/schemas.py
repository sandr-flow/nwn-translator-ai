"""Pydantic schemas for the web API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TranslateResponse(BaseModel):
    """Response after starting a translation job."""

    task_id: str


class TaskStatusResponse(BaseModel):
    """JSON snapshot of task state (polling / initial load)."""

    task_id: str
    status: str
    progress: float = 0.0
    current_file: Optional[str] = None
    phase: Optional[str] = None
    result_filename: Optional[str] = None
    error: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None


class TestConnectionRequest(BaseModel):
    """Body for OpenRouter connectivity check."""

    api_key: str = Field(..., min_length=1)
    model: Optional[str] = None
    target_lang: str = "russian"


class TestConnectionResponse(BaseModel):
    """Result of connectivity check."""

    ok: bool
    translated: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None


class ModelsResponse(BaseModel):
    """Curated model list for the UI."""

    default_model: str
    models: List[str]


class ErrorResponse(BaseModel):
    """Generic error payload."""

    detail: str
