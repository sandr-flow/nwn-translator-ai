"""Shared fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path

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
