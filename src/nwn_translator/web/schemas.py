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
    target_lang: Optional[str] = None


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


class TranslationItem(BaseModel):
    """A single original/translated pair."""

    original: str
    translated: str
    shared_with: List[str] = Field(
        default_factory=list,
        description="Other filenames containing the same original text",
    )


class TranslationFileGroup(BaseModel):
    """Translations grouped by source file."""

    filename: str
    items: List[TranslationItem]


class TranslationsResponse(BaseModel):
    """Structured translation data for the editor."""

    files: List[TranslationFileGroup]


class RebuildRequest(BaseModel):
    """Request to rebuild .mod with edited translations."""

    translations: Dict[str, str]
    #: Must match the language used for translation (drives GFF/NCS encoding).
    target_lang: Optional[str] = None


class RebuildResponse(BaseModel):
    """Response after rebuild completes."""

    result_filename: str


class TaskHistoryItem(BaseModel):
    """One task in the history list."""

    task_id: str
    input_filename: str
    status: str
    created_at: float
    target_lang: Optional[str] = None
    source_lang: Optional[str] = None
    model: Optional[str] = None
    updated_at: Optional[float] = None
    stats: Optional[Dict[str, Any]] = None


class TaskHistoryResponse(BaseModel):
    """List of tasks for a client token."""

    items: List[TaskHistoryItem]
