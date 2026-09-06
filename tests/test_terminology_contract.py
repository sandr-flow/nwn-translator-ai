"""Terminology provides evidence, never fabricated occurrence translations."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.config import TranslationConfig
from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.glossary import Glossary, terminology_block
from nwn_translator.pipeline.artifacts import dump_glossary, load_glossary
from nwn_translator.translators.translation_manager import TranslationManager


def test_alias_family_survives_artifact_and_shared_translation(tmp_path):
    glossary = Glossary(
        {"Melee vs Mayhem": "Игра", "Melee vs. Mayhem": "Игра", "MVM": "MVM"},
        {"Melee vs. Mayhem": "Melee vs Mayhem", "MVM": "Melee vs Mayhem"},
    )
    path = tmp_path / "glossary.json"
    dump_glossary(path, glossary)
    restored = load_glossary(path)
    assert restored == glossary
    assert restored.matching_entries(["Tell me about MVM."]) == glossary.entries


@pytest.mark.parametrize(
    "edges",
    [
        {"MVM": "Missing"},
        {"MVM": "Melee vs Mayhem", "Melee vs Mayhem": "MVM"},
    ],
)
def test_invalid_aliases_do_not_create_entities(edges):
    registry = EntityCandidateRegistry()
    for name in edges:
        registry.add(name, category="term", source="entity_extractor")
    for name, root in edges.items():
        registry.mark_curated(name, decision="alias_of", alias_of=root)
    assert registry.resolved_aliases() == {}


def test_alias_chain_and_acronyms_do_not_weaken_engine_tag_filter():
    registry = EntityCandidateRegistry()
    for name in ["Melee vs Mayhem", "Melee vs. Mayhem", "MVM", "I&I", "WP_START"]:
        registry.add(name, category="term", source="entity_extractor")
    registry.mark_curated("MVM", decision="alias_of", alias_of="Melee vs. Mayhem")
    registry.mark_curated("Melee vs. Mayhem", decision="alias_of", alias_of="Melee vs Mayhem")
    assert registry.resolved_aliases() == {
        "MVM": "Melee vs Mayhem",
        "Melee vs. Mayhem": "Melee vs Mayhem",
    }
    names = {name for name, _ in registry.glossary_pairs()}
    assert {"MVM", "I&I"} <= names
    assert "WP_START" not in names


def test_shared_word_does_not_infer_translation_or_alias():
    glossary = Glossary({"Shadow Lord": "Повелитель теней", "Jade Falcon": "Джейд Фалкон"})
    assert glossary.matching_entries(["Shadow"]) == {}
    assert glossary.matching_entries(["Jade"]) == {}
    assert glossary.entries == {"Shadow Lord": "Повелитель теней", "Jade Falcon": "Джейд Фалкон"}


def test_project_term_has_one_authoritative_translation():
    block = terminology_block(["Sword Spider"], "russian", Glossary({"Sword Spider": "Другой"}))
    assert "мечепряд" in block
    assert "Другой" not in block


def _manager(glossary=None):
    provider = Mock()

    async def batch(items, **kwargs):
        return [
            TranslationResult(original=item.original, translated="TR:" + item.original)
            for item in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=batch)
    manager = TranslationManager(
        TranslationConfig(api_key="test-key", quiet=True), provider, glossary
    )
    return manager, provider


def test_numbered_labels_and_prefix_names_reach_provider_intact():
    texts = ["FDwarf6", "FDwarf7", "Shadow", "Shadow Lord"]
    items = [
        TranslatableItem(text, "Visible character name", str(i), "area.git")
        for i, text in enumerate(texts)
    ]
    manager, provider = _manager(Glossary({"Shadow Lord": "Повелитель теней"}))
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    sent = [
        item.original
        for call in provider.translate_batch_async.call_args_list
        for item in call.kwargs["items"]
    ]
    assert sorted(sent) == sorted(texts)
    assert result == {item.key: "TR:" + item.text for item in items}


def test_glossary_budget_splits_requests_without_dropping_terms(monkeypatch):
    import nwn_translator.translators.translation_manager as module

    monkeypatch.setattr(module, "GLOSSARY_MAX_CHARS", 600)
    glossary = Glossary({f"Entity {i}": "Long canonical form " * 8 for i in range(8)})
    manager, provider = _manager(glossary)
    items = [
        TranslatableItem(name, "item", str(i), "items.git")
        for i, name in enumerate(glossary.entries)
    ]
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    assert len(result) == len(items)
    assert provider.translate_batch_async.call_count > 1
    for call in provider.translate_batch_async.call_args_list:
        for item in call.kwargs["items"]:
            assert item.original in call.kwargs["glossary_block"]


def test_actual_name_fields_stay_together_across_length_tiers():
    from nwn_translator.extractors.creature_extractor import CreatureExtractor

    data = {
        "Tag": "jade",
        "Gender": 1,
        "Race": 1,
        "FirstName": {"Value": "Jade"},
        "LastName": {"Value": "Falcon with a deliberately long family name"},
    }
    content = CreatureExtractor().extract(Path("jade.utc"), data)
    manager, provider = _manager(
        Glossary({"Jade Falcon with a deliberately long family name": "Name"})
    )
    result = manager.translate_content(content)
    assert len(result) == 2
    provider.translate_batch_async.assert_called_once()
    call = provider.translate_batch_async.call_args
    assert {item.metadata["name_field"] for item in call.kwargs["items"]} == {
        "FirstName",
        "LastName",
    }
    assert all("Female" in item.context for item in call.kwargs["items"])
