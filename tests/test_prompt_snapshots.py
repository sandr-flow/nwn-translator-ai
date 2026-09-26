"""Byte-level snapshot of every prompt the pipeline sends to the model.

Each case is reduced to the sha256 of its canonical JSON and compared with
``tests/fixtures/prompt_snapshots.json``. Provider cases hash the complete
keyword arguments of ``chat.completions.create`` (system and user messages,
cache_control layout, temperature, token budget, reasoning and ``stream``
keys); builder cases hash the returned prompt text. A refactor that moves
prompt code must leave every hash unchanged.

After an intended prompt change regenerate the fixture with::

    python tests/test_prompt_snapshots.py --update
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openai.resources.chat.completions import AsyncCompletions

from nwn_translator import config
from nwn_translator.ai_providers import create_provider
from nwn_translator.ai_providers.base import TranslationItem
from nwn_translator.prompts.terminology import build_curator_system_prompt as curator_system_prompt
from nwn_translator.prompts import (
    build_dialog_system_prompt_parts,
    build_entity_extraction_system_prompt,
    build_glossary_system_prompt,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "prompt_snapshots.json"

#: Every target language with its own examples in ``prompts.examples``.
LANGUAGES = (
    "russian",
    "english",
    "ukrainian",
    "polish",
    "german",
    "french",
    "spanish",
    "italian",
    "portuguese",
    "czech",
    "romanian",
    "hungarian",
    "dutch",
)
PROFILES = ("default", "short_label", "script_message")
GENDERS = ("male", "female")

#: Unknown to the model catalog, so reasoning efforts pass through unclamped and
#: the requests do not depend on whether a test fetched the live catalog.
MODEL = "snapshot/model"
GLOSSARY = 'GLOSSARY:\n* "Perin Izrick" -> Perin\n* "Dark Ranger" -> Ranger'
#: Mentions race terms so the race block reaches the prompt when no glossary is given.
SINGLE_TEXT = "The dwarf and the elf greet you, <FirstName>."
SINGLE_CONTEXT = "Dialog line spoken by Innkeeper Bob"
WORLD_BLOCK = "WORLD CONTEXT:\n- Innkeeper Bob (Human, Male)"

#: One reply every provider task can parse, so each call makes exactly one request.
REPLY = '{"translation": "ok"}'


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _batch_items() -> List[TranslationItem]:
    return [
        TranslationItem(original="Sword of the dwarf", metadata={"type": "item_name"}),
        TranslationItem(
            original="A fine blade.",
            context="Description of item 'Sword'",
            metadata={"type": "item_description"},
        ),
        TranslationItem(original="Plain text"),
        TranslationItem(original="Speech", metadata={"type": "ncs_string", "ncs_hint": "Speak"}),
    ]


def _grouped_items() -> List[TranslationItem]:
    group = {"translation_group": "creature[0]", "batch_resource": "area.git"}
    return [
        TranslationItem(
            original="Jade",
            context="full fallback context",
            metadata={
                **group,
                "type": "creature_first_name",
                "batch_context": "First name of a creature",
                "shared_context": "Creature: Jade Falcon (Elf, Female)",
            },
        ),
        TranslationItem(
            original="Falcon",
            metadata={
                **group,
                "type": "creature_last_name",
                "batch_context": "First name of a creature",
                "shared_context": "Creature: Jade Falcon (Elf, Female)",
                "approved_neighbors": ["Hello there!", "Jade"],
            },
        ),
        TranslationItem(
            original="Hello there!",
            metadata={
                "translation_group": "script[bark]",
                "batch_resource": "bark.ncs",
                "type": "ncs_string",
                "hint": "SpeakString",
                "nss_snippet": 'SpeakString("Hello there!");',
                "nss_start": 10,
            },
        ),
        TranslationItem(
            original="Goodbye!",
            metadata={
                "translation_group": "script[bark]",
                "batch_resource": "bark.ncs",
                "type": "ncs_string",
                "nss_snippet": '("Hello there!");\nSpeakString("Goodbye!");',
                "nss_start": 21,
            },
        ),
        TranslationItem(original="Ungrouped", context="Name of a store"),
    ]


def _gate_entries(with_sources: bool) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = [
        {
            "key": "0",
            "text": "Stay back!",
            "file": "bark.ncs",
            "offset": 42,
            "hint": "SpeakString",
            "bytecode_context": {"consumer_proven": True, "next_action_name": "SpeakString"},
            "confidence": "likely_player",
        },
        {"key": "1", "text": "WP_HOME", "file": "bark.ncs", "offset": None},
        {
            "key": "2",
            "text": "Take this, friend.",
            "file": "give.ncs",
            "offset": 7,
            "nss_snippet": 'SpeakString("Take this, friend.");',
        },
    ]
    if with_sources:
        entries[0].update(nss_snippet='if (x) SpeakString("Stay back!");', nss_start=100)
        entries[1].update(
            nss_snippet='SpeakString("Stay back!");\nGetWaypointByTag(', nss_start=107
        )
    return entries


async def _provider_cases(record: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Drive every provider task once per case and collect the sent requests."""
    cases: Dict[str, Any] = {}

    async def capture(name: str, call: Any) -> None:
        record.clear()
        await call
        cases[name] = list(record)

    providers = {
        gender: create_provider("sk-or-snapshot", MODEL, player_gender=gender) for gender in GENDERS
    }
    for lang in LANGUAGES:
        for profile in PROFILES:
            for gender, provider in providers.items():
                for glossary_name, glossary in (("glossary", GLOSSARY), ("race", None)):
                    prefix = f"translation/{lang}/{profile}/{gender}"
                    await capture(
                        f"{prefix}/single/{glossary_name}",
                        provider.translate_async(
                            SINGLE_TEXT,
                            "english",
                            lang,
                            context=SINGLE_CONTEXT,
                            glossary_block=glossary,
                            content_profile=profile,
                        ),
                    )
                    await capture(
                        f"{prefix}/batch/{glossary_name}",
                        provider.translate_batch_async(
                            _batch_items(),
                            "english",
                            lang,
                            glossary_block=glossary,
                            content_profile=profile,
                        ),
                    )

    male = providers["male"]
    await capture(
        "user/single/no_context",
        male.translate_async("Hello", "english", "russian", glossary_block=GLOSSARY),
    )
    await capture(
        "user/batch/grouped",
        male.translate_batch_async(_grouped_items(), "english", "russian"),
    )
    await capture(
        "gate/sources",
        male.classify_ncs_translate_gate_batch_async(_gate_entries(True), source_lang="english"),
    )
    await capture(
        "gate/no_sources",
        male.classify_ncs_translate_gate_batch_async(_gate_entries(False), source_lang="auto"),
    )
    await capture(
        "json_chat/text",
        male.complete_json_chat_async("SYSTEM", "USER", max_tokens=100, temperature=0.3),
    )
    await capture(
        "json_chat/parts_no_reasoning",
        male.complete_json_chat_async(
            male.make_system_message_content("STABLE", "VARIABLE"),
            "USER",
            max_tokens=200,
            temperature=0.6,
            use_reasoning=False,
        ),
    )
    await capture(
        "glossary_chat",
        male.complete_glossary_chat_async(
            "SYSTEM", "USER", glossary_keys=["A", "B"], max_tokens=300, temperature=0.3
        ),
    )
    reasoning = create_provider("sk-or-snapshot", MODEL, reasoning_effort="high")
    await capture(
        "reasoning/single",
        reasoning.translate_async("Hello", "english", "german"),
    )
    polza = create_provider("pza-snapshot", MODEL)
    await capture(
        "polza/batch",
        polza.translate_batch_async(_batch_items(), "english", "french"),
    )
    for provider in (*providers.values(), reasoning, polza):
        await provider.close_async_client()
    return cases


def collect_snapshots(monkeypatch: Any) -> Dict[str, str]:
    """Return ``case id -> sha256`` for every prompt case.

    Args:
        monkeypatch: Object with ``setattr(target, name, value)`` used to replace
            the chat-completions endpoint for the duration of the collection.

    Returns:
        Mapping of case identifiers to hex digests, sorted by identifier.
    """
    record: List[Dict[str, Any]] = []

    async def fake_create(self: Any, **kwargs: Any) -> Any:
        record.append(kwargs)
        message = SimpleNamespace(content=REPLY)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    monkeypatch.setattr(AsyncCompletions, "create", fake_create)
    cases: Dict[str, Any] = asyncio.run(_provider_cases(record))

    content = create_provider("sk-or-snapshot", MODEL).make_system_message_content
    for lang in LANGUAGES:
        for gender in GENDERS:
            stable, variable = build_dialog_system_prompt_parts(lang, gender, WORLD_BLOCK, GLOSSARY)
            cases[f"dialog/{lang}/{gender}"] = {
                "parts": [stable, variable],
                "content": content(stable, variable),
                "bare": build_dialog_system_prompt_parts(lang, gender, "", ""),
            }
        cases[f"glossary/{lang}"] = build_glossary_system_prompt(lang)
        cases[f"curator/{lang}"] = curator_system_prompt(lang)
        cases[f"entity_extraction/{lang}"] = build_entity_extraction_system_prompt(lang)
    cases["entity_extraction/English"] = build_entity_extraction_system_prompt("English")
    return {name: _digest(cases[name]) for name in sorted(cases)}


@pytest.mark.skipif(
    not config.PROMPT_CACHE_BREAKPOINTS_ENABLED,
    reason="snapshots are recorded with prompt-cache breakpoints enabled",
)
def test_prompts_match_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = collect_snapshots(monkeypatch)
    changed = sorted(
        name for name in expected.keys() & actual.keys() if expected[name] != actual[name]
    )
    assert not changed, f"{len(changed)} prompt snapshot(s) changed, e.g. {changed[:5]}"
    assert sorted(actual) == sorted(expected)


if __name__ == "__main__" and "--update" in sys.argv:
    with pytest.MonkeyPatch.context() as patcher:
        snapshots = collect_snapshots(patcher)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(snapshots, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(snapshots)} snapshots to {FIXTURE}")
