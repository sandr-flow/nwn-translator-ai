"""Shared fixtures for the test suite."""

from __future__ import annotations

import builtins
import io
from pathlib import Path
from typing import Any, Callable, List

import pytest

from nwn_translator.web import database as db


@pytest.fixture(autouse=True)
def isolated_web_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test off the real ``workspace/web/translations.db``.

    Without ``NWN_WEB_DB_PATH`` the web layer defaults to a path under the
    working directory, so a test run wrote its fake tasks straight into the
    developer's live database — and ``TaskManager`` startup would flag any
    genuinely running translation as ``interrupted``.

    The file is created lazily by ``init_db``, so tests that never touch the
    database pay nothing but an environment variable.
    """
    db.close_db()
    monkeypatch.setenv("NWN_WEB_DB_PATH", str(tmp_path / "web" / "translations.db"))
    yield
    db.close_db()


@pytest.fixture
def opened_files(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], List[Any]]:
    """Record every file the test opens, through ``open()`` or ``Path.open()``.

    Whether a handle was released shows on its ``closed`` flag, on every
    platform; Windows sharing rules would only catch a leak there.

    Returns:
        A function giving the file objects opened on one path, in order.
    """
    real_open = io.open
    handles: List[Any] = []

    def recording_open(*args: Any, **kwargs: Any) -> Any:
        handle = real_open(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(io, "open", recording_open)
    return lambda path: [h for h in handles if Path(str(h.name)) == Path(path)]
