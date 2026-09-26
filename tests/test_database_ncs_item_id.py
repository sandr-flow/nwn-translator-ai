"""SQLite translations table: ``item_id`` column and the per-file map used by rebuild."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

import pytest

from nwn_translator.web import database as db


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    db.init_db(tmp_path / "t.db")


def test_insert_and_get_item_map(isolated_db: None) -> None:
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="127.0.0.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    db.insert_translation(
        task_id="t1",
        original="Hello",
        translated="Привет",
        file="s.ncs",
        item_id="s:off_1a",
    )
    m = db.get_item_translation_map_by_task("t1")
    assert m == {"s.ncs": {"s:off_1a": "Привет"}}

    rows = db.get_translations_by_task("t1")
    assert len(rows) == 1
    assert rows[0]["item_id"] == "s:off_1a"


def test_sqlite_log_writer_ignores_diagnostic_events(isolated_db: None) -> None:
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="127.0.0.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    writer = db.SqliteTranslationLogWriter("t1")

    writer.write(
        {
            "event": "ncs_diagnostic",
            "file": "s.ncs",
            "item_id": "s:off_1a",
            "reason": "skipped_fail_closed_ambiguous",
        }
    )

    assert db.get_translations_by_task("t1") == []
    assert db.get_item_translation_map_by_task("t1") == {}


def test_sqlite_log_writer_persists_failed_row_with_original(isolated_db: None) -> None:
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="127.0.0.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    writer = db.SqliteTranslationLogWriter("t1")
    writer.write(
        {
            "original": "Boom",
            "translated": "Boom",
            "file": "a.uti",
            "item_id": "x:0",
            "success": False,
        }
    )
    rows = db.get_translations_by_task("t1")
    assert len(rows) == 1
    assert rows[0]["original"] == "Boom"
    assert rows[0]["translated"] == "Boom"
    assert rows[0]["success"] == 0


def test_sqlite_log_writer_drops_rows_of_a_deleted_task_quietly(
    isolated_db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A job may still log rows after its task was deleted; that is not a failure."""
    writer = db.SqliteTranslationLogWriter("gone")

    with caplog.at_level(logging.DEBUG, logger=db.__name__):
        writer.write({"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"})

    assert db.get_translations_by_task("gone") == []
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


def test_sqlite_log_writer_warns_when_a_row_is_lost(
    isolated_db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Any other failure loses an editor row that rebuild relies on, so it must be visible."""

    def locked(**_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "insert_translation", locked)
    writer = db.SqliteTranslationLogWriter("t1")

    with caplog.at_level(logging.DEBUG, logger=db.__name__):
        writer.write({"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"})

    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "database is locked" in caplog.records[0].getMessage()


def test_migrate_adds_item_id_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older DB without ``item_id`` gets column via ``_migrate``."""
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    path = tmp_path / "legacy.db"
    conn = __import__("sqlite3").connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            client_token TEXT NOT NULL,
            client_ip TEXT NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            input_filename TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            original TEXT NOT NULL,
            translated TEXT NOT NULL,
            context TEXT,
            model TEXT,
            file TEXT,
            UNIQUE(task_id, file, original)
        );
        """)
    conn.close()

    monkeypatch.setattr(db, "_connection", None)
    db.init_db(path)
    cur = db.get_db().execute("PRAGMA table_info(translations)")
    cols = {row[1] for row in cur.fetchall()}
    assert "item_id" in cols
    assert "success" in cols


def test_concurrent_access_is_serialized(isolated_db: None) -> None:
    """Many threads reading/writing the shared connection must not raise or scramble.

    Regression: without a lock around execute/commit (and with ``row_factory`` on the
    shared connection), concurrent calls raised ``InterfaceError`` / ``OperationalError``
    ("cannot start a transaction within a transaction") and could mix up rows.
    """
    errors: list[str] = []

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
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(db.list_tasks_by_token("tok")) == 12 * 40


def test_startup_reconciles_unfinished_tasks(isolated_db: None, tmp_path: Path) -> None:
    """A non-terminal row left by a dead worker becomes ``interrupted`` on init.

    Regression: after a restart, ``running``/``extracting`` rows had no worker but
    the status endpoint kept reporting them as running forever.
    """
    from nwn_translator.web.task_manager import TaskManager

    db.create_task_row("alive", "tok", "127.0.0.1", 1.0, "m.mod")
    db.update_task_row("alive", status="extracting")  # non-terminal -> interrupted
    db.create_task_row("done", "tok", "127.0.0.1", 2.0, "m.mod")
    db.update_task_row("done", status="completed")  # terminal -> untouched

    tm = TaskManager(workspace_root=tmp_path / "tasks")

    # DB row flipped to a terminal status; the completed row is left alone.
    assert db.get_task_row("alive")["status"] == "interrupted"
    assert db.get_task_row("done")["status"] == "completed"

    # The interrupted task is registered in memory and counts as finished;
    # the already-terminal one is not reloaded into memory.
    interrupted = tm.get("alive")
    assert interrupted is not None and interrupted.status == "interrupted"
    assert interrupted.is_finished()
    assert tm.get("done") is None

    # TTL purge picks the interrupted task up like any other finished task.
    tm.task_ttl_seconds = -1
    tm.purge_expired()
    assert tm.get("alive") is None


def test_count_translations_counts_every_row_under_the_lock(isolated_db: None) -> None:
    """Job threads count rows on the connection route threads share, so under its lock."""
    db.create_task_row("t1", "tok", "127.0.0.1", 1.0, "m.mod")
    db.insert_translation("t1", "A", "А", file="a.uti", item_id="1")
    db.insert_translation("t1", "B", "B", file="a.uti", item_id="2", success=False)
    counted: list[int] = []

    with db._lock:
        reader = threading.Thread(target=lambda: counted.append(db.count_translations("t1")))
        reader.start()
        reader.join(timeout=0.3)
        assert reader.is_alive(), "count ran without the connection lock"
    reader.join(timeout=5)

    assert counted == [2]  # rejected lines are editor rows too


def test_update_task_row_rejects_unknown_columns(isolated_db: None) -> None:
    """Column names are interpolated into SQL, so only real task columns may pass."""
    db.create_task_row("t1", "tok", "127.0.0.1", 1.0, "m.mod")
    with pytest.raises(ValueError):
        db.update_task_row("t1", **{"client_token = 'x', status": "running"})
    row = db.get_task_row("t1")
    assert row is not None
    assert (row["client_token"], row["status"]) == ("tok", "pending")
