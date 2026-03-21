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

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..config import max_concurrent_from_environment
from fastapi.responses import FileResponse, StreamingResponse

from ..ai_providers import OpenRouterProvider, create_provider
from .schemas import (
    ModelsResponse,
    TaskStatusResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    TranslateResponse,
)
from .task_manager import MAX_UPLOAD_BYTES, TranslationTask, get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


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


@router.post("/translate", response_model=TranslateResponse)
async def start_translate(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Form(...),
    target_lang: str = Form(...),
    source_lang: str = Form("auto"),
    model: Optional[str] = Form(None),
    preserve_tokens: bool = Form(True),
    use_context: bool = Form(True),
    max_concurrent_requests: Optional[int] = Form(None),
    player_gender: str = Form("male"),
) -> TranslateResponse:
    """Accept a .mod/.erf/.hak upload and start translation in the background."""
    tm = get_task_manager()
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

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой (максимум {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ)",
        )

    task = tm.create_task(ip, file.filename)
    base = tm.workspace_for_task(task.task_id)
    input_path = base / Path(file.filename).name
    input_path.write_bytes(body)
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
            model=model.strip() if model else None,
            preserve_tokens=preserve_tokens,
            use_context=use_context,
            max_concurrent_requests=mc,
            player_gender=player_gender.strip() or "male",
            input_path=input_path,
        )

    asyncio.create_task(run_job())
    return TranslateResponse(task_id=task.task_id)


def _task_or_404(task_id: str) -> TranslationTask:
    """Look up a translation task by ID or raise HTTP 404.

    Args:
        task_id: UUID string of the task.

    Returns:
        The matching ``TranslationTask``.

    Raises:
        HTTPException: 400 if task_id is not a valid UUID.
        HTTPException: 404 if the task is not found.
    """
    if not _UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Неверный формат task_id")
    tm = get_task_manager()
    task = tm.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def task_status(task_id: str) -> TaskStatusResponse:
    """Return a JSON snapshot of the current task state."""
    task = _task_or_404(task_id)
    result_name = task.result_path.name if task.result_path else None
    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_file=task.current_file,
        phase=task.phase,
        result_filename=result_name,
        error=task.error,
        stats=task.stats,
    )


@router.get("/tasks/{task_id}/progress")
async def task_progress(task_id: str) -> StreamingResponse:
    """Server-Sent Events stream of progress updates."""
    task = _task_or_404(task_id)

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
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    # Small delay so the proxy flushes the final event before we close
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
async def download_result(task_id: str) -> FileResponse:
    """Download the translated module file for a completed task."""
    task = _task_or_404(task_id)
    if task.status != "completed" or not task.result_path or not task.result_path.is_file():
        raise HTTPException(status_code=400, detail="Файл результата ещё не готов")
    return FileResponse(
        path=task.result_path,
        filename=task.result_path.name,
        media_type="application/octet-stream",
    )


@router.get("/tasks/{task_id}/log")
async def download_log(task_id: str) -> FileResponse:
    """Download the JSONL translation log for a task."""
    task = _task_or_404(task_id)
    if not task.log_path or not task.log_path.is_file():
        raise HTTPException(status_code=404, detail="Лог недоступен")
    return FileResponse(
        path=task.log_path,
        filename="translation_log.jsonl",
        media_type="application/jsonl",
    )


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(body: TestConnectionRequest) -> TestConnectionResponse:
    """Verify OpenRouter API key and model with a tiny translation."""
    text = "Hello, welcome to my module!"
    try:
        provider = create_provider(body.api_key.strip(), body.model)
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
