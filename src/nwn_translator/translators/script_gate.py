"""Decision on which string literals of compiled scripts may be translated.

A script literal is patched only when it is player-visible text. Hard vetoes
(code identifiers, sentence fragments, …) always reject. Every other candidate
needs an explicit approval from the model gate; with the gate disabled, only
literals that the bytecode proves to be displayed are approved. Every decision is
recorded in the NCS diagnostics.
"""

import asyncio
import logging
from typing import Any, Dict, List, Sequence

from ..ai_providers import TranslationProvider
from ..async_utils import run_async
from ..config import TranslationConfig
from ..extractors.base import Occurrence, TranslatableItem
from ..extractors.ncs_extractor import ncs_hard_veto_reason
from ..translation_logging import TranslationLogWriter, logged_model_call
from .ncs_diagnostics import NcsDiagnostics
from .work_plan import is_ncs_item

logger = logging.getLogger(__name__)

#: Candidates per gate request. Each carries a source excerpt of up to ~2000
#: characters, so 20 stay well inside the context window; the provider splits
#: further when an answer does not parse.
GATE_CHUNK_SIZE = 20

#: Neighbouring approved strings quoted on each side as script context.
_NEIGHBORS = 3
#: Characters quoted per neighbouring string.
_NEIGHBOR_CHARS = 600
#: Characters quoted from the matching script source.
_SNIPPET_CHARS = 2000

_MISSING_VERDICT = {"translate": False, "reason": "gate_missing_key"}
_UNAVAILABLE_VERDICT = {"translate": False, "reason": "gate_unavailable"}


class ScriptGate:
    """Approves or rejects the string literals of compiled scripts.

    Attributes:
        config: Run settings (source language, gate switch, concurrency).
        provider: Model provider that classifies the candidates.
        log_writer: Translation log of the run.
        diagnostics: Recorder of the decisions.
    """

    def __init__(
        self,
        config: TranslationConfig,
        provider: TranslationProvider,
        log_writer: TranslationLogWriter,
        diagnostics: NcsDiagnostics,
    ):
        """Creates a gate for one run.

        Args:
            config: Run settings (source language, gate switch, concurrency).
            provider: Model provider that classifies the candidates.
            log_writer: Translation log of the run.
            diagnostics: Recorder of the decisions.
        """
        self.config = config
        self.provider = provider
        self.log_writer = log_writer
        self.diagnostics = diagnostics

    def decide(self, items: Sequence[TranslatableItem]) -> Dict[Occurrence, bool]:
        """Decides every script literal among *items*.

        Args:
            items: Items of any kind; only script literals are decided.

        Returns:
            Approval per script literal occurrence.
        """
        approvals: Dict[Occurrence, bool] = {}
        pending: List[TranslatableItem] = []
        skip_gate = self.config.skip_ncs_llm_gate
        for item in items:
            if not is_ncs_item(item):
                continue
            meta = item.metadata
            veto = ncs_hard_veto_reason(
                item.text,
                proven_player=bool(meta.get("proven_player")),
                is_concat=bool(meta.get("concat_parts")),
                player_candidate=bool(meta.get("player_candidate")) and not skip_gate,
            )
            if veto:
                approvals[item.key] = False
                self.diagnostics.record(item, reason=veto, count_field="skipped_hard_veto")
            elif skip_gate:
                approved = meta.get("proven_player") is True
                approvals[item.key] = approved
                self.diagnostics.record(
                    item,
                    reason="gate_bypassed_proven" if approved else "gate_disabled_unproven",
                    count_field="approved" if approved else "skipped_fail_closed",
                )
            else:
                pending.append(item)
        if not pending:
            return approvals

        # No overall timeout: a large module may need many minutes to gate;
        # per-call timeouts and retries live in the provider.
        verdicts = run_async(self._ask_all(pending), timeout=None)
        for item, cell in zip(pending, verdicts):
            approved = cell.get("translate") is True
            approvals[item.key] = approved
            outcome = "approved" if approved else "rejected"
            self.diagnostics.record(
                item,
                reason=f"gate_{outcome}:{cell.get('reason', 'unspecified')}",
                count_field="approved" if approved else "skipped_fail_closed",
            )
        return approvals

    async def _ask_all(self, pending: List[TranslatableItem]) -> List[Dict[str, Any]]:
        """Asks the model about *pending* in concurrent chunks; one verdict per item.

        A chunk whose request fails rejects only its own candidates.
        """
        sem = asyncio.Semaphore(max(1, int(self.config.max_concurrent_requests)))

        async def ask(chunk: List[TranslatableItem]) -> List[Dict[str, Any]]:
            """Sends one gate request; a failed request rejects the whole chunk."""
            entries = [_gate_entry(str(index), item) for index, item in enumerate(chunk)]
            async with sem:
                try:
                    result = await logged_model_call(
                        self.log_writer,
                        self.provider.classify_ncs_translate_gate_batch_async,
                        trace_context={"occurrences": [item.key for item in chunk]},
                        entries=entries,
                        source_lang=self.config.source_lang,
                    )
                    return [result.get(str(i), _MISSING_VERDICT) for i in range(len(chunk))]
                except Exception as exc:
                    logger.warning(
                        "NCS LLM gate failed for %d item(s): %s — defaulting to reject.",
                        len(chunk),
                        exc,
                    )
                    return [_UNAVAILABLE_VERDICT] * len(chunk)

        chunks = [
            pending[start : start + GATE_CHUNK_SIZE]
            for start in range(0, len(pending), GATE_CHUNK_SIZE)
        ]
        answers = await asyncio.gather(*(ask(chunk) for chunk in chunks))
        return [cell for answer in answers for cell in answer]


def _gate_entry(key: str, item: TranslatableItem) -> Dict[str, Any]:
    """Returns the gate request entry of one candidate."""
    meta = item.metadata
    return {
        "key": key,
        "text": item.text,
        "file": item.key[0],
        "offset": meta.get("offset"),
        "hint": meta.get("ncs_hint", ""),
        "nss_snippet": meta.get("nss_snippet"),
        "nss_start": meta.get("nss_start"),
        "bytecode_context": meta.get("bytecode_context"),
        "confidence": meta.get("confidence"),
    }


def add_script_context(approved: Sequence[TranslatableItem]) -> None:
    """Gives approved script strings the context of their script.

    Each string gets its source excerpt and the neighbouring approved strings of
    the same script (by bytecode offset) as prompt context, and joins its script's
    batch group (``translation_group`` within its ``batch_resource``). Gate
    decisions are not affected. Items are updated in place.

    Args:
        approved: Approved script strings.
    """
    scripts: Dict[str, List[TranslatableItem]] = {}
    for item in approved:
        scripts.setdefault(item.key[0], []).append(item)
    for script_items in scripts.values():
        ordered = sorted(script_items, key=lambda item: item.metadata.get("offset", 0))
        for index, item in enumerate(ordered):
            item.metadata = {
                **item.metadata,
                "translation_group": "script",
                "batch_resource": item.key[0],
                "batch_context": item.context or "",
            }
            context = [item.context or ""]
            snippet = item.metadata.get("nss_snippet")
            if snippet:
                context.append(
                    "Matching source excerpt (context only):\n" + snippet[:_SNIPPET_CHARS]
                )
            neighbors = (
                ordered[max(0, index - _NEIGHBORS) : index]
                + ordered[index + 1 : index + 1 + _NEIGHBORS]
            )
            item.metadata["approved_neighbors"] = [
                other.text[:_NEIGHBOR_CHARS] for other in neighbors
            ]
            if neighbors:
                context.append(
                    "Other approved speech in this script (constant order, not proven "
                    "execution order; context only, do not translate these as extra outputs):\n"
                    + "\n".join(other.text[:_NEIGHBOR_CHARS] for other in neighbors)
                )
            item.context = "\n".join(context)
