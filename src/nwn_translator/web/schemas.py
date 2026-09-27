"""Pydantic request and response models of the web API.

A model's docstring, ``Attributes`` included, becomes its schema description in
the OpenAPI document, so the attributes describe the HTTP fields; most fields
carry no ``Field(description=...)`` of their own.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TranslateResponse(BaseModel):
    """Response after starting a translation job.

    Attributes:
        task_id: Id of the new task.
    """

    task_id: str


class TaskStatusResponse(BaseModel):
    """Snapshot of a task's state, polled by the UI.

    Attributes:
        task_id: Task id.
        status: Task status (``pending``, ``translating``, ``completed``, …).
        progress: Weighted overall progress, 0 to 1.
        current_file: File or step the task is working on.
        phase: Pipeline progress phase.
        result_filename: File name of the translated module, once there is one.
        error: Error message of a failed task.
        stats: Run statistics, trimmed for polling.
        target_lang: Target language of the task.
    """

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
    """Body of the provider connectivity check (OpenRouter or POLZA.AI, by key).

    Attributes:
        api_key: API key to try.
        model: Model slug; the provider default when omitted.
        target_lang: Language of the test translation.
        reasoning_effort: Requested reasoning effort.
    """

    api_key: str = Field(..., min_length=1)
    model: Optional[str] = None
    target_lang: str = "russian"
    reasoning_effort: Optional[str] = None


class TestConnectionResponse(BaseModel):
    """Result of the connectivity check.

    Attributes:
        ok: The test translation succeeded.
        translated: The test translation.
        error: Why the check failed.
        model: Model that was asked.
        provider: Provider name.
    """

    ok: bool
    translated: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class DetectProviderRequest(BaseModel):
    """Body for provider detection by API key.

    Attributes:
        api_key: API key to inspect.
    """

    api_key: str = Field(..., min_length=1)


class DetectProviderResponse(BaseModel):
    """Provider inferred from an API key.

    Attributes:
        provider: Provider name (``openrouter``, ``polza``), empty for a blank key.
        label: Human-readable provider name.
    """

    provider: str
    label: str


class ModelReasoningInfo(BaseModel):
    """OpenRouter reasoning metadata for one model slug.

    Attributes:
        supported: The model accepts a reasoning effort.
        mandatory: Reasoning cannot be turned off.
        default_effort: Effort the model uses when none is sent.
        supported_efforts: Efforts the UI may offer, lowest first.
    """

    supported: bool
    mandatory: bool = False
    default_effort: Optional[str] = None
    supported_efforts: List[str] = Field(default_factory=list)


class ModelListItem(BaseModel):
    """One curated model in the UI pool.

    Attributes:
        id: Model slug.
        reasoning: Its reasoning options.
    """

    id: str
    reasoning: ModelReasoningInfo


class ModelsResponse(BaseModel):
    """Curated model list for the UI, with per-model reasoning options.

    Attributes:
        default_model: Model used when the client picks none.
        models: The curated pool, in display order.
    """

    default_model: str
    models: List[ModelListItem]


class ModelLookupResponse(BaseModel):
    """Live OpenRouter catalog lookup for a custom model slug.

    Attributes:
        id: The looked-up slug.
        found: The catalog knows the slug.
        reasoning: Its reasoning options (unsupported when not found).
    """

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
    """Editor rows of one source file.

    Attributes:
        filename: Resource file name.
        items: Editor rows of the file.
    """

    filename: str
    items: List[TranslationItem]


class TranslationsResponse(BaseModel):
    """Editor rows of a task, grouped by source file.

    Attributes:
        files: One group per file, in first-seen order.
    """

    files: List[TranslationFileGroup]


class RebuildEdit(BaseModel):
    """One edited translation, addressed by source file and ``item_id``.

    Attributes:
        file: Resource file name.
        item_id: Item of the edited editor row.
        translated: New translation.
    """

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
    """Response after a rebuild completes.

    Attributes:
        result_filename: File name of the rebuilt module.
    """

    result_filename: str


class TaskHistoryItem(BaseModel):
    """One task in the history list.

    Attributes:
        task_id: Task id.
        input_filename: Name of the uploaded module.
        status: Task status.
        created_at: Unix time of creation.
        target_lang: Target language.
        source_lang: Source language.
        model: Model slug the client requested.
        updated_at: Unix time the task last finished or was rebuilt.
        stats: Run statistics, trimmed for polling.
    """

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
    """Tasks of one client token.

    Attributes:
        items: The tasks, newest first.
    """

    items: List[TaskHistoryItem]


class ConfigResponse(BaseModel):
    """Server-side defaults for the UI.

    Attributes:
        api_key: The server's ``.env`` key, only in local mode.
        default_model: Model used when the client picks none.
    """

    api_key: Optional[str] = None
    default_model: str
