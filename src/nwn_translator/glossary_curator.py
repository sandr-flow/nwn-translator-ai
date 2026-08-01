"""LLM-assisted curation of entity candidates before glossary translation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

from .config import GLOSSARY_LLM_TIMEOUT, GLOSSARY_MAX_TOKENS, GLOSSARY_TEMPERATURE
from .context.entity_candidates import EntityCandidate, EntityCandidateRegistry
from .context.string_filters import classify_entity_candidate
from .telemetry import llm_phase

logger = logging.getLogger(__name__)

_BATCH_SIZE = 80
_MAX_RETRIES = 1
_VALID_DECISIONS = {"keep", "local_only", "drop", "alias_of"}


class GlossaryCurator:
    """Curate candidates so glossary building starts from a cleaner set."""

    def curate(
        self,
        registry: EntityCandidateRegistry,
        provider: Any,
        config: Any,
        progress_callback=None,
    ) -> EntityCandidateRegistry:
        """Apply deterministic and optional LLM curation in-place."""
        candidates = registry.values()
        if not candidates:
            return registry

        for candidate in candidates:
            result = classify_entity_candidate(
                candidate.name,
                candidate.category,
                source=",".join(candidate.sources),
            )
            candidate.technical_score = result.technical_score
            if result.decision == "drop":
                candidate.curation_decision = "drop"
                candidate.curation_reason = result.reason
            elif result.decision == "deprioritize" and candidate.curation_decision != "drop":
                candidate.curation_decision = "local_only"
                candidate.curation_reason = result.reason

        if not hasattr(provider, "complete_json_chat_async"):
            return registry

        llm_candidates = [
            c for c in registry.values() if c.curation_decision != "drop" and _needs_llm_curation(c)
        ]
        if not llm_candidates:
            return registry

        batches = [
            llm_candidates[i : i + _BATCH_SIZE] for i in range(0, len(llm_candidates), _BATCH_SIZE)
        ]
        from .async_utils import run_async

        async def run_all() -> List[Dict[str, Dict[str, Any]]]:
            sem = asyncio.Semaphore(max(1, int(getattr(config, "max_concurrent_requests", 3))))

            async def run_one(idx: int, batch: List[EntityCandidate]) -> Dict[str, Dict[str, Any]]:
                async with sem:
                    if progress_callback:
                        progress_callback(
                            "scanning",
                            idx,
                            len(batches),
                            f"Curating glossary candidates {idx + 1}/{len(batches)}",
                        )
                    return await self._curate_batch(provider, config, batch)

            gathered: List[Dict[str, Dict[str, Any]]] = await asyncio.gather(
                *[run_one(i, batch) for i, batch in enumerate(batches)]
            )
            return gathered

        try:
            results = run_async(
                run_all(),
                timeout=max(GLOSSARY_LLM_TIMEOUT, GLOSSARY_LLM_TIMEOUT * len(batches)),
            )
        except Exception as exc:
            logger.warning("Glossary curation failed; using deterministic decisions: %s", exc)
            return registry

        for batch_result in results:
            for name, decision in batch_result.items():
                registry.mark_curated(
                    name,
                    decision=str(decision.get("decision", "keep")),
                    reason=str(decision.get("reason", "")),
                    priority=_optional_int(decision.get("priority")),
                    alias_of=_optional_str(decision.get("alias_of")),
                    canonical_name=_optional_str(decision.get("canonical_name")),
                )

        return registry

    async def _curate_batch(
        self,
        provider: Any,
        config: Any,
        candidates: List[EntityCandidate],
    ) -> Dict[str, Dict[str, Any]]:
        expected = {candidate.name for candidate in candidates}
        accepted: Dict[str, Dict[str, Any]] = {}
        remaining: Set[str] = set(expected)
        by_name = {candidate.name: candidate for candidate in candidates}

        for _attempt in range(_MAX_RETRIES + 1):
            if not remaining:
                break
            batch = [by_name[name] for name in sorted(remaining, key=str.lower)]
            prompt = _build_user_prompt(batch)
            try:
                with llm_phase("glossary_curation"):
                    raw = await asyncio.wait_for(
                        provider.complete_json_chat_async(
                            _build_system_prompt(getattr(config, "target_lang", "russian")),
                            prompt,
                            max_tokens=GLOSSARY_MAX_TOKENS,
                            temperature=GLOSSARY_TEMPERATURE,
                            use_reasoning=False,
                        ),
                        timeout=GLOSSARY_LLM_TIMEOUT,
                    )
            except Exception as exc:
                logger.warning("Glossary curation batch failed: %s", exc)
                break
            parsed = _parse_curator_json(raw, remaining)
            accepted.update(parsed)
            remaining -= set(parsed)

        for missing in remaining:
            accepted[missing] = {
                "decision": by_name[missing].curation_decision or "keep",
                "reason": "curator_missing_key",
                "priority": by_name[missing].priority,
            }
        return accepted


def _needs_llm_curation(candidate: EntityCandidate) -> bool:
    if candidate.is_speaker_or_dialog_actor:
        return True
    if candidate.frequency > 1:
        return True
    if candidate.curation_decision == "local_only":
        return True
    return candidate.category in {"unknown", "term", "faction", "organization"}


def _build_system_prompt(target_lang: str) -> str:
    return (
        "You curate proper-name candidates for a Neverwinter Nights translation glossary. "
        f"Target language: {target_lang}. Decide whether each candidate should be a "
        "run-wide glossary entity. Return only JSON. Valid decisions are keep, "
        "local_only, drop, alias_of. Use drop for technical labels, numbered generic "
        "placeables, route labels, and generic creature/person labels. Use local_only "
        "for labels useful only near their resource. Use alias_of for shorter/variant "
        "names of another candidate."
    )


def _build_user_prompt(candidates: List[EntityCandidate]) -> str:
    data = {candidate.name: candidate.to_curator_record() for candidate in candidates}
    return (
        "Curate these candidates. Return a JSON object keyed by candidate name. "
        "Each value must contain decision, reason, priority, and optionally alias_of "
        "or canonical_name.\n\n" + json.dumps(data, ensure_ascii=False, indent=2)
    )


def _parse_curator_json(raw: str, expected_keys: Set[str]) -> Dict[str, Dict[str, Any]]:
    if not raw or not raw.strip():
        return {}
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    folded = {key.casefold(): key for key in expected_keys}
    for raw_key, raw_value in data.items():
        key = raw_key if raw_key in expected_keys else folded.get(str(raw_key).casefold())
        if key is None or not isinstance(raw_value, dict):
            continue
        decision = str(raw_value.get("decision", "keep")).strip().lower()
        if decision not in _VALID_DECISIONS:
            continue
        out[key] = {
            "decision": decision,
            "reason": str(raw_value.get("reason", "")),
            "priority": _optional_int(raw_value.get("priority")) or 0,
            "alias_of": _optional_str(raw_value.get("alias_of")),
            "canonical_name": _optional_str(raw_value.get("canonical_name")),
        }
    return out


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
