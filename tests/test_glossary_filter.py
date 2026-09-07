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

    def test_filter_matches_possessive_explicit_alias(self):
        """A possessive suffix preserves the complete explicit source alias."""
        g = Glossary(
            entries={"Merrick Winters": "Меррик Винтерс", "Winter": "Винтер"},
            aliases={"Winter": "Merrick Winters"},
        )
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

    def test_filter_matches_explicit_spelling_variant(self):
        """Spelling variants require an explicit alias relation."""
        g = Glossary(
            entries={"Merrick": "Меррик", "Merric": "Меррик"}, aliases={"Merric": "Merrick"}
        )
        block = g.to_prompt_block(texts=["I met Merric in the hall."])
        assert '"Merrick"' in block

    def test_filter_does_not_match_word_prefixes(self):
        """Short entity tokens must not pull random near-matches."""
        g = Glossary(entries={"Iris": "Ирис"})
        assert g.to_prompt_block(texts=["The Irish warrior approached."]) == ""

    def test_filter_does_not_strip_numbered_suffixes(self):
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


class TestMemoizedMatching:
    """Per-text memoized matching is equivalent to scanning the joined corpus."""

    ENTRIES = {
        "Aloro": "Алоро",
        "Aloro Harmony": "Гармония Алоро",
        "Nas": "Нас",
        "R. Freely": "Р. Фрили",
        '"Thesis Paper Room"': "«Зал диссертаций»",
    }

    @staticmethod
    def _reference(glossary, texts):
        import re

        corpus = "\n".join(str(text) for text in texts if text)
        return {
            key
            for key in glossary.entries
            if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", corpus, re.IGNORECASE)
        }

    def test_union_of_texts_equals_corpus_scan(self):
        g = Glossary(entries=dict(self.ENTRIES), aliases={"Aloro Harmony": "Aloro"})
        batches = [
            ["Sing Aloro Harmony now.", "nasher stood there"],
            ["ALORO!", 'Enter "Thesis Paper Room".'],
            ["Contact R. Freely.", "", None],
            ["Nas.", "Aloro Harmony"],
        ]
        for texts in batches:
            expected = self._reference(g, texts)
            roots = {g.aliases.get(k, k) for k in expected}
            assert set(g.matching_entries(texts)) == {
                k for k in g.entries if k in expected or g.aliases.get(k, k) in roots
            }
        # Repeated calls hit the memo and return the same result.
        assert g.matching_entries(batches[0]) == g.matching_entries(batches[0])

    def test_terminology_block_reuses_merged_glossary(self):
        from src.nwn_translator.glossary import terminology_block

        g = Glossary(entries={"Perin": "Перин"})
        first = terminology_block(["Perin met a dwarf."], "russian", g)
        second = terminology_block(["Perin met a dwarf."], "Russian", g)
        assert first == second
        assert '"Perin"' in first and '"dwarf"' in first
        assert list(g._with_terms) == ["russian"]
        assert terminology_block(["a dwarf"], "russian", None).count("dwarf") == 1
