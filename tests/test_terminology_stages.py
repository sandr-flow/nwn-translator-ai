"""Pipeline glue of the terminology stages: candidates, curation, glossary and trace."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

from nwn_translator.config import TranslationConfig
from nwn_translator.context.world_context import WorldContext
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.pipeline.stages import (
    PipelineState,
    stage_build_glossary,
    stage_collect_entities,
)


class _Log:
    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    def write(self, entry: Dict[str, Any]) -> None:
        self.entries.append(entry)


class _Provider:
    """Scripted replies of the three terminology stages."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        if user_prompt.startswith("Extract proper nouns"):
            self.calls.append("entities")
            return json.dumps({"entities": [{"name": "Stout Village", "type": "location"}]})
        self.calls.append("curation")
        return json.dumps({"Gewia": {"decision": "keep", "reason": "speaker", "priority": 1}})

    async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
        self.calls.append("glossary")
        return json.dumps({key: key.upper() for key in kwargs["glossary_keys"]})


def _state(provider: Any, log: _Log) -> PipelineState:
    config = TranslationConfig(
        api_key="test-key",
        target_lang="russian",
        use_context=True,
        max_concurrent_requests=1,
        translation_log_writer=log,
        quiet=True,
    )
    state = PipelineState(config=config, provider=provider)
    state.world_context = WorldContext()
    return state


def test_stages_record_candidates_metrics_and_terminology_trace() -> None:
    log = _Log()
    provider = _Provider()
    state = _state(provider, log)
    content = ExtractedContent(
        content_type="dialog",
        source_file=Path("gewia.dlg"),
        items=[
            TranslatableItem(
                "Leading a coach to Stout Village with farming equipment.",
                metadata={"type": "entry", "speaker": "Gewia"},
            )
        ],
    )

    stage_collect_entities(state, {Path("gewia.dlg"): ({}, content, ".dlg")})
    stage_build_glossary(state)

    assert provider.calls == ["entities", "curation", "glossary"]
    assert state.metrics_recorder.summary()["counters"] == {
        "entity_candidates.raw": 2,
        "entity_candidates.keep": 2,
    }
    assert state.glossary is not None
    assert state.glossary.entries == {"Gewia": "GEWIA", "Stout Village": "STOUT VILLAGE"}
    trace = log.entries[-1]
    assert list(trace) == ["event", "entries", "aliases", "candidates"]
    assert trace["event"] == "terminology_resolved"
    assert trace["aliases"] == {}
    assert trace["candidates"] == [
        {"name": "Gewia", "decision": "keep", "reason": "speaker", "alias_of": None},
        {"name": "Stout Village", "decision": "keep", "reason": "", "alias_of": None},
    ]


def test_glossary_overall_timeout_keeps_the_run_going(monkeypatch) -> None:
    import nwn_translator.glossary_builder as builder

    monkeypatch.setattr(builder, "_STAGE", replace(builder._STAGE, run_timeout_per_batch=0.05))

    class _Stalled(_Provider):
        async def complete_glossary_chat_async(self, system_prompt, user_prompt, **kwargs):
            await asyncio.sleep(5)
            return "{}"

    log = _Log()
    state = _state(_Stalled(), log)
    assert state.world_context is not None
    state.world_context.extracted_names = [("Perin", "character")]

    stage_build_glossary(state)

    assert state.glossary is not None
    assert state.glossary.entries == {}
    assert log.entries[-1]["event"] == "terminology_resolved"
