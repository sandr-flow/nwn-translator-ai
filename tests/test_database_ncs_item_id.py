"""SQLite translations table: ``item_id`` column and NCS map for rebuild."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nwn_translator.web import database as db


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    db.init_db(tmp_path / "t.db")


def test_insert_and_get_ncs_map(isolated_db: None) -> None:
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
    m = db.get_ncs_translation_map_by_task("t1")
    assert m == {"s:off_1a": "Привет"}

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
    assert db.get_translation_map_by_task("t1") == {}
    assert db.get_ncs_translation_map_by_task("t1") == {}


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
                assert db.get_ncs_translation_map_by_task(tid) == {"x": "tr"}
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
