"""Shared fixtures and parametrization for the realdata corpus tests.

Any test function that takes a ``corpus_module`` argument is automatically
parametrized over every module archive in the corpus. When the corpus is not
configured the test is collected as a single skipped case, so ``pytest -m
realdata`` is always green (it has nothing to fail on) and the reason is visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._corpus import list_module_paths

_THIS_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag every test in this package with the ``realdata`` marker.

    A ``pytestmark`` in a conftest does not propagate to collected tests, so the
    marker is applied here, keyed on the test's location under this directory.
    """
    for item in items:
        try:
            item_path = Path(str(item.fspath)).resolve()
        except Exception:  # noqa: BLE001 - defensive; fspath should always resolve
            continue
        if _THIS_DIR in item_path.parents:
            item.add_marker(pytest.mark.realdata)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize ``corpus_module`` over the corpus archives."""
    if "corpus_module" not in metafunc.fixturenames:
        return

    modules = list_module_paths()
    if not modules:
        metafunc.parametrize(
            "corpus_module",
            [pytest.param(None, marks=pytest.mark.skip(reason="corpus not configured"))],
        )
        return

    metafunc.parametrize(
        "corpus_module",
        modules,
        ids=[p.name for p in modules],
    )
