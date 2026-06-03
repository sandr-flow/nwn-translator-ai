"""Tests for pipeline seam (de)serialization and isolated stage execution."""

from pathlib import Path
from unittest.mock import Mock

from nwn_translator.config import TranslationConfig
from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.file_handlers.ncs_parser import parse_ncs
from nwn_translator.glossary import Glossary
from nwn_translator.pipeline import artifacts
from nwn_translator.pipeline.stages import PipelineState, stage_extract, stage_inject

from tests.test_ncs import _consts, _retn, _write_ncs

# ── artifact roundtrips (synthetic data) ────────────────────────────────


def test_items_roundtrip(tmp_path: Path) -> None:
    contents = [
        ExtractedContent(
            content_type="item",
            items=[
                TranslatableItem(
                    text="Sword of Truth",
                    context="Item name",
                    item_id="sword_name",
                    location=str(tmp_path / "sword.uti"),
                    metadata={"type": "item_name", "tag": "sword"},
                ),
                TranslatableItem(text="A sharp blade.", item_id="sword_desc"),
            ],
            source_file=tmp_path / "sword.uti",
            metadata={"tag": "sword", "item_count": 2},
        )
    ]
    path = tmp_path / "items.jsonl"
    artifacts.dump_items(path, contents)
    loaded = artifacts.load_items(path)

    assert len(loaded) == 1
    assert loaded[0].content_type == "item"
    assert loaded[0].metadata == {"tag": "sword", "item_count": 2}
    assert [i.text for i in loaded[0].items] == ["Sword of Truth", "A sharp blade."]
    assert loaded[0].items[0].metadata == {"type": "item_name", "tag": "sword"}
    assert loaded[0].items[0].item_id == "sword_name"


def test_world_context_roundtrip(tmp_path: Path) -> None:
    wc = WorldContext()
    wc.npcs["npc_a"] = NPCInfo(
        tag="npc_a",
        first_name="Elora",
        last_name="Swift",
        description="A ranger.",
        race="elf",
        gender="female",
        conversation="elora_conv",
    )
    wc.areas = {"area1": "The Forest"}
    wc.quests = {"q1": "Find the amulet"}
    wc.items = {"i1": "Amulet"}
    wc.extracted_names = [("Elora Swift", "character"), ("The Forest", "location")]

    path = tmp_path / "world_context.json"
    artifacts.dump_world_context(path, wc)
    loaded = artifacts.load_world_context(path)

    assert loaded.npcs["npc_a"] == wc.npcs["npc_a"]
    assert loaded.areas == wc.areas
    assert loaded.quests == wc.quests
    assert loaded.items == wc.items
    assert loaded.extracted_names == wc.extracted_names


def test_candidates_roundtrip_preserves_curation(tmp_path: Path) -> None:
    registry = EntityCandidateRegistry()
    registry.add(
        "Aragorn",
        category="character",
        source="dialog",
        resource="conv.dlg",
        context="Hail, Aragorn!",
        is_speaker_or_dialog_actor=True,
    )
    registry.mark_curated("Aragorn", decision="keep", reason="protagonist", priority=99)

    path = tmp_path / "candidates.json"
    artifacts.dump_candidates(path, registry)
    loaded = artifacts.load_candidates(path)

    orig = registry.values()[0]
    got = loaded.values()[0]
    assert got.name == orig.name
    assert got.normalized_name == orig.normalized_name
    assert got.category == orig.category
    assert got.frequency == orig.frequency
    assert got.curation_decision == orig.curation_decision
    assert got.priority == orig.priority == 99
    assert got.technical_score == orig.technical_score
    assert got.is_speaker_or_dialog_actor is True
    assert len(got.evidence) == len(orig.evidence) == 1
    assert got.evidence[0].resource == "conv.dlg"


def test_glossary_and_translations_roundtrip(tmp_path: Path) -> None:
    glossary = Glossary(entries={"Aragorn": "Арагорн", "The Forest": "Лес"})
    gpath = tmp_path / "glossary.json"
    artifacts.dump_glossary(gpath, glossary)
    assert artifacts.load_glossary(gpath).entries == glossary.entries

    translations = {"Hello": "Привет", "Bye": "Пока"}
    tpath = tmp_path / "translations.json"
    artifacts.dump_translations(tpath, translations)
    assert artifacts.load_translations(tpath) == translations

    ncs = {"id_1": "Привет", "id_2": "Пока"}
    npath = tmp_path / "ncs.json"
    artifacts.dump_ncs_by_item_id(npath, ncs)
    assert artifacts.load_ncs_by_item_id(npath) == ncs


# ── isolated deterministic stages ───────────────────────────────────────


def _det_state(tmp_path: Path) -> PipelineState:
    config = TranslationConfig(
        api_key="test-key",
        model="test-model",
        source_lang="english",
        target_lang="russian",
        input_file=tmp_path / "m.mod",
    )
    return PipelineState(config=config, provider=Mock())


def test_extract_then_inject_stage_isolated(tmp_path: Path) -> None:
    """extract + inject run from extract_dir alone, with no LLM calls."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    _write_ncs(extract_dir, "s.ncs", _consts("Hello world!"), _retn())

    state = _det_state(tmp_path)
    state.extract_dir = extract_dir

    files = state._find_translatable_files(extract_dir)
    extracted_map = stage_extract(state, files)
    assert len(extracted_map) == 1

    translations = {"Hello world!": "Hi there all!"}
    # Translations seam roundtrips through disk before injection.
    tpath = tmp_path / "translations.json"
    artifacts.dump_translations(tpath, translations)
    stage_inject(state, extracted_map, artifacts.load_translations(tpath))

    patched = parse_ncs(extract_dir / "s.ncs")
    assert any((c.string_value or "") == "Hi there all!" for c in patched.string_constants)


def test_only_ext_filter_isolates_file_type(tmp_path: Path) -> None:
    """A type filter restricts extraction/injection to one extension."""
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    _write_ncs(extract_dir, "a.ncs", _consts("Greetings!"), _retn())
    (extract_dir / "note.txt").write_text("ignored", encoding="utf-8")

    state = _det_state(tmp_path)
    state.extract_dir = extract_dir

    all_files = state._find_translatable_files(extract_dir)
    ncs_only = [f for f in all_files if f.suffix.lower() == ".ncs"]
    extracted_map = stage_extract(state, ncs_only)

    assert {p.suffix.lower() for p in extracted_map} == {".ncs"}
