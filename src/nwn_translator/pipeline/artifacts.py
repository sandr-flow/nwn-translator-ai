"""Serialization of the data passed between pipeline stages.

Each seam (extracted items, world context, entity candidates, glossary,
translations) has a ``dump_*``/``load_*`` pair, so a stage can run from a saved
input and its output be inspected or hand-edited. Parsed resources are not
serialized: the stage runner and :func:`nwn_translator.main.rebuild_module` parse
them again from ``extract_dir``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
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

#: Stored fields of a candidate that are :class:`EntityCandidate` fields.
_CANDIDATE_FIELDS = frozenset(f.name for f in fields(EntityCandidate)) - {"evidence"}


def write_json(path: Path, data: Any, *, sort_keys: bool = False) -> None:
    """Writes *data* as indented UTF-8 JSON, creating the parent directory.

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
    """Reads a UTF-8 JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_items(path: Path, contents: List[ExtractedContent]) -> None:
    """Writes ``items.jsonl``, one row per translatable item.

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
    """Reads ``items.jsonl`` written by :func:`dump_items`.

    Args:
        path: The artifact.

    Returns:
        One :class:`ExtractedContent` per source file, in file order.
    """
    contents: Dict[str, ExtractedContent] = {}
    # JSON leaves U+2028 and U+0085 unescaped, and splitlines() would split on them.
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        key = row.get("source_file", "")
        if key not in contents:
            contents[key] = ExtractedContent(
                content_type=row.get("content_type", ""),
                items=[],
                source_file=Path(key),
                metadata=row.get("content_metadata", {}),
            )
        contents[key].items.append(
            TranslatableItem(
                text=row.get("text", ""),
                context=row.get("context"),
                item_id=row.get("item_id"),
                location=row.get("location"),
                metadata=row.get("metadata") or {},
            )
        )
    return list(contents.values())


def dump_world_context(path: Path, world_context: Optional[WorldContext]) -> None:
    """Writes ``world_context.json`` without the candidates (empty for ``None``).

    Script owners keep their order: it decides the speaker hint of a script.

    Args:
        path: Target file.
        world_context: Scanned module objects, or ``None``.
    """
    data: Dict[str, Any] = {}
    if world_context is not None:
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
        data = {
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
    write_json(Path(path), data)


def load_world_context(path: Path) -> WorldContext:
    """Reads ``world_context.json``; registries missing from the file load empty.

    Args:
        path: ``world_context.json``.

    Returns:
        The world context without candidates (see :func:`load_candidates`).
    """
    data = _read_json(path)
    wc = WorldContext()
    wc.npcs = {tag: NPCInfo(**npc) for tag, npc in data.get("npcs", {}).items()}
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


def _candidate_to_dict(candidate: EntityCandidate) -> Dict[str, Any]:
    """Serializes an entity candidate with its curation and evidence."""
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
    """Writes ``candidates.json`` (an empty list for ``None``).

    Args:
        path: Target file.
        registry: Candidates, or ``None``.
    """
    values = registry.values() if registry is not None else []
    write_json(Path(path), [_candidate_to_dict(c) for c in values])


def load_candidates(path: Path) -> EntityCandidateRegistry:
    """Reads ``candidates.json``, restoring the curated fields as stored.

    Args:
        path: The artifact.

    Returns:
        The candidate registry.
    """
    registry = EntityCandidateRegistry()
    registry.restore(
        EntityCandidate(
            **{key: value for key, value in row.items() if key in _CANDIDATE_FIELDS},
            evidence=[EntityEvidence(**ev) for ev in row.get("evidence", [])],
        )
        for row in _read_json(path)
    )
    return registry


def dump_glossary(path: Path, glossary: Optional[Glossary]) -> None:
    """Writes ``glossary.json``: version 2 with entries and aliases, keys sorted.

    Args:
        path: Target file.
        glossary: The glossary, or ``None`` for an empty one.
    """
    glossary = glossary or Glossary()
    data = {"version": 2, "entries": glossary.entries, "aliases": glossary.aliases}
    write_json(Path(path), data, sort_keys=True)


def load_glossary(path: Path) -> Glossary:
    """Reads ``glossary.json``; a file without ``version`` is a bare entry map.

    Args:
        path: The artifact.

    Returns:
        The glossary.
    """
    data = _read_json(path)
    if data.get("version") == 2:
        return Glossary(entries=data["entries"], aliases=data["aliases"])
    return Glossary(entries=data)


def dump_translations(path: Path, translations: Translations) -> None:
    """Writes ``translations.json``: one row per occurrence, sorted by address.

    Args:
        path: Target file.
        translations: Translation per occurrence.
    """
    rows = [
        {"file": resource, "item_id": item_id, "translated": text}
        for (resource, item_id), text in sorted(translations.items())
    ]
    write_json(path, {"version": 2, "items": rows})


def load_translations(path: Path) -> Translations:
    """Reads ``translations.json`` written by :func:`dump_translations`.

    Args:
        path: The artifact.

    Returns:
        Translation per occurrence.

    Raises:
        ValueError: If the file is not a version-2 artifact (a text-only map
            cannot address occurrences) or lists an occurrence twice.
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
