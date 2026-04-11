"""Tests for FastAPI web layer."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nwn_translator.ai_providers.base import TranslationResult
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


def test_translate_rate_limit_second_request(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_sse_progress_snapshot(client: TestClient) -> None:
    files = {"file": ("s.mod", b"\x03" * 200, "application/octet-stream")}
    data = {"api_key": "sk-y", "target_lang": "french"}
    r = client.post("/api/translate", files=files, data=data)
    task_id = r.json()["task_id"]

    with client.stream("GET", f"/api/tasks/{task_id}/progress") as resp:
        assert resp.status_code == 200
        buf = b""
        for chunk in resp.iter_bytes():
            buf += chunk
            if b"\n\n" in buf:
                break
        text = buf.decode("utf-8", errors="replace")
        assert "data:" in text
        line = [ln for ln in text.split("\n") if ln.startswith("data:")][0]
        payload = json.loads(line.replace("data: ", "", 1))
        assert payload["type"] == "snapshot"


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


def test_translate_rejects_oversized_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nwn_translator.web.routes.MAX_UPLOAD_BYTES", 800)
    payload = b"y" * 900
    files = {"file": ("huge.mod", payload, "application/octet-stream")}
    data = {"api_key": "sk-big", "target_lang": "russian"}
    r = client.post("/api/translate", files=files, data=data)
    assert r.status_code == 413
