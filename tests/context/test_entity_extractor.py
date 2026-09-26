"""EntityExtractor: which texts are sent to the model and which names come back."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import List

import pytest

from nwn_translator.config import GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from nwn_translator.context import entity_extractor
from nwn_translator.context.entity_extractor import EntityExtractor
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.prompts.terminology import (
    build_entity_extraction_system_prompt,
    build_entity_extraction_user_prompt,
)

LONG = "This long passage mentions a named place and is well over forty characters."


def _item(text: str, **metadata) -> TranslatableItem:
    return TranslatableItem(text=text, metadata=metadata)


class _Provider:
    """Records calls and answers with scripted payloads (then an empty entity list)."""

    def __init__(self, payloads: List[str] = ()):
        self.payloads = list(payloads)
        self.calls: List[tuple] = []
        self.requests: List[dict] = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        self.requests.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.payloads.pop(0) if self.payloads else '{"entities": []}'


def _extract(items, payloads=(), known=(), provider=None, source_lang="English"):
    provider = provider or _Provider(payloads)
    config = SimpleNamespace(source_lang=source_lang, max_concurrent_requests=3)
    return EntityExtractor().extract(items, provider, config, known_names=set(known)), provider


def _entities(*pairs) -> str:
    return '{"entities": [%s]}' % ", ".join('{"name": "%s", "type": "%s"}' % pair for pair in pairs)


@pytest.mark.parametrize(
    "items, selected",
    [
        ([_item("Hi"), _item("x" * 60)], ["x" * 60]),
        ([_item(LONG), _item(LONG)], [LONG]),
        ([_item(""), _item("   "), _item("a" * 45)], ["a" * 45]),
        # Service markup and code-like labels are not prose.
        (
            [
                _item("<FirstName>" * 10),
                _item("ARCH_TARGET " * 8),
                _item("BakersPleaBakersPleaBakersPleaBakersPlea"),
                _item("DMFI Admin Server Wand " * 3),
                _item("This long passage mentions Stout Village and should be analyzed."),
            ],
            ["This long passage mentions Stout Village and should be analyzed."],
        ),
        (
            [
                _item("CastleExt1To2SouthCastleExt1To2South", type="trigger_name"),
                _item("This trigger says Madam Eva waits in Barovia.", type="trigger_name"),
            ],
            ["This trigger says Madam Eva waits in Barovia."],
        ),
        # Proven script barks are short but carry nicknames the glossary must lock.
        (
            [
                _item("Stay back, staff-one!", type="ncs_string", proven_player=True),
                _item("short dlg", type="entry"),
            ],
            ["Stay back, staff-one!"],
        ),
    ],
)
def test_texts_sent_to_the_model(items, selected):
    _found, provider = _extract(items)
    assert [call[1] for call in provider.calls] == [build_entity_extraction_user_prompt(selected)]


def test_exact_request():
    items = [
        _item('Leading a coach to "Stout Village"\nwith farming equipment.'),
        _item("Stay back, sword-one!", type="ncs_string"),
        _item("Stay back, sword-one!", type="ncs_string", proven_player=True),
    ]
    provider = _Provider([_entities(("Stout Village", "location"))])

    # An "auto" source language asks for English names.
    found, _provider = _extract(items, provider=provider, source_lang="auto")

    assert found == [("Stout Village", "location")]
    assert provider.requests == [
        {
            "system": build_entity_extraction_system_prompt("English"),
            "user": "Extract proper nouns from these texts:\n\n"
            "[0] \"Leading a coach to 'Stout Village' with farming equipment.\"\n"
            '[1] "Stay back, sword-one!"',
            "max_tokens": GLOSSARY_MAX_TOKENS,
            "temperature": GLOSSARY_TEMPERATURE,
            "use_reasoning": False,
        }
    ]


def test_no_request_without_long_enough_texts():
    found, provider = _extract([_item("hi"), _item("short")])
    assert (found, provider.calls) == ([], [])


@pytest.mark.parametrize(
    "payload, found",
    [
        (
            _entities(("Stout Village", "location"), ("Marvin", "character")),
            [("Stout Village", "location"), ("Marvin", "character")],
        ),
        # Text around the object is tolerated.
        (
            "here you go: " + _entities(("Ancient Blade", "item")) + " done",
            [("Ancient Blade", "item")],
        ),
        # Some models use another wrapper key for the list.
        ('{"results": [{"name": "Zedrick", "type": "character"}]}', [("Zedrick", "character")]),
        # Entries without a name are skipped; a missing or invalid type is unknown.
        (
            '{"entities": [{"type": "character"}, {"name": "Western Gate"}]}',
            [("Western Gate", "unknown")],
        ),
        ("not json at all", []),
        ("", []),
    ]
    + [
        (_entities(("Western Gate", category)), [("Western Gate", expected)])
        for category, expected in [
            ("character", "character"),
            ("LOCATION", "location"),
            ("Organization", "organization"),
            ("item", "item"),
            ("unknown", "unknown"),
            ("weapon", "unknown"),
        ]
    ]
    + [
        (
            '{"entities": [{"name": "Western Gate", "type": %s}]}' % value,
            [("Western Gate", "unknown")],
        )
        for value in ("null", "42")
    ],
)
def test_reply_parsing(payload, found):
    assert _extract([_item(LONG)], [payload])[0] == found


def test_known_names_and_model_noise_are_filtered():
    noise = _entities(
        ("DMFI", "organization"),
        ("<FirstName>", "character"),
        ("ARCH_TARGET", "location"),
        ("BakersPlea", "quest"),
        ("Madam Eva", "character"),
        ("Stout Village", "location"),
        ("stout village", "location"),
        ("Glod Gloddson", "character"),
        ("glod gloddson", "character"),
        ("Barovia", "unknown"),  # a single unknown word
        ("Western Gate", "unknown"),
    )
    found, _provider = _extract([_item(LONG)], [noise], known={"Glod Gloddson"})
    assert found == [
        ("Madam Eva", "character"),
        ("Stout Village", "location"),
        ("Western Gate", "unknown"),
    ]


def test_failed_request_yields_nothing():
    class _Failing(_Provider):
        async def complete_json_chat_async(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert _extract([_item(LONG)], provider=_Failing())[0] == []


def test_names_are_deduplicated_across_batches():
    texts = [f"Sentence number {i} that is well over forty chars long here." for i in range(60)]
    found, provider = _extract(
        [_item(t) for t in texts],
        [_entities(("Dup Name", "location")), _entities(("dup name", "character"))],
    )
    assert len(provider.calls) == 2  # batches of 50 and 10
    assert found == [("Dup Name", "location")]


def test_overall_timeout_keeps_finished_batches(monkeypatch):
    stage = replace(entity_extractor._STAGE, run_timeout_per_batch=0.1)
    monkeypatch.setattr(entity_extractor, "_STAGE", stage)

    class _SecondBatchStalls(_Provider):
        async def complete_json_chat_async(self, system_prompt, user_prompt, **_kw):
            self.calls.append((system_prompt, user_prompt))
            if len(self.calls) == 2:
                await asyncio.sleep(5)
            return _entities(("Stout Village", "location"))

    texts = [f"Sentence number {i} that is well over forty chars long here." for i in range(60)]
    found, provider = _extract([_item(t) for t in texts], provider=_SecondBatchStalls())

    assert len(provider.calls) == 2
    assert found == [("Stout Village", "location")]
