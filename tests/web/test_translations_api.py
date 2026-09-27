"""Editor rows of a finished task and the rebuild endpoint that applies their edits."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from nwn_translator.context.world_context import WorldScanner
from nwn_translator.formats.gff import read_gff
from nwn_translator.pipeline.stages import PipelineState, stage_extract, stage_translate
from nwn_translator.web import database as db
from tests.support.fakes import DialogProvider, make_config
from tests.support.gff_writer import write_gff

PLAYER = {"kind": "player", "name": "", "tag": ""}
OWNER_UNKNOWN = {"kind": "owner_unknown", "name": "", "tag": ""}


def _loc(text: str) -> dict:
    return {"StrRef": -1, "Value": text}


def _task(tmp_path: Path, extract_dir: Path = None, rows=(), filename: str = "") -> str:
    """A completed task owned by ``tok``, with ``(item_id, original, translated, success)`` rows."""
    task_id = str(uuid.uuid4())
    db.create_task_row(task_id, "tok", "1.1.1.1", 1.0, "in.mod", target_lang="russian")
    fields = {"status": "completed"}
    if extract_dir is not None:
        fields.update(
            extract_dir=str(extract_dir),
            result_path=str(tmp_path / "out.mod"),
            input_path=str(tmp_path / "missing.mod"),
        )
    db.update_task_row(task_id, **fields)
    for item_id, original, translated, success in rows:
        db.insert_translation(
            task_id, original, translated, file=filename, item_id=item_id, success=success
        )
    return task_id


def _rebuild(client, task_id: str, *edits) -> None:
    body = {
        "edits": [{"file": f, "item_id": i, "translated": t} for f, i, t in edits],
        "target_lang": "russian",
    }
    response = client.post(f"/api/tasks/{task_id}/rebuild", json=body)
    assert response.status_code == 200, response.text


def _files(client, task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}/translations")
    assert response.status_code == 200, response.text
    return {group["filename"]: group["items"] for group in response.json()["files"]}


def _creature(path: Path, tag: str, name: str) -> None:
    write_gff(path, {"StructType": "UTC", "Tag": tag, "FirstName": _loc(name)}, file_type="UTC")


def _first_names(*paths: Path) -> list:
    return [read_gff(path)["FirstName"]["Value"] for path in paths]


def _area(extract_dir: Path, names) -> list:
    """An area with a creature per name; return the item ids of their first names."""
    creatures = [{"Tag": f"NPC{i}", "FirstName": _loc(name)} for i, name in enumerate(names)]
    write_gff(
        extract_dir / "area.git", {"StructType": "GIT", "Creature List": creatures}, file_type="GIT"
    )
    return [f"area_Creature List_{i}_FirstName" for i in range(len(names))]


@pytest.fixture
def extract_dir(tmp_path: Path) -> Path:
    path = tmp_path / "ex"
    path.mkdir()
    return path


# ---------------------------------------------------------------------------
# Rebuild edits
# ---------------------------------------------------------------------------


def test_edit_reaches_only_its_file_and_is_idempotent(owner_client, tmp_path, extract_dir):
    for name in ("a", "b"):
        _creature(extract_dir / f"{name}.utc", "GOBLIN", "Гоблин")
    task_id = _task(tmp_path, extract_dir)
    for name in ("a.utc", "b.utc"):
        db.insert_translation(task_id, "Goblin", "Гоблин", file=name, item_id="GOBLIN_first_name")

    for _ in range(2):  # re-sending the same edits reproduces the same result
        _rebuild(owner_client, task_id, ("a.utc", "GOBLIN_first_name", "Гоблин-А"))
        assert _first_names(extract_dir / "a.utc", extract_dir / "b.utc") == ["Гоблин-А", "Гоблин"]

    items = _files(owner_client, task_id)["a.utc"]
    assert (items[0]["translated"], items[0]["item_id"]) == ("Гоблин-А", "GOBLIN_first_name")


def test_sequential_rebuilds_keep_every_edit(owner_client, tmp_path, extract_dir):
    """A rebuild of file B must not revert the edit an earlier rebuild made to file A."""
    task_id = _task(tmp_path, extract_dir)
    for name, tag, value in (("a.utc", "GOBLIN", "Гоблин"), ("b.utc", "ORC", "Орк")):
        _creature(extract_dir / name, tag, value)
        db.insert_translation(task_id, value, value, file=name, item_id=f"{tag}_first_name")

    _rebuild(owner_client, task_id, ("a.utc", "GOBLIN_first_name", "Гоблин!"))
    _rebuild(owner_client, task_id, ("b.utc", "ORC_first_name", "Орк!"))

    assert _first_names(extract_dir / "a.utc", extract_dir / "b.utc") == ["Гоблин!", "Орк!"]
    files = _files(owner_client, task_id)
    assert {name: items[0]["translated"] for name, items in files.items()} == {
        "a.utc": "Гоблин!",
        "b.utc": "Орк!",
    }


def test_edit_of_a_shared_row_reaches_every_identical_line(owner_client, tmp_path, extract_dir):
    """Three guards placed in one area are one editor row; its edit patches all three."""
    names = ["Guard", "Guard", "Captain", "Guard"]
    ids = _area(extract_dir, names)
    rows = [
        (item_id, name, "Капитан" if name == "Captain" else "Стражник", True)
        for item_id, name in zip(ids, names)
    ]
    task_id = _task(tmp_path, extract_dir, rows, "area.git")

    items = _files(owner_client, task_id)["area.git"]
    assert [(it["original"], it["item_id"], it["duplicate_item_ids"]) for it in items] == [
        ("Guard", ids[0], [ids[1], ids[3]]),
        ("Captain", ids[2], []),
    ]

    # The editor sends one edit per changed row, addressed by the row's item id.
    _rebuild(owner_client, task_id, ("area.git", items[0]["item_id"], "Часовой"))

    creatures = read_gff(extract_dir / "area.git", source_encoding="cp1251")["Creature List"]
    assert [c["FirstName"]["Value"] for c in creatures] == [
        "Часовой",
        "Часовой",
        "Капитан",
        "Часовой",
    ]
    items = _files(owner_client, task_id)["area.git"]
    assert [(it["translated"], it["duplicate_item_ids"]) for it in items] == [
        ("Часовой", [ids[1], ids[3]]),
        ("Капитан", []),
    ]


def test_edit_skips_identical_lines_with_another_translation(owner_client, tmp_path, extract_dir):
    ids = _area(extract_dir, ["Guard"] * 3)
    translations = ["Стражник", "Страж", "Стражник"]
    rows = [(item_id, "Guard", text, True) for item_id, text in zip(ids, translations)]
    task_id = _task(tmp_path, extract_dir, rows, "area.git")

    _rebuild(owner_client, task_id, ("area.git", ids[0], "Часовой"))

    items = _files(owner_client, task_id)["area.git"]
    assert [(it["translated"], it["item_id"]) for it in items] == [
        ("Часовой", ids[0]),
        ("Страж", ids[1]),
    ]


def test_fixed_failed_line_merges_into_a_clean_row(owner_client, tmp_path, extract_dir):
    ids = _area(extract_dir, ["Guard"] * 3)
    rows = [
        (ids[0], "Guard", "Стражник", True),
        (ids[1], "Guard", "Стражник", True),
        (ids[2], "Guard", "Guard", False),
    ]
    task_id = _task(tmp_path, extract_dir, rows, "area.git")

    _rebuild(owner_client, task_id, ("area.git", ids[2], "Стражник"))

    items = _files(owner_client, task_id)["area.git"]
    assert [(it["item_id"], it["duplicate_item_ids"], it["failed"]) for it in items] == [
        (ids[0], [ids[1], ids[2]], False)
    ]


def test_edit_of_a_dialog_line_does_not_reach_identical_lines(owner_client, tmp_path, extract_dir):
    entries = [{"Text": _loc("Hi."), "Speaker": ""} for _ in range(2)]
    write_gff(
        extract_dir / "a.dlg",
        {"StructType": "DLG", "EntryList": entries, "ReplyList": []},
        file_type="DLG",
    )
    rows = [("a:entry:0", "Hi.", "Привет.", True), ("a:entry:1", "Hi.", "Привет.", True)]
    task_id = _task(tmp_path, extract_dir, rows, "a.dlg")

    _rebuild(owner_client, task_id, ("a.dlg", "a:entry:1", "Здорово."))

    entries = read_gff(extract_dir / "a.dlg", source_encoding="cp1251")["EntryList"]
    assert [entry["Text"]["Value"] for entry in entries] == ["Привет.", "Здорово."]
    assert [it["translated"] for it in _files(owner_client, task_id)["a.dlg"]] == [
        "Привет.",
        "Здорово.",
    ]


# ---------------------------------------------------------------------------
# Editor rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rows, expected",
    [
        # Identical lines with different translations stay separate rows.
        (
            [
                ("x0", "Rubble", "Обломки", True),
                ("x1", "Rubble", "Щебень", True),
                ("x2", "Rubble", "Обломки", True),
            ],
            [("x0", ["x2"], False, "Обломки"), ("x1", [], False, "Щебень")],
        ),
        # A shared row is failed when any of its lines failed.
        (
            [("x0", "Guard", "Guard", True), ("x1", "Guard", "Guard", False)],
            [("x0", ["x1"], True, "Guard")],
        ),
        (
            [("hello", "Hello", "Hello", False), ("bye", "Bye", "Пока", True)],
            [("hello", [], True, "Hello"), ("bye", [], False, "Пока")],
        ),
    ],
)
def test_identical_lines_share_a_row_only_with_the_same_translation(
    owner_client, tmp_path, rows, expected
):
    task_id = _task(tmp_path, rows=rows, filename="area.git")
    items = _files(owner_client, task_id)["area.git"]
    assert [
        (it["item_id"], it["duplicate_item_ids"], it["failed"], it["translated"]) for it in items
    ] == expected


@pytest.mark.parametrize(
    "filename, rows, expected",
    [
        # Rows stored before item ids group like other files: by original and translation.
        (
            "OLD.DLG",
            [(None, "Hi.", "Привет."), (None, "Bye.", "Пока."), (None, "Hi.", "Привет.")],
            [("", []), ("", [])],
        ),
        # An upper-case dialog extension keeps one row per node.
        (
            "A.DLG",
            [("a:entry:0", "Hi.", "Привет."), ("a:reply:0", "Hi.", "Привет.")],
            [("a:entry:0", []), ("a:reply:0", [])],
        ),
    ],
)
def test_dialog_rows_are_never_merged(owner_client, tmp_path, filename, rows, expected):
    task_id = _task(tmp_path)
    for item_id, original, translated in rows:
        db.get_db().execute(
            "INSERT INTO translations (task_id, original, translated, file, item_id) VALUES (?, ?, ?, ?, ?)",
            (task_id, original, translated, filename, item_id),
        )
    db.get_db().commit()

    items = _files(owner_client, task_id)[filename]

    assert [(it["item_id"], it["duplicate_item_ids"]) for it in items] == expected


def test_identical_dialog_lines_stay_separate_rows_with_their_speakers(owner_client, tmp_path):
    task_id = _task(tmp_path)
    severina = {"kind": "npc", "name": "Severina", "tag": "sev_tag"}
    stumpy = {"kind": "npc", "name": "Stumpy", "tag": "stumpy_tag"}
    lines = [
        ("a:entry:0", "Привет.", severina),
        ("a:entry:1", "Здорово.", stumpy),
        ("a:reply:0", "Привет!", PLAYER),
    ]
    for item_id, translated, speaker in lines:
        db.insert_translation(
            task_id, "Hello.", translated, file="a.dlg", item_id=item_id, speaker=speaker
        )
    db.insert_translation(task_id, "Hello.", "Привет.", file="b.dlg", item_id="b:entry:0")
    for item_id in ("x_first_name", "y_first_name"):
        db.insert_translation(task_id, "Goblin", "Гоблин", file="c.utc", item_id=item_id)

    files = _files(owner_client, task_id)

    assert [(it["item_id"], it["translated"], it["speaker"]) for it in files["a.dlg"]] == lines
    assert [it["shared_with"] for it in files["a.dlg"]] == [["b.dlg"]] * 3
    assert files["b.dlg"][0]["shared_with"] == ["a.dlg"]
    (goblin,) = files["c.utc"]
    assert (goblin["speaker"], goblin["shared_with"]) == (None, [])


def test_dialog_rows_without_a_speaker_fall_back_to_the_context(owner_client, tmp_path):
    """Tasks stored before speakers were recorded still label dialog lines."""
    task_id = _task(tmp_path)
    for item_id, context in [
        ("a:entry:0", "NPC dialog line in a.dlg"),
        ("a:entry:1", "Dialog line in a.dlg (speaker: BOB_2)"),
        ("a:reply:0", "Player reply in a.dlg"),
        ("a:entry:2", "Dialog node E2 in a.dlg"),
    ]:
        db.insert_translation(task_id, item_id, "x", context=context, file="a.dlg", item_id=item_id)
    db.insert_translation(
        task_id, "Sword", "Меч", context="Player reply in a.dlg", file="a.uti", item_id="a:0"
    )

    files = _files(owner_client, task_id)

    assert {item["item_id"]: item["speaker"] for item in files["a.dlg"]} == {
        "a:entry:0": OWNER_UNKNOWN,
        "a:entry:1": {"kind": "npc", "name": "", "tag": "BOB_2"},
        "a:reply:0": PLAYER,
        "a:entry:2": None,
    }
    assert files["a.uti"][0]["speaker"] is None


def test_a_translated_dialog_reaches_the_editor_with_its_speakers(owner_client, tmp_path):
    """World scan, extraction, dialog translation, SQLite rows and the editor API."""
    extract_dir = tmp_path / "extract"
    for tag, name, conversation in [
        ("sev_tag", "Severina", "severina"),
        ("stumpy_tag", "Stumpy", "stumpy"),
    ]:
        write_gff(
            extract_dir / f"{conversation}.utc",
            {
                "StructType": "UTC",
                "Tag": tag,
                "FirstName": _loc(name),
                "Conversation": conversation,
            },
            file_type="UTC",
        )
    dlg_path = extract_dir / "severina.dlg"
    hello = _loc("Hello.")
    dialog = {
        "StructType": "DLG",
        "StartingList": [{"Index": 0}],
        "EntryList": [
            {"Text": hello, "Speaker": "", "RepliesList": [{"Index": 0}]},
            {"Text": hello, "Speaker": "stumpy_tag", "RepliesList": []},
        ],
        "ReplyList": [{"Text": hello, "EntriesList": [{"Index": 1}]}],
    }
    write_gff(dlg_path, dialog, file_type="DLG")
    task_id = _task(tmp_path)
    provider = DialogProvider(['{"E0": "Привет.", "E1": "Здорово.", "R0": "Привет!"}'])
    config = make_config(
        api_key="k",
        model="fake/model",
        input_file=tmp_path / "m.mod",
        translation_log_writer=db.SqliteTranslationLogWriter(task_id),
    )
    state = PipelineState(config=config, provider=provider)
    state.extract_dir = extract_dir
    state.world_context = WorldScanner().scan_directory(extract_dir)

    stage_translate(state, stage_extract(state, [dlg_path]))

    assert len(provider.calls) == 1
    assert [
        (item["item_id"], item["translated"], item["speaker"])
        for item in _files(owner_client, task_id)["severina.dlg"]
    ] == [
        ("severina:entry:0", "Привет.", {"kind": "npc", "name": "Severina", "tag": "sev_tag"}),
        ("severina:entry:1", "Здорово.", {"kind": "npc", "name": "Stumpy", "tag": "stumpy_tag"}),
        ("severina:reply:0", "Привет!", PLAYER),
    ]
