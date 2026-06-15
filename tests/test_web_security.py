"""Web security: server key only leaks in local mode; CORS denies by default."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nwn_translator.web import database as db
from nwn_translator.web.__main__ import _enable_local_mode_if_loopback
from nwn_translator.web.app import _parse_cors_origins, create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager

_SERVER_KEY = "sk-or-server-secret-value"


@pytest.fixture
def isolated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh app/client with task manager and DB pointed at a temp dir."""
    monkeypatch.setenv("NWN_WEB_DB_PATH", str(tmp_path / "web.db"))
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)
    set_task_manager(TaskManager(workspace_root=tmp_path / "tasks"))

    def _make() -> TestClient:
        return TestClient(create_app())

    yield _make

    set_task_manager(None)
    db.close_db()
    monkeypatch.setattr(db, "_connection", None)


# ---------------------------------------------------------------------------
# Local-mode flag derived from the bind host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_enables_local_mode(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NWN_WEB_LOCAL_MODE", raising=False)
    assert _enable_local_mode_if_loopback(host) is True
    assert os.environ.get("NWN_WEB_LOCAL_MODE") == "1"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_non_loopback_bind_leaves_local_mode_unset(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NWN_WEB_LOCAL_MODE", raising=False)
    assert _enable_local_mode_if_loopback(host) is False
    assert "NWN_WEB_LOCAL_MODE" not in os.environ


# ---------------------------------------------------------------------------
# /api/config: server key only in local mode
# ---------------------------------------------------------------------------


def test_config_hides_server_key_when_not_local(
    isolated_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NWN_TRANSLATE_API_KEY", _SERVER_KEY)
    monkeypatch.delenv("NWN_WEB_LOCAL_MODE", raising=False)
    with isolated_app() as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["api_key"] is None
    assert _SERVER_KEY not in resp.text


def test_config_exposes_server_key_in_local_mode(
    isolated_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NWN_TRANSLATE_API_KEY", _SERVER_KEY)
    monkeypatch.setenv("NWN_WEB_LOCAL_MODE", "1")
    with isolated_app() as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["api_key"] == _SERVER_KEY


# ---------------------------------------------------------------------------
# CORS: deny by default, allow only when explicitly configured
# ---------------------------------------------------------------------------


def test_cors_origins_default_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NWN_WEB_CORS_ORIGINS", raising=False)
    assert _parse_cors_origins() == []


def test_cors_default_denies_cross_origin(isolated_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NWN_WEB_CORS_ORIGINS", raising=False)
    with isolated_app() as client:
        resp = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_cors_explicit_origin_is_allowed(isolated_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NWN_WEB_CORS_ORIGINS", "https://trusted.example")
    with isolated_app() as client:
        resp = client.get("/api/health", headers={"Origin": "https://trusted.example"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://trusted.example"


# ---------------------------------------------------------------------------
# Task routes enforce client_token ownership
# ---------------------------------------------------------------------------


def _seed_task(owner: str, status: str = "completed") -> str:
    task_id = str(uuid.uuid4())
    db.create_task_row(
        task_id=task_id,
        client_token=owner,
        client_ip="1.1.1.1",
        created_at=1.0,
        input_filename="m.mod",
        target_lang="russian",
    )
    db.update_task_row(task_id, status=status)
    return task_id


def _task_routes(task_id: str):
    """The six per-task routes that must verify ownership: (method, path, kwargs)."""
    base = f"/api/tasks/{task_id}"
    return [
        ("get", f"{base}/status", {}),
        ("get", f"{base}/progress", {}),
        ("get", f"{base}/download", {}),
        ("get", f"{base}/log", {}),
        ("get", f"{base}/translations", {}),
        ("post", f"{base}/rebuild", {"json": {"edits": []}}),
    ]


def test_task_routes_reject_foreign_and_missing_token(isolated_app) -> None:
    with isolated_app() as client:
        task_id = _seed_task(owner="owner-tok")
        for method, path, kwargs in _task_routes(task_id):
            wrong = client.request(method, path, headers={"X-Client-Token": "intruder"}, **kwargs)
            assert wrong.status_code == 403, f"{method} {path} with foreign token"
            none = client.request(method, path, **kwargs)
            assert none.status_code == 403, f"{method} {path} with no token"


def test_task_routes_allow_owner(isolated_app) -> None:
    with isolated_app() as client:
        task_id = _seed_task(owner="owner-tok")
        headers = {"X-Client-Token": "owner-tok"}
        for method, path, kwargs in _task_routes(task_id):
            # The ownership gate must pass; downstream preconditions may yield
            # 200/400/404, but never 403.
            resp = client.request(method, path, headers=headers, **kwargs)
            assert resp.status_code != 403, f"{method} {path} denied owner ({resp.status_code})"


def test_ownerless_task_is_accessible_without_token(isolated_app) -> None:
    with isolated_app() as client:
        task_id = _seed_task(owner="")
        resp = client.get(f"/api/tasks/{task_id}/status")
        assert resp.status_code == 200
