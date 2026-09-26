"""Editor row model: grouping stored translations into rows and expanding edits."""

from __future__ import annotations

from typing import Any, Dict

from nwn_translator.web.editor import dialog_speaker, expand_edits, group_rows
from nwn_translator.web.schemas import RebuildEdit


def _row(file: str, item_id: str, original: str, translated: str, **extra: Any) -> Dict[str, Any]:
    return {
        "file": file,
        "item_id": item_id,
        "original": original,
        "translated": translated,
        "context": None,
        "success": 1,
        "speaker": None,
        **extra,
    }


def test_identical_lines_of_a_file_share_one_row() -> None:
    rows = [
        _row("a.utc", "a:1", "Goblin", "Гоблин"),
        _row("a.utc", "a:2", "Goblin", "Гоблин", success=0),
        _row("a.utc", "a:3", "Goblin", "Гоблин-2"),
        _row("b.uti", "b:1", "Goblin", "Гоблин"),
        _row("b.uti", "b:2", "", "ignored"),
    ]

    groups = group_rows(rows)

    assert [g.filename for g in groups] == ["a.utc", "b.uti"]
    first, other = groups[0].items
    assert (first.item_id, first.duplicate_item_ids, first.failed) == ("a:1", ["a:2"], True)
    assert first.shared_with == ["b.uti"]
    assert (other.item_id, other.translated, other.duplicate_item_ids) == ("a:3", "Гоблин-2", [])
    assert [i.item_id for i in groups[1].items] == ["b:1"]


def test_dialog_lines_keep_their_own_rows_and_speakers() -> None:
    rows = [
        _row("d.dlg", "d:e0", "Hi", "Привет", speaker={"kind": "npc", "name": "Bob", "tag": "B"}),
        _row("d.dlg", "d:r0", "Hi", "Привет", context="Player reply in d.dlg"),
    ]

    (group,) = group_rows(rows)

    assert [(i.item_id, i.speaker.kind if i.speaker else None) for i in group.items] == [
        ("d:e0", "npc"),
        ("d:r0", "player"),
    ]
    assert group.items[0].speaker is not None and group.items[0].speaker.name == "Bob"


def test_dialog_speaker_falls_back_to_context() -> None:
    tagged = dialog_speaker({"context": "NPC dialog line in d.dlg (speaker: GUARD)"})
    assert tagged is not None and (tagged.kind, tagged.tag) == ("npc", "GUARD")
    owner = dialog_speaker({"context": "NPC dialog line in d.dlg"})
    assert owner is not None and owner.kind == "owner_unknown"
    assert dialog_speaker({"context": "Item name"}) is None


def test_edit_reaches_every_item_of_its_row() -> None:
    rows = [
        _row("a.utc", "a:1", "Goblin", "Гоблин"),
        _row("a.utc", "a:2", "Goblin", "Гоблин"),
        _row("a.utc", "a:3", "Orc", "Орк"),
        _row("d.dlg", "d:e0", "Hi", "Привет"),
        _row("d.dlg", "d:e1", "Hi", "Привет"),
    ]
    edits = [
        RebuildEdit(file="a.utc", item_id="a:2", translated="Гоблин!"),
        RebuildEdit(file="d.dlg", item_id="d:e1", translated="Здравствуй"),
        RebuildEdit(file="x.uti", item_id="x:9", translated="Новое"),
    ]

    assert expand_edits(rows, edits) == {
        ("a.utc", "a:1"): "Гоблин!",
        ("a.utc", "a:2"): "Гоблин!",
        ("d.dlg", "d:e1"): "Здравствуй",
        ("x.uti", "x:9"): "Новое",
    }
