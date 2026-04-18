"""HTTP and SSE routes for the web API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from queue import Empty
from typing import Optional

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ..config import (
    max_concurrent_from_environment,
    parse_reasoning_effort,
    target_lang_supported_for_nwn_injection,
)
from fastapi.responses import FileResponse, StreamingResponse

from ..ai_providers import OpenRouterProvider, create_provider
from .deps import web_task_manager
from .database import (
    delete_task_row,
    get_ncs_translation_map_by_task,
    get_task_row,
    get_translation_map_by_task,
    get_translations_by_task,
    list_tasks_by_token,
    update_task_row,
)
from .schemas import (
    ConfigResponse,
    ModelsResponse,
    RebuildRequest,
    RebuildResponse,
    TaskHistoryItem,
    TaskHistoryResponse,
    TaskStatusResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    TranslateResponse,
    TranslationFileGroup,
    TranslationItem,
    TranslationsResponse,
)
from .task_manager import MAX_UPLOAD_BYTES, TaskManager, TranslationTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_READ_CHUNK = 1024 * 1024


async def _stream_upload_to_file(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    """Write upload body to *dest* in chunks; enforce *max_bytes* total size."""
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await upload.read(_READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл слишком большой (максимум {max_bytes // (1024 * 1024)} МБ)",
                    )
                out.write(chunk)
    except HTTPException:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
    except Exception:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise


@router.get("/health")
async def health() -> dict:
    """Liveness check for Docker / балансировщики."""
    return {"status": "ok"}


def _client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Trusts ``X-Forwarded-For`` only when ``NWN_WEB_TRUSTED_PROXIES`` is set
    (comma-separated list of IPs/CIDRs).  Otherwise uses the direct client
    address to prevent spoofing.

    Args:
        request: Incoming FastAPI/Starlette request.

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
    """Extract the anonymous client token from ``X-Client-Token`` header."""
    return (request.headers.get("x-client-token") or "").strip()


@router.post("/translate", response_model=TranslateResponse)
async def start_translate(
    request: Request,
    tm: TaskManager = Depends(web_task_manager),
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
    """Accept a .mod/.erf/.hak upload and start translation in the background."""
    ip = _client_ip(request)
    active = tm.active_task_id_for_ip(ip)
    if active:
        raise HTTPException(
            status_code=429,
            detail="Уже выполняется перевод с вашего IP. Дождитесь завершения.",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".mod", ".erf", ".hak"):
        raise HTTPException(
            status_code=400,
            detail="Допустимы только файлы .mod, .erf или .hak",
        )

    _cp1251_lang_error = (
        "Недоступно для модулей NWN: строки записываются в однобайтовую кодировку Windows "
        "(зависит от языка); китайский, японский и корейский в игре не отображаются. "
        "Выберите другой язык."
    )
    tl_norm = target_lang.strip()
    if not target_lang_supported_for_nwn_injection(tl_norm):
        raise HTTPException(status_code=400, detail=f"Целевой язык: {_cp1251_lang_error}")

    sl_norm = (source_lang or "").strip() or "auto"
    if sl_norm.lower() != "auto" and not target_lang_supported_for_nwn_injection(sl_norm):
        raise HTTPException(status_code=400, detail=f"Исходный язык: {_cp1251_lang_error}")

    try:
        reasoning_effort_norm = parse_reasoning_effort(reasoning_effort)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл слишком большой (максимум {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ)",
                )
        except ValueError:
            pass

    token = _client_token(request)
    model_slug = model.strip() if model else None
    task = tm.create_task(
        ip,
        file.filename,
        client_token=token,
        target_lang=target_lang.strip(),
        source_lang=source_lang.strip() or "auto",
        model=model_slug,
    )
    base = tm.workspace_for_task(task.task_id)
    input_path = base / Path(file.filename).name
    await _stream_upload_to_file(file, input_path, MAX_UPLOAD_BYTES)
    tm.register_active(ip, task.task_id)

    mc = (
        max(1, int(max_concurrent_requests))
        if max_concurrent_requests is not None
        else max_concurrent_from_environment()
    )

    async def run_job() -> None:
        await asyncio.to_thread(
            tm.run_translation_in_thread,
            task,
            api_key=api_key.strip(),
            target_lang=target_lang.strip(),
            source_lang=source_lang.strip() or "auto",
            model=model_slug,
            preserve_tokens=preserve_tokens,
            use_context=use_context,
            max_concurrent_requests=mc,
            player_gender=player_gender.strip() or "male",
            reasoning_effort=reasoning_effort_norm,
            input_path=input_path,
        )

    asyncio.create_task(run_job())
    return TranslateResponse(task_id=task.task_id)


def _task_or_404(task_id: str, tm: TaskManager) -> TranslationTask:
    """Look up a translation task by ID — first in-memory, then DB.

    For tasks found only in DB (finished, evicted from memory), a minimal
    ``TranslationTask`` is reconstructed from the stored row.

    Raises:
        HTTPException: 400 if task_id is not a valid UUID.
        HTTPException: 404 if the task is not found anywhere.
    """
    if not _UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Неверный формат task_id")
    task = tm.get(task_id)
    if task:
        return task
    # Fallback: reconstruct from DB
    row = get_task_row(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    task = TranslationTask(
        task_id=row["task_id"],
        client_ip=row["client_ip"],
        client_token=row.get("client_token", ""),
        created_at=row["created_at"],
        status=row["status"],
        input_filename=row.get("input_filename", ""),
    )
    if row.get("result_path"):
        task.result_path = Path(row["result_path"])
    if row.get("extract_dir"):
        task.extract_dir = Path(row["extract_dir"])
    if row.get("input_path"):
        task.input_path = Path(row["input_path"])
    task.target_lang = row.get("target_lang")
    task.source_lang = row.get("source_lang")
    task.error = row.get("error")
    if row.get("stats"):
        try:
            task.stats = json.loads(row["stats"])
        except (json.JSONDecodeError, TypeError):
            pass
    return task


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def task_status(
    task_id: str, tm: TaskManager = Depends(web_task_manager)
) -> TaskStatusResponse:
    """Return a JSON snapshot of the current task state."""
    task = _task_or_404(task_id, tm)
    result_name = task.result_path.name if task.result_path else None
    target_lang = task.target_lang
    if not target_lang:
        row = get_task_row(task_id)
        if row:
            target_lang = row.get("target_lang")
    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_file=task.current_file,
        phase=task.phase,
        result_filename=result_name,
        error=task.error,
        stats=task.stats,
        target_lang=target_lang,
    )


@router.get("/tasks/{task_id}/progress")
async def task_progress(
    task_id: str, tm: TaskManager = Depends(web_task_manager)
) -> StreamingResponse:
    """Server-Sent Events stream of progress updates."""
    task = _task_or_404(task_id, tm)

    async def event_stream():
        # Send current snapshot first
        snap = {
            "type": "snapshot",
            "status": task.status,
            "progress": task.progress,
            "phase": task.phase,
            "file": task.current_file,
        }
        yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"

        if task.is_finished():
            if task.status == "completed" and task.result_path:
                yield f"data: {json.dumps({'type': 'completed', 'result_filename': task.result_path.name, 'stats': task.stats}, ensure_ascii=False)}\n\n"
            elif task.status == "failed":
                yield f"data: {json.dumps({'type': 'failed', 'error': task.error}, ensure_ascii=False)}\n\n"
            return

        idle_ticks = 0
        while True:
            try:
                msg = task.event_queue.get_nowait()
            except Empty:
                if task.is_finished() and task.event_queue.empty():
                    if task.status == "completed" and task.result_path:
                        yield f"data: {json.dumps({'type': 'completed', 'result_filename': task.result_path.name, 'stats': task.stats}, ensure_ascii=False)}\n\n"
                    elif task.status == "failed":
                        yield f"data: {json.dumps({'type': 'failed', 'error': task.error}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.2)
                    break
                idle_ticks += 1
                # Send SSE comment as heartbeat every ~15s to keep proxies alive
                if idle_ticks % 30 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
                continue
            idle_ticks = 0
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg.get("type") in ("completed", "failed"):
                await asyncio.sleep(0.2)
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{task_id}/download")
async def download_result(
    task_id: str, tm: TaskManager = Depends(web_task_manager)
) -> FileResponse:
    """Download the translated module file for a completed task."""
    task = _task_or_404(task_id, tm)
    if task.status != "completed" or not task.result_path or not task.result_path.is_file():
        raise HTTPException(status_code=400, detail="Файл результата ещё не готов")
    return FileResponse(
        path=task.result_path,
        filename=task.result_path.name,
        media_type="application/octet-stream",
    )


@router.get("/tasks/{task_id}/log")
async def download_log(
    task_id: str, tm: TaskManager = Depends(web_task_manager)
) -> StreamingResponse:
    """Download the translation log as JSONL (generated from SQLite)."""
    _task_or_404(task_id, tm)
    rows = get_translations_by_task(task_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Лог недоступен")

    def generate():
        for row in rows:
            yield json.dumps(row, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/jsonl",
        headers={"Content-Disposition": "attachment; filename=translation_log.jsonl"},
    )


@router.get("/tasks/{task_id}/translations", response_model=TranslationsResponse)
async def get_translations(
    task_id: str, tm: TaskManager = Depends(web_task_manager)
) -> TranslationsResponse:
    """Return structured translation data grouped by source file for the editor."""
    _task_or_404(task_id, tm)  # validate exists

    rows = get_translations_by_task(task_id)
    if not rows:
        return TranslationsResponse(files=[])

    groups: dict[str, list[TranslationItem]] = {}
    seen: dict[str, set[str]] = {}
    text_to_files: dict[str, list[str]] = {}

    for entry in rows:
        original = entry["original"]
        translated = entry["translated"]
        filename = entry.get("file") or "unknown"
        if not original:
            continue
        if filename not in groups:
            groups[filename] = []
            seen[filename] = set()
        if original not in seen[filename]:
            seen[filename].add(original)
            groups[filename].append(TranslationItem(original=original, translated=translated))
            if original not in text_to_files:
                text_to_files[original] = []
            text_to_files[original].append(filename)

    for filename, items in groups.items():
        for item in items:
            all_files = text_to_files.get(item.original, [])
            if len(all_files) > 1:
                item.shared_with = [f for f in all_files if f != filename]

    files = [
        TranslationFileGroup(filename=fn, items=items)
        for fn, items in groups.items()
    ]
    return TranslationsResponse(files=files)


@router.post("/tasks/{task_id}/rebuild", response_model=RebuildResponse)
async def rebuild_task(
    task_id: str,
    body: RebuildRequest,
    tm: TaskManager = Depends(web_task_manager),
) -> RebuildResponse:
    """Rebuild the .mod file with edited translations (no LLM calls)."""
    task = _task_or_404(task_id, tm)
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="Задача ещё не завершена")
    if not task.extract_dir or not Path(task.extract_dir).is_dir():
        raise HTTPException(
            status_code=400,
            detail="Извлечённые файлы модуля недоступны (возможно, были очищены)",
        )

    # Build full translation map from SQLite, then apply user overrides
    all_translations = get_translation_map_by_task(task_id)
    ncs_by_item_id = get_ncs_translation_map_by_task(task_id)
    all_translations.update(body.translations)

    if body.translations:
        for row in get_translations_by_task(task_id):
            iid = row.get("item_id")
            file = row.get("file") or ""
            if not iid or not file.lower().endswith(".ncs"):
                continue
            orig = row.get("original")
            if orig in body.translations:
                ncs_by_item_id[iid] = body.translations[orig]

    extract_dir = Path(task.extract_dir)
    output_path = task.result_path
    original_mod_path = task.input_path or output_path

    try:
        from ..main import rebuild_module

        row = get_task_row(task_id)
        req_tl = (body.target_lang or "").strip() or None
        rebuild_target_lang = req_tl or task.target_lang or (row or {}).get("target_lang")

        await asyncio.to_thread(
            rebuild_module,
            extract_dir,
            all_translations,
            output_path,
            original_mod_path=original_mod_path,
            target_lang=rebuild_target_lang,
            ncs_translations_by_item_id=ncs_by_item_id,
        )
    except Exception as e:
        logger.exception("Rebuild failed for task %s", task.task_id)
        raise HTTPException(status_code=500, detail=f"Ошибка сборки: {e}")

    import time
    update_task_row(task_id, updated_at=time.time())
    return RebuildResponse(result_filename=output_path.name)


@router.get("/history", response_model=TaskHistoryResponse)
async def task_history(request: Request) -> TaskHistoryResponse:
    """Return translation history for the client identified by ``X-Client-Token``."""
    token = _client_token(request)
    if not token:
        return TaskHistoryResponse(items=[])
    rows = list_tasks_by_token(token)
    items = []
    for r in rows:
        stats = None
        if r.get("stats"):
            try:
                stats = json.loads(r["stats"])
            except (json.JSONDecodeError, TypeError):
                pass
        items.append(
            TaskHistoryItem(
                task_id=r["task_id"],
                input_filename=r.get("input_filename", ""),
                status=r["status"],
                created_at=r["created_at"],
                target_lang=r.get("target_lang"),
                source_lang=r.get("source_lang"),
                model=r.get("model"),
                updated_at=r.get("updated_at"),
                stats=stats,
            )
        )
    return TaskHistoryResponse(items=items)


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    tm: TaskManager = Depends(web_task_manager),
) -> dict:
    """Delete a task from history. Only the owning client can delete."""
    if not _UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Неверный формат task_id")
    token = _client_token(request)
    row = get_task_row(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if token and row.get("client_token") != token:
        raise HTTPException(status_code=403, detail="Нет доступа к этой задаче")

    # Remove workspace from disk
    workspace = tm.workspace_root / task_id
    if workspace.is_dir():
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)

    # Remove from in-memory store
    with tm._lock:
        tm._tasks.pop(task_id, None)

    # Delete from DB (CASCADE removes translations)
    delete_task_row(task_id)
    return {"ok": True}


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(body: TestConnectionRequest) -> TestConnectionResponse:
    """Verify OpenRouter API key and model with a tiny translation."""
    text = "Hello, welcome to my module!"
    try:
        try:
            reff = parse_reasoning_effort(body.reasoning_effort)
        except ValueError as e:
            return TestConnectionResponse(ok=False, error=str(e))
        provider = create_provider(
            body.api_key.strip(), body.model, reasoning_effort=reff
        )
        result = await asyncio.to_thread(
            provider.translate, text, "english", body.target_lang
        )
        model = getattr(provider, "model", None) or OpenRouterProvider.DEFAULT_MODEL
        if result.success:
            return TestConnectionResponse(
                ok=True,
                translated=result.translated,
                model=model,
            )
        return TestConnectionResponse(ok=False, error=result.error or "Unknown error", model=model)
    except Exception as e:
        logger.warning("test-connection failed: %s", e)
        return TestConnectionResponse(ok=False, error=str(e))


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """Return the default model and a curated list of popular OpenRouter slugs."""
    return ModelsResponse(
        default_model=OpenRouterProvider.DEFAULT_MODEL,
        models=list(OpenRouterProvider.POPULAR_MODELS),
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Return server-side defaults: API key from env (if set) and default model."""
    api_key = os.environ.get("NWN_TRANSLATE_API_KEY", "").strip() or None
    return ConfigResponse(
        api_key=api_key,
        default_model=OpenRouterProvider.DEFAULT_MODEL,
    )
