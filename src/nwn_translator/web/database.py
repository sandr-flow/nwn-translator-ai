"""SQLite persistence for web translation tasks and results."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    client_token   TEXT NOT NULL,
    client_ip      TEXT NOT NULL,
    created_at     REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    input_filename TEXT NOT NULL DEFAULT '',
    result_path    TEXT,
    extract_dir    TEXT,
    input_path     TEXT,
    error          TEXT,
    stats          TEXT,
    target_lang    TEXT,
    source_lang    TEXT,
    model          TEXT,
    updated_at     REAL
);

CREATE INDEX IF NOT EXISTS idx_tasks_client_token ON tasks(client_token);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);

CREATE TABLE IF NOT EXISTS translations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    original   TEXT NOT NULL,
    translated TEXT NOT NULL,
    context    TEXT,
    model      TEXT,
    file       TEXT,
    item_id    TEXT,

    UNIQUE(task_id, file, original)
);

CREATE INDEX IF NOT EXISTS idx_translations_task_id ON translations(task_id);
"""

_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _default_db_path() -> Path:
    env = os.environ.get("NWN_WEB_DB_PATH", "").strip()
    if env:
        return Path(env)
    return Path("workspace") / "web" / "translations.db"


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema (idempotent)."""
    cur = conn.execute("PRAGMA table_info(tasks)")
    existing = {row[1] for row in cur.fetchall()}
    for col, typedef in [("model", "TEXT"), ("updated_at", "REAL")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}")

    cur_tr = conn.execute("PRAGMA table_info(translations)")
    tr_cols = {row[1] for row in cur_tr.fetchall()}
    if "item_id" not in tr_cols:
        conn.execute("ALTER TABLE translations ADD COLUMN item_id TEXT")


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create tables if needed and return the singleton connection."""
    global _connection
    with _lock:
        if _connection is not None:
            return _connection
        path = db_path or _default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        # Migrate: add columns that may be missing in older DBs
        _migrate(conn)
        conn.commit()
        _connection = conn
        logger.info("SQLite database initialized at %s", path)
        return conn


def get_db() -> sqlite3.Connection:
    """Return the singleton connection (must call ``init_db`` first)."""
    if _connection is None:
        return init_db()
    return _connection


def close_db() -> None:
    """Close the singleton connection (for tests / shutdown)."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


def create_task_row(
    task_id: str,
    client_token: str,
    client_ip: str,
    created_at: float,
    input_filename: str,
    target_lang: Optional[str] = None,
    source_lang: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename, target_lang, source_lang, model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            client_token,
            client_ip,
            created_at,
            input_filename,
            target_lang,
            source_lang,
            model,
        ),
    )
    db.commit()


def update_task_row(task_id: str, **fields: Any) -> None:
    """Update one or more columns on a task row.

    Supported fields: status, result_path, extract_dir, input_path, error, stats,
    target_lang, source_lang.
    """
    if not fields:
        return
    # Serialize stats as JSON string
    if "stats" in fields and fields["stats"] is not None and not isinstance(fields["stats"], str):
        fields["stats"] = json.dumps(fields["stats"], ensure_ascii=False)
    # Convert Path objects to strings
    for key in ("result_path", "extract_dir", "input_path"):
        if key in fields and fields[key] is not None:
            fields[key] = str(fields[key])

    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [task_id]
    db = get_db()
    db.execute(f"UPDATE tasks SET {cols} WHERE task_id = ?", vals)  # noqa: S608
    db.commit()


def get_task_row(task_id: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    db.row_factory = sqlite3.Row
    cur = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def list_tasks_by_token(client_token: str) -> List[Dict[str, Any]]:
    db = get_db()
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "SELECT * FROM tasks WHERE client_token = ? ORDER BY created_at DESC",
        (client_token,),
    )
    return [dict(r) for r in cur.fetchall()]


def delete_task_row(task_id: str) -> bool:
    """Delete a task (CASCADE deletes translations). Returns True if row existed."""
    db = get_db()
    cur = db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    db.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


def insert_translation(
    task_id: str,
    original: str,
    translated: str,
    context: Optional[str] = None,
    model: Optional[str] = None,
    file: Optional[str] = None,
    item_id: Optional[str] = None,
) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO translations (task_id, original, translated, context, model, file, item_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, original, translated, context, model, file, item_id),
    )
    db.commit()


def get_translations_by_task(task_id: str) -> List[Dict[str, Any]]:
    db = get_db()
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "SELECT original, translated, context, model, file, item_id FROM translations WHERE task_id = ?",
        (task_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_ncs_translation_map_by_task(task_id: str) -> Dict[str, str]:
    """Return ``{item_id: translated}`` for rows that have a non-empty ``item_id``."""
    db = get_db()
    cur = db.execute(
        "SELECT item_id, translated FROM translations "
        "WHERE task_id = ? AND item_id IS NOT NULL AND item_id != ''",
        (task_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def get_translation_map_by_task(task_id: str) -> Dict[str, str]:
    """Return {original: translated} mapping for all translations in a task."""
    db = get_db()
    cur = db.execute(
        "SELECT original, translated FROM translations WHERE task_id = ?",
        (task_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# SqliteTranslationLogWriter — implements TranslationLogWriter protocol
# ---------------------------------------------------------------------------


class SqliteTranslationLogWriter:
    """Write translation log entries to SQLite instead of JSONL files."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def write(self, entry: Dict[str, Any]) -> None:
        original = entry.get("original", "")
        translated = entry.get("translated", "")
        if not original:
            return
        try:
            insert_translation(
                task_id=self.task_id,
                original=original,
                translated=translated,
                context=entry.get("context"),
                model=entry.get("model"),
                file=entry.get("file"),
                item_id=entry.get("item_id"),
            )
        except Exception as e:
            logger.debug("Failed to write translation to SQLite: %s", e)
