"""Tests for FastAPI web layer."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.web import database as db
from nwn_translator.web import routes as web_routes
from nwn_translator.web.app import UploadLimitMiddleware, create_app
from nwn_translator.web.schemas import RebuildEdit
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
        "nwn_translator.main.ModuleTranslator.translate",
        fake_translate,
    )

    app = create_app()
    with TestClient(app) as c:
        yield c

    set_task_manager(None)


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "active_tasks": 0}


def test_health_counts_running_job_until_it_finishes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deploy waits on ``active_tasks``; it must reflect a live worker."""
    release = threading.Event()

    def blocking_translate(self):
        release.wait(timeout=5)
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"DONE")
        return out

    monkeypatch.setattr(
        "nwn_translator.main.ModuleTranslator.translate",
        blocking_translate,
    )

    files = {"file": ("h.mod", b"\x05" * 200, "application/octet-stream")}
    data = {"api_key": "sk-x", "target_lang": "english"}
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    assert client.get("/api/health").json()["active_tasks"] == 1

    release.set()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if client.get(f"/api/tasks/{task_id}/status").json()["status"] == "completed":
            break
        time.sleep(0.05)
    assert client.get("/api/health").json()["active_tasks"] == 0


def _single_thread_default_executor() -> None:
    """Shrink the running loop's default executor to one thread."""
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))


def test_running_job_leaves_the_default_executor_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoints using ``asyncio.to_thread`` must answer while a translation runs.

    With the job on the loop's default executor, one busy thread was enough to
    block ``/api/models`` until the translation finished.
    """
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_translate(self):
        started.set()
        release.wait(timeout=10)
        finished.set()
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"DONE")
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", blocking_translate)
    monkeypatch.setattr(web_routes, "refresh_catalog", lambda force=False: {})
    client.portal.call(_single_thread_default_executor)  # type: ignore[union-attr]

    files = {"file": ("x.mod", b"\x05" * 200, "application/octet-stream")}
    r = client.post(
        "/api/translate", files=files, data={"api_key": "sk-x", "target_lang": "english"}
    )
    assert r.status_code == 200
    try:
        assert started.wait(timeout=5)
        assert client.get("/api/models").status_code == 200
        assert not finished.is_set(), "/api/models waited for the translation"
    finally:
        release.set()
    assert _wait_for_status(client, r.json()["task_id"], "completed")["status"] == "completed"


def test_deleting_a_running_task_cancels_it_and_frees_the_slot(
    client: TestClient, task_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete must stop the job, free the IP slot, and leave the workspace to the worker.

    Otherwise the worker keeps translating (and spending the client's budget)
    against a removed workspace while the deploy gate no longer counts it. The
    slot is freed at once, as on cancel.
    """
    started = threading.Event()
    release = threading.Event()
    cancel_seen: dict[str, bool] = {}

    def blocking_translate(self):
        started.set()
        release.wait(timeout=10)
        cancel_seen[self.config.input_file.parent.name] = self.config.cancel_check()
        out = self.config.output_file
        out.write_bytes(b"DONE")
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", blocking_translate)
    data = {"api_key": "sk-x", "target_lang": "english"}
    files = {"file": ("run.mod", b"\x05" * 200, "application/octet-stream")}
    task_id = client.post("/api/translate", files=files, data=data).json()["task_id"]
    workspace = task_workspace / task_id
    assert started.wait(timeout=5)

    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/tasks/{task_id}/status").status_code == 404
    assert client.get("/api/health").json()["active_tasks"] == 1  # the worker still runs
    assert workspace.is_dir()
    files = {"file": ("next.mod", b"\x06" * 200, "application/octet-stream")}
    second = client.post("/api/translate", files=files, data=data)
    assert second.status_code == 200, "the deleted job kept the IP slot"

    release.set()
    deadline = time.time() + 5.0
    while client.get("/api/health").json()["active_tasks"] and time.time() < deadline:
        time.sleep(0.05)
    assert client.get("/api/health").json()["active_tasks"] == 0
    assert cancel_seen[task_id] is True
    assert cancel_seen[second.json()["task_id"]] is False
    assert not workspace.exists()
    assert (task_workspace / second.json()["task_id"]).is_dir()


def test_health_ignores_tasks_interrupted_by_a_restart(
    task_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows left unfinished by a dead process are terminal and must not block deploys."""
    tm = TaskManager(workspace_root=task_workspace)
    task = tm.create_task("1.2.3.4", "old.mod")
    task.status = "translating"
    db.update_task_row(task.task_id, status="translating")

    restarted = TaskManager(workspace_root=task_workspace)
    assert restarted.get(task.task_id).status == "interrupted"
    assert restarted.active_task_count() == 0


def test_models(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nwn_translator.ai_providers.openrouter_models import FALLBACK

    monkeypatch.setattr(web_routes, "refresh_catalog", lambda force=False: dict(FALLBACK))
    r = client.get("/api/models")
    assert r.status_code == 200
    data = r.json()
    assert data["default_model"]
    assert isinstance(data["models"], list)
    assert len(data["models"]) >= 1
    first = data["models"][0]
    assert "id" in first
    assert "reasoning" in first
    flash = next(m for m in data["models"] if m["id"] == "google/gemini-3.8-flash")
    assert flash["reasoning"]["supported"] is True
    assert flash["reasoning"]["mandatory"] is True
    assert flash["reasoning"]["supported_efforts"] == ["low", "medium", "high"]


def test_model_lookup_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from nwn_translator.ai_providers.openrouter_models import FALLBACK

    def fake_lookup(slug: str):
        info = FALLBACK.get(slug)
        return (info is not None), info

    monkeypatch.setattr(web_routes, "lookup_model_reasoning", fake_lookup)
    r = client.get("/api/models/lookup", params={"slug": "google/gemini-3.8-flash"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert "none" not in body["reasoning"]["supported_efforts"]
    assert body["reasoning"]["supported_efforts"][0] == "low"


def test_model_lookup_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_routes, "lookup_model_reasoning", lambda slug: (False, None))
    r = client.get("/api/models/lookup", params={"slug": "vendor/does-not-exist"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert body["reasoning"]["supported"] is False


def test_model_lookup_invalid_slug(client: TestClient) -> None:
    r = client.get("/api/models/lookup", params={"slug": "not a slug"})
    assert r.status_code == 400


def test_test_connection_mocked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProvider:
        model = "fake/model"
        closed = False

        async def translate_async(self, text, source_lang, target_lang):
            return TranslationResult(
                translated="тест",
                original=text,
                success=True,
            )

        async def close_async_client(self):
            FakeProvider.closed = True

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
    assert body == {
        "ok": True,
        "translated": "тест",
        "error": None,
        "model": "fake/model",
        "provider": "openrouter",
    }
    assert FakeProvider.closed is True


@pytest.mark.parametrize(
    "api_key,expected",
    [
        ("pza-abcdef", {"provider": "polza", "label": "POLZA.AI"}),
        ("sk-or-v1-abc", {"provider": "openrouter", "label": "OpenRouter"}),
        ("   ", {"provider": "", "label": ""}),
    ],
)
def test_detect_provider(client: TestClient, api_key: str, expected: dict) -> None:
    r = client.post("/api/detect-provider", json={"api_key": api_key})
    assert r.status_code == 200
    assert r.json() == expected


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


def _wait_for_status(client: TestClient, task_id: str, status: str) -> dict:
    """Poll the status endpoint until the task reaches *status* (or 5 s pass)."""
    deadline = time.time() + 5.0
    payload: dict = {}
    while time.time() < deadline:
        payload = client.get(f"/api/tasks/{task_id}/status").json()
        if payload["status"] == status:
            break
        time.sleep(0.05)
    return payload


def test_texts_translated_counts_every_editor_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``texts_translated`` is the number of stored rows, rejected lines included."""

    def translate_with_rows(self):
        writer = self.config.translation_log_writer
        writer.write({"original": "A", "translated": "А", "file": "a.uti", "item_id": "1"})
        writer.write(
            {"original": "B", "translated": "B", "file": "a.uti", "item_id": "2", "success": False}
        )
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"MOD")
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", translate_with_rows)
    files = {"file": ("rows.mod", b"\x04" * 200, "application/octet-stream")}
    r = client.post(
        "/api/translate", files=files, data={"api_key": "sk-x", "target_lang": "english"}
    )
    payload = _wait_for_status(client, r.json()["task_id"], "completed")

    assert payload["status"] == "completed", payload
    assert payload["stats"]["texts_translated"] == 2


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 7), ("3", 3), ("0", 1), ("10000", 7)],
)
def test_requested_concurrency_is_capped_by_the_server_setting(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    requested: str | None,
    expected: int,
) -> None:
    """The job's parallelism sizes thread pools; an API client must not raise it."""
    monkeypatch.setenv("NWN_TRANSLATE_MAX_CONCURRENT", "7")
    seen: list[int] = []

    def recording_translate(self):
        seen.append(self.config.max_concurrent_requests)
        out = self.config.output_file
        out.write_bytes(b"MOD")
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", recording_translate)
    data = {"api_key": "sk-x", "target_lang": "english"}
    if requested is not None:
        data["max_concurrent_requests"] = requested
    files = {"file": ("c.mod", b"\x07" * 200, "application/octet-stream")}
    r = client.post("/api/translate", files=files, data=data)
    assert _wait_for_status(client, r.json()["task_id"], "completed")["status"] == "completed"
    assert seen == [expected]


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
        "nwn_translator.main.ModuleTranslator.translate",
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


def test_rebuild_without_a_result_path_reports_unavailable_files(
    client: TestClient, tmp_path: Path
) -> None:
    """A completed row that lost its result path cannot be rebuilt."""
    task_id = "0d6f1c3e-5b7a-4c2d-9e8f-1a2b3c4d5e6f"
    db.create_task_row(task_id, "", "1.1.1.1", 1.0, "m.mod", target_lang="russian")
    db.update_task_row(task_id, status="completed", extract_dir=str(tmp_path))

    r = client.post(f"/api/tasks/{task_id}/rebuild", json={"edits": []})

    assert r.status_code == 400
    assert r.json() == {"detail": "Извлечённые файлы модуля недоступны (возможно, были очищены)"}


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

    def test_cancel_persists_cancelling_status(self, isolated_tm: TaskManager) -> None:
        """Cancel must write ``cancelling`` to memory and SQLite immediately.

        Without this, history/resume keep showing a live translating job while
        the worker is stuck on an in-flight LLM call.
        """
        tm = isolated_tm
        set_task_manager(tm)
        try:
            task = tm.create_task("9.9.9.9", "a.mod", client_token="tok")
            assert tm.try_register_active("9.9.9.9", task.task_id)
            task.status = "translating"
            db.update_task_row(task.task_id, status="translating")
            with TestClient(create_app()) as client:
                resp = client.post(
                    f"/api/tasks/{task.task_id}/cancel", headers={"X-Client-Token": "tok"}
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"
            assert task.status == "cancelling"
            row = db.get_task_row(task.task_id)
            assert row is not None
            assert row["status"] == "cancelling"
        finally:
            set_task_manager(None)

    def test_progress_callback_does_not_clobber_cancelling(self, isolated_tm: TaskManager) -> None:
        """In-flight progress updates must not overwrite ``cancelling`` status."""
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod", client_token="tok")
        task.status = "translating"
        db.update_task_row(task.task_id, status="translating")
        cb = tm._make_progress_callback(task)

        task.request_cancel()
        task.status = "cancelling"
        db.update_task_row(task.task_id, status="cancelling")
        # Force an immediate persist by changing phase vs persisted_phase.
        task.persisted_phase = None
        task.last_persist_at = 0.0
        cb("translating", 5, 10, "npc.dlg")

        assert task.status == "cancelling"
        row = db.get_task_row(task.task_id)
        assert row is not None
        assert row["status"] == "cancelling"
        assert row["phase"] == "translating"
        assert row["current_file"] == "npc.dlg"

    def test_deleting_a_task_that_never_ran_removes_it_everywhere(
        self, isolated_tm: TaskManager
    ) -> None:
        """A task that lost the IP race or its upload leaves no row, memory or files."""
        tm = isolated_tm
        task = tm.create_task("9.9.9.9", "a.mod")
        workspace = tm.workspace_for_task(task.task_id)
        assert db.get_task_row(task.task_id) is not None
        tm.delete(task.task_id)
        assert tm.get(task.task_id) is None
        assert db.get_task_row(task.task_id) is None
        assert not workspace.exists()


def test_rebuilds_of_one_task_run_one_at_a_time(
    isolated_tm: TaskManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two quick rebuilds must not interleave patches of the same extracted files.

    Each rebuild also has to start from the edits the previous one stored, or
    the later module silently drops the earlier edit.
    """
    tm = isolated_tm
    task = tm.create_task("9.9.9.9", "a.mod")
    task.extract_dir, task.result_path = tmp_path, tmp_path / "out.mod"
    db.insert_translation(task.task_id, "Goblin", "Гоблин", file="a.utc", item_id="a")
    db.insert_translation(task.task_id, "Orc", "Орк", file="b.utc", item_id="b")
    guard = threading.Lock()
    running: list[int] = []
    overlaps: list[int] = []
    seen: list[dict] = []

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
    threads = [threading.Thread(target=tm.rebuild, args=(task, e, "russian")) for e in edits]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert overlaps == [1, 1]
    assert seen[1] == {"a.utc": {"a": "Гоблин!"}, "b.utc": {"b": "Орк!"}}


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

    async def held_upload(upload, dest: Path) -> None:
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

    async def broken_upload(upload, dest: Path) -> None:
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", broken_upload)
    files = {"file": ("a.mod", b"\x01" * 200, "application/octet-stream")}
    data = {"api_key": "sk-x", "target_lang": "russian"}
    r1 = client.post("/api/translate", files=files, data=data)
    assert r1.status_code == 413

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", original_upload)
    r2 = client.post("/api/translate", files=files, data=data)
    assert r2.status_code == 200


def test_failed_upload_leaves_no_workspace(
    client: TestClient, task_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discarded task has no row, so no TTL purge would ever remove its directory."""

    async def interrupted_upload(upload, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(web_routes, "_stream_upload_to_file", interrupted_upload)
    files = {"file": ("a.mod", b"\x01" * 200, "application/octet-stream")}
    r = client.post("/api/translate", files=files, data={"api_key": "sk-x", "target_lang": "en"})
    assert r.status_code == 413
    assert list(task_workspace.iterdir()) == []


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


# ---------------------------------------------------------------------------
# Upload limit applied while the body streams in
# ---------------------------------------------------------------------------


def _chunks(*sizes: int) -> list[dict]:
    """``http.request`` messages carrying bodies of the given sizes."""
    return [
        {"type": "http.request", "body": b"x" * size, "more_body": i < len(sizes) - 1}
        for i, size in enumerate(sizes)
    ]


def _run_upload_limit(
    messages: list[dict], headers: list[tuple[bytes, bytes]], path: str = "/api/translate"
) -> tuple[list[bytes], list[dict], HTTPException | None]:
    """Feed *messages* through the middleware into an app that reads the whole body.

    Returns:
        Bodies the app received, messages left unread, and the raised error.
    """
    pending = list(messages)
    delivered: list[bytes] = []

    async def receive() -> dict:
        return pending.pop(0)

    async def body_reader(scope, receive, send) -> None:
        while True:
            message = await receive()
            delivered.append(message["body"])
            if not message["more_body"]:
                return

    middleware = UploadLimitMiddleware(body_reader, path="/api/translate", max_bytes=25)
    scope = {"type": "http", "path": path, "headers": headers}
    try:
        asyncio.run(middleware(scope, receive, None))  # type: ignore[arg-type]
    except HTTPException as e:
        return delivered, pending, e
    return delivered, pending, None


def test_upload_limit_rejects_a_declared_oversize_before_reading_it() -> None:
    """Starlette would spool the whole body to disk before the handler ran."""
    delivered, pending, error = _run_upload_limit(
        _chunks(10, 10, 10, 10), [(b"content-length", b"40")]
    )
    assert error is not None and error.status_code == 413
    assert delivered == []
    assert pending == [], "the rest of the body must be drained for the client"


def test_upload_limit_stops_an_undeclared_body_at_the_limit() -> None:
    delivered, pending, error = _run_upload_limit(_chunks(10, 10, 10, 10), [])
    assert error is not None and error.status_code == 413
    assert delivered == [b"x" * 10, b"x" * 10]
    assert pending == []


def test_upload_limit_passes_bodies_within_the_limit_and_other_paths() -> None:
    assert _run_upload_limit(_chunks(10, 15), [(b"content-length", b"25")])[2] is None
    other = _run_upload_limit(_chunks(40), [(b"content-length", b"40")], path="/api/health")
    assert other[2] is None and other[0] == [b"x" * 40]


def test_oversized_upload_gets_the_413_message(
    task_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit answers with the usual error and never reaches the handler."""
    monkeypatch.setattr("nwn_translator.web.app.MAX_UPLOAD_BYTES", 1024)
    set_task_manager(TaskManager(workspace_root=task_workspace))
    try:
        with TestClient(create_app()) as client:
            files = {"file": ("big.mod", b"z" * 4096, "application/octet-stream")}
            r = client.post(
                "/api/translate", files=files, data={"api_key": "k", "target_lang": "x"}
            )
    finally:
        set_task_manager(None)
    assert r.status_code == 413
    assert r.json() == {"detail": "Файл слишком большой (максимум 0 МБ)"}
    assert not task_workspace.exists()
