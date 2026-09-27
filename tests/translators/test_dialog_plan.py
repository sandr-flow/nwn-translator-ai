"""Packing small dialog files into group requests."""

from pathlib import Path

import pytest

from nwn_translator.translators import dialog_plan
from nwn_translator.translators.dialog_plan import PreparedDialog, pack_groups


def _small(name, script):
    return PreparedDialog(Path(name), 1, {}, {}, {}, {}, script)


def _names(entries):
    return [entry.file_path.name for entry in entries]


@pytest.mark.parametrize(
    "limit, value, scripts",
    [
        ("GROUP_TARGET_CHARS", 8000, ["x" * 4000] * 3),
        ("GROUP_MAX_FILES", 2, ["x" * 10] * 3),
    ],
)
def test_groups_respect_the_character_and_file_limits(monkeypatch, limit, value, scripts):
    monkeypatch.setattr(dialog_plan, limit, value)
    entries = [_small(f"{name}.dlg", script) for name, script in zip("abc", scripts)]

    groups, loners = pack_groups(entries, "russian", None)

    assert [_names(group) for group in groups] == [["a.dlg", "b.dlg"]]
    assert _names(loners) == ["c.dlg"]


def test_a_single_file_is_a_loner():
    assert pack_groups([], "russian", None) == ([], [])
    groups, loners = pack_groups([_small("a.dlg", "x" * 10)], "russian", None)
    assert (groups, _names(loners)) == ([], ["a.dlg"])
