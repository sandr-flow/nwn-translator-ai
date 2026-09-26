"""Pydantic request and response models of the web API."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TranslateResponse(BaseModel):
    """Response after starting a translation job."""

    task_id: str


class TaskStatusResponse(BaseModel):
    """Snapshot of a task's state, polled by the UI."""

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
    """Body of the provider connectivity check (OpenRouter or POLZA.AI, by key)."""

    api_key: str = Field(..., min_length=1)
    model: Optional[str] = None
    target_lang: str = "russian"
    reasoning_effort: Optional[str] = None


class TestConnectionResponse(BaseModel):
    """Result of the connectivity check."""

    ok: bool
    translated: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class DetectProviderRequest(BaseModel):
    """Body for provider detection by API key."""

    api_key: str = Field(..., min_length=1)


class DetectProviderResponse(BaseModel):
    """Provider inferred from an API key."""

    provider: str
    label: str


class ModelReasoningInfo(BaseModel):
    """OpenRouter reasoning metadata for one model slug."""

    supported: bool
    mandatory: bool = False
    default_effort: Optional[str] = None
    supported_efforts: List[str] = Field(default_factory=list)


class ModelListItem(BaseModel):
    """One curated model in the UI pool."""

    id: str
    reasoning: ModelReasoningInfo


class ModelsResponse(BaseModel):
    """Curated model list for the UI, with per-model reasoning options."""

    default_model: str
    models: List[ModelListItem]


class ModelLookupResponse(BaseModel):
    """Live OpenRouter catalog lookup for a custom model slug."""

    id: str
    found: bool
    reasoning: ModelReasoningInfo


class DialogSpeaker(BaseModel):
    """Who speaks a dialog line.

    Attributes:
        kind: ``npc`` (a creature, placeable or door), ``player`` (a reply) or
            ``owner_unknown`` (the dialog owner, when no scanned object uses
            this dialog).
        name: Object name; several are joined with " / " (up to three, then "+N").
        tag: Object tag, joined like ``name``.
    """

    kind: Literal["npc", "player", "owner_unknown"]
    name: str = ""
    tag: str = ""


class TranslationItem(BaseModel):
    """One editor row: an original with its translation.

    Attributes:
        original: Source text.
        translated: Current translation.
        item_id: Stable per-file identifier used to address this item on rebuild.
        duplicate_item_ids: Other items the row stands for.
        failed: The model was asked to translate the line and its answer was rejected.
        shared_with: Other files containing the same original.
        speaker: Speaker of a ``.dlg`` line; ``None`` for other files.
    """

    original: str
    translated: str
    item_id: str = ""
    duplicate_item_ids: List[str] = Field(
        default_factory=list,
        description="Other items of this file with the same original and translation; "
        "an edit of this row applies to them too",
    )
    failed: bool = False
    shared_with: List[str] = Field(
        default_factory=list,
        description="Other filenames containing the same original text",
    )
    speaker: Optional[DialogSpeaker] = None


class TranslationFileGroup(BaseModel):
    """Editor rows of one source file."""

    filename: str
    items: List[TranslationItem]


class TranslationsResponse(BaseModel):
    """Editor rows of a task, grouped by source file."""

    files: List[TranslationFileGroup]


class RebuildEdit(BaseModel):
    """One edited translation, addressed by source file and ``item_id``."""

    file: str
    item_id: str
    translated: str


class RebuildRequest(BaseModel):
    """Request to rebuild the module with edited translations.

    Edits are addressed by ``(file, item_id)`` so two identical originals in
    different files (or different dialog nodes) can be edited independently.

    Attributes:
        edits: Edited translations.
        target_lang: Language used for the translation (drives the GFF/NCS
            string encoding); the task's language when omitted.
    """

    edits: List[RebuildEdit] = Field(default_factory=list)
    target_lang: Optional[str] = None


class RebuildResponse(BaseModel):
    """Response after a rebuild completes."""

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
    """Tasks of one client token, newest first."""

    items: List[TaskHistoryItem]


class ConfigResponse(BaseModel):
    """Server-side defaults for the UI.

    Attributes:
        api_key: The server's ``.env`` key, only in local mode.
        default_model: Model used when the client picks none.
    """

    api_key: Optional[str] = None
    default_model: str
