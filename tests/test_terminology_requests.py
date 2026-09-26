"""Exact requests of the terminology stages: entity extraction, curation and glossary.

The prompt snapshot test pins the system prompts; these cases pin the user
messages, the request keyword arguments and the retry requests, driven through
the public stage entry points with a recording provider.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

from nwn_translator.config import GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.context.entity_extractor import EntityExtractor
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.glossary_builder import GlossaryBuilder
from nwn_translator.glossary_curator import GlossaryCurator
from nwn_translator.prompts import (
    build_entity_extraction_system_prompt,
    build_glossary_system_prompt,
)


class _RecordingProvider:
    """Answers each request with the next scripted reply and records the call."""

    def __init__(self, replies: List[str]) -> None:
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.replies.pop(0)

    async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.replies.pop(0)


def _config(**overrides: Any) -> SimpleNamespace:
    values = {"source_lang": "auto", "target_lang": "russian", "max_concurrent_requests": 1}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_entity_extraction_request() -> None:
    items = [
        TranslatableItem(text='Leading a coach to "Stout Village"\nwith farming equipment.'),
        TranslatableItem(text="Stay back, sword-one!", metadata={"type": "ncs_string"}),
        TranslatableItem(
            text="Stay back, sword-one!",
            metadata={"type": "ncs_string", "proven_player": True},
        ),
    ]
    provider = _RecordingProvider(['{"entities": [{"name": "Stout Village", "type": "location"}]}'])

    found = EntityExtractor().extract(items, provider, _config(), known_names=set())

    assert found == [("Stout Village", "location")]
    assert provider.calls == [
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


def _curation_registry() -> EntityCandidateRegistry:
    registry = EntityCandidateRegistry()
    registry.add(
        "Gewia the Wererat",
        category="character",
        source="dlg_speaker",
        resource="gewia.dlg",
        field="Speaker",
        context="Hello   there,\ntraveller.",
        is_speaker_or_dialog_actor=True,
    )
    registry.add("Auren Society", category="organization", source="entity_extractor")
    registry.add("auren society", category="organization", source="are_name", resource="a.are")
    registry.add("Barrel", category="unknown", source="git_instance")
    return registry


def test_curation_requests_retry_only_missing_keys() -> None:
    registry = _curation_registry()
    provider = _RecordingProvider(
        [
            '{"Gewia the Wererat": {"decision": "keep", "reason": "unique", "priority": 90}}',
            '{"auren society": {"decision": "alias_of", "reason": "variant", "alias_of": "X"}}',
        ]
    )

    GlossaryCurator().curate(registry, provider, _config())

    first, second = provider.calls
    assert first["user"] == (
        "Curate these candidates. Return a JSON object keyed by candidate name. "
        "Each value must contain decision, reason (short tag), priority, and optionally "
        "alias_of (an existing source form).\n\n"
        + json.dumps(
            {
                "Auren Society": {
                    "name": "Auren Society",
                    "category": "organization",
                    "sources": ["are_name", "entity_extractor"],
                    "frequency": 2,
                    "contexts": [],
                    "technical_flags": [],
                    "is_speaker_or_dialog_actor": False,
                },
                "Barrel": {
                    "name": "Barrel",
                    "category": "unknown",
                    "sources": ["git_instance"],
                    "frequency": 1,
                    "contexts": [],
                    "technical_flags": ["unknown_single_word"],
                    "is_speaker_or_dialog_actor": False,
                },
                "Gewia the Wererat": {
                    "name": "Gewia the Wererat",
                    "category": "character",
                    "sources": ["dlg_speaker"],
                    "frequency": 1,
                    "contexts": ["Hello there, traveller."],
                    "technical_flags": [],
                    "is_speaker_or_dialog_actor": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert second["user"].endswith(
        json.dumps(
            {
                "Auren Society": registry.values()[0].to_curator_record(),
                "Barrel": registry.values()[1].to_curator_record(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for call in (first, second):
        assert call["system"].startswith("You curate proper-name candidates")
        assert "Target language: russian." in call["system"]
        assert (call["max_tokens"], call["temperature"], call["use_reasoning"]) == (
            GLOSSARY_MAX_TOKENS,
            GLOSSARY_TEMPERATURE,
            False,
        )
    decisions = {
        c.name: (c.curation_decision, c.curation_reason, c.alias_of) for c in registry.values()
    }
    assert decisions == {
        "Auren Society": ("alias_of", "variant", "X"),
        "Barrel": ("local_only", "curator_missing_key", None),
        "Gewia the Wererat": ("keep", "unique", None),
    }


def _glossary_world() -> WorldContext:
    world = WorldContext()
    world.npcs["dawn"] = NPCInfo("dawn", "Dawn", "Ioza", "", "Human", "Female", "dawn_conv")
    registry = world.candidates
    registry.add("Dawn Ioza", category="character", source="utc_name", context="A priestess.")
    registry.add("sword-one", category="nickname", source="entity_extractor")
    registry.add("Melee vs Mayhem", category="term", source="entity_extractor")
    registry.add("MVM", category="term", source="entity_extractor", context="Play MVM!")
    registry.add("Dark Forest", category="location", source="are_name")
    registry.mark_curated("MVM", decision="alias_of", alias_of="Melee vs Mayhem")
    return world


def test_glossary_requests_retry_with_accepted_forms() -> None:
    provider = _RecordingProvider(
        [
            '{"glossary": {"Dark Forest (location)": "Тёмный лес"}}',
            '{"Dawn Ioza": "Доун Иоза", "Melee vs Mayhem": "Битва", "MVM": "БТВ",'
            ' "sword-one": "ты с мечом"}',
        ]
    )

    glossary = GlossaryBuilder().build(_glossary_world(), provider, _config())

    assert glossary.entries == {
        "Dark Forest": "Тёмный лес",
        "Dawn Ioza": "Доун Иоза",
        "Melee vs Mayhem": "Битва",
        "MVM": "БТВ",
        "sword-one": "ты с мечом",
    }
    assert glossary.aliases == {"MVM": "Melee vs Mayhem"}
    header = (
        "Translate every name below. Keys in your JSON must be the English name only, "
        "without the parenthesized category hint:\n\n"
    )
    lines = {
        "Dark Forest": "- Dark Forest (location)",
        "Dawn Ioza": "- Dawn Ioza (character, A priestess., NPC fields: FirstName='Dawn', "
        "LastName='Ioza', gender=Female)",
        "Melee vs Mayhem": "- Melee vs Mayhem (term)",
        "MVM": "- MVM (term, alias of Melee vs Mayhem; preserve abbreviation or wordplay, "
        "Play MVM!)",
        "sword-one": "- sword-one (nickname, vocative epithet; translate meaning, not a name)",
    }
    order = ["Dark Forest", "Dawn Ioza", "Melee vs Mayhem", "MVM", "sword-one"]
    first, second = provider.calls
    assert first["user"] == header + "\n".join(lines[name] for name in order)
    assert first["glossary_keys"] == order
    assert second["user"] == (
        header
        + "\n".join(lines[name] for name in order[1:])
        + '\n\nAlready accepted forms in this family/batch: {"Dark Forest": "Тёмный лес"}'
    )
    assert second["glossary_keys"] == order[1:]
    for call in (first, second):
        assert call["system"] == build_glossary_system_prompt("russian")
        assert (call["max_tokens"], call["temperature"]) == (
            GLOSSARY_MAX_TOKENS,
            GLOSSARY_TEMPERATURE,
        )
        assert set(call) == {"system", "user", "glossary_keys", "max_tokens", "temperature"}
