"""Tests for evidence-backed entity candidate collection."""

from pathlib import Path

from src.nwn_translator.context.entity_candidates import (
    EntityCandidateRegistry,
    add_item_candidate,
)
from src.nwn_translator.extractors.base import ExtractedContent, TranslatableItem


def test_candidate_registry_merges_evidence_without_losing_sources():
    registry = EntityCandidateRegistry()
    registry.add(
        "Brynlo",
        category="character",
        source="utc_name",
        resource="brynlo.utc",
        field="FirstName",
    )
    registry.add(
        "Brynlo",
        category="character",
        source="dlg_speaker",
        resource="brynlo.dlg",
        field="Speaker",
        is_speaker_or_dialog_actor=True,
    )

    candidate = registry.values()[0]
    assert candidate.name == "Brynlo"
    assert candidate.frequency == 2
    assert candidate.sources == ["dlg_speaker", "utc_name"]
    assert candidate.is_speaker_or_dialog_actor


def test_candidates_from_extracted_content_cover_git_and_dlg_evidence():
    content = ExtractedContent(
        content_type="git_instance",
        source_file=Path("area.git"),
        items=[
            TranslatableItem(
                "Diving Dolphin",
                metadata={"type": "store_name", "git_field": "LocName"},
            ),
            TranslatableItem(
                "Hello there",
                metadata={"type": "entry", "speaker": "Gewia"},
            ),
        ],
    )

    registry = EntityCandidateRegistry.from_extracted_content([content])
    by_name = {candidate.name: candidate for candidate in registry.values()}

    assert by_name["Diving Dolphin"].evidence[0].source == "git_instance"
    assert by_name["Gewia"].evidence[0].source == "dlg_speaker"


def test_add_item_candidate_ignores_descriptions():
    registry = EntityCandidateRegistry()
    add_item_candidate(
        registry,
        TranslatableItem("Long description", metadata={"type": "item_description"}),
        "thing.uti",
    )
    assert registry.values() == []
