"""Evidence-backed entity candidates for glossary and prompt context."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..extractors.base import ExtractedContent, TranslatableItem
from .string_filters import classify_entity_candidate

_SPACE_RE = re.compile(r"\s+")

SOURCE_PRIORITY = {
    "dlg_speaker": 95,
    "utc_name": 90,
    "are_name": 85,
    "jrl_category": 80,
    "uti_name": 75,
    "git_instance": 45,
    "entity_extractor": 60,
}

TYPE_TO_CANDIDATE: Dict[str, Tuple[str, str, str]] = {
    "creature_first_name": ("character", "git_instance", "FirstName"),
    "creature_last_name": ("character", "git_instance", "LastName"),
    "area_name": ("location", "are_name", "Name"),
    "journal_category_name": ("quest", "jrl_category", "Name"),
    "item_name": ("item", "uti_name", "LocalizedName"),
    "store_name": ("location", "git_instance", "LocName"),
    "placeable_name": ("unknown", "git_instance", "LocalizedName"),
    "door_name": ("unknown", "git_instance", "LocalizedName"),
    "trigger_name": ("unknown", "git_instance", "LocalizedName"),
    "encounter_name": ("unknown", "git_instance", "LocalizedName"),
    "waypoint_map_note": ("location", "git_instance", "MapNote"),
}


def normalize_entity_name(name: object) -> str:
    """Return a stable casefolded entity key."""
    text = unicodedata.normalize("NFKC", "" if name is None else str(name))
    text = _SPACE_RE.sub(" ", text).strip().casefold()
    return text


@dataclass
class EntityEvidence:
    """One source observation supporting an entity candidate."""

    source: str
    resource: str = ""
    field: str = ""
    category: str = "unknown"
    context: str = ""
    is_speaker_or_dialog_actor: bool = False


@dataclass
class EntityCandidate:
    """A possible named entity backed by one or more evidence records."""

    name: str
    normalized_name: str
    category: str = "unknown"
    frequency: int = 0
    contexts: List[str] = field(default_factory=list)
    evidence: List[EntityEvidence] = field(default_factory=list)
    is_speaker_or_dialog_actor: bool = False
    technical_score: int = 0
    priority: int = 0
    curation_decision: str = "keep"
    curation_reason: str = ""
    alias_of: Optional[str] = None

    def add_evidence(self, evidence: EntityEvidence) -> None:
        """Merge one evidence record into this candidate."""
        self.evidence.append(evidence)
        self.frequency += 1
        self.is_speaker_or_dialog_actor = (
            self.is_speaker_or_dialog_actor or evidence.is_speaker_or_dialog_actor
        )
        if evidence.context:
            snippet = _SPACE_RE.sub(" ", evidence.context).strip()
            if snippet and snippet not in self.contexts and len(self.contexts) < 3:
                self.contexts.append(snippet[:240])
        if self.category == "unknown" and evidence.category != "unknown":
            self.category = evidence.category
        self.priority = max(self.priority, SOURCE_PRIORITY.get(evidence.source, 10))

    @property
    def sources(self) -> List[str]:
        """Return unique evidence source labels."""
        return sorted({ev.source for ev in self.evidence})

    @property
    def resources(self) -> List[str]:
        """Return unique resource labels."""
        return sorted({ev.resource for ev in self.evidence if ev.resource})

    @property
    def eligible_for_glossary(self) -> bool:
        """Whether this candidate may seed the run-wide glossary."""
        if self.curation_decision in {"drop", "local_only"}:
            return False
        if self.alias_of:
            return False
        filter_result = classify_entity_candidate(
            self.name,
            self.category,
            source=",".join(self.sources),
        )
        return filter_result.decision != "drop"

    def to_curator_record(self) -> Dict[str, object]:
        """Return the compact JSON-serializable record sent to the curator."""
        filter_result = classify_entity_candidate(
            self.name,
            self.category,
            source=",".join(self.sources),
        )
        return {
            "name": self.name,
            "category": self.category,
            "sources": self.sources,
            "frequency": self.frequency,
            "contexts": self.contexts,
            "technical_flags": sorted(filter_result.reasons),
            "is_speaker_or_dialog_actor": self.is_speaker_or_dialog_actor,
        }


class EntityCandidateRegistry:
    """Mutable registry that deduplicates candidates by normalized name."""

    def __init__(self) -> None:
        self._items: Dict[str, EntityCandidate] = {}

    def __bool__(self) -> bool:
        return bool(self._items)

    def add(
        self,
        name: object,
        *,
        category: str = "unknown",
        source: str,
        resource: str = "",
        field: str = "",
        context: str = "",
        is_speaker_or_dialog_actor: bool = False,
    ) -> Optional[EntityCandidate]:
        """Add evidence for *name* and return the candidate."""
        clean = _SPACE_RE.sub(" ", "" if name is None else str(name)).strip()
        normalized = normalize_entity_name(clean)
        if not normalized:
            return None

        candidate = self._items.get(normalized)
        if candidate is None:
            filter_result = classify_entity_candidate(clean, category, source=source)
            candidate = EntityCandidate(
                name=clean,
                normalized_name=normalized,
                category=category or "unknown",
                technical_score=filter_result.technical_score,
                curation_decision="drop" if filter_result.decision == "drop" else "keep",
                curation_reason=filter_result.reason,
            )
            self._items[normalized] = candidate
        candidate.add_evidence(
            EntityEvidence(
                source=source,
                resource=resource,
                field=field,
                category=category or "unknown",
                context=context,
                is_speaker_or_dialog_actor=is_speaker_or_dialog_actor,
            )
        )
        return candidate

    def extend(self, candidates: Iterable[EntityCandidate]) -> None:
        """Merge candidates from another source."""
        for candidate in candidates:
            for evidence in candidate.evidence:
                self.add(
                    candidate.name,
                    category=evidence.category or candidate.category,
                    source=evidence.source,
                    resource=evidence.resource,
                    field=evidence.field,
                    context=evidence.context,
                    is_speaker_or_dialog_actor=evidence.is_speaker_or_dialog_actor,
                )

    def values(self) -> List[EntityCandidate]:
        """Return candidates in deterministic order."""
        return [self._items[k] for k in sorted(self._items)]

    def mark_curated(
        self,
        name: str,
        *,
        decision: str,
        reason: str = "",
        priority: Optional[int] = None,
        alias_of: Optional[str] = None,
        canonical_name: Optional[str] = None,
    ) -> None:
        """Apply a curator decision to an existing candidate."""
        key = normalize_entity_name(name)
        candidate = self._items.get(key)
        if candidate is None:
            return
        candidate.curation_decision = decision
        candidate.curation_reason = reason
        if priority is not None:
            candidate.priority = max(candidate.priority, int(priority))
        candidate.alias_of = alias_of or None
        if canonical_name and decision != "alias_of":
            candidate.name = canonical_name

    def glossary_pairs(self) -> List[Tuple[str, str]]:
        """Return ``(name, category)`` pairs eligible for run-wide glossary."""
        out: List[Tuple[str, str]] = []
        for candidate in self.values():
            if candidate.eligible_for_glossary:
                out.append((candidate.name, candidate.category or "unknown"))
        return out

    @classmethod
    def from_extracted_content(
        cls, contents: Iterable[ExtractedContent]
    ) -> "EntityCandidateRegistry":
        """Build candidates from extracted translatable items and DLG speakers."""
        registry = cls()
        for content in contents:
            resource = Path(content.source_file).name if content.source_file else ""
            for item in content.items:
                add_item_candidate(registry, item, resource)
        return registry


def add_item_candidate(
    registry: EntityCandidateRegistry,
    item: TranslatableItem,
    resource: str,
) -> None:
    """Add candidate evidence derived from a single extracted item."""
    meta = item.metadata or {}
    item_type = str(meta.get("type", ""))
    if item_type in {"entry", "reply"}:
        speaker = str(meta.get("speaker", "")).strip()
        if speaker:
            registry.add(
                speaker,
                category="character",
                source="dlg_speaker",
                resource=resource,
                field="Speaker",
                context=item.text,
                is_speaker_or_dialog_actor=True,
            )
        return

    mapped = TYPE_TO_CANDIDATE.get(item_type)
    if mapped is None:
        return
    category, source, field = mapped
    if resource.lower().endswith(".git"):
        source = "git_instance"
    registry.add(
        item.text,
        category=category,
        source=source,
        resource=resource,
        field=str(meta.get("git_field") or field),
        context=item.context or "",
    )
