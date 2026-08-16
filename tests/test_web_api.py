"""Tests for FastAPI web layer."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.web import database as db
from nwn_translator.web import routes as web_routes
from nwn_translator.web.app import create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager


@pytest.fixture
def task_workspace(tmp_path: Path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def client(task_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """App with isolated task manager and mocked long-running translation."""
    tm = TaskManager(workspace_root=task_workspace)
    set_task_manager(tm)

    def fake_translate(self):
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE_MOD")
        self.stats["files_processed"] = 3
        self.stats["items_translated"] = 10
        return out

    monkeypatch.setattr(
        "nwn_translator.web.task_manager.ModuleTranslator.translate",
        fake_translate,
    )

    app = create_app()
    with TestClient(app) as c:
        yield c

    set_task_manager(None)


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_models(client: TestClient) -> None:
    r = client.get("/api/models")
    assert r.status_code == 200
    data = r.json()
    assert "default_model" in data
    assert isinstance(data["models"], list)
    assert len(data["models"]) >= 1


def test_test_connection_mocked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProvider:
        model = "fake/model"

        def translate(self, text, source_lang, target_lang, context=None, glossary_block=None):
            return TranslationResult(
                translated="тест",
                original=text,
                success=True,
            )

        def get_provider_name(self):
            return "openrouter"

    monkeypatch.setattr(
        "nwn_translator.web.routes.create_provider",
        lambda api_key, model=None, **kw: FakeProvider(),
    )

    r = client.post(
        "/api/test-connection",
        json={"api_key": "sk-test", "target_lang": "russian"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["translated"] == "тест"


def test_translate_invalid_reasoning_effort(client: TestClient) -> None:
    files = {"file": ("tiny.mod", b"\x00" * 200, "application/octet-stream")}
    data = {
        "api_key": "sk-or-test",
        "target_lang": "russian",
        "source_lang": "auto",
        "preserve_tokens": "true",
        "use_context": "true",
        "reasoning_effort": "not-a-valid-effort",
    }
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 400


def test_translate_status_download(client: TestClient) -> None:
    files = {"file": ("tiny.mod", b"\x00" * 200, "application/octet-stream")}
    data = {
        "api_key": "sk-or-test",
        "target_lang": "russian",
        "source_lang": "auto",
        "preserve_tokens": "true",
        "use_context": "true",
    }
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    deadline = time.time() + 5.0
    status_payload = {}
    while time.time() < deadline:
        s = client.get(f"/api/tasks/{task_id}/status")
        assert s.status_code == 200
        status_payload = s.json()
        if status_payload["status"] == "completed":
            break
        time.sleep(0.05)
    assert status_payload.get("status") == "completed", status_payload
    assert status_payload.get("target_lang") == "russian"

    d = client.get(f"/api/tasks/{task_id}/download")
    assert d.status_code == 200
    assert d.content == b"FAKE_MOD"


def test_translate_rate_limit_second_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_translate(self):
        time.sleep(0.5)
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"SLOW")
        self.stats["files_processed"] = 1
        self.stats["items_translated"] = 1
        return out

    monkeypatch.setattr(
        "nwn_translator.web.task_manager.ModuleTranslator.translate",
        slow_translate,
    )

    files = {"file": ("a.mod", b"\x01" * 200, "application/octet-stream")}
    data = {"api_key": "sk-x", "target_lang": "english"}
    r1 = client.post("/api/translate", files=files, data=data)
    assert r1.status_code == 200

    files2 = {"file": ("b.mod", b"\x02" * 200, "application/octet-stream")}
    r2 = client.post("/api/translate", files=files, data=data)
    assert r2.status_code == 429


def test_status_reports_a_full_snapshot(client: TestClient) -> None:
    """Task state is the progress API: one request must answer where the job is."""
    files = {"file": ("s.mod", b"\x03" * 200, "application/octet-stream")}
    data = {"api_key": "sk-y", "target_lang": "french"}
    r = client.post("/api/translate", files=files, data=data)
    task_id = r.json()["task_id"]

    resp = client.get(f"/api/tasks/{task_id}/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["task_id"] == task_id
    assert payload["status"]
    assert isinstance(payload["progress"], float)
    assert "phase" in payload
    assert "current_file" in payload


def test_reject_wrong_extension(client: TestClient) -> None:
    files = {"file": ("x.txt", b"hello", "text/plain")}
    data = {"api_key": "sk-z", "target_lang": "russian"}
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 400


def test_reject_cjk_target_lang_not_representable_in_game(client: TestClient) -> None:
    """Legacy Windows code pages cannot encode CJK; API must reject before starting a job."""
    files = {"file": ("m.mod", b"\x00" * 200, "application/octet-stream")}
    for lang in ("korean", "Korean", "chinese", "japanese"):
        data = {"api_key": "sk-cjk", "target_lang": lang}
        r = client.post("/api/translate", files=files, data=data)
        assert r.status_code == 400, lang
        detail = r.json()["detail"]
        assert "NWN" in detail
        assert "Windows" in detail
        assert "Целевой" in detail


# ---------------------------------------------------------------------------
# One-job-per-IP slot: atomic registration and cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_tm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A TaskManager with the SQLite singleton pointed at a temp file."""
    monkeypatch.setenv("NWN_WEB_DB_PATH", str(tmp_path / "web.db"))
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    yield TaskManager(workspace_root=tmp_path / "tasks")
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)


class TestOneJobPerIpSlot:
    def test_concurrent_registration_single_winner(self, isolated_tm: TaskManager) -> None:
        """Two threads race for the same IP slot; exactly one must win."""
        tm = isolated_tm
        tasks = [tm.create_task("9.9.9.9", f"m{i}.mod") for i in range(2)]
        barrier = threading.Barrier(2)
        results: dict[str, bool] = {}

        def attempt(task_id: str) -> None:
            barrier.wait()
            results[task_id] = tm.try_register_active("9.9.9.9", task_id)

        threads = [threading.Thread(target=attempt, args=(t.task_id,)) for t in tasks]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert sorted(results.values()) == [False, True]

    def test_slot_reusable_after_previous_task_finishes(self, isolated_tm: TaskManager) -> None:
        tm = isolated_tm
        first = tm.create_task("9.9.9.9", "a.mod")
        second = tm.create_task("9.9.9.9", "b.mod")
        assert tm.try_register_active("9.9.9.9", first.task_id) is True
        assert tm.try_register_active("9.9.9.9", second.task_id) is False
        first.status = "completed"
        assert tm.try_register_active("9.9.9.9", second.task_id) is True

    def test_cancel_request_releases_ip_slot(self, isolated_tm: TaskManager) -> None:
        """Cancel must free the one-job-per-IP slot immediately.

        The worker only releases the slot when it reaches a cancellation
        checkpoint, and a hung provider call can take minutes to time out;
        the user must be able to start a new translation right away.
        """
        tm = isolated_tm
        set_task_manager(tm)
        try:
            task = tm.create_task("9.9.9.9", "a.mod", client_token="tok")
            assert tm.try_register_active("9.9.9.9", task.task_id)
            task.status = "translating"
            with TestClient(create_app()) as client:
                resp = client.post(
                    f"/api/tasks/{task.task_id}/cancel", headers={"X-Client-Token": "tok"}
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"
            assert task.is_cancel_requested()
            assert tm.active_task_id_for_ip("9.9.9.9") is None
        finally:
            set_task_manager(None)

    def test_discard_task_removes_memory_and_db_row(self, isolated_tm: TaskManager) -> None:
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        assert db.get_task_row(task.task_id) is not None
        tm.discard_task(task.task_id)
        assert tm.get(task.task_id) is None
        assert db.get_task_row(task.task_id) is None


# ---------------------------------------------------------------------------
# TTL purge of workspace files
# ---------------------------------------------------------------------------


def _fill_workspace(tm: TaskManager, task_id: str) -> Path:
    """Create a realistic task workspace: input, extraction temp, result."""
    base = tm.workspace_for_task(task_id)
    (base / "input.mod").write_bytes(b"\x01" * 64)
    (base / "temp").mkdir(exist_ok=True)
    (base / "temp" / "area.git").write_bytes(b"\x02" * 32)
    (base / "result.mod").write_bytes(b"\x03" * 64)
    return base


class TestPurgeExpiredWorkspace:
    def test_expired_finished_task_files_deleted_row_kept(self, isolated_tm: TaskManager) -> None:
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        base = _fill_workspace(tm, task.task_id)
        task.status = "completed"
        task.created_at -= tm.task_ttl_seconds + 10
        db.update_task_row(task.task_id, status="completed", created_at=task.created_at)

        tm.purge_expired()

        assert not base.exists()
        assert tm.get(task.task_id) is None  # evicted from memory
        assert db.get_task_row(task.task_id) is not None  # history row kept

    def test_fresh_finished_task_files_kept(self, isolated_tm: TaskManager) -> None:
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        base = _fill_workspace(tm, task.task_id)
        task.status = "completed"
        db.update_task_row(task.task_id, status="completed")

        tm.purge_expired()

        assert base.is_dir()
        assert tm.get(task.task_id) is not None

    def test_old_running_task_files_kept(self, isolated_tm: TaskManager) -> None:
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        base = _fill_workspace(tm, task.task_id)
        task.created_at -= tm.task_ttl_seconds + 10
        db.update_task_row(task.task_id, status="translating", created_at=task.created_at)

        tm.purge_expired()

        assert base.is_dir()

    def test_expired_task_absent_from_memory_still_purged(self, isolated_tm: TaskManager) -> None:
        """Restart scenario: a completed row survives in the DB, its task object
        does not — the workspace directory must still be cleaned up."""
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        base = _fill_workspace(tm, task.task_id)
        db.update_task_row(task.task_id, status="completed")
        old_created = task.created_at - tm.task_ttl_seconds - 10
        conn = db.get_db()
        conn.execute(
            "UPDATE tasks SET created_at = ? WHERE task_id = ?",
            (old_created, task.task_id),
        )
        conn.commit()

        fresh_tm = TaskManager(
            workspace_root=tm.workspace_root, task_ttl_seconds=tm.task_ttl_seconds
        )
        assert fresh_tm.get(task.task_id) is None  # not reloaded into memory

        fresh_tm.purge_expired()

        assert not base.exists()
        assert db.get_task_row(task.task_id) is not None


def test_second_request_during_upload_gets_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The IP slot is claimed before the upload, not after it.

    The first request is held inside the (mocked) upload; a second request from
    the same IP must be rejected immediately instead of slipping through the
    old check-then-act window that spanned the whole upload.
    """
    upload_started = threading.Event()
    release_upload = threading.Event()

    async def held_upload(upload, dest: Path, max_bytes: int) -> None:
        dest.write_bytes(b"\x01" * 10)
        upload_started.set()
        while not release_upload.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", held_upload)

    results: dict[str, object] = {}

    def first_post() -> None:
        files = {"file": ("a.mod", b"\x01" * 200, "application/octet-stream")}
        data = {"api_key": "sk-x", "target_lang": "russian"}
        results["first"] = client.post("/api/translate", files=files, data=data)

    worker = threading.Thread(target=first_post)
    worker.start()
    try:
        assert upload_started.wait(timeout=5.0), "first request never reached the upload"
        files2 = {"file": ("b.mod", b"\x02" * 200, "application/octet-stream")}
        data2 = {"api_key": "sk-x", "target_lang": "russian"}
        r2 = client.post("/api/translate", files=files2, data=data2)
        assert r2.status_code == 429
    finally:
        release_upload.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    first = results["first"]
    assert first.status_code == 200  # type: ignore[attr-defined]


def test_failed_upload_frees_slot(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upload error must release the IP slot and discard the task."""
    original_upload = web_routes._stream_upload_to_file

    async def broken_upload(upload, dest: Path, max_bytes: int) -> None:
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", broken_upload)
    files = {"file": ("a.mod", b"\x01" * 200, "application/octet-stream")}
    data = {"api_key": "sk-x", "target_lang": "russian"}
    r1 = client.post("/api/translate", files=files, data=data)
    assert r1.status_code == 413

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", original_upload)
    r2 = client.post("/api/translate", files=files, data=data)
    assert r2.status_code == 200


def test_reject_cjk_source_lang_not_representable_in_game(client: TestClient) -> None:
    files = {"file": ("m.mod", b"\x00" * 200, "application/octet-stream")}
    data = {
        "api_key": "sk-cjk2",
        "target_lang": "russian",
        "source_lang": "korean",
    }
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "NWN" in detail
    assert "Windows" in detail
    assert "Исходный" in detail


def test_translate_streamed_upload_bytes_preserved(
    client: TestClient, task_workspace: Path
) -> None:
    """Large body is written via chunked read; on-disk file matches payload."""
    payload = (b"\xab\xcd" * 700) * 1024  # ~1.4 MiB
    files = {"file": ("chunky.mod", payload, "application/octet-stream")}
    data = {"api_key": "sk-stream", "target_lang": "russian"}
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    saved = task_workspace / task_id / "chunky.mod"
    assert saved.is_file()
    assert saved.read_bytes() == payload


def test_translate_rejects_oversized_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nwn_translator.web.routes.MAX_UPLOAD_BYTES", 800)
    payload = b"y" * 900
    files = {"file": ("huge.mod", payload, "application/octet-stream")}
    data = {"api_key": "sk-big", "target_lang": "russian"}
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 413
