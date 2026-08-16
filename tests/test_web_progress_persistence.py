"""In-flight progress is mirrored into SQLite so reconnecting clients can recover it."""

from __future__ import annotations

from pathlib import Path

import pytest

from nwn_translator.web import database as db
from nwn_translator.web.task_manager import (
    PROGRESS_PERSIST_INTERVAL_SECONDS,
    TaskManager,
    TranslationTask,
)


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    db.init_db(tmp_path / "t.db")


@pytest.fixture
def task(isolated_db: None) -> TranslationTask:
    db.create_task_row(
        task_id="t1",
        client_token="tok",
        client_ip="127.0.0.1",
        created_at=1.0,
        input_filename="m.mod",
    )
    return TranslationTask(task_id="t1", client_ip="127.0.0.1", client_token="tok")


@pytest.fixture
def manager(tmp_path: Path, isolated_db: None) -> TaskManager:
    return TaskManager(workspace_root=tmp_path / "tasks")


def test_progress_callback_persists_state(manager: TaskManager, task: TranslationTask) -> None:
    """The row must carry the live phase, not the one it had at extraction time."""
    callback = manager._make_progress_callback(task)
    callback("scanning", 1, 2, "area01.git")

    row = db.get_task_row("t1")
    assert row is not None
    assert row["status"] == "scanning"
    assert row["phase"] == "scanning"
    assert row["current_file"] == "area01.git"
    assert row["progress"] == pytest.approx(0.055)


def test_repeated_progress_within_interval_is_throttled(
    manager: TaskManager, task: TranslationTask
) -> None:
    """The callback fires per item; only one write per interval may reach SQLite."""
    callback = manager._make_progress_callback(task)
    callback("scanning", 1, 2, "area01.git")
    callback("scanning", 2, 2, "area02.git")

    row = db.get_task_row("t1")
    assert row is not None
    assert row["current_file"] == "area01.git"
    assert row["progress"] == pytest.approx(0.055)


def test_progress_persists_again_after_interval(
    manager: TaskManager, task: TranslationTask
) -> None:
    callback = manager._make_progress_callback(task)
    callback("scanning", 1, 2, "area01.git")
    task.last_persist_at -= PROGRESS_PERSIST_INTERVAL_SECONDS
    callback("scanning", 2, 2, "area02.git")

    row = db.get_task_row("t1")
    assert row is not None
    assert row["current_file"] == "area02.git"
    assert row["progress"] == pytest.approx(0.08)


def test_phase_change_persists_immediately(manager: TaskManager, task: TranslationTask) -> None:
    """A phase change must not wait out the throttle — it is the state users watch."""
    callback = manager._make_progress_callback(task)
    callback("scanning", 1, 2, "area01.git")
    callback("injecting", 1, 1, "patching")

    row = db.get_task_row("t1")
    assert row is not None
    assert row["phase"] == "injecting"
    assert row["current_file"] == "patching"
