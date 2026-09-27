"""Evidence-backed entity candidates for the glossary and prompt context.

Every source that sees a name (the world scan, extracted fields, dialog
speakers, entity extraction) adds an evidence record; the registry merges them
per normalized name. Curation then decides which candidates seed the glossary.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..extractors.base import ExtractedContent, TranslatableItem
from .string_filters import classify_entity_candidate

logger = logging.getLogger(__name__)

_SPACE_RE = re.compile(r"\s+")

#: Candidate priority by evidence source (10 for any other); the highest one seen
#: is kept. Only the ``candidates.json`` artifact shows it.
SOURCE_PRIORITY = {
    "dlg_speaker": 95,
    "utc_name": 90,
    "are_name": 85,
    "jrl_category": 80,
    "uti_name": 75,
    "git_instance": 45,
    "entity_extractor": 60,
}

#: Extracted item type -> ``(category, evidence source, field label)``.
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
    """Returns the registry key of *name*.

    Args:
        name: Observed name; ``None`` counts as empty.

    Returns:
        *name* NFKC-normalized, with whitespace collapsed, stripped and casefolded.
    """
    text = unicodedata.normalize("NFKC", "" if name is None else str(name))
    return _SPACE_RE.sub(" ", text).strip().casefold()


@dataclass
class EntityEvidence:
    """One source observation supporting an entity candidate.

    Attributes:
        source: Evidence source label (a :data:`SOURCE_PRIORITY` key).
        resource: File name the observation comes from.
        field: GFF field or item field label.
        category: Category the source suggests.
        context: Text around the name.
        is_speaker_or_dialog_actor: The name speaks or owns a dialog.
    """

    source: str
    resource: str = ""
    field: str = ""
    category: str = "unknown"
    context: str = ""
    is_speaker_or_dialog_actor: bool = False


@dataclass
class EntityCandidate:
    """A possible named entity backed by one or more evidence records.

    Attributes:
        name: Display form (first seen, whitespace collapsed).
        normalized_name: Registry key (:func:`normalize_entity_name`).
        category: First specific category any evidence suggested.
        frequency: Number of evidence records.
        contexts: Up to three distinct context snippets (240 characters each).
        evidence: All evidence records, in arrival order.
        is_speaker_or_dialog_actor: Any evidence marks the name as speaking.
        technical_score: Score of the deterministic filter (artifact only).
        priority: Highest source priority seen, or a curator's higher value.
        curation_decision: ``keep``, ``local_only``, ``drop`` or ``alias_of``.
        curation_reason: Short reason tag of the decision.
        alias_of: Source form of the entity this name is an alias of.
    """

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
        """Merges one evidence record into this candidate.

        Args:
            evidence: The observation to add.
        """
        self.evidence.append(evidence)
        self.frequency += 1
        self.is_speaker_or_dialog_actor = (
            self.is_speaker_or_dialog_actor or evidence.is_speaker_or_dialog_actor
        )
        snippet = _SPACE_RE.sub(" ", evidence.context or "").strip()
        if snippet and snippet not in self.contexts and len(self.contexts) < 3:
            self.contexts.append(snippet[:240])
        if self.category == "unknown" and evidence.category != "unknown":
            self.category = evidence.category
        self.priority = max(self.priority, SOURCE_PRIORITY.get(evidence.source, 10))

    @property
    def sources(self) -> List[str]:
        """Sorted unique evidence source labels."""
        return sorted({ev.source for ev in self.evidence})

    @property
    def resources(self) -> List[str]:
        """Sorted unique non-empty resource labels."""
        return sorted({ev.resource for ev in self.evidence if ev.resource})

    @property
    def eligible_for_glossary(self) -> bool:
        """Whether neither curation (``drop``, ``local_only``) nor the filter excludes it."""
        if self.curation_decision in {"drop", "local_only"}:
            return False
        return classify_entity_candidate(self.name, self.category).decision != "drop"

    def to_curator_record(self) -> Dict[str, object]:
        """Returns the JSON record the curator sees for this candidate.

        Returns:
            Name, category, sources, frequency, contexts, the filter's technical
            flags and the speaker flag.
        """
        return {
            "name": self.name,
            "category": self.category,
            "sources": self.sources,
            "frequency": self.frequency,
            "contexts": self.contexts,
            "technical_flags": sorted(classify_entity_candidate(self.name, self.category).reasons),
            "is_speaker_or_dialog_actor": self.is_speaker_or_dialog_actor,
        }


class EntityCandidateRegistry:
    """Mutable registry that deduplicates candidates by normalized name."""

    def __init__(self) -> None:
        """Creates an empty registry."""
        self._items: Dict[str, EntityCandidate] = {}

    def __bool__(self) -> bool:
        """Tells whether the registry holds any candidate."""
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
    ) -> None:
        """Adds one evidence record for *name*, creating its candidate on first sight.

        A new candidate starts with the deterministic filter's score, and with a
        ``drop`` decision when the filter drops the name. Blank names are ignored.

        Args:
            name: Observed name.
            category: Category the source suggests.
            source: Evidence source label.
            resource: File name of the observation.
            field: Field label of the observation.
            context: Text around the name.
            is_speaker_or_dialog_actor: The name speaks or owns a dialog.
        """
        clean = _SPACE_RE.sub(" ", "" if name is None else str(name)).strip()
        normalized = normalize_entity_name(clean)
        if not normalized:
            return
        candidate = self._items.get(normalized)
        if candidate is None:
            result = classify_entity_candidate(clean, category)
            candidate = self._items[normalized] = EntityCandidate(
                name=clean,
                normalized_name=normalized,
                category=category or "unknown",
                technical_score=result.technical_score,
                curation_decision="drop" if result.decision == "drop" else "keep",
                curation_reason=result.reason,
            )
        category = category or "unknown"
        candidate.add_evidence(
            EntityEvidence(source, resource, field, category, context, is_speaker_or_dialog_actor)
        )

    def extend(self, candidates: Iterable[EntityCandidate]) -> None:
        """Replays every evidence record of *candidates* through :meth:`add`.

        Args:
            candidates: Candidates of another registry.
        """
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

    def restore(self, candidates: Iterable[EntityCandidate]) -> None:
        """Inserts saved candidates as they are, keeping their curated fields.

        Args:
            candidates: Candidates keyed by ``normalized_name``; a later one
                replaces an earlier one with the same key.
        """
        for candidate in candidates:
            self._items[candidate.normalized_name] = candidate

    def values(self) -> List[EntityCandidate]:
        """Returns all candidates, sorted by normalized name."""
        return [self._items[k] for k in sorted(self._items)]

    def mark_curated(
        self,
        name: str,
        *,
        decision: str,
        reason: str = "",
        priority: Optional[int] = None,
        alias_of: Optional[str] = None,
    ) -> None:
        """Applies a curator decision to an existing candidate.

        Args:
            name: Candidate name (matched by normalized form); unknown names are ignored.
            decision: New curation decision.
            reason: Reason tag.
            priority: Curator priority; raises the candidate's priority only.
            alias_of: Alias target; ``None`` clears a previous one.
        """
        candidate = self._items.get(normalize_entity_name(name))
        if candidate is None:
            return
        candidate.curation_decision = decision
        candidate.curation_reason = reason
        if priority is not None:
            candidate.priority = max(candidate.priority, int(priority))
        candidate.alias_of = alias_of or None

    def glossary_pairs(self) -> List[Tuple[str, str]]:
        """Returns ``(name, category)`` of the glossary-eligible candidates, by normalized name."""
        return [(c.name, c.category or "unknown") for c in self.values() if c.eligible_for_glossary]

    def resolved_aliases(self) -> Dict[str, str]:
        """Resolves alias chains to their root candidate.

        Returns:
            Eligible alias name -> name of its eligible root; chains with a missing
            target, a cycle or an ineligible link are dropped with a warning.
        """
        result: Dict[str, str] = {}
        for candidate in self.values():
            if not candidate.alias_of or not candidate.eligible_for_glossary:
                continue
            current = candidate
            visited = {candidate.normalized_name}
            while current.alias_of:
                key = normalize_entity_name(current.alias_of)
                target = self._items.get(key)
                if key in visited or target is None or not target.eligible_for_glossary:
                    logger.warning(
                        "Unresolved glossary alias %r -> %r", candidate.name, current.alias_of
                    )
                    break
                visited.add(key)
                current = target
            else:
                result[candidate.name] = current.name
        return result

    @classmethod
    def from_extracted_content(
        cls, contents: Iterable[ExtractedContent]
    ) -> "EntityCandidateRegistry":
        """Builds candidates from extracted name fields and dialog speakers.

        Args:
            contents: Extracted content of every resource.

        Returns:
            A new registry.
        """
        registry = cls()
        for content in contents:
            resource = Path(content.source_file).name if content.source_file else ""
            for item in content.items:
                registry._add_item(item, resource)
        return registry

    def _add_item(self, item: TranslatableItem, resource: str) -> None:
        """Adds the evidence of one extracted item, if any.

        Dialog lines give their speaker; the types of :data:`TYPE_TO_CANDIDATE`
        give their text. Everything in a ``.git`` resource is ``git_instance``
        evidence.
        """
        meta = item.metadata or {}
        item_type = str(meta.get("type", ""))
        if item_type in {"entry", "reply"}:
            speaker = str(meta.get("speaker", "")).strip()
            if speaker:
                self.add(
                    speaker,
                    category="character",
                    source="dlg_speaker",
                    resource=resource,
                    field="Speaker",
                    context=item.text,
                    is_speaker_or_dialog_actor=True,
                )
        elif item_type in TYPE_TO_CANDIDATE:
            category, source, field_label = TYPE_TO_CANDIDATE[item_type]
            self.add(
                item.text,
                category=category,
                source="git_instance" if resource.lower().endswith(".git") else source,
                resource=resource,
                field=str(meta.get("git_field") or field_label),
                context=item.context or "",
            )
