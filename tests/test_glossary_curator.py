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
