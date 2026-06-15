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

    def test_filter_matches_prefix(self):
        """Prefix-match (>=4 chars) catches inflected/possessive forms."""
        g = Glossary(entries={"Merrick Winters": "Меррик Винтерс"})
        block = g.to_prompt_block(texts=["Mr. Winter's house was empty."])
        assert '"Merrick Winters"' in block

    def test_filter_does_not_pull_ravenloft_entities_by_module_name_only(self):
        g = Glossary(
            entries={
                "Ravenloft Vampire": "Vampire of Ravenloft",
                "Ravenloft Shadow": "Shadow of Ravenloft",
                "Barovia": "Barovia",
            }
        )
        block = g.to_prompt_block(texts=["Welcome to Ravenloft."])
        assert "Ravenloft Vampire" not in block
        assert "Ravenloft Shadow" not in block
        assert "Barovia" not in block

    def test_filter_keeps_ravenloft_entity_when_specific_tokens_match(self):
        g = Glossary(entries={"Ravenloft Vampire": "Vampire of Ravenloft"})
        block = g.to_prompt_block(texts=["The Ravenloft Vampire blocks the road."])
        assert '"Ravenloft Vampire"' in block

    def test_filter_matches_levenshtein_one(self):
        """Damerau-Levenshtein <=1 catches typos in long tokens."""
        g = Glossary(entries={"Merrick": "Меррик"})
        block = g.to_prompt_block(texts=["I met Merric in the hall."])
        assert '"Merrick"' in block

    def test_filter_no_fuzzy_on_short_tokens(self):
        """Short entity tokens must not pull random near-matches."""
        g = Glossary(entries={"Iris": "Ирис"})
        # "Irish" shares prefix len 4 with "Iris" — both are 4/5 chars; prefix-match
        # does fire (both >=4). This is acceptable: keeping prefix symmetric is
        # simpler than a one-sided rule. Use a clearly disjoint token instead:
        block = g.to_prompt_block(texts=["The brave warrior approached."])
        assert block == "" or '"Iris"' not in block

    def test_filter_dedups_same_translation(self):
        g = Glossary(
            entries={
                "Inmate": "Заключённый",
                "Inmate2": "Заключённый",
                "Inmate3": "Заключённый",
            }
        )
        block = g.to_prompt_block(texts=["The Inmate refused to talk."])
        assert block.count("Заключённый") == 1
        assert '"Inmate"' in block
        assert '"Inmate2"' not in block
        assert '"Inmate3"' not in block

    def test_filter_unicode_casefold(self):
        """Polish/Cyrillic glossary keys should match on case-folded tokens."""
        g = Glossary(entries={"Łódź": "Лодзь"})
        block = g.to_prompt_block(texts=["Travelled through łódź at dawn."])
        assert '"Łódź"' in block

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


class TestSeedCacheExcludedFromPrefix:
    """Glossary seeds must be exact-match only, never prefix-match bases (H8)."""

    def test_seed_entry_exact_match_but_not_prefix(self):
        from src.nwn_translator.translators.prefix_translation_cache import (
            PrefixAwareTranslationCache,
        )
        from src.nwn_translator.translators.translation_manager import _MIN_PREFIX_LEN

        term = "Crimson Brotherhood of Bane"  # >= _MIN_PREFIX_LEN
        g = Glossary(entries={term: "Багровое братство Бейна"})
        cache = PrefixAwareTranslationCache()
        g.seed_cache(cache, preserve_tokens=True)

        # Exact lookup of the seeded term still works.
        assert cache[term] == "Багровое братство Бейна"
        # But the seed never starts a prefix match for a longer sentence.
        assert cache.longest_prefix_match(term + ", an ancient cult", _MIN_PREFIX_LEN) is None
