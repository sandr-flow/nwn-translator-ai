"""Evidence-backed entity candidates and their curated aliases."""

from pathlib import Path

import pytest

from nwn_translator.context.entity_candidates import EntityCandidateRegistry, add_item_candidate
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem


def test_registry_merges_evidence_without_losing_sources():
    registry = EntityCandidateRegistry()
    registry.add(
        "Brynlo", category="character", source="utc_name", resource="brynlo.utc", field="FirstName"
    )
    registry.add(
        "Brynlo",
        category="character",
        source="dlg_speaker",
        resource="brynlo.dlg",
        field="Speaker",
        is_speaker_or_dialog_actor=True,
    )

    (candidate,) = registry.values()
    assert (candidate.name, candidate.frequency) == ("Brynlo", 2)
    assert candidate.sources == ["dlg_speaker", "utc_name"]
    assert candidate.is_speaker_or_dialog_actor


def test_candidates_from_extracted_content_cover_git_and_dlg_evidence():
    content = ExtractedContent(
        content_type="git_instance",
        source_file=Path("area.git"),
        items=[
            TranslatableItem(
                "Diving Dolphin", metadata={"type": "store_name", "git_field": "LocName"}
            ),
            TranslatableItem("Hello there", metadata={"type": "entry", "speaker": "Gewia"}),
        ],
    )

    by_name = {
        c.name: c for c in EntityCandidateRegistry.from_extracted_content([content]).values()
    }

    assert by_name["Diving Dolphin"].evidence[0].source == "git_instance"
    assert by_name["Gewia"].evidence[0].source == "dlg_speaker"


def test_restore_keeps_curated_fields_and_replaces_by_key():
    source = EntityCandidateRegistry()
    source.add("Brynlo", category="character", source="utc_name")
    source.mark_curated("Brynlo", decision="local_only", reason="kept_local", priority=3)
    saved = source.values()[0]

    registry = EntityCandidateRegistry()
    registry.add("brynlo", category="unknown", source="git_instance")
    registry.restore([saved])

    assert registry.values() == [saved]
    assert (saved.curation_decision, saved.priority, saved.frequency) == ("local_only", 90, 1)


def test_item_descriptions_are_not_candidates():
    registry = EntityCandidateRegistry()
    item = TranslatableItem("Long description", metadata={"type": "item_description"})
    add_item_candidate(registry, item, "thing.uti")
    assert registry.values() == []


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


def test_alias_chains_resolve_and_acronyms_do_not_weaken_the_engine_tag_filter():
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
