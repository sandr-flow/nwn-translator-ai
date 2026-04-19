"""Tests for WorldContext.to_prompt_block(source_texts=...) filtering."""

from src.nwn_translator.context.world_context import NPCInfo, WorldContext


def _ctx() -> WorldContext:
    return WorldContext(
        npcs={
            "Thea": NPCInfo(
                tag="Thea",
                first_name="Thea",
                last_name="Wendt",
                description="",
                race="Human",
                gender="Female",
                conversation="",
            ),
            "Merrick": NPCInfo(
                tag="Merrick",
                first_name="Merrick",
                last_name="Winters",
                description="",
                race="Ooze",
                gender="Male",
                conversation="",
            ),
            "Mayor": NPCInfo(
                tag="Mayor",
                first_name="Mayor",
                last_name="Castelon",
                description="",
                race="Human",
                gender="Male",
                conversation="",
            ),
        },
        areas={
            "MasterBedroom": "Master Bedroom",
            "Lighthouse": "Lighthouse",
        },
        quests={"Vault": "Vault of Secrets"},
        items={"DarkScythe": "Dark Scythe"},
    )


class TestWorldContextFilter:
    def test_none_returns_full_block(self):
        block = _ctx().to_prompt_block()
        assert "Thea" in block
        assert "Mayor" in block
        assert "Lighthouse" in block
        assert "Dark Scythe" in block

    def test_filter_keeps_only_mentioned_npcs(self):
        text = "I suspect the Mayor might be dead. Mr. Winters had a key."
        block = _ctx().to_prompt_block(source_texts=[text])
        assert "Mayor" in block
        assert "Merrick" in block or "Winters" in block
        assert "Thea" not in block

    def test_filter_drops_empty_section_headers(self):
        text = "I suspect the Mayor might be dead."
        block = _ctx().to_prompt_block(source_texts=[text])
        assert "- KEY CHARACTERS IN THE GAME:" in block
        # No locations / quests / items match → headers must not appear
        assert "- LOCATIONS:" not in block
        assert "- QUESTS:" not in block
        assert "- KEY ITEMS:" not in block

    def test_filter_returns_empty_when_no_match(self):
        block = _ctx().to_prompt_block(source_texts=["random unrelated talk"])
        assert block == ""

    def test_filter_uses_filename_stem_corpus(self):
        # Owner-NPC safety net: filename stem "thea2" tokenizes to "thea",
        # which prefix-matches Thea / Thea Wendt.
        block = _ctx().to_prompt_block(source_texts=["Hello there.", "thea2"])
        assert "Thea" in block

    def test_filter_picks_up_areas_and_items(self):
        text = "Move the cabinet in the Master Bedroom to find the Dark Scythe."
        block = _ctx().to_prompt_block(source_texts=[text])
        assert "Master Bedroom" in block
        assert "Dark Scythe" in block
        assert "Lighthouse" not in block
