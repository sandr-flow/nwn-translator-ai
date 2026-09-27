"""The HTTP API: translation jobs, their lifecycle, models, key checks and uploads."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.ai_providers.openrouter_models import FALLBACK
from nwn_translator.config import DEFAULT_MODEL, TranslationCancelled
from nwn_translator.web import database as db
from nwn_translator.web import routes as web_routes
from nwn_translator.web.app import UploadLimitMiddleware, create_app
from nwn_translator.web.task_manager import TaskManager, get_task_manager, set_task_manager


def _post(client: TestClient, name: str = "m.mod", body: bytes = b"\x05" * 200, **data):
    form = {"api_key": "sk-x", "target_lang": "english", **data}
    return client.post(
        "/api/translate", files={"file": (name, body, "application/octet-stream")}, data=form
    )


def _start(client: TestClient, **kwargs) -> str:
    response = _post(client, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


def _wait_for_status(client: TestClient, task_id: str, status: str, **kwargs) -> dict:
    """Poll the status endpoint until the task reaches *status* (or 5 s pass)."""
    deadline = time.time() + 5.0
    payload: dict = {}
    while time.time() < deadline:
        payload = client.get(f"/api/tasks/{task_id}/status", **kwargs).json()
        if payload["status"] == status:
            break
        time.sleep(0.05)
    return payload


def _wait_until_idle(client: TestClient) -> None:
    deadline = time.time() + 5.0
    while client.get("/api/health").json()["active_tasks"] and time.time() < deadline:
        time.sleep(0.05)
    assert client.get("/api/health").json()["active_tasks"] == 0


def _translate_with(monkeypatch, body: Callable) -> None:
    """Make the job run *body(job)* and then write ``DONE`` as its module."""

    def translate(self):
        body(self)
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"DONE")
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", translate)


class _Gate:
    """A job that signals its start and waits to be released."""

    def __init__(self) -> None:
        self.started, self.release = threading.Event(), threading.Event()

    def __call__(self, job) -> None:
        self.started.set()
        self.release.wait(timeout=10)


# ---------------------------------------------------------------------------
# Health and the lifecycle of a job
# ---------------------------------------------------------------------------


def test_health_counts_a_running_job_until_it_finishes(client, monkeypatch):
    """The deploy waits on ``active_tasks``; it must reflect a live worker."""
    assert client.get("/api/health").json() == {"status": "ok", "active_tasks": 0}
    gate = _Gate()
    _translate_with(monkeypatch, gate)

    task_id = _start(client, name="h.mod")
    assert client.get("/api/health").json()["active_tasks"] == 1

    gate.release.set()
    assert _wait_for_status(client, task_id, "completed")["status"] == "completed"
    assert client.get("/api/health").json()["active_tasks"] == 0


def test_translate_status_and_download(client):
    task_id = _start(
        client,
        name="tiny.mod",
        body=b"\x00" * 200,
        api_key="sk-or-test",
        target_lang="russian",
        source_lang="auto",
        preserve_tokens="true",
        use_context="true",
    )
    status = client.get(f"/api/tasks/{task_id}/status").json()
    assert status["task_id"] == task_id and status["status"]
    assert isinstance(status["progress"], float)
    assert "phase" in status and "current_file" in status

    payload = _wait_for_status(client, task_id, "completed")

    assert (payload["status"], payload["target_lang"]) == ("completed", "russian")
    download = client.get(f"/api/tasks/{task_id}/download")
    assert (download.status_code, download.content) == (200, b"FAKE_MOD")


def test_running_job_leaves_the_default_executor_free(client, monkeypatch):
    """Endpoints using ``asyncio.to_thread`` must answer while a translation runs."""
    gate = _Gate()
    finished = threading.Event()
    _translate_with(monkeypatch, lambda job: (gate(job), finished.set()))
    monkeypatch.setattr(web_routes, "refresh_catalog", lambda force=False: {})

    def single_thread_default_executor() -> None:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))

    client.portal.call(single_thread_default_executor)  # type: ignore[union-attr]

    task_id = _start(client, name="x.mod")
    try:
        assert gate.started.wait(timeout=5)
        assert client.get("/api/models").status_code == 200
        assert not finished.is_set(), "/api/models waited for the translation"
    finally:
        gate.release.set()
    assert _wait_for_status(client, task_id, "completed")["status"] == "completed"


def test_deleting_a_running_task_cancels_it_and_frees_the_slot(client, task_workspace, monkeypatch):
    """The worker must stop spending the client's budget; the slot is freed at once."""
    gate = _Gate()
    cancel_seen: dict = {}

    def job(self):
        # A running job holds its trace file open; the removal must still get it.
        self.config.translation_log_writer.write({"event": "model_request"})
        gate(self)
        cancel_seen[self.config.input_file.parent.name] = self.config.cancel_check()

    _translate_with(monkeypatch, job)
    task_id = _start(client, name="run.mod")
    workspace = task_workspace / task_id
    assert gate.started.wait(timeout=5)

    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/tasks/{task_id}/status").status_code == 404
    assert client.get("/api/health").json()["active_tasks"] == 1  # the worker still runs
    assert workspace.is_dir()
    second = _start(client, name="next.mod", body=b"\x06" * 200)  # the slot is free

    gate.release.set()
    _wait_until_idle(client)
    assert (cancel_seen[task_id], cancel_seen[second]) == (True, False)
    assert not workspace.exists()
    assert (task_workspace / second).is_dir()


def test_a_deleted_task_stays_active_until_its_workspace_is_removed(
    client, task_workspace, monkeypatch
):
    """The deploy recreates the container at zero active tasks; cleanup must be done by then."""
    gate = _Gate()
    removing, finish_removal = threading.Event(), threading.Event()
    real_rmtree = shutil.rmtree

    def slow_rmtree(path, *args, **kwargs):
        removing.set()
        finish_removal.wait(timeout=10)
        real_rmtree(path, *args, **kwargs)

    _translate_with(monkeypatch, gate)
    task_id = _start(client, name="run.mod")
    assert gate.started.wait(timeout=5)
    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    monkeypatch.setattr("nwn_translator.web.task_manager.shutil.rmtree", slow_rmtree)
    try:
        gate.release.set()
        assert removing.wait(timeout=5)
        assert client.get("/api/health").json()["active_tasks"] == 1
    finally:
        finish_removal.set()
    _wait_until_idle(client)
    assert not (task_workspace / task_id).exists()


def test_deleting_a_finished_task_removes_its_workspace_at_once(client, task_workspace):
    task_id = _start(client, name="d.mod", body=b"\x0a" * 200)
    assert _wait_for_status(client, task_id, "completed")["status"] == "completed"
    get_task_manager().join_workers()

    assert client.delete(f"/api/tasks/{task_id}").json() == {"ok": True}
    assert not (task_workspace / task_id).exists()
    assert client.get("/api/health").json()["active_tasks"] == 0
    assert client.get(f"/api/tasks/{task_id}/status").status_code == 404


def test_health_ignores_tasks_interrupted_by_a_restart(task_workspace):
    """Rows left unfinished by a dead process are terminal and must not block deploys."""
    task = TaskManager(workspace_root=task_workspace).create_task("1.2.3.4", "old.mod")
    task.status = "translating"
    db.update_task_row(task.task_id, status="translating")

    restarted = TaskManager(workspace_root=task_workspace)

    assert restarted.get(task.task_id).status == "interrupted"
    assert restarted.active_task_count() == 0


def test_a_failed_job_records_its_error_and_frees_the_slot(client, monkeypatch):
    def failing(job):
        raise RuntimeError("boom")

    _translate_with(monkeypatch, failing)
    task_id = _start(client, name="f.mod", body=b"\x08" * 200)
    payload = _wait_for_status(client, task_id, "failed")

    assert (payload["status"], payload["error"]) == ("failed", "boom")
    assert (payload["progress"], payload["phase"], payload["current_file"]) == (1.0, None, None)
    row = db.get_task_row(task_id)
    assert (row["status"], row["error"], row["progress"], row["phase"]) == (
        "failed",
        "boom",
        1.0,
        None,
    )
    assert client.get(f"/api/tasks/{task_id}/download").status_code == 400
    assert _post(client, name="f.mod", body=b"\x08" * 200).status_code == 200


def test_a_cancelled_job_ends_cancelled_without_an_error(client, monkeypatch):
    gate = _Gate()

    def cancellable(job):
        gate(job)
        if job.config.cancel_check():
            raise TranslationCancelled()

    _translate_with(monkeypatch, cancellable)
    task_id = _start(client, name="c.mod", body=b"\x09" * 200)
    assert gate.started.wait(timeout=5)
    try:
        assert client.post(f"/api/tasks/{task_id}/cancel").json() == {
            "ok": True,
            "status": "cancelling",
        }
    finally:
        gate.release.set()
    payload = _wait_for_status(client, task_id, "cancelled")

    assert (payload["status"], payload["error"]) == ("cancelled", None)
    assert (payload["progress"], payload["phase"], payload["current_file"]) == (1.0, None, None)
    row = db.get_task_row(task_id)
    assert (row["status"], row["error"]) == ("cancelled", None)


def test_cancel_frees_the_ip_slot_and_persists_cancelling(task_workspace):
    """A hung provider call can take minutes to time out; the user must be able to go on."""
    manager = TaskManager(workspace_root=task_workspace)
    set_task_manager(manager)
    try:
        task = manager.create_task("9.9.9.9", "a.mod", client_token="tok")
        assert manager.try_register_active("9.9.9.9", task.task_id)
        task.status = "translating"
        db.update_task_row(task.task_id, status="translating")
        with TestClient(create_app()) as client:
            response = client.post(
                f"/api/tasks/{task.task_id}/cancel", headers={"X-Client-Token": "tok"}
            )
        assert (response.status_code, response.json()["status"]) == (200, "cancelling")
        assert task.is_cancel_requested() and task.status == "cancelling"
        assert manager.active_task_id_for_ip("9.9.9.9") is None
        assert db.get_task_row(task.task_id)["status"] == "cancelling"
    finally:
        set_task_manager(None)


def test_shutdown_waits_for_running_jobs(task_workspace, monkeypatch):
    """Job threads are daemons: only the lifespan's join keeps a stop from cutting a job off."""
    gate = _Gate()
    _translate_with(monkeypatch, gate)
    set_task_manager(TaskManager(workspace_root=task_workspace))
    try:
        client = TestClient(create_app())
        client.__enter__()
        task_id = _start(client, name="s.mod", body=b"\x0b" * 200)
        assert gate.started.wait(timeout=5)
        stopper = threading.Thread(target=client.__exit__, args=(None, None, None))
        stopper.start()
        stopper.join(timeout=0.5)
        waited = stopper.is_alive()
        gate.release.set()
        stopper.join(timeout=10)
    finally:
        gate.release.set()
        set_task_manager(None)

    assert waited, "shutdown did not wait for the running job"
    assert not stopper.is_alive()
    assert db.get_task_row(task_id)["status"] == "completed"


def test_history_and_status_of_tasks_known_only_to_the_database(client):
    done = "3f2c7a1e-8b4d-4e6f-a0b1-c2d3e4f5a6b7"
    broken = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
    db.create_task_row(
        done, "tok", "1.1.1.1", 200.0, "done.mod", "russian", "auto", model="vendor/m"
    )
    db.update_task_row(
        done,
        status="completed",
        result_path=str(Path("w") / "done_russian.mod"),
        updated_at=300.0,
        stats={
            "files_processed": 2,
            "errors": [f"e{i}" for i in range(7)],
            "metrics": {"requests": [{"id": 1}], "calls": 3},
        },
    )
    db.create_task_row(broken, "tok", "1.1.1.1", 100.0, "broken.mod", "german")
    db.update_task_row(broken, status="failed", error="boom")
    conn = db.get_db()
    conn.execute("UPDATE tasks SET stats = ? WHERE task_id = ?", ("{not json", broken))
    conn.commit()
    headers = {"X-Client-Token": "tok"}
    compact = {
        "files_processed": 2,
        "errors": ["e0", "e1", "e2", "e3", "e4"],
        "total_errors": 7,
        "metrics": {"calls": 3},
    }

    assert client.get("/api/history", headers=headers).json() == {
        "items": [
            {
                "task_id": done,
                "input_filename": "done.mod",
                "status": "completed",
                "created_at": 200.0,
                "target_lang": "russian",
                "source_lang": "auto",
                "model": "vendor/m",
                "updated_at": 300.0,
                "stats": compact,
            },
            {
                "task_id": broken,
                "input_filename": "broken.mod",
                "status": "failed",
                "created_at": 100.0,
                "target_lang": "german",
                "source_lang": None,
                "model": None,
                "updated_at": None,
                "stats": None,
            },
        ]
    }
    assert client.get("/api/history").json() == {"items": []}
    base = {"progress": 0.0, "current_file": None, "phase": None}
    assert client.get(f"/api/tasks/{done}/status", headers=headers).json() == {
        "task_id": done,
        "status": "completed",
        **base,
        "result_filename": "done_russian.mod",
        "error": None,
        "stats": compact,
        "target_lang": "russian",
    }
    assert client.get(f"/api/tasks/{broken}/status", headers=headers).json() == {
        "task_id": broken,
        "status": "failed",
        **base,
        "result_filename": None,
        "error": "boom",
        "stats": None,
        "target_lang": "german",
    }


# ---------------------------------------------------------------------------
# Job settings and statistics
# ---------------------------------------------------------------------------


def test_texts_translated_counts_every_editor_row(client, monkeypatch):
    """``texts_translated`` is the number of stored rows, rejected lines included."""

    def job(self):
        writer = self.config.translation_log_writer
        writer.write({"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"})
        writer.write(
            {"original": "B", "translated": "B", "file": "a.uti", "item_id": "2", "success": False}
        )

    _translate_with(monkeypatch, job)
    payload = _wait_for_status(client, _start(client, name="rows.mod"), "completed")

    assert payload["status"] == "completed", payload
    assert payload["stats"]["texts_translated"] == 2


def test_a_failed_row_count_keeps_the_job_completed(client, monkeypatch):
    """The module is already written; a lost statistic must not refuse the download."""

    def locked(task_id: str) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("nwn_translator.web.task_manager.count_translations", locked)
    task_id = _start(client, name="count.mod", body=b"\x04" * 200)
    payload = _wait_for_status(client, task_id, "completed")

    assert (payload["status"], payload["error"]) == ("completed", None)
    assert "texts_translated" not in payload["stats"]
    assert client.get(f"/api/tasks/{task_id}/download").content == b"FAKE_MOD"


@pytest.mark.parametrize(("requested", "expected"), [(None, 7), ("3", 3), ("0", 1), ("10000", 7)])
def test_requested_concurrency_is_capped_by_the_server_setting(
    client, monkeypatch, requested, expected
):
    """The job's parallelism sizes thread pools; an API client must not raise it."""
    monkeypatch.setenv("NWN_TRANSLATE_MAX_CONCURRENT", "7")
    seen: list = []
    _translate_with(monkeypatch, lambda job: seen.append(job.config.max_concurrent_requests))
    data = {} if requested is None else {"max_concurrent_requests": requested}

    task_id = _start(client, name="c.mod", body=b"\x07" * 200, **data)

    assert _wait_for_status(client, task_id, "completed")["status"] == "completed"
    assert seen == [expected]


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        (
            {},
            {
                "api_key": "sk-x",
                "target_lang": "english",
                "source_lang": "auto",
                "model": DEFAULT_MODEL,
                "player_gender": "male",
                "reasoning_effort": None,
                "preserve_tokens": True,
                "use_context": True,
            },
        ),
        (
            {
                "api_key": " sk-x ",
                "target_lang": " russian ",
                "source_lang": "   ",
                "model": " vendor/m ",
                "player_gender": "  ",
                "reasoning_effort": "HIGH",
                "preserve_tokens": "false",
                "use_context": "false",
            },
            {
                "api_key": "sk-x",
                "target_lang": "russian",
                "source_lang": "auto",
                "model": "vendor/m",
                "player_gender": "male",
                "reasoning_effort": "high",
                "preserve_tokens": False,
                "use_context": False,
            },
        ),
    ],
)
def test_form_fields_reach_the_translation_config_normalized(client, monkeypatch, form, expected):
    """A renamed job field or a lost normalization would fall back to config defaults."""
    seen: list = []
    _translate_with(
        monkeypatch, lambda job: seen.append({k: getattr(job.config, k) for k in expected})
    )

    task_id = _start(client, name="f.mod", body=b"\x07" * 200, **form)

    assert _wait_for_status(client, task_id, "completed")["status"] == "completed"
    assert seen == [expected]
    row = db.get_task_row(task_id)
    assert (row["target_lang"], row["source_lang"], row["model"]) == (
        expected["target_lang"],
        "auto",
        form.get("model", "").strip() or None,
    )


# ---------------------------------------------------------------------------
# Rejected requests
# ---------------------------------------------------------------------------


def test_a_second_job_of_one_ip_is_refused(client, monkeypatch):
    _translate_with(monkeypatch, lambda job: time.sleep(0.5))
    assert _post(client, name="a.mod", body=b"\x01" * 200).status_code == 200
    assert _post(client, name="b.mod", body=b"\x02" * 200).status_code == 429


@pytest.mark.parametrize(
    "data, status, detail",
    [
        ({"reasoning_effort": "not-a-valid-effort"}, 400, None),
        # Legacy Windows code pages cannot encode CJK text.
        *(
            ({"target_lang": lang}, 400, "Целевой")
            for lang in ("korean", "Korean", "chinese", "japanese")
        ),
        ({"target_lang": "russian", "source_lang": "korean"}, 400, "Исходный"),
    ],
)
def test_invalid_jobs_are_refused_before_they_start(client, data, status, detail):
    response = _post(client, body=b"\x00" * 200, **data)
    assert response.status_code == status
    if detail:
        message = response.json()["detail"]
        assert "NWN" in message and "Windows" in message and detail in message


def test_only_module_archives_are_accepted(client):
    response = client.post(
        "/api/translate",
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"api_key": "sk-z", "target_lang": "russian"},
    )
    assert response.status_code == 400


def test_rebuild_without_a_result_path_reports_unavailable_files(client, tmp_path):
    task_id = "0d6f1c3e-5b7a-4c2d-9e8f-1a2b3c4d5e6f"
    db.create_task_row(task_id, "", "1.1.1.1", 1.0, "m.mod", target_lang="russian")
    db.update_task_row(task_id, status="completed", extract_dir=str(tmp_path))

    response = client.post(f"/api/tasks/{task_id}/rebuild", json={"edits": []})

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Извлечённые файлы модуля недоступны (возможно, были очищены)"
    }


# ---------------------------------------------------------------------------
# Models and key checks
# ---------------------------------------------------------------------------


def test_models_list_the_catalog_with_reasoning(client, monkeypatch):
    monkeypatch.setattr(web_routes, "refresh_catalog", lambda force=False: dict(FALLBACK))
    data = client.get("/api/models").json()
    assert data["default_model"] and data["models"]
    assert {"id", "reasoning"} <= set(data["models"][0])
    flash = next(m for m in data["models"] if m["id"] == "google/gemini-3.8-flash")
    assert (flash["reasoning"]["supported"], flash["reasoning"]["mandatory"]) == (True, True)
    assert flash["reasoning"]["supported_efforts"] == ["low", "medium", "high"]


def test_model_lookup(client, monkeypatch):
    monkeypatch.setattr(
        web_routes, "lookup_model_reasoning", lambda slug: (slug in FALLBACK, FALLBACK.get(slug))
    )
    found = client.get("/api/models/lookup", params={"slug": "google/gemini-3.8-flash"}).json()
    assert found["found"] is True
    assert "none" not in found["reasoning"]["supported_efforts"]
    assert found["reasoning"]["supported_efforts"][0] == "low"
    missing = client.get("/api/models/lookup", params={"slug": "vendor/does-not-exist"}).json()
    assert (missing["found"], missing["reasoning"]["supported"]) == (False, False)
    assert client.get("/api/models/lookup", params={"slug": "not a slug"}).status_code == 400


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("ok", {"ok": True, "translated": "тест", "error": None, "model": "fake/model"}),
        ("raises", {"ok": False, "translated": None, "error": "boom", "model": None}),
        (
            "unparseable",
            {"ok": False, "translated": None, "error": "bad reply", "model": "fake/model"},
        ),
    ],
)
def test_key_check_closes_the_client_whatever_the_outcome(client, monkeypatch, outcome, expected):
    closed: list = []

    class FakeProvider:
        model = "fake/model"

        async def translate_async(self, text, source_lang, target_lang, json_attempts=2):
            if outcome == "raises":
                raise RuntimeError("boom")
            if outcome == "ok":
                return TranslationResult(translated="тест", original=text)
            return TranslationResult(translated="", original=text, success=False, error="bad reply")

        async def close_async_client(self):
            closed.append(True)

        def get_provider_name(self):
            return "openrouter"

    monkeypatch.setattr(
        web_routes, "create_provider", lambda api_key, model=None, **kw: FakeProvider()
    )

    response = client.post(
        "/api/test-connection", json={"api_key": "sk-test", "target_lang": "russian"}
    )

    assert response.status_code == 200
    assert response.json() == {**expected, "provider": "openrouter"}
    assert closed == [True]


def test_key_check_sends_one_request_for_an_unparseable_reply(client, monkeypatch):
    calls: list = []

    async def unparseable(self, system, user, **kwargs):
        calls.append(user)
        return "not json"

    monkeypatch.setattr(
        "nwn_translator.ai_providers.openrouter_provider.OpenRouterProvider._complete", unparseable
    )

    body = client.post("/api/test-connection", json={"api_key": "sk-or-test"}).json()

    assert (body["ok"], body["error"]) == (False, "Model returned empty or unparseable JSON")
    assert len(calls) == 1


def test_key_check_rejects_an_unknown_reasoning_effort(client):
    body = client.post(
        "/api/test-connection", json={"api_key": "sk-test", "reasoning_effort": "bogus"}
    ).json()
    assert (body["ok"], body["model"], body["provider"]) == (False, None, "openrouter")
    assert body["error"].startswith("Invalid reasoning_effort 'bogus'")


@pytest.mark.parametrize(
    "api_key,expected",
    [
        ("pza-abcdef", {"provider": "polza", "label": "POLZA.AI"}),
        ("sk-or-v1-abc", {"provider": "openrouter", "label": "OpenRouter"}),
        ("   ", {"provider": "", "label": ""}),
    ],
)
def test_detect_provider(client, api_key, expected):
    response = client.post("/api/detect-provider", json={"api_key": api_key})
    assert (response.status_code, response.json()) == (200, expected)


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def test_uploaded_bytes_are_stored_unchanged(client, task_workspace):
    payload = (b"\xab\xcd" * 700) * 1024  # about 1.4 MiB, written in chunks
    task_id = _start(client, name="chunky.mod", body=payload)
    assert (task_workspace / task_id / "chunky.mod").read_bytes() == payload


def test_the_ip_slot_is_claimed_before_the_upload(client, monkeypatch):
    """A second request during the first one's upload is refused at once."""
    upload_started, release_upload = threading.Event(), threading.Event()

    async def held_upload(upload, dest: Path) -> None:
        dest.write_bytes(b"\x01" * 10)
        upload_started.set()
        while not release_upload.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", held_upload)
    results: dict = {}
    worker = threading.Thread(target=lambda: results.update(first=_post(client, name="a.mod")))
    worker.start()
    try:
        assert upload_started.wait(timeout=5.0), "first request never reached the upload"
        assert _post(client, name="b.mod", body=b"\x02" * 200).status_code == 429
    finally:
        release_upload.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert results["first"].status_code == 200


def test_failed_upload_frees_the_slot_and_leaves_no_workspace(client, task_workspace, monkeypatch):
    """A discarded task has no row, so no TTL purge would ever remove its directory."""
    original_upload = web_routes._stream_upload_to_file

    async def interrupted_upload(upload, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", interrupted_upload)
    assert _post(client, name="a.mod").status_code == 413
    assert list(task_workspace.iterdir()) == []

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", original_upload)
    assert _post(client, name="a.mod").status_code == 200


def _chunks(*sizes: int) -> list:
    """``http.request`` messages carrying bodies of the given sizes."""
    return [
        {"type": "http.request", "body": b"x" * size, "more_body": i < len(sizes) - 1}
        for i, size in enumerate(sizes)
    ]


def _run_upload_limit(messages: list, headers: list, path: str = "/api/translate"):
    """Feed *messages* through the middleware into an app that reads the whole body.

    Returns:
        Bodies the app received, messages left unread, and the raised error.
    """
    pending = list(messages)
    delivered: list = []

    async def receive() -> dict:
        return pending.pop(0)

    async def body_reader(scope, receive, send) -> None:
        while True:
            message = await receive()
            delivered.append(message["body"])
            if not message["more_body"]:
                return

    middleware = UploadLimitMiddleware(body_reader, path="/api/translate", max_bytes=25)
    try:
        asyncio.run(middleware({"type": "http", "path": path, "headers": headers}, receive, None))
    except HTTPException as error:
        return delivered, pending, error
    return delivered, pending, None


def test_upload_limit_applies_while_the_body_streams_in():
    # A declared oversize is refused before anything is read (Starlette would spool
    # the whole body to disk first); the rest is drained for the client.
    delivered, pending, error = _run_upload_limit(
        _chunks(10, 10, 10, 10), [(b"content-length", b"40")]
    )
    assert (error.status_code, delivered, pending) == (413, [], [])
    # An undeclared body is stopped at the limit.
    delivered, pending, error = _run_upload_limit(_chunks(10, 10, 10, 10), [])
    assert (error.status_code, delivered, pending) == (413, [b"x" * 10, b"x" * 10], [])
    # Bodies within the limit and other paths pass.
    assert _run_upload_limit(_chunks(10, 15), [(b"content-length", b"25")])[2] is None
    other = _run_upload_limit(_chunks(40), [(b"content-length", b"40")], path="/api/health")
    assert other[2] is None and other[0] == [b"x" * 40]


def test_oversized_upload_gets_the_413_message(task_workspace, monkeypatch):
    monkeypatch.setattr("nwn_translator.web.app.MAX_UPLOAD_BYTES", 1024)
    set_task_manager(TaskManager(workspace_root=task_workspace))
    try:
        with TestClient(create_app()) as client:
            response = _post(client, name="big.mod", body=b"z" * 4096, api_key="k", target_lang="x")
    finally:
        set_task_manager(None)
    assert response.status_code == 413
    assert response.json() == {"detail": "Файл слишком большой (максимум 0 МБ)"}
    assert not task_workspace.exists()
