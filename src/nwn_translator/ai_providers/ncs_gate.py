"""NCS safety gate: request payload, strict verdict parsing and bisection recovery."""

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..config import NCS_GATE_MAX_TOKENS
from ..json_utils import strip_json_markdown_fences
from ..prompts.ncs_gate import build_gate_user_prompt
from .batch_payload import source_windows

logger = logging.getLogger(__name__)

#: ``{"translate": bool, "reason": str}``
Verdict = Dict[str, Any]

#: Sends one gate request: ``(user_prompt, max_tokens, batch_size) -> raw reply``.
GateRequest = Callable[[str, int, int], Awaitable[str]]


def gate_user_prompt(entries: List[Dict[str, Any]], source_lang: str) -> str:
    """Build the user message that asks for a verdict per entry.

    Excerpts of one script with a known position (``nss_start``) are merged into
    shared source windows; other excerpts stay inline as ``nss_snippet``.

    Args:
        entries: Candidates with ``key``, ``text`` and optional ``file``, ``offset``,
            ``hint``, ``nss_snippet``, ``nss_start``, ``bytecode_context`` and
            ``confidence``.
        source_lang: Source language label.

    Returns:
        The user message.
    """
    cells: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        cell: Dict[str, Any] = {
            "text": entry.get("text", ""),
            "file": str(entry.get("file", "")),
            "offset": str(entry.get("offset", "")),
            "hint": str(entry.get("hint", "")),
        }
        for name in ("nss_snippet", "bytecode_context", "confidence"):
            if entry.get(name):
                cell[name] = entry[name]
        cells[str(entry["key"])] = cell

    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("nss_snippet") and isinstance(entry.get("nss_start"), int):
            by_file.setdefault(str(entry.get("file", "")), []).append(entry)
    sources: Dict[str, List[Dict[str, Any]]] = {}
    for filename, file_entries in by_file.items():
        sources[filename], refs = source_windows(file_entries)
        for entry, ref in zip(file_entries, refs):
            cell = cells[str(entry["key"])]
            cell.pop("nss_snippet", None)
            cell["source_window"] = ref
    return build_gate_user_prompt(source_lang, cells, sources)


def parse_gate_verdicts(raw: str, entries: List[Dict[str, Any]]) -> Dict[str, Verdict]:
    """Parse the gate reply into one verdict per entry.

    Parsing is strict on purpose: text around the JSON object is rejected, and
    only a JSON ``true`` approves a string. Entries without a verdict object are
    rejected with reason ``missing_verdict``.

    Args:
        raw: Model reply, optionally wrapped in a markdown fence.
        entries: The requested entries (their ``key`` values are looked up).

    Returns:
        ``key -> {"translate": bool, "reason": str}`` for every entry.

    Raises:
        json.JSONDecodeError: When the reply is not exactly one JSON object.
    """
    cleaned = strip_json_markdown_fences(raw)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("NCS gate response must be an object", cleaned, 0)
    verdicts: Dict[str, Verdict] = {}
    for entry in entries:
        key = str(entry["key"])
        cell = parsed.get(key)
        if isinstance(cell, dict):
            verdicts[key] = {
                "translate": cell.get("translate") is True,
                "reason": str(cell.get("reason", "")) or "unspecified",
            }
        else:
            verdicts[key] = {"translate": False, "reason": "missing_verdict"}
    return verdicts


async def classify_with_recovery(
    request: GateRequest,
    entries: List[Dict[str, Any]],
    source_lang: str,
) -> Dict[str, Verdict]:
    """Ask the gate for verdicts, recovering from replies that do not parse.

    Each batch gets one attempt per budget of :data:`~nwn_translator.config.NCS_GATE_MAX_TOKENS`.
    If none parses, the batch is split in halves (left first), each re-keyed from
    ``"0"`` and classified recursively; a single entry that still fails is rejected
    with reason ``gate_parse_failed``.

    Args:
        request: Sends one gate request.
        entries: Candidates with unique ``key`` values.
        source_lang: Source language label.

    Returns:
        ``key -> {"translate": bool, "reason": str}`` for every entry.
    """
    if not entries:
        return {}
    user_prompt = gate_user_prompt(entries, source_lang)
    error: Optional[json.JSONDecodeError] = None
    for attempt, max_tokens in enumerate(NCS_GATE_MAX_TOKENS, start=1):
        try:
            return parse_gate_verdicts(
                await request(user_prompt, max_tokens, len(entries)), entries
            )
        except json.JSONDecodeError as err:
            error = err
            logger.warning(
                "NCS gate JSON parse failed (attempt %d/%d, %d entries): %s",
                attempt,
                len(NCS_GATE_MAX_TOKENS),
                len(entries),
                err,
            )

    if len(entries) == 1:
        logger.warning("NCS gate giving up on batch; defaulting to translate=false: %s", error)
        return {str(entries[0]["key"]): {"translate": False, "reason": "gate_parse_failed"}}

    mid = len(entries) // 2
    verdicts: Dict[str, Verdict] = {}
    for half in (entries[:mid], entries[mid:]):
        rekeyed = [{**entry, "key": str(i)} for i, entry in enumerate(half)]
        found = await classify_with_recovery(request, rekeyed, source_lang)
        for i, entry in enumerate(half):
            verdicts[str(entry["key"])] = found[str(i)]
    return verdicts
