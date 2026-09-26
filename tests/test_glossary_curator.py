"""Tests for GlossaryCurator."""

from types import SimpleNamespace

from src.nwn_translator.context.entity_candidates import EntityCandidateRegistry
from src.nwn_translator.glossary_curator import GlossaryCurator


class _CuratorProvider:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt, kwargs))
        return self.payloads.pop(0)

    async def close_async_client(self):
        return None


def _config():
    return SimpleNamespace(target_lang="russian", max_concurrent_requests=1)


def test_curator_accepts_partial_json_and_retries_missing_keys():
    registry = EntityCandidateRegistry()
    registry.add("Gewia the Wererat", category="character", source="dlg_speaker")
    registry.add("Auren Society", category="faction", source="entity_extractor")

    provider = _CuratorProvider(
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


def test_curator_drops_numbered_labels_before_llm():
    registry = EntityCandidateRegistry()
    registry.add("Food 5", category="item", source="git_instance")
    provider = _CuratorProvider([])

    GlossaryCurator().curate(registry, provider, _config())

    candidate = registry.values()[0]
    assert candidate.curation_decision == "drop"
    assert provider.calls == []


def test_batch_keeps_its_slot_for_its_retry_and_reports_progress_when_it_starts():
    import asyncio
    import json
    from itertools import product

    registry = EntityCandidateRegistry()
    for a, b in list(product("abcdefghijklmn", repeat=2))[:170]:
        registry.add(f"Guild of {a.upper()}{b}", category="term", source="entity_extractor")
    events = []

    class _AnswersHalfFirst(_CuratorProvider):
        async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
            records = json.loads(user_prompt[user_prompt.index("{") :])
            events.append(("request", len(records)))
            await asyncio.sleep(0.01)
            answered = list(records)[: len(records) // 2]
            return json.dumps({name: {"decision": "keep", "reason": "llm"} for name in answered})

    def progress(phase, done, total, message):
        events.append(("progress", message))

    GlossaryCurator().curate(registry, _AnswersHalfFirst([]), _config(), progress)

    assert events == [
        ("progress", "Curating glossary candidates 1/3"),
        ("request", 80),
        ("request", 40),
        ("progress", "Curating glossary candidates 2/3"),
        ("request", 80),
        ("request", 40),
        ("progress", "Curating glossary candidates 3/3"),
        ("request", 10),
        ("request", 5),
    ]


def test_overall_timeout_keeps_decisions_of_finished_batches(monkeypatch):
    import asyncio
    import json
    from dataclasses import replace
    from itertools import product

    import src.nwn_translator.glossary_curator as module

    monkeypatch.setattr(module, "_STAGE", replace(module._STAGE, batch_timeout=0.1))
    registry = EntityCandidateRegistry()
    names = [f"Guild of {a.upper()}{b}" for a, b in product("abcdefghi", repeat=2)][:81]
    for name in names:
        registry.add(name, category="term", source="entity_extractor")

    class _SecondBatchStalls(_CuratorProvider):
        async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
            records = json.loads(user_prompt[user_prompt.index("{") :])
            if len(records) < 80:
                await asyncio.sleep(5)
            return json.dumps({name: {"decision": "keep", "reason": "llm"} for name in records})

    config = SimpleNamespace(target_lang="russian", max_concurrent_requests=2)
    GlossaryCurator().curate(registry, _SecondBatchStalls([]), config)

    reasons = [candidate.curation_reason for candidate in registry.values()]
    assert reasons == ["llm"] * 80 + [""]


def test_infinite_priority_reads_as_no_priority():
    from src.nwn_translator.glossary_curator import _parse_curator_json

    parsed = _parse_curator_json(
        '{"Auren Society": {"decision": "keep", "reason": "faction", "priority": Infinity}}',
        {"Auren Society"},
    )

    assert parsed == {
        "Auren Society": {"decision": "keep", "reason": "faction", "priority": 0, "alias_of": None}
    }


def test_batch_that_raises_keeps_the_decisions_of_the_other_batches(monkeypatch):
    import json
    from itertools import product

    import src.nwn_translator.glossary_curator as module

    parse = module._parse_curator_json

    def parse_or_fail(raw, expected_keys):
        if "boom" in raw:
            raise RuntimeError("parser bug")
        return parse(raw, expected_keys)

    monkeypatch.setattr(module, "_parse_curator_json", parse_or_fail)
    registry = EntityCandidateRegistry()
    for a, b in list(product("abcdefghi", repeat=2))[:81]:
        registry.add(f"Guild of {a.upper()}{b}", category="term", source="entity_extractor")

    class _SecondBatchBreaksTheParser(_CuratorProvider):
        async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
            records = json.loads(user_prompt[user_prompt.index("{") :])
            reason = "llm" if len(records) == 80 else "boom"
            return json.dumps({name: {"decision": "keep", "reason": reason} for name in records})

    GlossaryCurator().curate(registry, _SecondBatchBreaksTheParser([]), _config())

    reasons = [candidate.curation_reason for candidate in registry.values()]
    assert reasons == ["llm"] * 80 + [""]
