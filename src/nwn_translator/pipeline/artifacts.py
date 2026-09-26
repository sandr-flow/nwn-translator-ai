"""Serialize/deserialize the data passed between pipeline stages.

Each pipeline seam (extracted items, world context, entity candidates,
glossary, translations) has a ``dump_*``/``load_*`` pair so a stage can be run
from a saved input or its output inspected and hand-edited.  The only seam that
is *not* serialized is the parsed GFF (binary ``_record_offsets``); it is
rebuilt from ``extract_dir`` by :func:`nwn_translator.main.rebuild_module`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context.entity_candidates import (
    EntityCandidate,
    EntityCandidateRegistry,
    EntityEvidence,
)
from ..context.world_context import NPCInfo, WorldContext
from ..extractors.base import ExtractedContent, TranslatableItem, Translations
from ..glossary import Glossary


def write_json(path: Path, data: Any, *, sort_keys: bool = False) -> None:
    """Write *data* as indented UTF-8 JSON, creating the parent directory.

    Args:
        path: Target file.
        data: JSON-serializable value.
        sort_keys: Sort object keys.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── extracted items (JSONL) ─────────────────────────────────────────────


def dump_items(path: Path, contents: List[ExtractedContent]) -> None:
    """Write extracted content as ``items.jsonl``, one row per translatable item.

    Args:
        path: Target file.
        contents: Extracted files, in pipeline order.
    """
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
    """Read ``items.jsonl`` written by :func:`dump_items`.

    Args:
        path: The artifact.

    Returns:
        One :class:`ExtractedContent` per source file, in file order.
    """
    groups: Dict[str, Dict[str, Any]] = {}
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
    """Serialize the world context registry (candidates are dumped separately).

    Args:
        world_context: Scanned module objects, or None.

    Returns:
        JSON-ready registries (empty for None). Script owners keep their order:
        it decides the speaker hint of a script.
    """
    if world_context is None:
        return {}
    # An actor is indexed by Conversation and by tag; store each once.
    actors = {
        tuple(asdict(actor).values()): asdict(actor)
        for index in (
            world_context.dialog_actors_by_conversation,
            world_context.dialog_actors_by_tag,
        )
        for registered in index.values()
        for actor in registered
    }
    return {
        "npcs": {tag: asdict(npc) for tag, npc in sorted(world_context.npcs.items())},
        "areas": dict(sorted(world_context.areas.items())),
        "quests": dict(sorted(world_context.quests.items())),
        "items": dict(sorted(world_context.items.items())),
        "extracted_names": list(world_context.extracted_names),
        "dialog_actors": [actors[key] for key in sorted(actors)],
        "script_owners": {
            script: [asdict(owner) for owner in owners]
            for script, owners in sorted(world_context.script_owners.items())
        },
    }


def dump_world_context(path: Path, world_context: Optional[WorldContext]) -> None:
    """Write ``world_context.json`` (see :func:`world_context_to_dict`).

    Args:
        path: Target file.
        world_context: Scanned module objects, or None.
    """
    write_json(Path(path), world_context_to_dict(world_context))


def load_world_context(path: Path) -> WorldContext:
    """Read ``world_context.json`` back into a :class:`WorldContext`.

    Candidates are not part of this artifact; attach them separately via
    :func:`load_candidates` when needed. Registries missing from older
    artifacts (dialog actors, script owners) load empty.

    Args:
        path: ``world_context.json``.

    Returns:
        The world context without candidates.
    """
    data = _read_json(path)
    wc = WorldContext()
    for tag, npc in data.get("npcs", {}).items():
        wc.npcs[tag] = NPCInfo(**npc)
    wc.areas = dict(data.get("areas", {}))
    wc.quests = dict(data.get("quests", {}))
    wc.items = dict(data.get("items", {}))
    wc.extracted_names = [tuple(pair) for pair in data.get("extracted_names", [])]
    for actor in data.get("dialog_actors", []):
        wc.register_dialog_actor(NPCInfo(**actor))
    wc.script_owners = {
        script: [NPCInfo(**owner) for owner in owners]
        for script, owners in data.get("script_owners", {}).items()
    }
    return wc


# ── entity candidates ───────────────────────────────────────────────────


def candidate_to_dict(candidate: EntityCandidate) -> Dict[str, Any]:
    """Serialize an entity candidate with its curation and evidence.

    Args:
        candidate: The candidate.

    Returns:
        JSON-ready fields of the candidate.
    """
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
    """Write ``candidates.json``.

    Args:
        path: Target file.
        registry: Candidates, or None for an empty list.
    """
    values = registry.values() if registry is not None else []
    write_json(Path(path), [candidate_to_dict(c) for c in values])


def load_candidates(path: Path) -> EntityCandidateRegistry:
    """Read ``candidates.json`` back into a registry.

    The curated fields (decision, priority, score) are restored exactly, not
    recomputed from the evidence.

    Args:
        path: The artifact.

    Returns:
        The candidate registry.
    """
    registry = EntityCandidateRegistry()
    registry.restore(
        EntityCandidate(
            name=row["name"],
            normalized_name=row["normalized_name"],
            category=row.get("category", "unknown"),
            frequency=row.get("frequency", 0),
            contexts=list(row.get("contexts", [])),
            evidence=[EntityEvidence(**ev) for ev in row.get("evidence", [])],
            is_speaker_or_dialog_actor=row.get("is_speaker_or_dialog_actor", False),
            technical_score=row.get("technical_score", 0),
            priority=row.get("priority", 0),
            curation_decision=row.get("curation_decision", "keep"),
            curation_reason=row.get("curation_reason", ""),
            alias_of=row.get("alias_of"),
        )
        for row in _read_json(path)
    )
    return registry


# ── glossary ────────────────────────────────────────────────────────────


def dump_glossary(path: Path, glossary: Optional[Glossary]) -> None:
    """Write ``glossary.json``: version 2 with entries and aliases, keys sorted.

    Args:
        path: Target file.
        glossary: The glossary, or None for an empty one.
    """
    entries = glossary.entries if glossary is not None else {}
    write_json(
        Path(path),
        {"version": 2, "entries": entries, "aliases": glossary.aliases if glossary else {}},
        sort_keys=True,
    )


def load_glossary(path: Path) -> Glossary:
    """Read ``glossary.json``; a file without a version holds only the entries.

    Args:
        path: The artifact.

    Returns:
        The glossary.
    """
    data = _read_json(path)
    if data.get("version") == 2:
        return Glossary(entries=data["entries"], aliases=data["aliases"])
    return Glossary(entries=data)


# ── translations ────────────────────────────────────────────────────────


def dump_translations(path: Path, translations: Translations) -> None:
    """Write ``translations.json``: one row per occurrence, sorted by address.

    Args:
        path: Target file.
        translations: Translation per occurrence.
    """
    write_json(
        path,
        {
            "version": 2,
            "items": [
                {"file": resource, "item_id": item_id, "translated": text}
                for (resource, item_id), text in sorted(translations.items())
            ],
        },
    )


def load_translations(path: Path) -> Translations:
    """Read ``translations.json`` written by :func:`dump_translations`.

    Args:
        path: The artifact.

    Returns:
        Translation per occurrence.

    Raises:
        ValueError: For a text-only artifact of an older version (it cannot
            address occurrences) or an occurrence listed twice.
    """
    data = _read_json(path)
    if data.get("version") != 2:
        raise ValueError("Text-only translation artifacts are ambiguous; rerun the translate stage")
    result: Translations = {}
    for row in data["items"]:
        key = (row["file"], row["item_id"])
        if key in result:
            raise ValueError(f"Duplicate translation occurrence: {key}")
        result[key] = row["translated"]
    return result
