"""Clients of the web app with an isolated task manager (``tests/conftest.py`` isolates the DB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nwn_translator.web.app import create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager


@pytest.fixture
def task_workspace(tmp_path: Path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def client(task_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """The app with a translation that writes ``FAKE_MOD`` at once instead of translating."""
    set_task_manager(TaskManager(workspace_root=task_workspace))

    def fake_translate(self):
        out = self.config.output_file
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE_MOD")
        self.stats["files_processed"] = 3
        self.stats["items_translated"] = 10
        return out

    monkeypatch.setattr("nwn_translator.main.ModuleTranslator.translate", fake_translate)
    with TestClient(create_app()) as test_client:
        yield test_client
    set_task_manager(None)


@pytest.fixture
def owner_client(task_workspace: Path):
    """The app acting as the owner ``tok`` of the tasks the test seeds."""
    set_task_manager(TaskManager(workspace_root=task_workspace))
    with TestClient(create_app()) as test_client:
        test_client.headers["X-Client-Token"] = "tok"
        yield test_client
    set_task_manager(None)
