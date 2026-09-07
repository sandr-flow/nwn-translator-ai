"""Structural grouping preserves occurrence addresses and argument-specific evidence."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers.base import TranslationItem, TranslationResult
from nwn_translator.ai_providers.batch_payload import build_batch_payload, source_windows
from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from nwn_translator.config import TranslationConfig
from nwn_translator.context.dialog_formatter import DialogFormatter
from nwn_translator.extractors.base import DialogNode, ExtractedContent, TranslatableItem
from nwn_translator.extractors.creature_extractor import CreatureExtractor
from nwn_translator.extractors.git_extractor import GitExtractor
from nwn_translator.extractors.item_extractor import ItemExtractor
from nwn_translator.extractors.journal_extractor import JournalExtractor
from nwn_translator.extractors.nss_index import snippet_for_text, snippet_with_position
from nwn_translator.translators.translation_manager import TranslationManager


def loc(text):
    return {"StrRef": -1, "Value": text}


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_source_positions_recover_exact_capped_excerpt(newline):
    source = newline.join(["//" + "x" * 300] * 20 + ['SpeakString("Hello");'] + ["// tail"] * 8)
    text, start = snippet_with_position("Hello", source)
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    assert text == snippet_for_text("Hello", source)
    assert normalized[start : start + len(text)] == text
    assert snippet_with_position("Missing", source) == (None, None)


def test_source_window_union_is_lossless_and_checks_overlap():
    entries = [
        {"nss_start": 4, "nss_snippet": "efghij"},
        {"nss_start": 0, "nss_snippet": "abcdef"},
        {"nss_start": 2, "nss_snippet": "cd"},
        {"nss_start": 4, "nss_snippet": "DIFFERENT"},
        {"nss_snippet": "unknown position"},
    ]
    windows, refs = source_windows(entries)
    assert refs[0] == refs[1] == refs[2]
    assert refs[3] != refs[0]
    for entry, ref in zip(entries, refs):
        window = windows[ref]
        if entry.get("nss_start") is not None:
            start = entry["nss_start"] - window["start"]
            assert window["text"][start : start + len(entry["nss_snippet"])] == entry["nss_snippet"]
        else:
            assert window["text"] == entry["nss_snippet"]


def test_batch_sources_never_merge_across_files_and_keep_numeric_outputs():
    items = [
        TranslationItem(
            "Hello",
            "full fallback context",
            {
                "batch_resource": file,
                "translation_group": "script",
                "batch_context": "Consumer: SpeakString argument 0",
                "nss_snippet": snippet,
                "nss_start": start,
            },
        )
        for file, snippet, start in [
            ("a.ncs", "abcdef", 0),
            ("a.ncs", "efghij", 4),
            ("b.ncs", "efghij", 4),
        ]
    ]
    payload = build_batch_payload(items)
    assert set(payload["items"]) == {"0", "1", "2"}
    assert payload["items"]["0"]["group"] == payload["items"]["1"]["group"]
    assert payload["items"]["0"]["group"] != payload["items"]["2"]["group"]
    assert payload["groups"]["0"]["matching_source_context_only"][0]["text"] == "abcdefghij"
    assert payload["groups"]["1"]["matching_source_context_only"][0]["text"] == "efghij"
    assert all(i.context == "full fallback context" for i in items)


def test_gate_shared_sources_preserve_per_occurrence_consumers():
    entries = [
        {
            "key": str(i),
            "file": "a.ncs",
            "text": "Hello",
            "offset": i,
            "nss_snippet": "abcdef"[i:],
            "nss_start": i,
            "bytecode_context": {"consumer": consumer},
        }
        for i, consumer in enumerate(["SpeakString:0", "SetLocalString:1"])
    ]
    provider = OpenRouterProvider(api_key="test")
    payload = json.loads(
        provider._ncs_gate_build_user_prompt(source_lang="en", entries=entries).split("\n\n", 1)[1]
    )
    assert len(payload["sources"]["a.ncs"]) == 1
    assert (
        payload["entries"]["0"]["bytecode_context"] != payload["entries"]["1"]["bytecode_context"]
    )
    assert (
        provider._parse_ncs_gate_raw('{"0":{"translate":true}}', entries)["1"]["translate"] is False
    )


def test_structural_fields_share_request_despite_different_lengths():
    extracted = ItemExtractor().extract(
        Path("sword.uti"),
        {
            "LocalizedName": loc("Silver Sword"),
            "Description": loc("A fine blade. " * 100),
            "DescIdentified": loc("An enchanted silver blade."),
        },
    )
    provider = Mock()

    async def translate(items, **kwargs):
        assert len(build_batch_payload(items)["groups"]) == 1
        return [
            TranslationResult(original=i.original, translated=f"TR:{i.original}") for i in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=translate)
    manager = TranslationManager(TranslationConfig(api_key="test", quiet=True), provider)
    result = manager.translate_content(extracted)
    assert result == {i.key: f"TR:{i.text}" for i in extracted.items}
    provider.translate_batch_async.assert_called_once()
    provider.translate_async.assert_not_called()


def test_gff_groups_use_structural_indices_not_shared_tags(tmp_path):
    creature = {
        "Tag": "same",
        "FirstName": loc("Aria"),
        "LastName": loc("the Wise"),
        "Description": loc("A wise mage."),
        "ItemList": [
            {"LocalizedName": loc("Silver Sword"), "Description": loc("A fine blade.")},
            {"LocalizedName": loc("WP_START")},
        ],
    }
    extracted = GitExtractor().extract(
        tmp_path / "area.git", {"Creature List": [creature, creature]}
    )
    groups = {}
    for item in extracted.items:
        groups.setdefault(item.metadata["translation_group"], []).append(item.text)
    assert len(groups) == 4
    assert groups["Creature List[0]"] == groups["Creature List[1]"]
    assert "WP_START" not in [i.text for i in extracted.items]
    categories = [
        {"Tag": "same", "Name": loc("A quest"), "EntryList": [{"Text": loc("Find the sword.")}]}
    ] * 2
    journal = JournalExtractor().extract(tmp_path / "quests.jrl", {"Categories": categories})
    assert [i.metadata["translation_group"] for i in journal.items] == [
        "category[0]",
        "category[0]",
        "category[1]",
        "category[1]",
    ]
    assert "A quest" in journal.items[1].context
    assert "quest title: 'same'" not in journal.items[1].context


def test_split_large_group_keeps_name_pair_and_every_occurrence():
    creature = CreatureExtractor().extract(
        Path("mage.utc"),
        {
            "FirstName": loc("Aria"),
            "LastName": loc("the Wise"),
            "Description": loc("Long description. " * 100),
        },
    )
    manager = TranslationManager(TranslationConfig(api_key="test", quiet=True), Mock())
    manager._BATCH_TEXT_BUDGET = 100
    groups = [[{"item": i, "sanitized": i.text} for i in creature.items]]
    batches = manager._pack_structural_groups(groups)
    assert [[d["item"].metadata["type"] for d in b] for b in batches] == [
        ["creature_first_name", "creature_last_name"],
        ["creature_description"],
    ]


def test_chunk_retains_edges_and_adjacent_context_without_extra_targets():
    before = DialogNode(node_id=0, text="Who are you?", is_entry=True)
    selected = DialogNode(node_id=1, text="A traveler.", is_entry=False)
    after = DialogNode(node_id=2, text="Welcome.", is_entry=True)
    before.replies = [selected]
    selected.replies = [after]
    after.replies = [selected]
    script = DialogFormatter().format_nodes(["R1"], {"E0": before, "R1": selected, "E2": after}, {})
    assert "<<<A traveler.>>>" in script
    assert "Who are you?" in script and "Welcome." in script
    assert "<<<Who are you?>>>" not in script and "<<<Welcome.>>>" not in script
    assert "NPC Response" in script and "E2" in script
    assert "do not return translations for these IDs" in script


def test_partial_group_response_does_not_shift_next_group_results():
    items = [
        TranslatableItem(text, "", text, file, {"translation_group": "root"})
        for file, text in [("a.uti", "First"), ("a.uti", "Missing"), ("b.uti", "Second")]
    ]
    provider = Mock()

    async def translate(items, **kwargs):
        return [TranslationResult(original=items[0].original, translated="TR:" + items[0].original)]

    provider.translate_batch_async = AsyncMock(side_effect=translate)
    provider.translate_async = AsyncMock(
        return_value=TranslationResult(
            original="Missing", translated="", success=False, error="test failure"
        )
    )
    manager = TranslationManager(TranslationConfig(api_key="test", quiet=True), provider)
    manager._BATCH_MAX_ITEMS = 2
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    assert result == {items[0].key: "TR:First", items[2].key: "TR:Second"}
    assert items[1].key in manager.failed_items
