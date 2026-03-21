"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import router
from .task_manager import purge_loop_task_manager

logger = logging.getLogger(__name__)


def _parse_cors_origins() -> List[str]:
    """Parse ``NWN_WEB_CORS_ORIGINS`` env var into a list of allowed origins.

    Returns:
        List of origin strings.  Defaults to ``["*"]`` if unset.
    """
    raw = os.environ.get("NWN_WEB_CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager.

    Starts a background task that periodically purges expired translation
    tasks and cancels it on shutdown.
    """
    purge_task = asyncio.create_task(purge_loop_task_manager(3600))
    yield
    purge_task.cancel()
    try:
        await purge_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    """Build FastAPI app with API routes and optional static SPA."""
    app = FastAPI(
        title="NWN Modules Translator",
        description="Веб-API перевода модулей Neverwinter Nights",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(),
        allow_credentials=True,
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
