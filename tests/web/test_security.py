"""Web security: server key only leaks in local mode; CORS denies by default; owners only."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from nwn_translator.web import database as db
from nwn_translator.web.__main__ import _enable_local_mode_if_loopback
from nwn_translator.web.app import _parse_cors_origins, create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager

SERVER_KEY = "sk-or-server-secret-value"
OWNER = {"X-Client-Token": "owner-tok"}
INTRUDER = {"X-Client-Token": "intruder"}


@pytest.fixture
def app(task_workspace):
    """Build a fresh app; CORS origins are read when it is created."""
    set_task_manager(TaskManager(workspace_root=task_workspace))
    yield lambda: TestClient(create_app())
    set_task_manager(None)


def _seed_task(owner: str = "owner-tok") -> str:
    task_id = str(uuid.uuid4())
    db.create_task_row(task_id, owner, "1.1.1.1", 1.0, "m.mod", target_lang="russian")
    db.update_task_row(task_id, status="completed")
    return task_id


@pytest.mark.parametrize(
    "host, local",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("example.com", False),
    ],
)
def test_only_a_loopback_bind_enables_local_mode(host, local, monkeypatch):
    monkeypatch.delenv("NWN_WEB_LOCAL_MODE", raising=False)
    assert _enable_local_mode_if_loopback(host) is local
    assert os.environ.get("NWN_WEB_LOCAL_MODE") == ("1" if local else None)


@pytest.mark.parametrize("local", [False, True])
def test_config_exposes_the_server_key_only_in_local_mode(app, monkeypatch, local):
    monkeypatch.setenv("NWN_TRANSLATE_API_KEY", SERVER_KEY)
    if local:
        monkeypatch.setenv("NWN_WEB_LOCAL_MODE", "1")
    else:
        monkeypatch.delenv("NWN_WEB_LOCAL_MODE", raising=False)
    with app() as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["api_key"] == (SERVER_KEY if local else None)
    assert (SERVER_KEY in resp.text) is local


@pytest.mark.parametrize(
    "configured, origin, allowed",
    [(None, "http://evil.example", None), ("https://trusted.example",) * 3],
)
def test_cors_allows_only_configured_origins(app, monkeypatch, configured, origin, allowed):
    if configured:
        monkeypatch.setenv("NWN_WEB_CORS_ORIGINS", configured)
    else:
        monkeypatch.delenv("NWN_WEB_CORS_ORIGINS", raising=False)
        assert _parse_cors_origins() == []
    with app() as client:
        resp = client.get("/api/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == allowed


def test_task_routes_are_open_to_the_owner_only(app):
    """Other routes may fail on preconditions (400/404) but never deny the owner."""
    with app() as client:
        base = f"/api/tasks/{_seed_task()}"
        routes = [("get", f"{base}/{name}", {}) for name in ("status", "download", "log")]
        routes += [("get", f"{base}/translations", {})]
        routes += [("post", f"{base}/rebuild", {"json": {"edits": []}})]
        for method, path, kwargs in routes:
            for headers in (INTRUDER, {}):
                resp = client.request(method, path, headers=headers, **kwargs)
                assert resp.status_code == 403, f"{method} {path} with {headers}"
            resp = client.request(method, path, headers=OWNER, **kwargs)
            assert resp.status_code != 403, f"{method} {path} denied owner"


def test_the_token_may_come_as_a_query_parameter(app):
    """Plain links and navigations cannot send headers, so ``?client_token=`` is accepted."""
    with app() as client:
        base = f"/api/tasks/{_seed_task()}"
        for kind in ("download", "log"):
            assert client.get(f"{base}/{kind}?client_token=owner-tok").status_code != 403
            assert client.get(f"{base}/{kind}?client_token=intruder").status_code == 403
        assert client.get(f"{base}/status?client_token=owner-tok").status_code == 200
        assert client.get(f"{base}/status?client_token=intruder").status_code == 403


def test_ownerless_task_is_accessible_without_token(app):
    with app() as client:
        assert client.get(f"/api/tasks/{_seed_task(owner='')}/status").status_code == 200


def test_only_the_owner_can_cancel_and_delete(app):
    """An empty token must not bypass the owner check."""
    with app() as client:
        base = f"/api/tasks/{_seed_task()}"
        for headers in ({}, INTRUDER):
            assert client.post(f"{base}/cancel", headers=headers).status_code == 403
            assert client.delete(base, headers=headers).status_code == 403
        assert client.post(f"{base}/cancel", headers=OWNER).status_code == 200
        assert client.delete(base, headers=OWNER).status_code == 200
        assert client.get(f"{base}/status", headers=OWNER).status_code == 404
