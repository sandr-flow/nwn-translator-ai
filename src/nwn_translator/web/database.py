"""SQLite persistence for web translation tasks and their translation rows.

The web process keeps one connection (``check_same_thread=False``) shared by
route handlers, job threads and progress callbacks; every statement runs under
:data:`_lock`. The schema grows additively: new columns are listed in
:data:`_ADDED_COLUMNS` and added to older databases on startup.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..translation_logging import translation_log_writer_for_config

logger = logging.getLogger(__name__)

#: Statuses a task can no longer leave. ``interrupted`` marks a task whose
#: worker died (process restart) so it stops looking forever-running.
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")

_TERMINAL_PLACEHOLDERS = ", ".join("?" for _ in TERMINAL_STATUSES)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    client_token   TEXT NOT NULL,
    client_ip      TEXT NOT NULL,
    created_at     REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    progress       REAL,
    phase          TEXT,
    current_file   TEXT,
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
    success    INTEGER NOT NULL DEFAULT 1,
    speaker    TEXT,

    UNIQUE(task_id, file, item_id)
);

CREATE INDEX IF NOT EXISTS idx_translations_task_id ON translations(task_id);
"""

#: ``(table, column, type)`` of columns added after the first released schema, in
#: the order they were introduced. ``translations.item_id`` has to exist before
#: :func:`_migrate_translations_unique_key` copies it into the rebuilt table.
_ADDED_COLUMNS: Tuple[Tuple[str, str, str], ...] = (
    ("tasks", "model", "TEXT"),
    ("tasks", "updated_at", "REAL"),
    ("tasks", "progress", "REAL"),
    ("tasks", "phase", "TEXT"),
    ("tasks", "current_file", "TEXT"),
    ("translations", "item_id", "TEXT"),
    ("translations", "success", "INTEGER NOT NULL DEFAULT 1"),
    ("translations", "speaker", "TEXT"),
)

#: Columns :func:`update_task_row` may set; the names are interpolated into SQL.
_TASK_COLUMNS = frozenset(
    {
        "client_token",
        "client_ip",
        "created_at",
        "status",
        "progress",
        "phase",
        "current_file",
        "input_filename",
        "result_path",
        "extract_dir",
        "input_path",
        "error",
        "stats",
        "target_lang",
        "source_lang",
        "model",
        "updated_at",
    }
)

#: Max error strings returned on status/history polls (full list stays in SQLite).
_STATS_ERROR_SAMPLE_LIMIT = 5

_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _default_db_path() -> Path:
    """Database file from ``NWN_WEB_DB_PATH``, else ``workspace/web/translations.db``."""
    env = os.environ.get("NWN_WEB_DB_PATH", "").strip()
    if env:
        return Path(env)
    return Path("workspace") / "web" / "translations.db"


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema (idempotent)."""
    existing = {
        table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for table in ("tasks", "translations")
    }
    for table, column, typedef in _ADDED_COLUMNS:
        if column not in existing[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
    _migrate_translations_unique_key(conn)


def _migrate_translations_unique_key(conn: sqlite3.Connection) -> None:
    """Rebuild ``translations`` if it still uses the old UNIQUE(task_id, file, original).

    Addressing edits by ``item_id`` requires the row identity to be
    ``(task_id, file, item_id)`` so two identical originals in the same file do
    not collapse into one row.
    """
    target = ["task_id", "file", "item_id"]
    for idx in conn.execute("PRAGMA index_list(translations)").fetchall():
        name, is_unique = idx[1], idx[2]
        if not is_unique:
            continue
        cols = [row[2] for row in conn.execute(f"PRAGMA index_info({name})").fetchall()]
        if cols == target:
            return

    conn.execute("ALTER TABLE translations RENAME TO translations_old")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO translations "
        "(task_id, original, translated, context, model, file, item_id) "
        "SELECT task_id, original, translated, context, model, file, item_id "
        "FROM translations_old"
    )
    conn.execute("DROP TABLE translations_old")
    logger.info("Migrated translations table to UNIQUE(task_id, file, item_id)")


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the process-wide connection, creating and migrating the schema.

    Args:
        db_path: Database file; defaults to :func:`_default_db_path`. Ignored when
            the connection is already open.

    Returns:
        The shared connection.
    """
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
        _migrate(conn)
        conn.commit()
        _connection = conn
        logger.info("SQLite database initialized at %s", path)
        return conn


def get_db() -> sqlite3.Connection:
    """Return the shared connection, opening it with :func:`init_db` on first use."""
    if _connection is None:
        return init_db()
    return _connection


def close_db() -> None:
    """Close the shared connection (tests / shutdown); the next use reopens it."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


def _query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run a SELECT under the connection lock.

    Args:
        sql: Statement with ``?`` placeholders.
        params: Placeholder values.

    Returns:
        The rows as dicts.
    """
    db = get_db()
    with _lock:
        cur = db.execute(sql, params)
        cur.row_factory = sqlite3.Row
        return [dict(row) for row in cur.fetchall()]


def _execute(sql: str, params: Sequence[Any] = ()) -> None:
    """Run one write statement under the connection lock and commit it.

    Args:
        sql: Statement with ``?`` placeholders.
        params: Placeholder values.
    """
    db = get_db()
    with _lock:
        db.execute(sql, params)
        db.commit()


# ---------------------------------------------------------------------------
# Tasks
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
    """Insert a new ``pending`` task.

    Args:
        task_id: Task UUID.
        client_token: Anonymous owner token (empty for ownerless tasks).
        client_ip: Client address that started the task.
        created_at: Unix timestamp of creation.
        input_filename: Name of the uploaded module.
        target_lang: Target language.
        source_lang: Source language, ``"auto"`` to detect; ``None`` stores NULL.
        model: Model slug requested by the client.
    """
    _execute(
        "INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename, "
        "target_lang, source_lang, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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


def update_task_row(task_id: str, **fields: Any) -> None:
    """Set columns of a task row; a missing row is left alone.

    A ``stats`` dict is stored as JSON.

    Args:
        task_id: Task UUID.
        **fields: Column values; names must be columns of ``tasks`` other than
            ``task_id``. Path columns take strings.

    Raises:
        ValueError: If a field is not a task column.
    """
    if not fields:
        return
    unknown = fields.keys() - _TASK_COLUMNS
    if unknown:
        raise ValueError(f"Unknown task columns: {sorted(unknown)}")
    if fields.get("stats") is not None:
        fields["stats"] = json.dumps(fields["stats"], ensure_ascii=False)
    assignments = ", ".join(f"{column} = ?" for column in fields)
    _execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", [*fields.values(), task_id])


def decode_stats(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a stored ``tasks.stats`` value.

    Args:
        raw: Column value.

    Returns:
        The stats dict, or ``None`` when the column is empty or not valid JSON.
    """
    if not raw:
        return None
    try:
        stats: Dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return stats


def compact_stats_for_api(stats: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a poll-safe copy of task stats without unbounded error dumps.

    Keeps ``total_errors`` and the first few ``errors``, and drops the per-call
    ``metrics.requests`` telemetry that rows written by older versions carry. The
    full payload stays in the SQLite ``stats`` column.

    Args:
        stats: Stats dict as stored for the task, or ``None``.

    Returns:
        The trimmed copy, or ``None`` when *stats* is ``None``.
    """
    if stats is None:
        return None
    out = dict(stats)
    errors = out.get("errors")
    if isinstance(errors, list):
        total = out.get("total_errors")
        if not isinstance(total, int):
            total = len(errors)
            out["total_errors"] = total
        out["errors"] = errors[:_STATS_ERROR_SAMPLE_LIMIT]
    metrics = out.get("metrics")
    if isinstance(metrics, dict):
        metrics_out = dict(metrics)
        metrics_out.pop("requests", None)
        out["metrics"] = metrics_out
    return out


def get_task_row(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the task row as a dict, or ``None`` if it does not exist."""
    rows = _query("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    return rows[0] if rows else None


def list_tasks_by_token(client_token: str) -> List[Dict[str, Any]]:
    """Return the rows of one client's tasks, newest first."""
    return _query(
        "SELECT * FROM tasks WHERE client_token = ? ORDER BY created_at DESC", (client_token,)
    )


def get_unfinished_task_rows() -> List[Dict[str, Any]]:
    """Return task rows still in a non-terminal state.

    Used at startup to reconcile tasks whose worker died: anything not in
    :data:`TERMINAL_STATUSES` (``pending``/``extracting``/``translating``/…) has
    no live worker after a restart and must be flipped to ``interrupted``.
    """
    return _query(
        f"SELECT * FROM tasks WHERE status NOT IN ({_TERMINAL_PLACEHOLDERS})",  # noqa: S608
        TERMINAL_STATUSES,
    )


def get_finished_task_ids_older_than(cutoff: float) -> List[str]:
    """Return IDs of terminal-status tasks created before *cutoff*.

    Used by workspace TTL cleanup. Reads the DB rather than the in-memory task
    dict because finished tasks are not reloaded into memory after a process
    restart, while their rows (and workspace files) survive it.

    Args:
        cutoff: Unix timestamp; only tasks with ``created_at`` strictly below
            it are returned.

    Returns:
        Matching task IDs.
    """
    rows = _query(
        f"SELECT task_id FROM tasks WHERE status IN ({_TERMINAL_PLACEHOLDERS}) "  # noqa: S608
        "AND created_at < ?",
        (*TERMINAL_STATUSES, cutoff),
    )
    return [row["task_id"] for row in rows]


def delete_task_row(task_id: str) -> None:
    """Delete a task; the foreign key cascades to its translation rows.

    Args:
        task_id: Task UUID.
    """
    _execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))


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
    success: bool = True,
    speaker: Optional[Dict[str, str]] = None,
) -> None:
    """Insert or replace the row of one ``(task_id, file, item_id)``.

    Args:
        task_id: Owning task.
        original: Source text.
        translated: Translation (the original for rejected lines).
        context: Extractor context string.
        model: Model slug that produced the translation.
        file: Resource file name.
        item_id: Stable per-file item identifier.
        success: ``False`` when the model's answer was rejected.
        speaker: Dialog speaker (``.dlg`` lines only), stored as JSON.
    """
    speaker_json = json.dumps(speaker, ensure_ascii=False) if speaker else None
    _execute(
        "INSERT OR REPLACE INTO translations "
        "(task_id, original, translated, context, model, file, item_id, success, speaker) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            original,
            translated,
            context,
            model,
            file,
            item_id,
            1 if success else 0,
            speaker_json,
        ),
    )


def update_translation_text(task_id: str, file: str, item_id: str, translated: str) -> None:
    """Persist an editor edit: set ``translated`` for one ``(task_id, file, item_id)``.

    The original text and the row identity are kept, and the line counts as
    translated (the user has reviewed it). An edit of an item with no stored
    row changes nothing.

    Args:
        task_id: Owning task.
        file: Resource file name.
        item_id: Per-file item identifier.
        translated: New translation.
    """
    _execute(
        "UPDATE translations SET translated = ?, success = 1 "
        "WHERE task_id = ? AND file = ? AND item_id = ?",
        (translated, task_id, file, item_id),
    )


def get_translations_by_task(task_id: str) -> List[Dict[str, Any]]:
    """Return the task's translation rows with ``speaker`` decoded (``None`` when absent)."""
    rows = _query(
        "SELECT original, translated, context, model, file, item_id, success, speaker "
        "FROM translations WHERE task_id = ?",
        (task_id,),
    )
    for row in rows:
        row["speaker"] = json.loads(row["speaker"]) if row["speaker"] else None
    return rows


def count_translations(task_id: str) -> int:
    """Return how many translation rows the task has, rejected lines included."""
    rows = _query("SELECT COUNT(*) AS n FROM translations WHERE task_id = ?", (task_id,))
    return int(rows[0]["n"])


def get_item_translation_map_by_task(task_id: str) -> Dict[str, Dict[str, str]]:
    """Return ``{file: {item_id: translated}}`` for rows that carry an ``item_id``.

    This is the addressing used by rebuild: a translation is identified by its
    source file plus the stable per-file ``item_id`` the extractor assigned, so
    identical originals in different files (or different nodes) stay distinct.
    """
    rows = _query(
        "SELECT file, item_id, translated FROM translations "
        "WHERE task_id = ? AND item_id IS NOT NULL AND item_id != ''",
        (task_id,),
    )
    result: Dict[str, Dict[str, str]] = {}
    for row in rows:
        result.setdefault(row["file"] or "", {})[row["item_id"]] = row["translated"]
    return result


class SqliteTranslationLogWriter:
    """Translation log writer that stores editor rows in SQLite.

    Rows with an ``event`` key are diagnostics and go to the JSONL trace file
    instead; rows without an original text are dropped.

    Attributes:
        task_id: Task the rows belong to.
    """

    def __init__(self, task_id: str, trace_path: Optional[Path] = None) -> None:
        """Create the writer.

        Args:
            task_id: Task the rows belong to.
            trace_path: JSONL file for diagnostic events; ``None`` discards them.
        """
        self.task_id = task_id
        self._trace_writer = translation_log_writer_for_config(trace_path)

    def write(self, entry: Dict[str, Any]) -> None:
        """Store one log entry.

        A failed insert (e.g. the task was deleted meanwhile) is logged at debug
        level and never interrupts the translation.

        Args:
            entry: Translation log record.
        """
        if entry.get("event"):
            self._trace_writer.write(entry)
            return
        original = entry.get("original", "")
        if not original:
            return
        try:
            insert_translation(
                task_id=self.task_id,
                original=original,
                translated=entry.get("translated", ""),
                context=entry.get("context"),
                model=entry.get("model"),
                file=entry.get("file"),
                item_id=entry.get("item_id"),
                success=entry.get("success", True) not in (False, 0, "0"),
                speaker=entry.get("speaker"),
            )
        except Exception as e:
            logger.debug("Failed to write translation to SQLite: %s", e)
