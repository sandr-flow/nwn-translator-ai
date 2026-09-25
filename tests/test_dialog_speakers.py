"""Dialog line speakers: resolution, pipeline rows, storage and the editor API."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nwn_translator.config import TranslationConfig
from nwn_translator.context.dialog_speakers import dialog_line_speaker
from nwn_translator.context.world_context import NPCInfo, WorldContext, WorldScanner
from nwn_translator.extractors.base import DialogNode, ExtractedContent, TranslatableItem
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.file_handlers.gff_handler import read_gff
from nwn_translator.file_handlers.gff_writer import write_gff
from nwn_translator.main import rebuild_module
from nwn_translator.pipeline.stages import PipelineState, stage_extract, stage_translate
from nwn_translator.translators import context_translator as context_module
from nwn_translator.translators.context_translator import ContextualTranslationManager
from nwn_translator.translators.translation_manager import TranslationManager
from nwn_translator.web import database as db
from nwn_translator.web.app import create_app
from nwn_translator.web.task_manager import TaskManager, set_task_manager

from tests.test_context_translation import _FakeOpenRouter

PLAYER = {"kind": "player", "name": "", "tag": ""}
OWNER_UNKNOWN = {"kind": "owner_unknown", "name": "", "tag": ""}


def _npc(tag: str, first: str = "", last: str = "", conversation: str = "") -> NPCInfo:
    return NPCInfo(
        tag=tag,
        first_name=first,
        last_name=last,
        description="",
        race="Human",
        gender="Female",
        conversation=conversation,
    )


def _world(*npcs: NPCInfo) -> WorldContext:
    world = WorldContext()
    for npc in npcs:
        world.npcs[npc.tag] = npc
    return world


def _loc(text: str) -> dict:
    return {"StrRef": -1, "Value": text}


class _CapturingWriter:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def write(self, entry: dict) -> None:
        self.entries.append(entry)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestDialogLineSpeaker:
    def test_tagged_line_names_known_creature(self):
        world = _world(_npc("stumpy_tag", "Stumpy", "Stoneaxe", conversation="stumpy"))

        speaker = dialog_line_speaker(world, "severina", is_entry=True, speaker_tag="stumpy_tag")

        assert speaker == {"kind": "npc", "name": "Stumpy Stoneaxe", "tag": "stumpy_tag"}

    def test_tagged_line_with_unknown_tag_keeps_the_tag(self):
        world = _world(_npc("sev_tag", "Severina", conversation="severina"))

        speaker = dialog_line_speaker(world, "severina", is_entry=True, speaker_tag="ghost")

        assert speaker == {"kind": "npc", "name": "", "tag": "ghost"}

    def test_owner_line_names_the_dialog_owner(self):
        world = _world(_npc("sev_tag", "Severina", conversation="Severina"))

        speaker = dialog_line_speaker(world, "severina", is_entry=True)

        assert speaker == {"kind": "npc", "name": "Severina", "tag": "sev_tag"}

    def test_dialog_shared_by_two_creatures_lists_both(self):
        world = _world(
            _npc("b_tag", "Bob", conversation="tavern"),
            _npc("a_tag", "Anna", conversation="tavern"),
            _npc("c_tag", "Carl", conversation="other"),
        )

        speaker = dialog_line_speaker(world, "tavern", is_entry=True)

        assert speaker == {"kind": "npc", "name": "Anna / Bob", "tag": "a_tag / b_tag"}

    def test_owners_with_one_name_are_named_once(self):
        world = _world(
            _npc("GUARD2", "Guard", conversation="guard"),
            _npc("GUARD1", "Guard", conversation="guard"),
        )

        speaker = dialog_line_speaker(world, "guard", is_entry=True)

        assert speaker == {"kind": "npc", "name": "Guard", "tag": "GUARD1 / GUARD2"}

    def test_owners_beyond_three_are_counted(self):
        world = _world(*(_npc(f"c{i}", f"Commoner {i}", conversation="commoner") for i in range(5)))

        speaker = dialog_line_speaker(world, "commoner", is_entry=True)

        assert speaker == {
            "kind": "npc",
            "name": "Commoner 0 / Commoner 1 / Commoner 2 +2",
            "tag": "c0 / c1 / c2 +2",
        }

    def test_owner_line_without_owner_is_unknown(self):
        world = _world(_npc("sev_tag", "Severina", conversation="severina"))

        assert dialog_line_speaker(world, "door_talk", is_entry=True) == OWNER_UNKNOWN

    def test_reply_is_the_player(self):
        world = _world(_npc("sev_tag", "Severina", conversation="severina"))

        assert dialog_line_speaker(world, "severina", is_entry=False) == PLAYER
        assert dialog_line_speaker(world, "severina", is_entry=False, speaker_tag="x") == PLAYER

    def test_creature_without_localized_name_shows_its_tag(self):
        world = _world(_npc("sev_tag", conversation="severina"), _npc("bare_tag", " ", " "))

        assert dialog_line_speaker(world, "severina", is_entry=True) == {
            "kind": "npc",
            "name": "sev_tag",
            "tag": "sev_tag",
        }
        assert dialog_line_speaker(world, "severina", is_entry=True, speaker_tag="bare_tag") == {
            "kind": "npc",
            "name": "bare_tag",
            "tag": "bare_tag",
        }

    def test_without_world_context(self):
        assert dialog_line_speaker(None, "severina", is_entry=True) == OWNER_UNKNOWN
        assert dialog_line_speaker(None, "severina", is_entry=True, speaker_tag="bob") == {
            "kind": "npc",
            "name": "",
            "tag": "bob",
        }
        assert dialog_line_speaker(None, "severina", is_entry=False) == PLAYER


def test_prompt_speaker_lines_are_unchanged():
    """The prompt block reads owners through the shared helper; its text stays the same."""
    world = _world(
        NPCInfo("anna_tag", " Anna ", "Smith", "", "Human", "Female", "Tavern"),
        NPCInfo("bare_tag", "", "", "", "Dwarf", "", "tavern"),
        NPCInfo("bob_tag", "Bob", "", "", "Halfling", "Male", "bob"),
    )
    manager = ContextualTranslationManager(
        TranslationConfig(api_key="k", input_file=Path("m.mod")), Mock(), world
    )
    node_map = {
        "E0": DialogNode(node_id=0, text="Hi", is_entry=True),
        "E1": DialogNode(node_id=1, text="Yo", speaker="bob_tag", is_entry=True),
        "E2": DialogNode(node_id=2, text="Boo", speaker="ghost", is_entry=True),
        "R0": DialogNode(node_id=0, text="Hey", is_entry=False),
    }

    assert manager._speaker_lines("tavern", node_map, "tavern.dlg") == [
        "- In tavern.dlg, lines marked [NPC]: spoken by Anna Smith (Human, Female); "
        "or bare_tag (Dwarf)",
        "- In tavern.dlg, lines marked [bob_tag]: spoken by Bob (Halfling, Male)",
    ]


# ---------------------------------------------------------------------------
# Pipeline rows
# ---------------------------------------------------------------------------


def _severina_dlg() -> dict:
    """Three nodes with the same text: owner line, tagged line, player reply."""
    return {
        "StructType": "DLG",
        "StartingList": [{"Index": 0}],
        "EntryList": [
            {"Text": _loc("Hello."), "Speaker": "", "RepliesList": [{"Index": 0}]},
            {"Text": _loc("Hello."), "Speaker": "stumpy_tag", "RepliesList": []},
        ],
        "ReplyList": [{"Text": _loc("Hello."), "EntriesList": [{"Index": 1}]}],
    }


def test_per_file_rows_carry_speakers_for_dialog_lines_only(tmp_path: Path) -> None:
    writer = _CapturingWriter()
    config = TranslationConfig(
        api_key="k", input_file=tmp_path / "m.mod", translation_log_writer=writer
    )
    manager = TranslationManager(config, Mock())
    dlg_path = tmp_path / "severina.dlg"
    dlg_data = _severina_dlg()
    dialog = DialogExtractor().extract(dlg_path, dlg_data)
    uti_path = tmp_path / "a.uti"
    item = ExtractedContent(
        content_type="item",
        items=[TranslatableItem(text="Sword", item_id="a:name", location=str(uti_path))],
        source_file=uti_path,
    )
    state = PipelineState(config=config, provider=Mock())
    state.world_context = _world(
        _npc("sev_tag", "Severina", conversation="severina"),
        _npc("stumpy_tag", "Stumpy", conversation="stumpy"),
    )
    translations = {line.key: "Привет." for line in dialog.items}
    translations[("a.uti", "a:name")] = "Меч"

    state._log_per_file_translations(
        {dlg_path: (dlg_data, dialog, ".dlg"), uti_path: ({}, item, ".uti")},
        translations,
        manager,
    )

    rows = {entry["item_id"]: entry for entry in writer.entries}
    assert rows["severina:entry:0"]["speaker"] == {
        "kind": "npc",
        "name": "Severina",
        "tag": "sev_tag",
    }
    assert rows["severina:entry:1"]["speaker"] == {
        "kind": "npc",
        "name": "Stumpy",
        "tag": "stumpy_tag",
    }
    assert rows["severina:reply:0"]["speaker"] == PLAYER
    assert "speaker" not in rows["a:name"]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _translation_columns() -> set[str]:
    return {row[1] for row in db.get_db().execute("PRAGMA table_info(translations)")}


def test_migration_adds_speaker_column_and_keeps_rows(tmp_path: Path) -> None:
    """A database from before speakers gains the column; a second start is a no-op."""
    path = tmp_path / "before.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, client_token TEXT NOT NULL, client_ip TEXT NOT NULL,
            created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            input_filename TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            original TEXT NOT NULL, translated TEXT NOT NULL, context TEXT, model TEXT,
            file TEXT, item_id TEXT, success INTEGER NOT NULL DEFAULT 1,
            UNIQUE(task_id, file, item_id)
        );
        INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename)
            VALUES ('t1', 'tok', '1.1.1.1', 1.0, 'm.mod');
        INSERT INTO translations (task_id, original, translated, context, file, item_id)
            VALUES ('t1', 'Hello.', 'Привет.', 'Player reply in a.dlg', 'a.dlg', 'a:reply:0');
        """)
    conn.close()

    db.init_db(path)
    assert "speaker" in _translation_columns()
    rows = db.get_translations_by_task("t1")
    assert len(rows) == 1
    assert rows[0]["translated"] == "Привет."
    assert rows[0]["speaker"] is None

    db.close_db()
    db.init_db(path)
    assert "speaker" in _translation_columns()
    assert db.get_translations_by_task("t1") == rows


def test_old_unique_key_migration_ends_with_speaker_column(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, client_token TEXT NOT NULL, client_ip TEXT NOT NULL,
            created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            input_filename TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            original TEXT NOT NULL, translated TEXT NOT NULL, context TEXT, model TEXT,
            file TEXT, UNIQUE(task_id, file, original)
        );
        INSERT INTO tasks (task_id, client_token, client_ip, created_at, input_filename)
            VALUES ('t1', 'tok', '1.1.1.1', 1.0, 'm.mod');
        INSERT INTO translations (task_id, original, translated, file)
            VALUES ('t1', 'Goblin', 'Гоблин', 'a.utc');
        """)
    conn.close()

    db.init_db(path)

    assert {"item_id", "success", "speaker"} <= _translation_columns()
    assert [row["translated"] for row in db.get_translations_by_task("t1")] == ["Гоблин"]


def test_final_dialog_row_keeps_the_speaker(tmp_path: Path) -> None:
    """The per-file row with a speaker replaces the dialog translator's earlier row."""
    db.init_db(tmp_path / "t.db")
    db.create_task_row("t1", "tok", "1.1.1.1", 1.0, "m.mod")
    writer = db.SqliteTranslationLogWriter("t1")
    row = {"original": "Hello.", "translated": "Привет.", "file": "a.dlg", "item_id": "a:entry:0"}
    writer.write({**row, "context": "Dialog node E0 in a.dlg"})
    speaker = {"kind": "npc", "name": "Ölaf", "tag": "olaf"}
    writer.write({**row, "context": "NPC dialog line in a.dlg", "speaker": speaker})

    rows = db.get_translations_by_task("t1")

    assert len(rows) == 1
    assert rows[0]["speaker"] == speaker
    stored = db.get_db().execute("SELECT speaker FROM translations").fetchone()[0]
    assert "Ölaf" in stored


# ---------------------------------------------------------------------------
# Editor API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    set_task_manager(TaskManager(workspace_root=tmp_path / "tasks"))
    with TestClient(create_app()) as c:
        c.headers["X-Client-Token"] = "tok"
        yield c
    set_task_manager(None)


def _completed_task() -> str:
    task_id = str(uuid.uuid4())
    db.create_task_row(task_id, "tok", "1.1.1.1", 1.0, "m.mod")
    db.update_task_row(task_id, status="completed")
    return task_id


def _editor_files(client: TestClient, task_id: str) -> dict[str, list[dict]]:
    resp = client.get(f"/api/tasks/{task_id}/translations")
    assert resp.status_code == 200, resp.text
    return {group["filename"]: group["items"] for group in resp.json()["files"]}


def test_identical_dialog_lines_stay_separate_rows(client: TestClient) -> None:
    task_id = _completed_task()
    severina = {"kind": "npc", "name": "Severina", "tag": "sev_tag"}
    stumpy = {"kind": "npc", "name": "Stumpy", "tag": "stumpy_tag"}
    for item_id, translated, speaker in [
        ("a:entry:0", "Привет.", severina),
        ("a:entry:1", "Здорово.", stumpy),
        ("a:reply:0", "Привет!", PLAYER),
    ]:
        db.insert_translation(
            task_id, "Hello.", translated, file="a.dlg", item_id=item_id, speaker=speaker
        )
    db.insert_translation(task_id, "Hello.", "Привет.", file="b.dlg", item_id="b:entry:0")
    for item_id in ("x_first_name", "y_first_name"):
        db.insert_translation(task_id, "Goblin", "Гоблин", file="c.utc", item_id=item_id)

    files = _editor_files(client, task_id)

    assert [(item["item_id"], item["translated"], item["speaker"]) for item in files["a.dlg"]] == [
        ("a:entry:0", "Привет.", severina),
        ("a:entry:1", "Здорово.", stumpy),
        ("a:reply:0", "Привет!", PLAYER),
    ]
    assert [item["shared_with"] for item in files["a.dlg"]] == [["b.dlg"]] * 3
    assert files["b.dlg"][0]["shared_with"] == ["a.dlg"]
    assert len(files["c.utc"]) == 1
    assert files["c.utc"][0]["speaker"] is None
    assert files["c.utc"][0]["shared_with"] == []


def test_dialog_rows_without_speaker_fall_back_to_context(client: TestClient) -> None:
    """Tasks stored before speakers were recorded still label dialog lines."""
    task_id = _completed_task()
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

    files = _editor_files(client, task_id)

    speakers = {item["item_id"]: item["speaker"] for item in files["a.dlg"]}
    assert speakers == {
        "a:entry:0": OWNER_UNKNOWN,
        "a:entry:1": {"kind": "npc", "name": "", "tag": "BOB_2"},
        "a:reply:0": PLAYER,
        "a:entry:2": None,
    }
    assert files["a.uti"][0]["speaker"] is None


def test_translated_dialog_reaches_the_editor_with_speakers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """World scan, extraction, contextual dialog translation, SQLite rows and the editor API."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
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
    write_gff(dlg_path, _severina_dlg(), file_type="DLG")

    task_id = _completed_task()
    monkeypatch.setattr(context_module, "OpenRouterProvider", _FakeOpenRouter)
    provider = _FakeOpenRouter(['{"E0": "Привет.", "E1": "Здорово.", "R0": "Привет!"}'])
    config = TranslationConfig(
        api_key="k",
        model="fake/model",
        source_lang="english",
        target_lang="russian",
        input_file=tmp_path / "m.mod",
        translation_log_writer=db.SqliteTranslationLogWriter(task_id),
    )
    state = PipelineState(config=config, provider=provider)
    state.extract_dir = extract_dir
    state.world_context = WorldScanner().scan_directory(extract_dir)
    extracted_map = stage_extract(state, [dlg_path])

    stage_translate(state, extracted_map)

    assert len(provider.calls) == 1
    assert [
        (item["item_id"], item["translated"], item["speaker"])
        for item in _editor_files(client, task_id)["severina.dlg"]
    ] == [
        ("severina:entry:0", "Привет.", {"kind": "npc", "name": "Severina", "tag": "sev_tag"}),
        ("severina:entry:1", "Здорово.", {"kind": "npc", "name": "Stumpy", "tag": "stumpy_tag"}),
        ("severina:reply:0", "Привет!", PLAYER),
    ]


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def test_rebuild_edits_one_of_two_identical_dialog_lines(tmp_path: Path) -> None:
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    dlg = extract_dir / "a.dlg"
    write_gff(
        dlg,
        {
            "StructType": "DLG",
            "EntryList": [{"Text": _loc("Привет."), "Speaker": ""} for _ in range(2)],
            "ReplyList": [],
        },
        file_type="DLG",
    )

    rebuild_module(
        extract_dir,
        {"a.dlg": {"a:entry:1": "Здорово."}},
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="russian",
    )

    texts = [entry["Text"]["Value"] for entry in read_gff(dlg)["EntryList"]]
    assert texts == ["Привет.", "Здорово."]
