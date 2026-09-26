"""The glossary: entries relevant to a batch and the terminology prompt block."""

import re

import pytest

from nwn_translator.glossary import Glossary, restore_wrapping_quotes, terminology_block


def _keys(block: str) -> list:
    """Source names of the ``* "name" -> translation`` lines of a glossary block."""
    return [line.split('"')[1] for line in block.splitlines() if line.lstrip().startswith("*")]


def test_empty_glossary_or_texts_give_an_empty_block():
    assert Glossary(entries={}).to_prompt_block(texts=["Anything"]) == ""
    glossary = Glossary(entries={"Perin": "Перин"})
    assert glossary.to_prompt_block(texts=[]) == ""
    assert glossary.to_prompt_block(texts=["", None]) == ""


def test_without_texts_the_block_lists_every_entry():
    block = Glossary(entries={"Perin": "Перин", "Dark Forest": "Тёмный лес"}).to_prompt_block()
    assert '"Perin"' in block and '"Dark Forest"' in block


@pytest.mark.parametrize(
    "entries, aliases, text, present, absent",
    [
        (
            {"Perin": "Перин", "Dark Forest": "Тёмный лес", "Golden Gate": "Золотые врата"},
            {},
            "Meet Perin at the tavern.",
            ["Perin"],
            ["Dark Forest", "Golden Gate"],
        ),
        ({"Nasher": "Нашер"}, {}, "lord NASHER will see you now", ["Nasher"], []),
        # Substrings are not matches: "Nasher" does not contain the word "Nas".
        ({"Nas": "Нас"}, {}, "Nasher stood there.", [], ["Nas"]),
        (
            {"Dark Forest": "Тёмный лес", "Golden Gate": "Золотые врата"},
            {},
            "Enter the Dark Forest tonight.",
            ["Dark Forest"],
            ["Golden Gate"],
        ),
        ({"Perin": "Перин"}, {}, '"Perin!" she cried.', ["Perin"], []),
        # A possessive keeps the complete explicit alias.
        (
            {"Merrick Winters": "Меррик Винтерс", "Winter": "Винтер"},
            {"Winter": "Merrick Winters"},
            "Mr. Winter's house was empty.",
            ["Merrick Winters"],
            [],
        ),
        # A module name alone does not pull the entities named after it.
        (
            {
                "Ravenloft Vampire": "Vampire of Ravenloft",
                "Ravenloft Shadow": "Shadow of Ravenloft",
                "Barovia": "Barovia",
            },
            {},
            "Welcome to Ravenloft.",
            [],
            ["Ravenloft Vampire", "Ravenloft Shadow", "Barovia"],
        ),
        (
            {"Ravenloft Vampire": "Vampire of Ravenloft"},
            {},
            "The Ravenloft Vampire blocks the road.",
            ["Ravenloft Vampire"],
            [],
        ),
        # Spelling variants need an explicit alias.
        (
            {"Merrick": "Меррик", "Merric": "Меррик"},
            {"Merric": "Merrick"},
            "I met Merric in the hall.",
            ["Merrick"],
            [],
        ),
        ({"Iris": "Ирис"}, {}, "The Irish warrior approached.", [], ["Iris"]),
        (
            {"Inmate": "Заключённый", "Inmate2": "Заключённый", "Inmate3": "Заключённый"},
            {},
            "The Inmate refused to talk.",
            ["Inmate"],
            ["Inmate2", "Inmate3"],
        ),
        ({"Łódź": "Лодзь"}, {}, "Travelled through łódź at dawn.", ["Łódź"], []),
        # Hierarchical keys need their specific components, not only the shared prefix.
        (
            {
                **{f"Almraiven - District {i} - Street {i}": f"Алмрайвен {i}" for i in range(20)},
                "Almraiven": "Алмрайвен",
                "Almraiven - Dock Ward - Rosetyl Street": "Алмрайвен — Док — Розетил",
            },
            {},
            "I have lived in Almraiven all my life.",
            ["Almraiven"],
            ["Almraiven - Dock Ward - Rosetyl Street"]
            + [f"Almraiven - District {i} - Street {i}" for i in range(20)],
        ),
        (
            {
                "Almraiven - Dock Ward - Rosetyl Street": "Алмрайвен — Док — Розетил",
                "Almraiven - Emerald Ward - Loom Avenue": "Алмрайвен — Изумруд — Ткацкая",
                "Rosetyl Street": "улица Розетил",
            },
            {"Rosetyl Street": "Almraiven - Dock Ward - Rosetyl Street"},
            "The Rosetyl Street merchants gathered at the Dock Ward gates.",
            ["Rosetyl Street"],
            ["Almraiven - Emerald Ward - Loom Avenue"],
        ),
    ],
)
def test_the_block_keeps_the_entries_the_text_names(entries, aliases, text, present, absent):
    keys = _keys(Glossary(entries=entries, aliases=aliases).to_prompt_block(texts=[text]))
    assert set(present) <= set(keys)
    assert not set(absent) & set(keys)


def test_each_translation_is_listed_once_in_sorted_order():
    block = Glossary(
        entries={"Zephyr": "Зефир", "Arlena": "Арлена", "Morin": "Морин"}
    ).to_prompt_block(texts=["Morin met Arlena and Zephyr at dawn"])
    assert 0 < block.find('"Arlena"') < block.find('"Morin"') < block.find('"Zephyr"')
    inmates = Glossary(entries={"Inmate": "Заключённый", "Inmate2": "Заключённый"})
    assert inmates.to_prompt_block(texts=["The Inmate refused."]).count("Заключённый") == 1


def test_block_budget_caps_a_relevant_prefix_explosion():
    entries = {f"Almraiven - Area {idx}": f"Алмрейвен {idx}" for idx in range(100)}
    entries["Gewia the Wererat"] = "Гевия-веркрыса"

    block = Glossary(entries=entries).to_prompt_block(
        texts=["Gewia the Wererat mentions Almraiven only briefly."]
    )

    assert '"Gewia the Wererat"' in block
    assert block.count("Almraiven - Area") <= 40
    assert len(block) < 6000


def test_matching_texts_one_by_one_equals_scanning_the_joined_corpus():
    entries = {
        "Aloro": "Алоро",
        "Aloro Harmony": "Гармония Алоро",
        "Nas": "Нас",
        "R. Freely": "Р. Фрили",
        '"Thesis Paper Room"': "«Зал диссертаций»",
    }
    glossary = Glossary(entries=dict(entries), aliases={"Aloro Harmony": "Aloro"})
    batches = [
        ["Sing Aloro Harmony now.", "nasher stood there"],
        ["ALORO!", 'Enter "Thesis Paper Room".'],
        ["Contact R. Freely.", "", None],
        ["Nas.", "Aloro Harmony"],
    ]
    for texts in batches:
        corpus = "\n".join(str(text) for text in texts if text)
        found = {
            key
            for key in entries
            if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", corpus, re.IGNORECASE)
        }
        roots = {glossary.aliases.get(k, k) for k in found}
        assert set(glossary.matching_entries(texts)) == {
            k for k in entries if k in found or glossary.aliases.get(k, k) in roots
        }
    # Repeated calls hit the memo and return the same result.
    assert glossary.matching_entries(batches[0]) == glossary.matching_entries(batches[0])


def test_shared_words_do_not_infer_matches():
    glossary = Glossary({"Shadow Lord": "Повелитель теней", "Jade Falcon": "Джейд Фалкон"})
    assert glossary.matching_entries(["Shadow"]) == {}
    assert glossary.matching_entries(["Jade"]) == {}
    assert glossary.entries == {"Shadow Lord": "Повелитель теней", "Jade Falcon": "Джейд Фалкон"}


def test_terminology_block_merges_race_terms_once_per_language():
    glossary = Glossary(entries={"Perin": "Перин"})
    first = terminology_block(["Perin met a dwarf."], "russian", glossary)
    assert first == terminology_block(["Perin met a dwarf."], "Russian", glossary)
    assert '"Perin"' in first and '"dwarf"' in first
    assert list(glossary._with_terms) == ["russian"]
    assert terminology_block(["a dwarf"], "russian", None).count("dwarf") == 1


def test_project_term_has_one_authoritative_translation():
    block = terminology_block(["Sword Spider"], "russian", Glossary({"Sword Spider": "Другой"}))
    assert "мечепряд" in block
    assert "Другой" not in block


def test_restore_wrapping_quotes():
    assert restore_wrapping_quotes('"Welcome!"', "Добро пожаловать!") == '"Добро пожаловать!"'
