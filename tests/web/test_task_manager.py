"""TaskManager: the one-job-per-IP slot, progress persistence, purging and rebuilds."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nwn_translator.web import database as db
from nwn_translator.web.schemas import RebuildEdit
from nwn_translator.web.task_manager import (
    PROGRESS_PERSIST_INTERVAL_SECONDS,
    TaskManager,
    TranslationTask,
)

IP = "9.9.9.9"


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    """A TaskManager with its own workspace; ``conftest`` isolates the database."""
    return TaskManager(workspace_root=tmp_path / "tasks")


# ---------------------------------------------------------------------------
# One job per IP
# ---------------------------------------------------------------------------


def test_one_of_two_racing_registrations_wins(manager):
    tasks = [manager.create_task(IP, f"m{i}.mod") for i in range(2)]
    barrier = threading.Barrier(2)
    results: dict = {}

    def attempt(task_id: str) -> None:
        barrier.wait()
        results[task_id] = manager.try_register_active(IP, task_id)

    threads = [threading.Thread(target=attempt, args=(t.task_id,)) for t in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(results.values()) == [False, True]


def test_the_slot_is_free_again_when_the_task_finishes(manager):
    first, second = manager.create_task(IP, "a.mod"), manager.create_task(IP, "b.mod")
    assert manager.try_register_active(IP, first.task_id) is True
    assert manager.try_register_active(IP, second.task_id) is False
    first.status = "completed"
    assert manager.try_register_active(IP, second.task_id) is True


def test_deleting_a_task_that_never_ran_removes_it_everywhere(manager):
    """A task that lost the IP race or its upload leaves no row, memory or files."""
    task = manager.create_task(IP, "a.mod")
    workspace = manager.workspace_for_task(task.task_id)
    assert db.get_task_row(task.task_id) is not None
    manager.delete(task.task_id)
    assert manager.get(task.task_id) is None
    assert db.get_task_row(task.task_id) is None
    assert not workspace.exists()


# ---------------------------------------------------------------------------
# Progress mirrored into SQLite for reconnecting clients
# ---------------------------------------------------------------------------


@pytest.fixture
def task() -> TranslationTask:
    db.create_task_row("t1", "tok", "127.0.0.1", 1.0, "m.mod")
    return TranslationTask(task_id="t1", client_ip="127.0.0.1", client_token="tok")


def _row():
    row = db.get_task_row("t1")
    return row["status"], row["phase"], row["current_file"], row["progress"]


def test_progress_is_persisted_with_the_live_phase(manager, task):
    callback = manager._make_progress_callback(task)

    callback("scanning", 1, 2, "area01.git")
    assert _row() == ("scanning", "scanning", "area01.git", pytest.approx(0.055))

    # One write per interval: the callback fires for every item.
    callback("scanning", 2, 2, "area02.git")
    assert _row()[2:] == ("area01.git", pytest.approx(0.055))

    task.last_persist_at -= PROGRESS_PERSIST_INTERVAL_SECONDS
    callback("scanning", 2, 2, "area02.git")
    assert _row()[2:] == ("area02.git", pytest.approx(0.08))

    # A phase change is written at once: it is the state users watch.
    callback("injecting", 1, 1, "patching")
    assert _row()[1:3] == ("injecting", "patching")


def test_progress_does_not_overwrite_cancelling(manager):
    task = manager.create_task(IP, "a.mod", client_token="tok")
    task.status = "translating"
    db.update_task_row(task.task_id, status="translating")
    callback = manager._make_progress_callback(task)
    task.request_cancel()
    task.status = "cancelling"
    db.update_task_row(task.task_id, status="cancelling")
    # Force an immediate persist by changing the phase against the persisted one.
    task.persisted_phase = None
    task.last_persist_at = 0.0

    callback("translating", 5, 10, "npc.dlg")

    assert task.status == "cancelling"
    row = db.get_task_row(task.task_id)
    assert (row["status"], row["phase"], row["current_file"]) == (
        "cancelling",
        "translating",
        "npc.dlg",
    )


# ---------------------------------------------------------------------------
# Workspace purge after the TTL
# ---------------------------------------------------------------------------


def _task_with_workspace(manager: TaskManager, status: str, expired: bool):
    task = manager.create_task(IP, "a.mod")
    base = manager.workspace_for_task(task.task_id)
    (base / "input.mod").write_bytes(b"\x01" * 64)
    (base / "temp").mkdir(exist_ok=True)
    (base / "temp" / "area.git").write_bytes(b"\x02" * 32)
    (base / "result.mod").write_bytes(b"\x03" * 64)
    if status == "completed":
        task.status = status
    if expired:
        task.created_at -= manager.task_ttl_seconds + 10
    db.update_task_row(task.task_id, status=status, created_at=task.created_at)
    return task, base


@pytest.mark.parametrize(
    "status, expired, purged",
    [("completed", True, True), ("completed", False, False), ("translating", True, False)],
)
def test_purge_removes_the_files_of_expired_finished_tasks(manager, status, expired, purged):
    task, base = _task_with_workspace(manager, status, expired)

    manager.purge_expired()

    assert base.exists() is not purged
    assert (manager.get(task.task_id) is None) is purged  # evicted from memory
    assert db.get_task_row(task.task_id) is not None  # the history row stays


def test_purge_also_finds_tasks_known_only_to_the_database(manager):
    """After a restart the row survives but the task object does not."""
    task, base = _task_with_workspace(manager, "completed", expired=False)
    conn = db.get_db()
    old = task.created_at - manager.task_ttl_seconds - 10
    conn.execute("UPDATE tasks SET created_at = ? WHERE task_id = ?", (old, task.task_id))
    conn.commit()
    fresh = TaskManager(
        workspace_root=manager.workspace_root, task_ttl_seconds=manager.task_ttl_seconds
    )
    assert fresh.get(task.task_id) is None  # not reloaded into memory

    fresh.purge_expired()

    assert not base.exists()
    assert db.get_task_row(task.task_id) is not None


# ---------------------------------------------------------------------------
# Rebuilds
# ---------------------------------------------------------------------------


def test_rebuilds_of_one_task_run_one_at_a_time(manager, tmp_path, monkeypatch):
    """Rebuilds must not interleave patches, and each starts from the edits stored before."""
    task = manager.create_task(IP, "a.mod")
    task.extract_dir, task.result_path = tmp_path, tmp_path / "out.mod"
    db.insert_translation(task.task_id, "Goblin", "Гоблин", file="a.utc", item_id="a")
    db.insert_translation(task.task_id, "Orc", "Орк", file="b.utc", item_id="b")
    guard = threading.Lock()
    running: list = []
    overlaps: list = []
    seen: list = []

    def slow_rebuild(extract_dir, translations, output_path, original_mod_path, target_lang):
        with guard:
            running.append(1)
            overlaps.append(len(running))
        time.sleep(0.2)
        seen.append({name: dict(items) for name, items in translations.items()})
        with guard:
            running.pop()
        return output_path

    monkeypatch.setattr("nwn_translator.web.task_manager.rebuild_module", slow_rebuild)
    edits = [
        [RebuildEdit(file="a.utc", item_id="a", translated="Гоблин!")],
        [RebuildEdit(file="b.utc", item_id="b", translated="Орк!")],
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(manager.rebuild, task, e, "russian") for e in edits]:
            future.result(timeout=5)

    assert overlaps == [1, 1]
    expected = {"a.utc": {"a": "Гоблин!"}, "b.utc": {"b": "Орк!"}}
    assert seen[1] == expected
    assert db.get_item_translation_map_by_task(task.task_id) == expected
