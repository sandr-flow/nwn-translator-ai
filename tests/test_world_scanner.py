"""World scan of areas, items and journal categories."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nwn_translator.context.world_context import WorldScanner
from tests.support.gff_writer import write_gff


def _loc(text: str) -> dict:
    return {"StrRef": -1, "Value": text}


def test_tagged_named_entities_register_with_their_name_evidence(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_gff(tmp_path / "harbor.are", {"Tag": "HARBOR", "Name": _loc("Old Harbor")}, "ARE")
    write_gff(tmp_path / "void.are", {"Name": _loc("Nowhere")}, "ARE")
    write_gff(tmp_path / "sword.uti", {"Tag": "SWORD", "LocalizedName": _loc("Sunblade")}, "UTI")
    write_gff(tmp_path / "blank.uti", {"Tag": "BLANK", "LocalizedName": _loc("")}, "UTI")
    write_gff(
        tmp_path / "module.jrl",
        {
            "Categories": [
                {"Tag": "q_main", "Name": _loc("The Lost Heir")},
                {"Name": _loc("Untagged Quest")},
                {"Tag": "q_side", "Name": _loc("Rats in the Cellar")},
            ]
        },
        "JRL",
    )
    caplog.set_level(logging.INFO, logger="nwn_translator.context.world_context")

    world = WorldScanner().scan_directory(tmp_path)

    assert world.areas == {"HARBOR": "Old Harbor"}
    assert world.items == {"SWORD": "Sunblade"}
    assert world.quests == {"q_main": "The Lost Heir", "q_side": "Rats in the Cellar"}
    assert (
        "World context built: 0 NPCs, 1 locations, 2 quests, 1 items, 0 other dialog actors"
        in caplog.messages
    )
    evidence = {
        candidate.name: (
            candidate.category,
            [(e.source, e.resource, e.field, e.category) for e in candidate.evidence],
        )
        for candidate in world.candidates.values()
    }
    assert evidence == {
        "Old Harbor": ("location", [("are_name", "harbor.are", "Name", "location")]),
        "Sunblade": ("item", [("uti_name", "sword.uti", "LocalizedName", "item")]),
        "The Lost Heir": ("quest", [("jrl_category", "module.jrl", "Name", "quest")]),
        "Rats in the Cellar": ("quest", [("jrl_category", "module.jrl", "Name", "quest")]),
    }
