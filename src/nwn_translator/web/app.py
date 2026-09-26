"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from .database import init_db
from .routes import MAX_UPLOAD_BYTES, router, upload_too_large
from .task_manager import get_task_manager

logger = logging.getLogger(__name__)

_UPLOAD_PATH = "/api/translate"


class UploadLimitMiddleware:
    """Cap the request body of the upload route while it streams in.

    FastAPI parses the whole multipart body, spooling files to disk, before a
    handler runs, so a handler-side check comes too late. Once the declared or
    received size passes the limit, the rest of the body is read and discarded
    (the client then gets the 413 response rather than a reset connection) and
    the request fails with 413.

    Attributes:
        app: Wrapped ASGI application.
        path: Request path the limit applies to.
        max_bytes: Largest accepted body.
    """

    def __init__(self, app: ASGIApp, path: str, max_bytes: int) -> None:
        """Wrap *app*, limiting bodies sent to *path* to *max_bytes*."""
        self.app = app
        self.path = path
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        declared = 0
        for name, value in scope["headers"]:
            if name == b"content-length":
                with contextlib.suppress(ValueError):
                    declared = int(value)
        received = 0

        async def receive_within_limit() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if max(declared, received) > self.max_bytes:
                    while message.get("more_body", False):
                        message = await receive()
                    raise upload_too_large(self.max_bytes)
            return message

        await self.app(scope, receive_within_limit, send)


def _parse_cors_origins() -> List[str]:
    """Parse ``NWN_WEB_CORS_ORIGINS`` into a list of allowed origins.

    Defaults to an empty list (no cross-origin access) when unset: the SPA is
    served from the same origin as the API — directly or behind nginx — so CORS
    is not needed. Set the variable to a comma-separated origin list (or ``*``)
    to opt in explicitly.

    Returns:
        List of origin strings.
    """
    raw = os.environ.get("NWN_WEB_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open the database and run the periodic workspace purge while the app lives.

    Shutdown waits for running translation jobs, so a graceful stop never cuts
    a job off mid-write.
    """
    init_db()
    task_manager = get_task_manager()
    purge_task = asyncio.create_task(task_manager.purge_periodically())
    yield
    purge_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await purge_task
    await asyncio.to_thread(task_manager.join_workers)


def create_app() -> FastAPI:
    """Build the FastAPI app: API routes, CORS, and the SPA from ``NWN_WEB_STATIC_DIR``."""
    app = FastAPI(
        title="NWN Modules Translator",
        description="Веб-API перевода модулей Neverwinter Nights",
        version=__version__,
        lifespan=lifespan,
    )

    # Added first so CORS wraps it and also covers its 413 responses.
    app.add_middleware(UploadLimitMiddleware, path=_UPLOAD_PATH, max_bytes=MAX_UPLOAD_BYTES)
    origins = _parse_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    static_dir = os.environ.get("NWN_WEB_STATIC_DIR", "").strip()
    if static_dir:
        path = Path(static_dir)
        if path.is_dir():
            app.mount("/", StaticFiles(directory=str(path), html=True), name="static")
            logger.info("Serving static files from %s", path)
        else:
            logger.warning("NWN_WEB_STATIC_DIR is not a directory: %s", static_dir)

    return app
