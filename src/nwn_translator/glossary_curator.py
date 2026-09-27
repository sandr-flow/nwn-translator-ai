"""Curation of entity candidates before glossary translation.

A deterministic pass drops technical labels and demotes generic ones; the model
then reviews the candidates whose status the rules cannot settle.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set

from .config import GLOSSARY_LLM_TIMEOUT, ProgressCallback
from .context.entity_candidates import EntityCandidate, EntityCandidateRegistry
from .context.string_filters import classify_entity_candidate
from .json_utils import load_brace_span
from .llm_batches import LlmStage, chunks, json_request
from .prompts.terminology import build_curator_system_prompt, build_curator_user_prompt

if TYPE_CHECKING:
    from .ai_providers.base import TranslationProvider
    from .config import TranslationConfig
    from .llm_batches import Slot

logger = logging.getLogger(__name__)

_VALID_DECISIONS = frozenset({"keep", "local_only", "drop", "alias_of"})
#: Categories whose candidates the rules leave to the model (unless the filter drops them).
_UNCERTAIN_CATEGORIES = frozenset({"unknown", "term", "faction", "organization"})

#: A retry asks only for the keys the first reply left out; a failed request ends
#: the batch. A batch keeps its concurrency slot for its retry.
_STAGE = LlmStage(
    phase="glossary_curation",
    label="Glossary curation",
    batch_size=80,
    run_timeout_per_batch=GLOSSARY_LLM_TIMEOUT,
    max_attempts=2,
    slot_per_batch=True,
)


class GlossaryCurator:
    """Curator of the entity candidates, so glossary building starts from a cleaner set."""

    def curate(
        self,
        registry: EntityCandidateRegistry,
        provider: "TranslationProvider",
        config: "TranslationConfig",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> EntityCandidateRegistry:
        """Applies the deterministic decisions, then the model's, to *registry* in place.

        Args:
            registry: Candidates to curate.
            provider: Model provider.
            config: Run configuration (target language, concurrency).
            progress_callback: Optional progress reporter.

        Returns:
            *registry*. The candidates of a batch that raised, or was unfinished when
            the overall budget ran out, keep their deterministic decisions.
        """
        for candidate in registry.values():
            result = classify_entity_candidate(candidate.name, candidate.category)
            candidate.technical_score = result.technical_score
            if result.decision == "drop":
                candidate.curation_decision = "drop"
                candidate.curation_reason = result.reason
            elif result.decision == "deprioritize" and candidate.curation_decision != "drop":
                candidate.curation_decision = "local_only"
                candidate.curation_reason = result.reason

        llm_candidates = [
            c for c in registry.values() if c.curation_decision != "drop" and _needs_llm_curation(c)
        ]
        if not llm_candidates:
            return registry
        batches = chunks(llm_candidates, _STAGE.batch_size)
        system_prompt = build_curator_system_prompt(config.target_lang)

        async def curate_batch(
            slot: "Slot", number: int, batch: List[EntityCandidate]
        ) -> Dict[str, Dict[str, Any]]:
            """Curates one batch; names the model leaves out keep their decision."""
            # The batch holds its slot here, so the progress names the batch being curated.
            if progress_callback:
                progress_callback(
                    "scanning",
                    number - 1,
                    len(batches),
                    f"Curating glossary candidates {number}/{len(batches)}",
                )
            by_name = {candidate.name: candidate for candidate in batch}
            # Built exactly like this on purpose: the set's iteration order decides
            # the order of the answers and of the missing-key fallback.
            remaining: Set[str] = set({candidate.name for candidate in batch})

            def prepare(
                keys: List[str], _accepted: object, _attempt: int
            ) -> Callable[[], Awaitable[str]]:
                """Builds the curation request of the candidates *keys*."""
                records = {name: by_name[name].to_curator_record() for name in keys}
                user_prompt = build_curator_user_prompt(records)
                return functools.partial(json_request, provider, system_prompt, user_prompt)

            decisions = await _STAGE.fill_keys(
                slot,
                remaining,
                prepare,
                _parse_curator_json,
                name=f"Glossary curation batch {number}/{len(batches)}",
            )
            for missing in remaining:
                decisions[missing] = {
                    "decision": by_name[missing].curation_decision or "keep",
                    "reason": "curator_missing_key",
                    "priority": by_name[missing].priority,
                }
            return decisions

        results = _STAGE.run(batches, curate_batch, concurrency=config.max_concurrent_requests)
        for batch_result in results:
            if isinstance(batch_result, BaseException):
                logger.warning("Glossary curation batch failed: %s", batch_result)
                continue
            for name, decision in batch_result.items():
                registry.mark_curated(
                    name,
                    decision=str(decision.get("decision", "keep")),
                    reason=str(decision.get("reason", "")),
                    priority=_optional_int(decision.get("priority")),
                    alias_of=_optional_str(decision.get("alias_of")),
                )
        return registry


def _needs_llm_curation(candidate: EntityCandidate) -> bool:
    """Tells whether the rules leave *candidate*'s glossary status to the model."""
    return (
        candidate.is_speaker_or_dialog_actor
        or candidate.frequency > 1
        or candidate.curation_decision == "local_only"
        or candidate.category in _UNCERTAIN_CATEGORIES
    )


def _parse_curator_json(raw: str, expected_keys: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Parses a curator reply into decisions for the expected candidate names.

    Keys match exactly, else case-insensitively; values with an unknown decision
    are skipped.

    Args:
        raw: Model reply, decoded with :func:`~nwn_translator.json_utils.load_brace_span`.
        expected_keys: Candidate names still awaiting a decision.

    Returns:
        Candidate name -> ``decision``, ``reason``, ``priority`` and ``alias_of``, in
        reply order; empty when the reply does not decode.
    """
    try:
        data = load_brace_span(raw) if raw and raw.strip() else None
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    folded = {key.casefold(): key for key in expected_keys}
    for raw_key, value in data.items():
        key = raw_key if raw_key in expected_keys else folded.get(str(raw_key).casefold())
        if key is None or not isinstance(value, dict):
            continue
        decision = str(value.get("decision", "keep")).strip().lower()
        if decision in _VALID_DECISIONS:
            out[key] = {
                "decision": decision,
                "reason": str(value.get("reason", "")),
                "priority": _optional_int(value.get("priority")) or 0,
                "alias_of": _optional_str(value.get("alias_of")),
            }
    return out


def _optional_int(value: Any) -> Optional[int]:
    """Returns *value* as ``int``, or ``None`` when it does not convert.

    ``json.loads`` reads ``Infinity`` and ``NaN``, which ``int`` rejects.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    """Returns *value* as a stripped non-empty string, or ``None``."""
    text = "" if value is None else str(value).strip()
    return text or None
