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

from .. import __version__
from .database import init_db
from .routes import router
from .task_manager import get_task_manager

logger = logging.getLogger(__name__)


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
    """Open the database and run the periodic workspace purge while the app lives."""
    init_db()
    purge_task = asyncio.create_task(get_task_manager().purge_periodically())
    yield
    purge_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await purge_task


def create_app() -> FastAPI:
    """Build the FastAPI app: API routes, CORS, and the SPA from ``NWN_WEB_STATIC_DIR``."""
    app = FastAPI(
        title="NWN Modules Translator",
        description="Веб-API перевода модулей Neverwinter Nights",
        version=__version__,
        lifespan=lifespan,
    )

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
