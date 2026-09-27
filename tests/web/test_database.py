"""The SQLite store: tasks, translation rows, migrations and the log writer."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from nwn_translator.web import database as db
from nwn_translator.web.task_manager import TaskManager

_TASKS = """
    CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, client_token TEXT NOT NULL, client_ip TEXT NOT NULL,
        created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        input_filename TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename)
        VALUES ('t1', 'tok', '1.1.1.1', 1.0, 'm.mod');
"""
_TRANSLATIONS = """
    CREATE TABLE translations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        original TEXT NOT NULL, translated TEXT NOT NULL, context TEXT, model TEXT,
        file TEXT{columns}
    );
"""


def _task(task_id: str = "t1") -> None:
    db.create_task_row(task_id, "tok", "127.0.0.1", 1.0, "m.mod")


def _translation_columns() -> set:
    return {row[1] for row in db.get_db().execute("PRAGMA table_info(translations)")}


def test_the_suite_never_touches_the_repository_database():
    """Losing the ``isolated_web_db`` fixture would silently pollute the live database.

    Without it the suite writes fake task rows into
    ``workspace/web/translations.db``, and a TaskManager started against it
    flags any genuinely running translation as ``interrupted``.
    """
    assert os.environ.get("NWN_WEB_DB_PATH")
    default = (Path.cwd() / "workspace" / "web" / "translations.db").resolve()
    assert db._default_db_path().resolve() != default


def test_rows_are_keyed_by_file_and_item_id():
    _task()
    db.insert_translation("t1", "Goblin", "Гоблин-А", file="a.utc", item_id="GOBLIN_first_name")
    db.insert_translation("t1", "Goblin", "Гоблин-Б", file="b.utc", item_id="GOBLIN_first_name")
    db.insert_translation("t1", "Goblin", "Гоблин-В", file="b.utc", item_id="y_first_name")

    assert db.get_item_translation_map_by_task("t1") == {
        "a.utc": {"GOBLIN_first_name": "Гоблин-А"},
        "b.utc": {"GOBLIN_first_name": "Гоблин-Б", "y_first_name": "Гоблин-В"},
    }
    rows = db.get_translations_by_task("t1")
    assert [row["item_id"] for row in rows] == ["GOBLIN_first_name"] * 2 + ["y_first_name"]


@pytest.mark.parametrize(
    "columns, rows",
    [
        # Before item ids: UNIQUE(task_id, file, original).
        (", UNIQUE(task_id, file, original)", []),
        (
            ", UNIQUE(task_id, file, original)",
            [
                "INSERT INTO translations (task_id, original, translated, file) "
                "VALUES ('t1', 'Goblin', 'Гоблин', 'a.utc')"
            ],
        ),
        # Item ids under the old unique key.
        (
            ", item_id TEXT, UNIQUE(task_id, file, original)",
            [
                "INSERT INTO translations (task_id, original, translated, file, item_id) "
                "VALUES ('t1', 'Goblin', 'Гоблин', 'a.utc', 'x_first_name')"
            ],
        ),
        # Before speakers.
        (
            ", item_id TEXT, success INTEGER NOT NULL DEFAULT 1, UNIQUE(task_id, file, item_id)",
            [
                "INSERT INTO translations (task_id, original, translated, context, file, item_id) "
                "VALUES ('t1', 'Goblin', 'Гоблин', 'Player reply in a.dlg', 'a.utc', 'x_first_name')"
            ],
        ),
    ],
)
def test_older_databases_are_migrated_and_keep_their_rows(tmp_path, columns, rows):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_TASKS + _TRANSLATIONS.format(columns=columns) + ";".join(rows))
    conn.close()

    db.init_db(path)

    assert {"item_id", "success", "speaker"} <= _translation_columns()
    task_columns = {row[1] for row in db.get_db().execute("PRAGMA table_info(tasks)")}
    assert {"model", "updated_at", "progress", "phase", "current_file"} <= task_columns
    stored = db.get_translations_by_task("t1")
    assert [(row["translated"], row["speaker"]) for row in stored] == [("Гоблин", None)] * len(rows)
    # Rows with the same file and original but another item id no longer collapse.
    db.insert_translation("t1", "Goblin", "new", file="a.utc", item_id="y_first_name")
    assert len(db.get_translations_by_task("t1")) == len(rows) + 1
    indexed = [
        row[2]
        for index in db.get_db().execute("PRAGMA index_list(translations)").fetchall()
        if index[2]
        for row in db.get_db().execute(f"PRAGMA index_info({index[1]})").fetchall()
    ]
    assert "item_id" in indexed
    # A second start is a no-op.
    db.close_db()
    db.init_db(path)
    assert db.get_translations_by_task("t1")[: len(rows)] == stored


def test_log_writer_stores_rows_and_ignores_events():
    _task()
    writer = db.SqliteTranslationLogWriter("t1")
    writer.write({"event": "ncs_diagnostic", "file": "s.ncs", "item_id": "s:off_1a"})
    assert db.get_translations_by_task("t1") == []

    row = {"original": "Boom", "translated": "Boom", "file": "a.uti", "item_id": "x:0"}
    writer.write({**row, "success": False})
    (stored,) = db.get_translations_by_task("t1")
    assert (stored["original"], stored["translated"], stored["success"]) == ("Boom", "Boom", 0)


def test_the_final_dialog_row_keeps_the_speaker():
    """The per-file row with a speaker replaces the dialog translator's earlier row."""
    _task()
    writer = db.SqliteTranslationLogWriter("t1")
    row = {"original": "Hello.", "translated": "Привет.", "file": "a.dlg", "item_id": "a:entry:0"}
    speaker = {"kind": "npc", "name": "Ölaf", "tag": "olaf"}
    writer.write({**row, "context": "Dialog node E0 in a.dlg"})
    writer.write({**row, "context": "NPC dialog line in a.dlg", "speaker": speaker})

    (stored,) = db.get_translations_by_task("t1")

    assert stored["speaker"] == speaker
    assert "Ölaf" in db.get_db().execute("SELECT speaker FROM translations").fetchone()[0]


def _row_log_levels(caplog) -> list:
    return [r.levelno for r in caplog.records if "translation row" in r.getMessage()]


def test_rows_of_a_deleted_task_are_dropped_quietly(caplog):
    """A job may still log rows after its task was deleted; that is not a failure."""
    with caplog.at_level(logging.DEBUG, logger=db.__name__):
        db.SqliteTranslationLogWriter("gone").write(
            {"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"}
        )
    assert db.get_translations_by_task("gone") == []
    assert _row_log_levels(caplog) == [logging.DEBUG]


def test_a_lost_row_is_warned_about(monkeypatch, caplog):
    """Any other failure loses an editor row that rebuild relies on."""

    def locked(**_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "insert_translation", locked)
    with caplog.at_level(logging.DEBUG, logger=db.__name__):
        db.SqliteTranslationLogWriter("t1").write(
            {"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"}
        )
    assert _row_log_levels(caplog) == [logging.WARNING]
    assert "database is locked" in caplog.text


def test_concurrent_access_is_serialized():
    """Threads sharing the connection neither raise nor mix up rows."""
    errors: list = []

    def worker(n: int) -> None:
        try:
            for i in range(40):
                tid = f"t{n}_{i}"
                db.create_task_row(tid, "tok", "127.0.0.1", 1.0 + i, "m.mod")
                db.insert_translation(tid, "orig", "tr", file="a.dlg", item_id="x")
                db.update_task_row(tid, status="running")
                row = db.get_task_row(tid)
                assert row is not None and row["task_id"] == tid and row["status"] == "running"
                assert db.get_item_translation_map_by_task(tid) == {"a.dlg": {"x": "tr"}}
                db.list_tasks_by_token("tok")
        except Exception as exc:  # noqa: BLE001 - the test asserts none occur
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(db.list_tasks_by_token("tok")) == 12 * 40


def test_rows_are_counted_under_the_connection_lock():
    _task()
    db.insert_translation("t1", "A", "А", file="a.uti", item_id="1")
    db.insert_translation("t1", "B", "B", file="a.uti", item_id="2", success=False)
    counted: list = []

    with db._lock:
        reader = threading.Thread(target=lambda: counted.append(db.count_translations("t1")))
        reader.start()
        reader.join(timeout=0.3)
        assert reader.is_alive(), "count ran without the connection lock"
    reader.join(timeout=5)

    assert counted == [2]  # rejected lines are editor rows too


def test_unknown_task_columns_are_rejected():
    """Column names are interpolated into SQL, so only real task columns may pass."""
    _task()
    with pytest.raises(ValueError):
        db.update_task_row("t1", **{"client_token = 'x', status": "running"})
    row = db.get_task_row("t1")
    assert (row["client_token"], row["status"]) == ("tok", "pending")


def test_startup_marks_unfinished_tasks_interrupted(tmp_path):
    """After a restart no worker runs them, so they must not report progress forever."""
    db.create_task_row("alive", "tok", "127.0.0.1", 1.0, "m.mod")
    db.update_task_row("alive", status="extracting")
    db.create_task_row("done", "tok", "127.0.0.1", 2.0, "m.mod")
    db.update_task_row("done", status="completed")

    manager = TaskManager(workspace_root=tmp_path / "tasks")

    assert db.get_task_row("alive")["status"] == "interrupted"
    assert db.get_task_row("done")["status"] == "completed"
    interrupted = manager.get("alive")
    assert interrupted is not None and interrupted.status == "interrupted"
    assert interrupted.is_finished()
    assert manager.get("done") is None
    # The TTL purge takes the interrupted task like any other finished task.
    manager.task_ttl_seconds = -1
    manager.purge_expired()
    assert manager.get("alive") is None


def test_api_stats_keep_five_errors_and_drop_the_request_list():
    stats = {
        "total_errors": 12,
        "errors": [f"e{i}" for i in range(12)],
        "metrics": {"requests": [{"id": 1}], "failed_requests": 0},
    }
    out = db.compact_stats_for_api(stats)
    assert out is not None
    assert out["total_errors"] == 12
    assert out["errors"] == [f"e{i}" for i in range(5)]
    assert "requests" not in out["metrics"]
    assert stats["errors"] == [f"e{i}" for i in range(12)]
