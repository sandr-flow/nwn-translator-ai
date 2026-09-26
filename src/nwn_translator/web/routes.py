"""HTTP routes of the web API (all under ``/api``).

Handlers validate requests and shape responses; job execution, rebuilds and task
lifecycle live in :class:`~nwn_translator.web.task_manager.TaskManager`, the
editor row model in :mod:`~nwn_translator.web.editor`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..ai_providers import (
    OpenRouterProvider,
    PolzaProvider,
    create_provider,
    detect_provider_from_key,
)
from ..ai_providers.openrouter_models import (
    FALLBACK as OPENROUTER_REASONING_FALLBACK,
    is_valid_model_slug,
    lookup_model_reasoning,
    reasoning_payload,
    refresh_catalog,
)
from ..config import (
    max_concurrent_from_environment,
    parse_reasoning_effort,
    target_lang_supported_for_nwn_injection,
)
from . import editor
from .database import (
    compact_stats_for_api,
    decode_stats,
    get_translations_by_task,
    list_tasks_by_token,
)
from .schemas import (
    ConfigResponse,
    DetectProviderRequest,
    DetectProviderResponse,
    ModelListItem,
    ModelLookupResponse,
    ModelReasoningInfo,
    ModelsResponse,
    RebuildRequest,
    RebuildResponse,
    TaskHistoryItem,
    TaskHistoryResponse,
    TaskStatusResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    TranslateResponse,
    TranslationsResponse,
)
from .task_manager import JobParams, TaskManager, TranslationTask, get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

#: Largest accepted upload. The bundled nginx enforces the same limit
#: (``client_max_body_size 50m``) in front of the app.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_READ_CHUNK = 1024 * 1024

_MODULE_SUFFIXES = (".mod", ".erf", ".hak")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

_IP_BUSY_DETAIL = "Уже выполняется перевод с вашего IP. Дождитесь завершения."

#: Why a language is refused: NWN stores strings in a single-byte Windows code page.
_UNSUPPORTED_LANG_DETAIL = (
    "Недоступно для модулей NWN: строки записываются в однобайтовую кодировку Windows "
    "(зависит от языка); китайский, японский, корейский и турецкий в игре не отображаются. "
    "Выберите другой язык."
)

#: Friendly labels for the providers exposed to the UI.
_PROVIDER_LABELS: dict[str, str] = {
    OpenRouterProvider.PROVIDER_NAME: OpenRouterProvider.PROVIDER_LABEL,
    PolzaProvider.PROVIDER_NAME: PolzaProvider.PROVIDER_LABEL,
}


def upload_too_large(max_bytes: int) -> HTTPException:
    """Return the 413 error for an upload over *max_bytes*."""
    return HTTPException(
        status_code=413,
        detail=f"Файл слишком большой (максимум {max_bytes // (1024 * 1024)} МБ)",
    )


async def _stream_upload_to_file(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    """Copy the upload to *dest* in chunks; a partial file is removed on failure.

    Raises:
        HTTPException: 413 when the upload exceeds *max_bytes*.
    """
    total = 0
    try:
        with dest.open("wb") as out:
            while chunk := await upload.read(_READ_CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    raise upload_too_large(max_bytes)
                out.write(chunk)
    except BaseException:
        with contextlib.suppress(OSError):
            dest.unlink(missing_ok=True)
        raise


def _client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Trusts ``X-Forwarded-For`` only when the direct peer is listed in
    ``NWN_WEB_TRUSTED_PROXIES`` (comma-separated IPs); otherwise uses the direct
    client address to prevent spoofing.

    Args:
        request: Incoming request.

    Returns:
        Client IP string, or ``"unknown"`` if not determinable.
    """
    trusted_proxies = os.environ.get("NWN_WEB_TRUSTED_PROXIES", "").strip()
    if trusted_proxies:
        trusted = {p.strip() for p in trusted_proxies.split(",") if p.strip()}
        direct_ip = request.client.host if request.client else None
        if direct_ip and direct_ip in trusted:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _client_token(request: Request) -> str:
    """Return the anonymous client token from the ``X-Client-Token`` header.

    Falls back to the ``client_token`` query parameter because plain browser
    navigations (download links) cannot send custom headers.
    """
    header = (request.headers.get("x-client-token") or "").strip()
    if header:
        return header
    return (request.query_params.get("client_token") or "").strip()


def _job_from_form(
    *,
    api_key: str,
    target_lang: str,
    source_lang: str,
    model: Optional[str],
    preserve_tokens: bool,
    use_context: bool,
    max_concurrent_requests: Optional[int],
    player_gender: str,
    reasoning_effort: Optional[str],
) -> JobParams:
    """Validate and normalize the job fields of a translate request.

    Args:
        api_key: Provider API key.
        target_lang: Target language.
        source_lang: Source language or ``"auto"``.
        model: Model slug, if any.
        preserve_tokens: Protect NWN tokens.
        use_context: Build world context and glossary.
        max_concurrent_requests: Parallel requests, at most the server's
            ``NWN_TRANSLATE_MAX_CONCURRENT`` (also the value when omitted): the
            number sizes the job's thread pools and semaphores, so a client may
            lower it but not raise it.
        player_gender: Player gender for grammatical agreement.
        reasoning_effort: Provider reasoning effort, if any.

    Returns:
        The job settings.

    Raises:
        HTTPException: 400 for a language NWN cannot display or an unknown
            reasoning effort.
    """
    target = target_lang.strip()
    if not target_lang_supported_for_nwn_injection(target):
        raise HTTPException(status_code=400, detail=f"Целевой язык: {_UNSUPPORTED_LANG_DETAIL}")
    source = source_lang.strip() or "auto"
    if source.lower() != "auto" and not target_lang_supported_for_nwn_injection(source):
        raise HTTPException(status_code=400, detail=f"Исходный язык: {_UNSUPPORTED_LANG_DETAIL}")
    try:
        effort = parse_reasoning_effort(reasoning_effort)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    concurrency = max_concurrent_from_environment()
    if max_concurrent_requests is not None:
        concurrency = min(max(1, max_concurrent_requests), concurrency)
    return JobParams(
        api_key=api_key.strip(),
        target_lang=target,
        source_lang=source,
        model=model.strip() if model else None,
        preserve_tokens=preserve_tokens,
        use_context=use_context,
        max_concurrent_requests=concurrency,
        player_gender=player_gender.strip() or "male",
        reasoning_effort=effort,
    )


def require_task_owner(
    task_id: str,
    request: Request,
    tm: TaskManager = Depends(get_task_manager),
) -> TranslationTask:
    """Resolve the path's task and enforce that the caller owns it.

    When the task has an owner (non-empty ``client_token``), the request's token
    must match it. Tasks without an owner stay accessible.

    Raises:
        HTTPException: 400 for a malformed task id, 404 if the task does not
            exist, 403 if the token does not match the owner.
    """
    if not _UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Неверный формат task_id")
    task = tm.find(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    owner = task.client_token.strip()
    if owner and _client_token(request) != owner:
        raise HTTPException(status_code=403, detail="Нет доступа к этой задаче")
    return task


@router.get("/health")
async def health(tm: TaskManager = Depends(get_task_manager)) -> dict:
    """Liveness check for Docker and load balancers.

    ``active_tasks`` is the number of unfinished translation jobs; the deploy
    script polls it and recreates the container only when it reaches zero.
    """
    return {"status": "ok", "active_tasks": tm.active_task_count()}


@router.post("/translate", response_model=TranslateResponse)
async def start_translate(
    request: Request,
    tm: TaskManager = Depends(get_task_manager),
    file: UploadFile = File(...),
    api_key: str = Form(...),
    target_lang: str = Form(...),
    source_lang: str = Form("auto"),
    model: Optional[str] = Form(None),
    preserve_tokens: bool = Form(True),
    use_context: bool = Form(True),
    max_concurrent_requests: Optional[int] = Form(None),
    player_gender: str = Form("male"),
    reasoning_effort: Optional[str] = Form(None),
) -> TranslateResponse:
    """Accept a .mod/.erf/.hak upload and start translating it in the background.

    Raises:
        HTTPException: 429 while the client IP has a running job, 400 for an
            invalid file name or job field, 413 for an oversized upload.
    """
    ip = _client_ip(request)
    if tm.active_task_id_for_ip(ip):
        raise HTTPException(status_code=429, detail=_IP_BUSY_DETAIL)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")
    if Path(file.filename).suffix.lower() not in _MODULE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Допустимы только файлы .mod, .erf или .hak")
    job = _job_from_form(
        api_key=api_key,
        target_lang=target_lang,
        source_lang=source_lang,
        model=model,
        preserve_tokens=preserve_tokens,
        use_context=use_context,
        max_concurrent_requests=max_concurrent_requests,
        player_gender=player_gender,
        reasoning_effort=reasoning_effort,
    )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        with contextlib.suppress(ValueError):
            if int(content_length) > MAX_UPLOAD_BYTES:
                raise upload_too_large(MAX_UPLOAD_BYTES)

    task = tm.create_task(
        ip,
        file.filename,
        client_token=_client_token(request),
        target_lang=job.target_lang,
        source_lang=job.source_lang,
        model=job.model,
    )
    # Claim the one-job-per-IP slot atomically before the (slow) upload; the
    # check at the top of the handler is only a fast path and is racy on its own.
    if not tm.try_register_active(ip, task.task_id):
        tm.discard_task(task.task_id)
        raise HTTPException(status_code=429, detail=_IP_BUSY_DETAIL)
    input_path = tm.workspace_for_task(task.task_id) / Path(file.filename).name
    try:
        await _stream_upload_to_file(file, input_path, MAX_UPLOAD_BYTES)
    except BaseException:
        tm.release_active(ip, task.task_id)
        tm.discard_task(task.task_id)
        raise

    tm.start(task, job, input_path)
    return TranslateResponse(task_id=task.task_id)


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def task_status(
    task: TranslationTask = Depends(require_task_owner),
) -> TaskStatusResponse:
    """Return a snapshot of the task state."""
    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_file=task.current_file,
        phase=task.phase,
        result_filename=task.result_path.name if task.result_path else None,
        error=task.error,
        stats=compact_stats_for_api(task.stats),
        target_lang=task.target_lang,
    )


@router.get("/tasks/{task_id}/download")
async def download_result(
    task: TranslationTask = Depends(require_task_owner),
) -> FileResponse:
    """Download the translated module of a completed task."""
    if task.status != "completed" or not task.result_path or not task.result_path.is_file():
        raise HTTPException(status_code=400, detail="Файл результата ещё не готов")
    return FileResponse(
        path=task.result_path,
        filename=task.result_path.name,
        media_type="application/octet-stream",
    )


@router.get("/tasks/{task_id}/log")
async def download_log(
    task: TranslationTask = Depends(require_task_owner),
) -> StreamingResponse:
    """Download the task's translation rows as JSONL."""
    rows = get_translations_by_task(task.task_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Лог недоступен")

    def generate() -> Iterator[str]:
        for row in rows:
            yield json.dumps(row, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/jsonl",
        headers={"Content-Disposition": "attachment; filename=translation_log.jsonl"},
    )


@router.get("/tasks/{task_id}/translations", response_model=TranslationsResponse)
async def get_translations(
    task: TranslationTask = Depends(require_task_owner),
) -> TranslationsResponse:
    """Return the task's translations as editor rows grouped by source file."""
    return TranslationsResponse(files=editor.group_rows(get_translations_by_task(task.task_id)))


@router.post("/tasks/{task_id}/rebuild", response_model=RebuildResponse)
async def rebuild_task(
    body: RebuildRequest,
    task: TranslationTask = Depends(require_task_owner),
    tm: TaskManager = Depends(get_task_manager),
) -> RebuildResponse:
    """Rebuild the module with the editor's edits (no provider calls).

    Raises:
        HTTPException: 400 when the task is not completed or its files are gone,
            500 when the rebuild fails.
    """
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="Задача ещё не завершена")
    if not task.extract_dir or not task.extract_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Извлечённые файлы модуля недоступны (возможно, были очищены)",
        )
    if task.result_path is None:
        raise HTTPException(status_code=400, detail="Task has no result path")

    target_lang = (body.target_lang or "").strip() or task.target_lang
    try:
        await asyncio.to_thread(tm.rebuild, task, body.edits, target_lang)
    except Exception as e:
        logger.exception("Rebuild failed for task %s", task.task_id)
        raise HTTPException(status_code=500, detail=f"Ошибка сборки: {e}") from e
    return RebuildResponse(result_filename=task.result_path.name)


@router.get("/history", response_model=TaskHistoryResponse)
async def task_history(request: Request) -> TaskHistoryResponse:
    """Return the translation history of the client identified by its token."""
    token = _client_token(request)
    if not token:
        return TaskHistoryResponse(items=[])
    return TaskHistoryResponse(
        items=[
            TaskHistoryItem(
                task_id=row["task_id"],
                input_filename=row["input_filename"],
                status=row["status"],
                created_at=row["created_at"],
                target_lang=row["target_lang"],
                source_lang=row["source_lang"],
                model=row["model"],
                updated_at=row["updated_at"],
                stats=compact_stats_for_api(decode_stats(row["stats"])),
            )
            for row in list_tasks_by_token(token)
        ]
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task: TranslationTask = Depends(require_task_owner),
    tm: TaskManager = Depends(get_task_manager),
) -> dict:
    """Stop a running task at its next checkpoint and free the client's slot.

    Progress is lost: in-flight provider calls finish, but their results are
    discarded.
    """
    if task.is_finished():
        return {"ok": True, "status": task.status}
    tm.cancel(task)
    return {"ok": True, "status": "cancelling"}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task: TranslationTask = Depends(require_task_owner),
    tm: TaskManager = Depends(get_task_manager),
) -> dict:
    """Delete a task from history."""
    tm.delete(task.task_id)
    return {"ok": True}


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(body: TestConnectionRequest) -> TestConnectionResponse:
    """Verify an API key and model with a tiny translation."""
    text = "Hello, welcome to my module!"
    provider_name = detect_provider_from_key(body.api_key)
    try:
        try:
            reff = parse_reasoning_effort(body.reasoning_effort)
        except ValueError as e:
            return TestConnectionResponse(ok=False, error=str(e), provider=provider_name)
        provider = create_provider(body.api_key.strip(), body.model, reasoning_effort=reff)
        result = await asyncio.to_thread(provider.translate, text, "english", body.target_lang)
        model = getattr(provider, "model", None) or OpenRouterProvider.DEFAULT_MODEL
        if result.success:
            return TestConnectionResponse(
                ok=True,
                translated=result.translated,
                model=model,
                provider=provider.get_provider_name(),
            )
        return TestConnectionResponse(
            ok=False,
            error=result.error or "Unknown error",
            model=model,
            provider=provider.get_provider_name(),
        )
    except Exception as e:
        logger.warning("test-connection failed: %s", e)
        return TestConnectionResponse(ok=False, error=str(e), provider=provider_name)


@router.post("/detect-provider", response_model=DetectProviderResponse)
async def detect_provider(body: DetectProviderRequest) -> DetectProviderResponse:
    """Infer the provider from an API key prefix (no network calls)."""
    name = detect_provider_from_key(body.api_key)
    return DetectProviderResponse(provider=name, label=_PROVIDER_LABELS.get(name, ""))


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """Return the curated model pool with per-model OpenRouter reasoning options."""
    catalog = await asyncio.to_thread(refresh_catalog)
    items = [
        ModelListItem(
            id=slug,
            reasoning=ModelReasoningInfo(
                **reasoning_payload(catalog.get(slug) or OPENROUTER_REASONING_FALLBACK.get(slug))
            ),
        )
        for slug in OpenRouterProvider.POPULAR_MODELS
    ]
    return ModelsResponse(default_model=OpenRouterProvider.DEFAULT_MODEL, models=items)


@router.get("/models/lookup", response_model=ModelLookupResponse)
async def lookup_model(slug: str = Query(..., min_length=1, max_length=200)) -> ModelLookupResponse:
    """Look up reasoning options for a custom OpenRouter model slug."""
    key = slug.strip()
    if not is_valid_model_slug(key):
        raise HTTPException(status_code=400, detail="Invalid model slug")
    found, info = await asyncio.to_thread(lookup_model_reasoning, key)
    return ModelLookupResponse(
        id=key,
        found=found,
        reasoning=ModelReasoningInfo(**reasoning_payload(info if found else None)),
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Return server-side UI defaults: the default model and, locally, the ``.env`` key.

    The key is exposed solely in local mode (the process bound to loopback by
    ``python -m nwn_translator.web``). A deployed instance never hands it out:
    this is a BYOK product, so remote users supply their own key.
    """
    api_key = None
    if os.environ.get("NWN_WEB_LOCAL_MODE") == "1":
        api_key = os.environ.get("NWN_TRANSLATE_API_KEY", "").strip() or None
    return ConfigResponse(api_key=api_key, default_model=OpenRouterProvider.DEFAULT_MODEL)
