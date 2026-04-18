"""Tests for Phase 2 glossary filtering (Glossary.to_prompt_block(texts=...))."""

from src.nwn_translator.glossary import Glossary


class TestGlossaryFilterByBatch:
    """Glossary.to_prompt_block(texts=...) keeps only entries that appear in texts."""

    def test_empty_glossary_returns_empty(self):
        g = Glossary(entries={})
        assert g.to_prompt_block(texts=["Anything"]) == ""

    def test_no_texts_passes_none_returns_full_block(self):
        g = Glossary(entries={"Perin": "Перин", "Dark Forest": "Тёмный лес"})
        full = g.to_prompt_block()
        assert '"Perin"' in full
        assert '"Dark Forest"' in full

    def test_filter_keeps_only_matching_entries(self):
        g = Glossary(
            entries={
                "Perin": "Перин",
                "Dark Forest": "Тёмный лес",
                "Golden Gate": "Золотые врата",
            }
        )
        block = g.to_prompt_block(texts=["Meet Perin at the tavern."])
        assert '"Perin"' in block
        assert "Dark Forest" not in block
        assert "Golden Gate" not in block

    def test_filter_is_case_insensitive(self):
        g = Glossary(entries={"Nasher": "Нашер"})
        block = g.to_prompt_block(texts=["lord NASHER will see you now"])
        assert '"Nasher"' in block

    def test_filter_requires_whole_word_match(self):
        """Substring-only matches must be rejected."""
        g = Glossary(entries={"Nas": "Нас"})
        # "Nasher" contains "nas" but not as a whole word
        block = g.to_prompt_block(texts=["Nasher stood there."])
        assert block == "" or '"Nas"' not in block

    def test_filter_matches_multi_word_names(self):
        g = Glossary(
            entries={
                "Dark Forest": "Тёмный лес",
                "Golden Gate": "Золотые врата",
            }
        )
        block = g.to_prompt_block(texts=["Enter the Dark Forest tonight."])
        assert '"Dark Forest"' in block
        assert "Golden Gate" not in block

    def test_filter_handles_punctuation_around_name(self):
        g = Glossary(entries={"Perin": "Перин"})
        block = g.to_prompt_block(texts=['"Perin!" she cried.'])
        assert '"Perin"' in block

    def test_empty_texts_yields_empty_block(self):
        g = Glossary(entries={"Perin": "Перин"})
        assert g.to_prompt_block(texts=[]) == ""
        assert g.to_prompt_block(texts=["", None]) == ""

    def test_entries_remain_sorted_in_output(self):
        """Phase 1 rule: order is deterministic (sorted, case-insensitive)."""
        g = Glossary(
            entries={
                "Zephyr": "Зефир",
                "Arlena": "Арлена",
                "Morin": "Морин",
            }
        )
        block = g.to_prompt_block(texts=["Morin met Arlena and Zephyr at dawn"])
        # All present — and Arlena appears before Morin appears before Zephyr
        a_pos = block.find('"Arlena"')
        m_pos = block.find('"Morin"')
        z_pos = block.find('"Zephyr"')
        assert 0 < a_pos < m_pos < z_pos
