"""Web security: server key only leaks in local mode; CORS denies by default."""

from __future__ import annotations

import os
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
