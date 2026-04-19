"""SQLite translations table: ``item_id`` column and NCS map for rebuild."""

from __future__ import annotations

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
