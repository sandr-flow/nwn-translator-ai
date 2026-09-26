"""GlossaryCurator: deciding which candidates reach the glossary."""

import asyncio
import json
from dataclasses import replace
from itertools import product
from types import SimpleNamespace

from nwn_translator import glossary_curator
from nwn_translator.config import GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.glossary_curator import GlossaryCurator, _parse_curator_json


class _Provider:
    """Answers from *payloads* in order, or keeps every record with ``reason(records)``."""

    def __init__(self, payloads=(), reason=None, share=1.0):
        self.payloads = list(payloads)
        self.reason, self.share = reason, share
        self.calls = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        if self.reason is None:
            return self.payloads.pop(0)
        records = list(json.loads(user_prompt[user_prompt.index("{") :]))
        answered = records[: int(len(records) * self.share)]
        reason = await self.reason(records)
        return json.dumps({name: {"decision": "keep", "reason": reason} for name in answered})


def _config(concurrency=1):
    return SimpleNamespace(target_lang="russian", max_concurrent_requests=concurrency)


def _guilds(count, letters="abcdefghijklmn"):
    registry = EntityCandidateRegistry()
    for a, b in list(product(letters, repeat=2))[:count]:
        registry.add(f"Guild of {a.upper()}{b}", category="term", source="entity_extractor")
    return registry


def test_curation_requests_retry_only_the_missing_keys():
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
    provider = _Provider(
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
    auren, barrel = registry.values()[:2]
    missing = {"Auren Society": auren.to_curator_record(), "Barrel": barrel.to_curator_record()}
    assert second["user"].endswith(json.dumps(missing, ensure_ascii=False, indent=2))
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


def test_partial_answers_are_completed_by_a_retry():
    registry = EntityCandidateRegistry()
    registry.add("Gewia the Wererat", category="character", source="dlg_speaker")
    registry.add("Auren Society", category="faction", source="entity_extractor")
    provider = _Provider(
        [
            '{"Gewia the Wererat": {"decision": "keep", "reason": "unique", "priority": 90}}',
            '{"Auren Society": {"decision": "keep", "reason": "faction", "priority": 80}}',
        ]
    )

    GlossaryCurator().curate(registry, provider, _config())

    by_name = {candidate.name: candidate for candidate in registry.values()}
    assert by_name["Gewia the Wererat"].curation_decision == "keep"
    assert by_name["Auren Society"].curation_reason == "faction"
    assert len(provider.calls) == 2


def test_numbered_labels_are_dropped_before_the_model():
    registry = EntityCandidateRegistry()
    registry.add("Food 5", category="item", source="git_instance")
    provider = _Provider()

    GlossaryCurator().curate(registry, provider, _config())

    assert registry.values()[0].curation_decision == "drop"
    assert provider.calls == []


def test_a_batch_keeps_its_slot_for_its_retry_and_reports_progress_when_it_starts():
    events = []

    async def reason(records):
        events.append(len(records))
        await asyncio.sleep(0.01)
        return "llm"

    def progress(phase, done, total, message):
        events.append(message)

    GlossaryCurator().curate(_guilds(170), _Provider(reason=reason, share=0.5), _config(), progress)

    assert events == [
        "Curating glossary candidates 1/3",
        80,
        40,
        "Curating glossary candidates 2/3",
        80,
        40,
        "Curating glossary candidates 3/3",
        10,
        5,
    ]


def test_overall_timeout_keeps_the_decisions_of_finished_batches(monkeypatch):
    stage = replace(glossary_curator._STAGE, run_timeout_per_batch=0.1)
    monkeypatch.setattr(glossary_curator, "_STAGE", stage)
    registry = _guilds(81, "abcdefghi")

    async def small_batch_stalls(records):
        if len(records) < 80:
            await asyncio.sleep(5)
        return "llm"

    GlossaryCurator().curate(registry, _Provider(reason=small_batch_stalls), _config(2))

    assert [c.curation_reason for c in registry.values()] == ["llm"] * 80 + [""]


def test_a_batch_that_raises_keeps_the_decisions_of_the_other_batches(monkeypatch):
    parse = glossary_curator._parse_curator_json

    def parse_or_fail(raw, expected_keys):
        if "boom" in raw:
            raise RuntimeError("parser bug")
        return parse(raw, expected_keys)

    monkeypatch.setattr(glossary_curator, "_parse_curator_json", parse_or_fail)
    registry = _guilds(81, "abcdefghi")

    async def small_batch_breaks_the_parser(records):
        return "llm" if len(records) == 80 else "boom"

    GlossaryCurator().curate(registry, _Provider(reason=small_batch_breaks_the_parser), _config())

    assert [c.curation_reason for c in registry.values()] == ["llm"] * 80 + [""]


def test_infinite_priority_reads_as_no_priority():
    parsed = _parse_curator_json(
        '{"Auren Society": {"decision": "keep", "reason": "faction", "priority": Infinity}}',
        {"Auren Society"},
    )
    assert parsed == {
        "Auren Society": {"decision": "keep", "reason": "faction", "priority": 0, "alias_of": None}
    }
