"""Rebuild addresses translations by (file, item_id), not by original text.

Covers the database migration + item_id map, the core rebuild_module addressing
(two identical originals in different files edited independently), idempotency,
and the /rebuild endpoint contract via TestClient.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nwn_translator.file_handlers.gff_handler import read_gff
from nwn_translator.file_handlers.gff_writer import write_gff
from nwn_translator.main import rebuild_module
from nwn_translator.web import database as db
from nwn_translator.web.app import create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_creature(path: Path, tag: str, first_name: str) -> None:
    """Write a minimal .utc with a Tag and a localized FirstName."""
    write_gff(
        path,
        {"StructType": "UTC", "Tag": tag, "FirstName": {"StrRef": -1, "Value": first_name}},
        file_type="UTC",
    )


def _first_name(path: Path) -> str:
    return read_gff(path).get("FirstName", {}).get("Value", "")


# ---------------------------------------------------------------------------
# Database: migration + item_id map
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    db.init_db(tmp_path / "t.db")


def test_item_translation_map_is_keyed_by_file_and_item_id(isolated_db: None) -> None:
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    db.insert_translation(
        task_id="t1",
        original="Goblin",
        translated="Гоблин-А",
        file="a.utc",
        item_id="GOBLIN_first_name",
    )
    db.insert_translation(
        task_id="t1",
        original="Goblin",
        translated="Гоблин-Б",
        file="b.utc",
        item_id="GOBLIN_first_name",
    )

    m = db.get_item_translation_map_by_task("t1")
    assert m == {
        "a.utc": {"GOBLIN_first_name": "Гоблин-А"},
        "b.utc": {"GOBLIN_first_name": "Гоблин-Б"},
    }


def test_same_original_different_files_kept_distinct(isolated_db: None) -> None:
    """The old UNIQUE(task_id, file, original) collapsed these; the new key keeps both."""
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    db.insert_translation(
        task_id="t1", original="Goblin", translated="A", file="a.utc", item_id="x_first_name"
    )
    db.insert_translation(
        task_id="t1", original="Goblin", translated="B", file="a.utc", item_id="y_first_name"
    )

    rows = db.get_translations_by_task("t1")
    assert len(rows) == 2


def test_migration_rebuilds_old_unique_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy DB with UNIQUE(task_id, file, original) migrates to item_id keying."""
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, client_token TEXT NOT NULL, client_ip TEXT NOT NULL,
            created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            input_filename TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            original TEXT NOT NULL, translated TEXT NOT NULL, context TEXT, model TEXT,
            file TEXT, item_id TEXT,
            UNIQUE(task_id, file, original)
        );
        INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename)
            VALUES ('t1', 'tok', '1.1.1.1', 1.0, 'm.mod');
        INSERT INTO translations (task_id, original, translated, file, item_id)
            VALUES ('t1', 'Goblin', 'old', 'a.utc', 'x_first_name');
        """)
    conn.close()

    monkeypatch.setattr(db, "_connection", None)
    db.init_db(path)

    # New rows with the same (file, original) but a different item_id no longer collapse.
    db.insert_translation(
        task_id="t1", original="Goblin", translated="new", file="a.utc", item_id="y_first_name"
    )
    rows = db.get_translations_by_task("t1")
    assert len(rows) == 2
    indexes = [
        row[2]
        for idx in db.get_db().execute("PRAGMA index_list(translations)").fetchall()
        if idx[2]
        for row in db.get_db().execute(f"PRAGMA index_info({idx[1]})").fetchall()
    ]
    assert "item_id" in indexes


# ---------------------------------------------------------------------------
# rebuild_module: (file, item_id) addressing
# ---------------------------------------------------------------------------


def test_rebuild_edits_only_targeted_file(tmp_path: Path) -> None:
    """Two files share Tag + FirstName; editing one item_id touches only that file."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    _write_creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    _write_creature(extract_dir / "b.utc", "GOBLIN", "Гоблин")

    translations_by_item_id = {
        "a.utc": {"GOBLIN_first_name": "Гоблин-А"},
        "b.utc": {"GOBLIN_first_name": "Гоблин"},
    }
    rebuild_module(
        extract_dir,
        translations_by_item_id,
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="russian",
    )

    assert _first_name(extract_dir / "a.utc") == "Гоблин-А"
    assert _first_name(extract_dir / "b.utc") == "Гоблин"


def test_rebuild_is_idempotent_without_edits(tmp_path: Path) -> None:
    """Rebuilding with each item mapped to its current text changes no bytes."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    _write_creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    before = (extract_dir / "a.utc").read_bytes()

    rebuild_module(
        extract_dir,
        {"a.utc": {"GOBLIN_first_name": "Гоблин"}},
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="russian",
    )
    assert (extract_dir / "a.utc").read_bytes() == before


# ---------------------------------------------------------------------------
# /rebuild endpoint contract (TestClient)
# ---------------------------------------------------------------------------


@pytest.fixture
def rebuild_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NWN_WEB_DB_PATH", str(tmp_path / "web.db"))
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    set_task_manager(TaskManager(workspace_root=tmp_path / "tasks"))
    app = create_app()
    with TestClient(app) as c:
        # All seeded tasks below are owned by "tok"; act as that owner.
        c.headers["X-Client-Token"] = "tok"
        yield c, tmp_path
    set_task_manager(None)
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)


def test_rebuild_endpoint_edits_one_of_two_identical_originals(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    _write_creature(extract_dir / "a.utc", "GOBLIN", "Гоблин")
    _write_creature(extract_dir / "b.utc", "GOBLIN", "Гоблин")

    task_id = str(uuid.uuid4())
    db.create_task_row(
        task_id=task_id,
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="in.mod",
        target_lang="russian",
    )
    db.update_task_row(
        task_id,
        status="completed",
        extract_dir=str(extract_dir),
        result_path=str(tmp_path / "out.mod"),
        input_path=str(tmp_path / "missing.mod"),
    )
    for fname in ("a.utc", "b.utc"):
        db.insert_translation(
            task_id=task_id,
            original="Goblin",
            translated="Гоблин",
            file=fname,
            item_id="GOBLIN_first_name",
        )

    resp = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={
            "edits": [{"file": "a.utc", "item_id": "GOBLIN_first_name", "translated": "Гоблин-А"}],
            "target_lang": "russian",
        },
    )
    assert resp.status_code == 200, resp.text
    assert _first_name(extract_dir / "a.utc") == "Гоблин-А"
    assert _first_name(extract_dir / "b.utc") == "Гоблин"

    # Idempotent: re-sending the same edits reproduces the same result.
    resp2 = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={
            "edits": [{"file": "a.utc", "item_id": "GOBLIN_first_name", "translated": "Гоблин-А"}],
            "target_lang": "russian",
        },
    )
    assert resp2.status_code == 200, resp2.text
    assert _first_name(extract_dir / "a.utc") == "Гоблин-А"
    assert _first_name(extract_dir / "b.utc") == "Гоблин"


# ---------------------------------------------------------------------------
# Edits persist across rebuilds (C4b)
# ---------------------------------------------------------------------------


def _seed_completed_task(tmp_path: Path, extract_dir: Path, creatures: dict[str, str]) -> str:
    """Create a completed task with one translation row per creature file."""
    task_id = str(uuid.uuid4())
    db.create_task_row(
        task_id=task_id,
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="in.mod",
        target_lang="russian",
    )
    db.update_task_row(
        task_id,
        status="completed",
        extract_dir=str(extract_dir),
        result_path=str(tmp_path / "out.mod"),
        input_path=str(tmp_path / "missing.mod"),
    )
    for fname, (tag, name) in creatures.items():  # type: ignore[misc]
        _write_creature(extract_dir / fname, tag, name)
        db.insert_translation(
            task_id=task_id,
            original=name,
            translated=name,
            file=fname,
            item_id=f"{tag}_first_name",
        )
    return task_id


def test_rebuild_persists_edit_to_translations(rebuild_client) -> None:
    """After a rebuild, GET /translations reflects the edited value."""
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    task_id = _seed_completed_task(tmp_path, extract_dir, {"a.utc": ("GOBLIN", "Гоблин")})

    resp = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={
            "edits": [{"file": "a.utc", "item_id": "GOBLIN_first_name", "translated": "Гоблин-А"}],
            "target_lang": "russian",
        },
    )
    assert resp.status_code == 200, resp.text

    data = client.get(f"/api/tasks/{task_id}/translations").json()
    item = data["files"][0]["items"][0]
    assert item["translated"] == "Гоблин-А"
    assert item["item_id"] == "GOBLIN_first_name"


def test_two_sequential_rebuilds_keep_both_edits(rebuild_client) -> None:
    """A second rebuild editing only file B must not revert file A's earlier edit."""
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    task_id = _seed_completed_task(
        tmp_path, extract_dir, {"a.utc": ("GOBLIN", "Гоблин"), "b.utc": ("ORC", "Орк")}
    )

    # First rebuild edits A only.
    r1 = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={
            "edits": [{"file": "a.utc", "item_id": "GOBLIN_first_name", "translated": "Гоблин!"}]
        },
    )
    assert r1.status_code == 200, r1.text

    # Second rebuild edits B only — A's edit is persisted, so it stays put.
    r2 = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={"edits": [{"file": "b.utc", "item_id": "ORC_first_name", "translated": "Орк!"}]},
    )
    assert r2.status_code == 200, r2.text

    assert _first_name(extract_dir / "a.utc") == "Гоблин!"
    assert _first_name(extract_dir / "b.utc") == "Орк!"

    data = client.get(f"/api/tasks/{task_id}/translations").json()
    by_file = {f["filename"]: f["items"][0]["translated"] for f in data["files"]}
    assert by_file == {"a.utc": "Гоблин!", "b.utc": "Орк!"}


def test_get_translations_marks_failed_rows(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    task_id = str(uuid.uuid4())
    db.create_task_row(
        task_id=task_id,
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="in.mod",
    )
    db.update_task_row(task_id, status="completed")
    db.insert_translation(
        task_id=task_id,
        original="Hello",
        translated="Hello",
        file="a.utc",
        item_id="hello",
        success=False,
    )
    db.insert_translation(
        task_id=task_id,
        original="Bye",
        translated="Пока",
        file="a.utc",
        item_id="bye",
        success=True,
    )

    data = client.get(f"/api/tasks/{task_id}/translations").json()
    items = {item["item_id"]: item for item in data["files"][0]["items"]}
    assert items["hello"]["failed"] is True
    assert items["hello"]["translated"] == "Hello"
    assert items["bye"]["failed"] is False
    assert items["bye"]["translated"] == "Пока"


# ---------------------------------------------------------------------------
# Identical lines inside one file
# ---------------------------------------------------------------------------


def _completed_task_with_rows(
    tmp_path: Path, extract_dir: Path, filename: str, rows: list[tuple[str, str, str, bool]]
) -> str:
    """Completed task whose *filename* has ``(item_id, original, translated, success)`` rows."""
    task_id = str(uuid.uuid4())
    db.create_task_row(
        task_id=task_id,
        client_token="tok",
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="in.mod",
        target_lang="russian",
    )
    db.update_task_row(
        task_id,
        status="completed",
        extract_dir=str(extract_dir),
        result_path=str(tmp_path / "out.mod"),
        input_path=str(tmp_path / "missing.mod"),
    )
    for item_id, original, translated, success in rows:
        db.insert_translation(
            task_id=task_id,
            original=original,
            translated=translated,
            file=filename,
            item_id=item_id,
            success=success,
        )
    return task_id


def _creature_ids(stem: str, count: int) -> list[str]:
    """Item ids the .git extractor gives the FirstName of placed creatures."""
    return [f"{stem}_Creature List_{i}_FirstName" for i in range(count)]


def test_edit_of_a_shared_row_reaches_every_identical_line(rebuild_client) -> None:
    """Three guards placed in one area are one editor row; its edit patches all three."""
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    names = ["Guard", "Guard", "Captain", "Guard"]
    write_gff(
        extract_dir / "area.git",
        {
            "StructType": "GIT",
            "Creature List": [
                {"Tag": f"NPC{i}", "FirstName": {"StrRef": -1, "Value": name}}
                for i, name in enumerate(names)
            ],
        },
        file_type="GIT",
    )
    ids = _creature_ids("area", len(names))
    task_id = _completed_task_with_rows(
        tmp_path,
        extract_dir,
        "area.git",
        [
            (item_id, name, "Капитан" if name == "Captain" else "Стражник", True)
            for item_id, name in zip(ids, names)
        ],
    )

    items = client.get(f"/api/tasks/{task_id}/translations").json()["files"][0]["items"]
    assert [(it["original"], it["item_id"], it["duplicate_item_ids"]) for it in items] == [
        ("Guard", ids[0], [ids[1], ids[3]]),
        ("Captain", ids[2], []),
    ]

    # The editor sends one edit per changed row, addressed by the row's item_id.
    edits = [{"file": "area.git", "item_id": items[0]["item_id"], "translated": "Часовой"}]
    resp = client.post(
        f"/api/tasks/{task_id}/rebuild", json={"edits": edits, "target_lang": "russian"}
    )
    assert resp.status_code == 200, resp.text

    creatures = read_gff(extract_dir / "area.git", source_encoding="cp1251")["Creature List"]
    assert [c["FirstName"]["Value"] for c in creatures] == [
        "Часовой",
        "Часовой",
        "Капитан",
        "Часовой",
    ]
    items = client.get(f"/api/tasks/{task_id}/translations").json()["files"][0]["items"]
    assert [(it["translated"], it["duplicate_item_ids"]) for it in items] == [
        ("Часовой", [ids[1], ids[3]]),
        ("Капитан", []),
    ]


def test_identical_lines_with_different_translations_stay_separate(rebuild_client) -> None:
    """A row never hides a line whose translation differs from the one it shows."""
    client, tmp_path = rebuild_client
    ids = _creature_ids("area", 3)
    task_id = _completed_task_with_rows(
        tmp_path,
        tmp_path,
        "area.git",
        [
            (ids[0], "Rubble", "Обломки", True),
            (ids[1], "Rubble", "Щебень", True),
            (ids[2], "Rubble", "Обломки", True),
        ],
    )

    items = client.get(f"/api/tasks/{task_id}/translations").json()["files"][0]["items"]
    assert [(it["translated"], it["item_id"], it["duplicate_item_ids"]) for it in items] == [
        ("Обломки", ids[0], [ids[2]]),
        ("Щебень", ids[1], []),
    ]


def _rebuild(client: TestClient, task_id: str, edits: list[tuple[str, str, str]]) -> None:
    resp = client.post(
        f"/api/tasks/{task_id}/rebuild",
        json={
            "edits": [
                {"file": filename, "item_id": item_id, "translated": translated}
                for filename, item_id, translated in edits
            ],
            "target_lang": "russian",
        },
    )
    assert resp.status_code == 200, resp.text


def _editor_rows(client: TestClient, task_id: str, filename: str) -> list[dict]:
    files = client.get(f"/api/tasks/{task_id}/translations").json()["files"]
    return next(f["items"] for f in files if f["filename"] == filename)


def _area_with_guards(extract_dir: Path, count: int) -> None:
    write_gff(
        extract_dir / "area.git",
        {
            "StructType": "GIT",
            "Creature List": [
                {"Tag": f"NPC{i}", "FirstName": {"StrRef": -1, "Value": "Guard"}}
                for i in range(count)
            ],
        },
        file_type="GIT",
    )


def test_edit_skips_identical_lines_with_another_translation(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    _area_with_guards(extract_dir, 3)
    ids = _creature_ids("area", 3)
    task_id = _completed_task_with_rows(
        tmp_path,
        extract_dir,
        "area.git",
        [
            (ids[0], "Guard", "Стражник", True),
            (ids[1], "Guard", "Страж", True),
            (ids[2], "Guard", "Стражник", True),
        ],
    )

    _rebuild(client, task_id, [("area.git", ids[0], "Часовой")])

    assert [
        (it["translated"], it["item_id"]) for it in _editor_rows(client, task_id, "area.git")
    ] == [
        ("Часовой", ids[0]),
        ("Страж", ids[1]),
    ]


def test_fixed_failed_line_is_no_longer_failed(rebuild_client) -> None:
    """Fixing the failed occurrence to the good translation merges it into a clean row."""
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    _area_with_guards(extract_dir, 3)
    ids = _creature_ids("area", 3)
    task_id = _completed_task_with_rows(
        tmp_path,
        extract_dir,
        "area.git",
        [
            (ids[0], "Guard", "Стражник", True),
            (ids[1], "Guard", "Стражник", True),
            (ids[2], "Guard", "Guard", False),
        ],
    )

    _rebuild(client, task_id, [("area.git", ids[2], "Стражник")])

    rows = _editor_rows(client, task_id, "area.git")
    assert [(it["item_id"], it["duplicate_item_ids"], it["failed"]) for it in rows] == [
        (ids[0], [ids[1], ids[2]], False)
    ]


def test_edit_of_a_dialog_line_does_not_reach_identical_lines(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    extract_dir = tmp_path / "ex"
    extract_dir.mkdir()
    write_gff(
        extract_dir / "a.dlg",
        {
            "StructType": "DLG",
            "EntryList": [
                {"Text": {"StrRef": -1, "Value": "Hi."}, "Speaker": ""} for _ in range(2)
            ],
            "ReplyList": [],
        },
        file_type="DLG",
    )
    task_id = _completed_task_with_rows(
        tmp_path,
        extract_dir,
        "a.dlg",
        [("a:entry:0", "Hi.", "Привет.", True), ("a:entry:1", "Hi.", "Привет.", True)],
    )

    _rebuild(client, task_id, [("a.dlg", "a:entry:1", "Здорово.")])

    entries = read_gff(extract_dir / "a.dlg", source_encoding="cp1251")["EntryList"]
    assert [entry["Text"]["Value"] for entry in entries] == ["Привет.", "Здорово."]
    assert [it["translated"] for it in _editor_rows(client, task_id, "a.dlg")] == [
        "Привет.",
        "Здорово.",
    ]


def test_legacy_dialog_rows_without_item_id_are_not_merged(rebuild_client) -> None:
    """Rows stored before item ids group like other files: by original and translation."""
    client, tmp_path = rebuild_client
    task_id = _completed_task_with_rows(tmp_path, tmp_path, "OLD.DLG", [])
    for original, translated in [("Hi.", "Привет."), ("Bye.", "Пока."), ("Hi.", "Привет.")]:
        db.get_db().execute(
            "INSERT INTO translations (task_id, original, translated, file, item_id) "
            "VALUES (?, ?, ?, ?, NULL)",
            (task_id, original, translated, "OLD.DLG"),
        )
    db.get_db().commit()

    rows = _editor_rows(client, task_id, "OLD.DLG")
    assert [(it["original"], it["item_id"], it["duplicate_item_ids"]) for it in rows] == [
        ("Hi.", "", []),
        ("Bye.", "", []),
    ]


def test_upper_case_dialog_extension_keeps_one_row_per_node(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    task_id = _completed_task_with_rows(
        tmp_path,
        tmp_path,
        "A.DLG",
        [("a:entry:0", "Hi.", "Привет.", True), ("a:reply:0", "Hi.", "Привет.", True)],
    )

    rows = _editor_rows(client, task_id, "A.DLG")
    assert [(it["item_id"], it["duplicate_item_ids"]) for it in rows] == [
        ("a:entry:0", []),
        ("a:reply:0", []),
    ]


def test_shared_row_is_failed_when_any_of_its_lines_failed(rebuild_client) -> None:
    client, tmp_path = rebuild_client
    ids = _creature_ids("area", 2)
    task_id = _completed_task_with_rows(
        tmp_path,
        tmp_path,
        "area.git",
        [(ids[0], "Guard", "Guard", True), (ids[1], "Guard", "Guard", False)],
    )

    items = client.get(f"/api/tasks/{task_id}/translations").json()["files"][0]["items"]
    assert [(it["item_id"], it["duplicate_item_ids"], it["failed"]) for it in items] == [
        (ids[0], [ids[1]], True)
    ]
