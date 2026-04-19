"""FastAPI dependencies for DB and TaskManager (backed by ``app.state``)."""

from __future__ import annotations

import sqlite3

from fastapi import Request

from .task_manager import TaskManager, get_task_manager


def db_connection(request: Request) -> sqlite3.Connection:
    """SQLite connection created in app lifespan."""
    db: sqlite3.Connection = request.app.state.db
    return db


def web_task_manager(request: Request) -> TaskManager:
    """In-process task manager shared with background workers."""
    tm = getattr(request.app.state, "task_manager", None)
    if isinstance(tm, TaskManager):
        return tm
    return get_task_manager()
