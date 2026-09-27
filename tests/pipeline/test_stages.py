"""Pipeline stages: isolation, ordering, cancellation, cleanup and log rows."""

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers import TranslationResult
from nwn_translator.config import TranslationCancelled
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.formats.erf import ERFReader, ERFWriter
from nwn_translator.formats.ncs import parse_ncs
from nwn_translator.injectors.base import InjectedContent
from nwn_translator.pipeline import artifacts, stages
from nwn_translator.pipeline.stages import (
    PipelineState,
    find_translatable_files,
    run_pipeline,
    stage_build_glossary,
    stage_collect_entities,
    stage_extract,
    stage_inject,
    stage_translate,
)
from nwn_translator.translators.ncs_diagnostics import new_ncs_diagnostics
from tests.support.fakes import DialogProvider, RecordingWriter, make_config
from tests.support.ncs import consts, retn, write_ncs
from tests.support.stub_managers import stub_translation_managers

PLAYER = {"kind": "player", "name": "", "tag": ""}
HELLO_DLG = {
    "StructType": "DLG",
    "StartingList": [{"Index": 0}],
    "EntryList": [{"Text": {"StrRef": -1, "Value": "Hello."}, "RepliesList": []}],
    "ReplyList": [],
}


def _state(tmp_path: Path, provider: Any = None, **config) -> PipelineState:
    config.setdefault("input_file", tmp_path / "m.mod")
    return PipelineState(config=make_config(**config), provider=provider or Mock())


def _dialog(path: Path, data: dict = HELLO_DLG) -> tuple:
    return data, DialogExtractor().extract(path, data), ".dlg"


def _item_file(path: Path, *items: TranslatableItem) -> tuple:
    for item in items:
        item.location = str(path)
    return {}, ExtractedContent(content_type="item", items=list(items), source_file=path), ".uti"


def _module(path: Path, *resources) -> Path:
    writer = ERFWriter(path)
    for stem, ext, data in resources:
        writer.add_resource(stem, ext, data)
    writer.write()
    return path


# ---------------------------------------------------------------------------
# Extract and inject
# ---------------------------------------------------------------------------


def test_extract_and_inject_run_from_the_extract_dir_alone(tmp_path):
    """No model call: the translations seam round-trips through its artifact."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    write_ncs(extract_dir, "s.ncs", consts("Hello world!"), retn())
    state = _state(tmp_path)
    state.extract_dir = extract_dir

    extracted_map = stage_extract(state, find_translatable_files(extract_dir))
    assert len(extracted_map) == 1
    path = tmp_path / "translations.json"
    artifacts.dump_translations(path, {("s.ncs", "s:c0"): "Hi there all!"})
    stage_inject(state, extracted_map, artifacts.load_translations(path))

    strings = parse_ncs(extract_dir / "s.ncs").string_constants
    assert any(c.string_value == "Hi there all!" for c in strings)


def test_translatable_files_come_in_ntfs_order_on_every_file_system(tmp_path, monkeypatch):
    """Upper-cased names sort '_' after letters and '.' before '_', as NTFS lists them."""
    for name in ["_x.ncs", "b.dlg", "a_b.utc", "A.uti", "a.dlg", "a-b.dlg", "a1.jrl", "note.txt"]:
        (tmp_path / name).write_bytes(b"")
    real_rglob = Path.rglob

    def other_file_system_order(self: Path, pattern: str):
        # Reverse code-point order stands for any file system other than NTFS.
        return sorted(real_rglob(self, pattern), key=lambda path: path.name, reverse=True)

    monkeypatch.setattr(Path, "rglob", other_file_system_order)

    assert [path.name for path in find_translatable_files(tmp_path)] == [
        "a-b.dlg",
        "a.dlg",
        "A.uti",
        "a1.jrl",
        "a_b.utc",
        "b.dlg",
        "_x.ncs",
    ]


def _reversed_speed(files: List[Path], result):
    """A stage function where earlier files finish later than the files after them."""

    def run(file_path, *_args, **_kwargs):
        time.sleep(0.02 * (len(files) - files.index(file_path)))
        return result(file_path)

    return run


def test_extraction_keeps_the_input_order(tmp_path, monkeypatch):
    files = [tmp_path / f"f{i}.uti" for i in range(8)]
    empty = lambda path: ({}, ExtractedContent("item", [], path))  # noqa: E731
    monkeypatch.setattr(stages, "load_parsed_and_extracted", _reversed_speed(files, empty))

    assert list(stage_extract(_state(tmp_path, max_concurrent_requests=8), files)) == files


def test_injection_results_are_handled_in_file_order(tmp_path, monkeypatch):
    """Injection events, errors and counts follow the file order, not thread timing."""
    files = [tmp_path / f"f{i}.uti" for i in range(8)]

    def inject(path):
        if files.index(path) % 2:
            raise ValueError(f"broken {path.name}")

    monkeypatch.setattr(stages, "inject_translations_into_file", _reversed_speed(files, inject))
    writer = RecordingWriter()
    state = _state(tmp_path, max_concurrent_requests=8, translation_log_writer=writer)
    empty = ExtractedContent(content_type="item", items=[], source_file=tmp_path)

    stage_inject(state, {path: ({}, empty, ".uti") for path in files}, {})

    assert [event["file"] for event in writer.entries] == [path.name for path in files]
    assert state.stats["errors"] == [
        f"Error injecting {path.name}: broken {path.name}" for path in files[1::2]
    ]
    assert state.stats["files_processed"] == 4


def test_failed_script_patch_is_counted_sampled_and_logged(tmp_path, monkeypatch):
    writer = RecordingWriter()
    state = _state(tmp_path, translation_log_writer=writer)
    script = tmp_path / "s.ncs"
    failure = {"type": "ncs_script", "ncs_patch_failed": True, "error": "validation failed"}
    monkeypatch.setattr(
        stages,
        "inject_translations_into_file",
        lambda *args, **kwargs: InjectedContent(script, False, 0, failure),
    )
    content = ExtractedContent(content_type="ncs_script", items=[], source_file=script)

    stage_inject(state, {script: ({}, content, ".ncs")}, {})

    stats = state.stats["ncs_diagnostics"]
    assert (stats["patch_failed"], stats["samples"][0]["reason"]) == (1, "patch_failed")
    assert state.stats["files_processed"] == 1
    assert writer.entries[1:] == [
        {
            "event": "ncs_diagnostic",
            "file": "s.ncs",
            "reason": "patch_failed",
            "error": "validation failed",
        }
    ]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancelled_extraction_drops_the_queued_files(tmp_path, monkeypatch):
    total_files, sleep_per_file = 40, 0.1
    started = []
    lock = threading.Lock()

    def slow_extract(file_path, *_args, **_kwargs):
        with lock:
            started.append(file_path)
        time.sleep(sleep_per_file)

    monkeypatch.setattr(stages, "load_parsed_and_extracted", slow_extract)
    state = _state(tmp_path, max_concurrent_requests=2, cancel_check=lambda: True)
    begun = time.monotonic()

    with pytest.raises(TranslationCancelled):
        stage_extract(state, [tmp_path / f"f{i}.uti" for i in range(total_files)])

    # All 40 files would take about 2 s on 2 workers; only those already started run.
    assert len(started) < total_files / 2
    assert time.monotonic() - begun < total_files * sleep_per_file / 2 / 2


@pytest.mark.parametrize(
    "run_stage",
    [
        lambda state, extracted: stages.stage_worldscan(state),
        stages.stage_collect_entities,
        lambda state, extracted: stages.stage_build_glossary(state),
        stages.stage_translate,
        lambda state, extracted: stage_inject(state, extracted, {("s.ncs", "s:c0"): "Bye all!"}),
    ],
    ids=["worldscan", "entities", "glossary", "translate", "inject"],
)
def test_stages_stop_before_their_work_when_the_run_is_cancelled(tmp_path, run_stage):
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    script = write_ncs(extract_dir, "s.ncs", consts("Hello world!"), retn())
    original = script.read_bytes()
    state = _state(tmp_path)
    state.extract_dir = extract_dir
    extracted = stage_extract(state, [script])
    state.world_context = WorldContext()
    state.config.cancel_check = lambda: True

    with pytest.raises(TranslationCancelled):
        run_stage(state, extracted)

    assert state.provider.mock_calls == []
    assert script.read_bytes() == original


# ---------------------------------------------------------------------------
# Whole runs: output, temporary files and the log
# ---------------------------------------------------------------------------


def _run_state(tmp_path: Path, input_mod: Path, **config) -> PipelineState:
    config = dict(
        api_key="unused",
        input_file=input_mod,
        output_file=tmp_path / "out.mod",
        temp_dir=tmp_path / "temp",
        quiet=True,
        **config,
    )
    # These runs send no request; they only close the client.
    return PipelineState(config=make_config(**config), provider=AsyncMock())


def test_module_without_translatable_resources_is_copied(tmp_path, opened_files):
    input_mod = _module(tmp_path / "empty.mod", ("cleanup", ".nss", b"void main() {}"))
    log = tmp_path / "log.jsonl"
    state = _run_state(tmp_path, input_mod, translation_log=log)
    state.trace.write({"event": "started"})

    result = run_pipeline(state)

    assert result == tmp_path / "out.mod"
    assert result.read_bytes() == input_mod.read_bytes()
    reader = ERFReader(result)
    reader.read_header()
    assert [e.res_ref for e in reader.read_entries()] == ["cleanup"]
    # The run closes the log file it opened.
    assert [handle.closed for handle in opened_files(log)] == [True]
    log.rename(tmp_path / "moved.jsonl")  # fails on Windows while a handle is open


@pytest.mark.parametrize("skip_cleanup, left", [(False, 0), (True, 1)])
def test_failed_stage_still_cleans_the_temp_dir(tmp_path, monkeypatch, skip_cleanup, left):
    input_mod = _module(tmp_path / "in.mod", ("greeting", ".dlg", b"garbage"))
    (tmp_path / "temp").mkdir()
    state = _run_state(tmp_path, input_mod, skip_cleanup=skip_cleanup)

    def boom(_state):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(stages, "stage_worldscan", boom)
    with pytest.raises(RuntimeError, match="stage exploded"):
        run_pipeline(state)

    leftovers = [p for p in (tmp_path / "temp").iterdir() if p.name.startswith("nwn_translate_")]
    assert len(leftovers) == left
    if not skip_cleanup:
        assert state.temp_dir is None
        state.provider.close_async_client.assert_awaited_once()


# ---------------------------------------------------------------------------
# The translate stage
# ---------------------------------------------------------------------------


def test_manager_statistics_are_merged_once_per_manager(tmp_path):
    def manager_stats(items: int, error: str, ncs_total: int, sample: dict) -> dict:
        ncs = new_ncs_diagnostics()
        ncs["total"] = ncs["translated"] = ncs_total
        ncs["samples"].append(sample)
        return {"items_translated": items, "errors": [error], "ncs_diagnostics": ncs}

    state = _state(tmp_path)
    state.merge_manager_stats(manager_stats(3, "first", 2, {"file": "a.ncs"}))
    state.merge_manager_stats(manager_stats(1, "second", 5, {"file": "b.ncs"}))

    assert state.stats["items_translated"] == 4
    assert state.stats["errors"] == ["first", "second"]
    ncs = state.stats["ncs_diagnostics"]
    assert (ncs["total"], ncs["translated"], ncs["failed"]) == (7, 7, 0)
    assert ncs["samples"] == [{"file": "a.ncs"}, {"file": "b.ncs"}]


def test_both_managers_log_through_the_run_log(tmp_path, opened_files):
    """The stage's editor rows and the requests of both managers share one handle."""

    class _Provider(DialogProvider):
        async def translate_batch_async(self, items, **kwargs):
            return [TranslationResult(original=item.original, translated="Меч") for item in items]

    log = tmp_path / "log.jsonl"
    state = _state(tmp_path, _Provider(['{"E0": "Привет."}']), api_key="k", translation_log=log)
    state.extract_dir = tmp_path
    state.world_context = WorldContext()
    uti = tmp_path / "sword.uti"

    stage_translate(
        state,
        {
            tmp_path / "talk.dlg": _dialog(tmp_path / "talk.dlg"),
            uti: _item_file(uti, TranslatableItem(text="Sword", item_id="sword:name")),
        },
    )
    state.close_log()

    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    requests = [line["method"] for line in lines if line.get("event") == "model_request"]
    assert sorted(requests) == ["complete_json_chat_async", "translate_batch_async"]
    assert {line["item_id"] for line in lines if "original" in line} >= {
        "sword:name",
        "talk:entry:0",
    }
    appends = [handle for handle in opened_files(log) if handle.mode == "a"]
    assert [handle.closed for handle in appends] == [True]


def test_rejected_lines_keep_their_source_text_and_untouched_items_get_no_row(
    tmp_path, monkeypatch
):
    writer = RecordingWriter()
    state = _state(tmp_path, translation_log_writer=writer)
    state.extract_dir = tmp_path
    state.world_context = WorldContext()
    uti, dlg = tmp_path / "a.uti", tmp_path / "talk.dlg"
    items = [
        TranslatableItem(text="Boom", item_id="x:0"),
        TranslatableItem(text="Fine", item_id="x:1"),
        TranslatableItem(text="Internal", item_id="skip"),
    ]
    stub_translation_managers(
        monkeypatch,
        batch=({("a.uti", "x:1"): "Хорошо"}, {("a.uti", "x:0")}),
        dialogs=({}, {("talk.dlg", "talk:entry:0")}),
    )

    stage_translate(state, {uti: _item_file(uti, *items), dlg: _dialog(dlg)})

    rows = [
        (r["file"], r["item_id"], r["original"], r["translated"], r["success"])
        for r in writer.entries
    ]
    assert rows == [
        ("a.uti", "x:0", "Boom", "Boom", False),
        ("a.uti", "x:1", "Fine", "Хорошо", True),
        ("talk.dlg", "talk:entry:0", "Hello.", "Hello.", False),
    ]
    assert {row["model"] for row in writer.entries} == {"test-model"}


def test_dialog_rows_carry_their_speakers(tmp_path, monkeypatch):
    writer = RecordingWriter()
    state = _state(tmp_path, translation_log_writer=writer, api_key="k")
    state.extract_dir = tmp_path
    world = WorldContext()
    for tag, name, conversation in [
        ("sev_tag", "Severina", "severina"),
        ("stumpy_tag", "Stumpy", "stumpy"),
    ]:
        world.npcs[tag] = NPCInfo(tag, name, "", "", "Human", "Female", conversation)
    state.world_context = world
    # Three nodes with the same text: an owner line, a tagged line and a player reply.
    loc = {"StrRef": -1, "Value": "Hello."}
    dlg_data = {
        "StructType": "DLG",
        "StartingList": [{"Index": 0}],
        "EntryList": [
            {"Text": loc, "Speaker": "", "RepliesList": [{"Index": 0}]},
            {"Text": loc, "Speaker": "stumpy_tag", "RepliesList": []},
        ],
        "ReplyList": [{"Text": loc, "EntriesList": [{"Index": 1}]}],
    }
    dlg_path, uti = tmp_path / "severina.dlg", tmp_path / "a.uti"
    dialog = _dialog(dlg_path, dlg_data)
    stub_translation_managers(
        monkeypatch,
        batch=({("a.uti", "a:name"): "Меч"}, set()),
        dialogs=({line.key: "Привет." for line in dialog[1].items}, set()),
    )

    stage_translate(
        state,
        {dlg_path: dialog, uti: _item_file(uti, TranslatableItem(text="Sword", item_id="a:name"))},
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
# Terminology stages
# ---------------------------------------------------------------------------


class _TerminologyProvider:
    """Scripted replies of the entity, curation and glossary stages."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        if user_prompt.startswith("Extract proper nouns"):
            self.calls.append("entities")
            return json.dumps({"entities": [{"name": "Stout Village", "type": "location"}]})
        self.calls.append("curation")
        return json.dumps({"Gewia": {"decision": "keep", "reason": "speaker", "priority": 1}})

    async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append("glossary")
        return json.dumps({key: key.upper() for key in kwargs["glossary_keys"]})


def _terminology_state(tmp_path, provider, writer):
    state = _state(
        tmp_path,
        provider,
        use_context=True,
        max_concurrent_requests=1,
        translation_log_writer=writer,
        quiet=True,
    )
    state.world_context = WorldContext()
    return state


def test_terminology_stages_record_candidates_metrics_and_their_trace(tmp_path):
    writer, provider = RecordingWriter(), _TerminologyProvider()
    state = _terminology_state(tmp_path, provider, writer)
    content = ExtractedContent(
        content_type="dialog",
        source_file=Path("gewia.dlg"),
        items=[
            TranslatableItem(
                "Leading a coach to Stout Village with farming equipment.",
                metadata={"type": "entry", "speaker": "Gewia"},
            )
        ],
    )

    stage_collect_entities(state, {Path("gewia.dlg"): ({}, content, ".dlg")})
    stage_build_glossary(state)

    assert provider.calls == ["entities", "curation", "glossary"]
    assert state.metrics_recorder.summary()["counters"] == {
        "entity_candidates.raw": 2,
        "entity_candidates.keep": 2,
    }
    assert state.glossary.entries == {"Gewia": "GEWIA", "Stout Village": "STOUT VILLAGE"}
    trace = writer.entries[-1]
    assert list(trace) == ["event", "entries", "aliases", "candidates"]
    assert (trace["event"], trace["aliases"]) == ("terminology_resolved", {})
    assert trace["candidates"] == [
        {"name": "Gewia", "decision": "keep", "reason": "speaker", "alias_of": None},
        {"name": "Stout Village", "decision": "keep", "reason": "", "alias_of": None},
    ]


def test_glossary_timeout_keeps_the_run_going(tmp_path, monkeypatch):
    import nwn_translator.glossary_builder as builder

    monkeypatch.setattr(builder, "_STAGE", replace(builder._STAGE, run_timeout_per_batch=0.05))

    class _Stalled(_TerminologyProvider):
        async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
            await asyncio.sleep(5)
            return "{}"

    writer = RecordingWriter()
    state = _terminology_state(tmp_path, _Stalled(), writer)
    state.world_context.extracted_names = [("Perin", "character")]

    stage_build_glossary(state)

    assert state.glossary is not None and state.glossary.entries == {}
    assert writer.entries[-1]["event"] == "terminology_resolved"
