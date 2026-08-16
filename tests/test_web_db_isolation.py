"""Guard for the autouse fixture that keeps tests off the developer's database."""

from __future__ import annotations

import os
from pathlib import Path

from nwn_translator.web import database as db


def test_db_path_is_isolated_from_the_repo_workspace() -> None:
    """Losing the ``isolated_web_db`` fixture would silently pollute the live DB.

    That is not a hypothetical: the suite used to write hundreds of fake task
    rows into ``workspace/web/translations.db``, and starting a TaskManager
    against it flags any genuinely running translation as ``interrupted``.
    """
    assert os.environ.get("NWN_WEB_DB_PATH")
    resolved = db._default_db_path().resolve()
    default = (Path.cwd() / "workspace" / "web" / "translations.db").resolve()
    assert resolved != default
