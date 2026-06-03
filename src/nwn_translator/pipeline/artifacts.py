"""Serialize/deserialize the data passed between pipeline stages.

Each pipeline seam (extracted items, world context, entity candidates,
glossary, translations) has a ``dump_*``/``load_*`` pair so a stage can be run
from a saved input or its output inspected and hand-edited.  The only seam that
is *not* serialized is the parsed GFF (binary ``_record_offsets``); it is
rebuilt from ``extract_dir`` by :func:`nwn_translator.main.rebuild_module`.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context.entity_candidates import (
    EntityCandidate,
    EntityCandidateRegistry,
    EntityEvidence,
)
from ..context.world_context import NPCInfo, WorldContext
from ..extractors.base import ExtractedContent, TranslatableItem
from ..glossary import Glossary


def _write_json(path: Path, data: Any, *, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── extracted items (JSONL) ─────────────────────────────────────────────


def dump_items(path: Path, contents: List[ExtractedContent]) -> None:
    """Write extracted content as JSONL (one row per translatable item)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for content in contents:
            source_file = str(content.source_file) if content.source_file else ""
            ext = Path(source_file).suffix.lower() if source_file else ""
            for item in content.items:
                row = {
                    "source_file": source_file,
                    "ext": ext,
                    "content_type": content.content_type,
                    "content_metadata": content.metadata or {},
                    "text": item.text,
                    "context": item.context,
                    "item_id": item.item_id,
                    "location": item.location,
                    "metadata": item.metadata or {},
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_items(path: Path) -> List[ExtractedContent]:
    """Read JSONL written by :func:`dump_items` back into ExtractedContent list."""
    groups: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = row.get("source_file", "")
        group = groups.get(key)
        if group is None:
            group = {
                "content_type": row.get("content_type", ""),
                "metadata": row.get("content_metadata", {}),
                "items": [],
            }
            groups[key] = group
        group["items"].append(
            TranslatableItem(
                text=row.get("text", ""),
                context=row.get("context"),
                item_id=row.get("item_id"),
                location=row.get("location"),
                metadata=row.get("metadata") or {},
            )
        )
    return [
        ExtractedContent(
            content_type=group["content_type"],
            items=group["items"],
            source_file=Path(key) if key else Path(""),
            metadata=group["metadata"],
        )
        for key, group in groups.items()
    ]


# ── world context ───────────────────────────────────────────────────────


def world_context_to_dict(world_context: Optional[WorldContext]) -> Dict[str, Any]:
    """Serialize the world context registry (candidates are dumped separately)."""
    if world_context is None:
        return {}
    return {
        "npcs": {tag: asdict(npc) for tag, npc in sorted(world_context.npcs.items())},
        "areas": dict(sorted(world_context.areas.items())),
        "quests": dict(sorted(world_context.quests.items())),
        "items": dict(sorted(world_context.items.items())),
        "extracted_names": list(world_context.extracted_names),
    }


def dump_world_context(path: Path, world_context: Optional[WorldContext]) -> None:
    """Write ``world_context.json``."""
    _write_json(Path(path), world_context_to_dict(world_context))


def load_world_context(path: Path) -> WorldContext:
    """Read ``world_context.json`` back into a :class:`WorldContext`.

    Candidates are not part of this artifact; attach them separately via
    :func:`load_candidates` when needed.
    """
    data = _read_json(path)
    wc = WorldContext()
    for tag, npc in data.get("npcs", {}).items():
        wc.npcs[tag] = NPCInfo(**npc)
    wc.areas = dict(data.get("areas", {}))
    wc.quests = dict(data.get("quests", {}))
    wc.items = dict(data.get("items", {}))
    wc.extracted_names = [tuple(pair) for pair in data.get("extracted_names", [])]
    return wc


# ── entity candidates ───────────────────────────────────────────────────


def candidate_to_dict(candidate: EntityCandidate) -> Dict[str, Any]:
    """Serialize an :class:`EntityCandidate` to JSON-friendly data."""
    return {
        "name": candidate.name,
        "normalized_name": candidate.normalized_name,
        "category": candidate.category,
        "frequency": candidate.frequency,
        "contexts": list(candidate.contexts),
        "sources": candidate.sources,
        "resources": candidate.resources,
        "is_speaker_or_dialog_actor": candidate.is_speaker_or_dialog_actor,
        "technical_score": candidate.technical_score,
        "priority": candidate.priority,
        "curation_decision": candidate.curation_decision,
        "curation_reason": candidate.curation_reason,
        "alias_of": candidate.alias_of,
        "evidence": [asdict(ev) for ev in candidate.evidence],
    }


def dump_candidates(path: Path, registry: Optional[EntityCandidateRegistry]) -> None:
    """Write ``candidates.json`` from a candidate registry."""
    values = registry.values() if registry is not None else []
    _write_json(Path(path), [candidate_to_dict(c) for c in values])


def load_candidates(path: Path) -> EntityCandidateRegistry:
    """Reconstruct an :class:`EntityCandidateRegistry` from ``candidates.json``.

    Restores exact curated fields (decision/priority/score), which the public
    ``add``/``extend`` API would recompute, so it assigns into the registry's
    backing store directly.
    """
    registry = EntityCandidateRegistry()
    for row in _read_json(path):
        evidence = [EntityEvidence(**ev) for ev in row.get("evidence", [])]
        candidate = EntityCandidate(
            name=row["name"],
            normalized_name=row["normalized_name"],
            category=row.get("category", "unknown"),
            frequency=row.get("frequency", 0),
            contexts=list(row.get("contexts", [])),
            evidence=evidence,
            is_speaker_or_dialog_actor=row.get("is_speaker_or_dialog_actor", False),
            technical_score=row.get("technical_score", 0),
            priority=row.get("priority", 0),
            curation_decision=row.get("curation_decision", "keep"),
            curation_reason=row.get("curation_reason", ""),
            alias_of=row.get("alias_of"),
        )
        registry._items[candidate.normalized_name] = candidate
    return registry


# ── glossary ────────────────────────────────────────────────────────────


def dump_glossary(path: Path, glossary: Optional[Glossary]) -> None:
    """Write ``glossary.json`` (canonical English -> translated entries)."""
    entries = glossary.entries if glossary is not None else {}
    _write_json(Path(path), entries, sort_keys=True)


def load_glossary(path: Path) -> Glossary:
    """Read ``glossary.json`` back into a :class:`Glossary`."""
    return Glossary(entries=_read_json(path))


# ── translations ────────────────────────────────────────────────────────


def dump_translations(path: Path, translations: Dict[str, str]) -> None:
    """Write the original-text -> translated-text map."""
    _write_json(Path(path), translations, sort_keys=True)


def load_translations(path: Path) -> Dict[str, str]:
    """Read the original-text -> translated-text map."""
    return dict(_read_json(path))


def dump_ncs_by_item_id(path: Path, ncs_translations_by_item_id: Dict[str, str]) -> None:
    """Write per-``item_id`` NCS translations (needed for NCS injection)."""
    _write_json(Path(path), ncs_translations_by_item_id, sort_keys=True)


def load_ncs_by_item_id(path: Path) -> Dict[str, str]:
    """Read per-``item_id`` NCS translations."""
    return dict(_read_json(path))
