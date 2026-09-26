"""World registry of NPCs, areas, quests and items for contextual translation.

:class:`WorldScanner` reads the module's creature, area, journal, item and
placement files once before translation. :class:`WorldContext` holds what it
found: the glossary takes its names, dialog speaker resolution its actors, NCS
translation its script owners, and every dialog prompt the WORLD CONTEXT block
of the entities that dialog mentions.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..config import ProgressCallback
from ..extractors.base import TranslatableItem, extract_local_string
from ..formats.gff import read_gff
from ..nwn_constants import gender_label, race_label
from .entity_candidates import EntityCandidateRegistry
from .relevance import (
    SourceTokenIndex,
    common_hierarchy_components,
    hierarchical_entry_passes,
    is_relevant,
    tokenize_corpus,
)
from .string_filters import classify_entity_candidate, is_generic_entity_label

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

WORLD_CONTEXT_MAX_ENTRIES = 30
WORLD_CONTEXT_MAX_CHARS = 12000

#: Area instance lists (.git) whose objects can own or speak in a dialog.
_GIT_DIALOG_ACTOR_LISTS: Tuple[Tuple[str, str], ...] = (
    ("Creature List", "creature"),
    ("Placeable List", "placeable"),
    ("Door List", "door"),
)
#: Blueprints of non-creature objects that can own a dialog.
_DIALOG_OBJECT_KINDS: Dict[str, str] = {".utp": "placeable", ".utd": "door"}


@dataclass(frozen=True)
class _NamedSpec:
    """How the world scan registers a tagged, named entity.

    Attributes:
        count: Key of the entity kind in the scan summary counts.
        registry: The ``WorldContext`` dict (tag -> name) the entity goes into.
        name_field: CExoLocString field holding the name.
        category: Category of the name candidate.
        source: Evidence source of the name candidate.
    """

    count: str
    registry: Callable[["WorldContext"], Dict[str, str]]
    name_field: str
    category: str
    source: str


#: Blueprints registered by tag and name.
_NAMED_BLUEPRINTS: Dict[str, _NamedSpec] = {
    ".are": _NamedSpec("areas", lambda ctx: ctx.areas, "Name", "location", "are_name"),
    ".uti": _NamedSpec("items", lambda ctx: ctx.items, "LocalizedName", "item", "uti_name"),
}
#: Journal categories (``.jrl`` ``Categories`` structs) register quests the same way.
_JOURNAL_CATEGORY = _NamedSpec("quests", lambda ctx: ctx.quests, "Name", "quest", "jrl_category")

#: One WORLD CONTEXT entry: ``(name, tag, rendered line)``.
_Row = Tuple[str, str, str]

#: Creature blueprint event-script ResRef fields (Aurora UTC). SpeakString in
#: those scripts runs as OBJECT_SELF — the creature that owns the assignment.
UTC_SCRIPT_FIELDS: Tuple[str, ...] = (
    "ScriptAttacked",
    "ScriptDamaged",
    "ScriptDeath",
    "ScriptDialogue",
    "ScriptDisturbed",
    "ScriptEndRound",
    "ScriptHeartbeat",
    "ScriptOnBlocked",
    "ScriptOnNotice",
    "ScriptRested",
    "ScriptSpawn",
    "ScriptSpellAt",
    "ScriptUserDefined",
)


def _local_string(struct: Dict[str, Any], key: str) -> str:
    """Embedded text of the CExoLocString field *key*, or ``""``."""
    return extract_local_string(struct.get(key, {})) or ""


@dataclass
class NPCInfo:
    """A creature, or another object that can speak in a dialog.

    Attributes:
        tag: Object tag.
        first_name: First name; the name of a placeable or door.
        last_name: Last name.
        description: Creature blueprint description.
        race: Race label (``Creature`` when unknown); empty for placeables and doors.
        gender: Gender label; empty for placeables and doors.
        conversation: Conversation ResRef.
        kind: ``creature``, ``placeable`` or ``door``.
    """

    tag: str
    first_name: str
    last_name: str
    description: str
    race: str
    gender: str
    conversation: str
    kind: str = "creature"

    @property
    def display_name(self) -> str:
        """First + last name, or empty when the creature has no localized name."""
        return " ".join(
            p for p in (self.first_name, self.last_name) if p and str(p).strip()
        ).strip()

    @classmethod
    def from_creature(cls, data: Dict[str, Any], description: str = "") -> "NPCInfo":
        """Summarizes a creature struct: a ``.utc`` blueprint or a ``.git`` placement.

        Args:
            data: Parsed creature struct.
            description: Description to keep (blueprints only).

        Returns:
            The creature's summary.
        """
        return cls(
            tag=str(data.get("Tag") or ""),
            first_name=_local_string(data, "FirstName"),
            last_name=_local_string(data, "LastName"),
            description=description,
            race=race_label(data.get("Race", -1)) or "Creature",
            gender=gender_label(data.get("Gender", -1)),
            conversation=str(data.get("Conversation") or ""),
        )


@dataclass
class WorldContext:
    """Registry of world entities for context injection.

    Attributes:
        npcs: Creature blueprints by tag (those with a conversation, a
            description or a first name).
        areas: Area name by tag.
        quests: Journal category name by tag.
        items: Item name by tag.
        extracted_names: ``(name, category)`` pairs found by entity extraction.
        candidates: Evidence-backed glossary candidates.
        script_owners: Script ResRef (casefolded) -> creatures that assign
            that script on an event.
        dialog_actors_by_conversation: Objects that can speak in dialogs besides
            the creature blueprints in ``npcs`` (creatures, placeables and doors
            placed in areas, placeable and door blueprints), by casefolded
            Conversation ResRef. Only dialog speaker resolution reads them.
        dialog_actors_by_tag: The same actors by tag.
    """

    npcs: Dict[str, NPCInfo] = field(default_factory=dict)
    areas: Dict[str, str] = field(default_factory=dict)
    quests: Dict[str, str] = field(default_factory=dict)
    items: Dict[str, str] = field(default_factory=dict)
    extracted_names: List[Tuple[str, str]] = field(default_factory=list)
    candidates: EntityCandidateRegistry = field(default_factory=EntityCandidateRegistry)
    script_owners: Dict[str, List[NPCInfo]] = field(default_factory=dict)
    dialog_actors_by_conversation: Dict[str, List[NPCInfo]] = field(default_factory=dict)
    dialog_actors_by_tag: Dict[str, List[NPCInfo]] = field(default_factory=dict)

    def register_dialog_actor(self, actor: NPCInfo) -> bool:
        """Indexes *actor* by its Conversation and its tag for dialog speaker lookup.

        Identical placements of one blueprint are indexed once.

        Args:
            actor: The object.

        Returns:
            ``True`` when the actor was new.
        """
        added = False
        conversation = str(actor.conversation or "").strip().casefold()
        for index, key in (
            (self.dialog_actors_by_conversation, conversation),
            (self.dialog_actors_by_tag, actor.tag),
        ):
            if not key:
                continue
            actors = index.setdefault(key, [])
            if actor not in actors:
                actors.append(actor)
                added = True
        return added

    def register_script_owner(self, resref: object, npc: NPCInfo) -> None:
        """Records that *npc* runs *resref* as an event script (OBJECT_SELF).

        Args:
            resref: Script ResRef; empty and ``****``/``nw_`` placeholders are ignored.
            npc: The creature; one owner per tag is kept.
        """
        key = str(resref or "").strip().casefold()
        if not key or key in {"****", "nw_"}:
            return
        owners = self.script_owners.setdefault(key, [])
        if any(existing.tag == npc.tag for existing in owners):
            return
        owners.append(npc)

    def speaker_hint_for_script(self, script_stem: object) -> Optional[str]:
        """Compacts speaker metadata for NCS translation of *script_stem*.

        Shared blueprints (many goblins → one bark script) summarize race and
        gender instead of listing every name.

        Args:
            script_stem: Script name without extension.

        Returns:
            The hint, or ``None`` when no creature assigns this script.
        """
        key = str(script_stem or "").strip().casefold()
        if not key:
            return None
        owners = self.script_owners.get(key) or []
        if not owners:
            return None

        if len(owners) == 1:
            npc = owners[0]
            parts: List[str] = []
            name = npc.display_name
            if name:
                parts.append(name)
            if npc.race:
                parts.append(npc.race)
            if npc.gender:
                parts.append(npc.gender)
            if not parts:
                parts.append(f"tag {npc.tag}" if npc.tag else "creature")
            return "Speaker (OBJECT_SELF / in-character): " + ", ".join(parts)

        races = sorted({n.race for n in owners if n.race})
        genders = sorted({n.gender for n in owners if n.gender})
        summary_parts: List[str] = ["in-character"]
        if len(races) == 1:
            summary_parts.append(races[0])
        if len(genders) == 1:
            summary_parts.append(genders[0])
        summary_parts.append(f"shared by {len(owners)} creatures")
        return "Speaker (OBJECT_SELF): " + ", ".join(summary_parts)

    def enrich_ncs_item_context(self, item: TranslatableItem) -> None:
        """Appends the speaker hint of its script to an ``ncs_string`` item's context.

        Args:
            item: Extracted item; other item types and scripts without an
                owner are left unchanged, and a hint is added only once.
        """
        meta = item.metadata or {}
        if meta.get("type") != "ncs_string":
            return
        location = item.location or ""
        stem = Path(str(location)).stem if location else ""
        hint = self.speaker_hint_for_script(stem)
        if not hint:
            return
        current = (item.context or "").strip()
        if hint in current:
            return
        item.context = f"{current} {hint}".strip() if current else hint

    def get_all_names(self) -> List[Tuple[str, str]]:
        """Collects every known name for the glossary, uncurated.

        Returns:
            ``(name, category)`` pairs: NPC full names (``character``), then
            areas (``location``), quests (``quest``) and items (``item``), each
            sorted by tag, then the extracted names.
        """
        out: List[Tuple[str, str]] = []

        for _tag, npc in sorted(self.npcs.items()):
            full = npc.display_name
            if full:
                out.append((full, "character"))

        for mapping, category in (
            (self.areas, "location"),
            (self.quests, "quest"),
            (self.items, "item"),
        ):
            for _tag, name in sorted(mapping.items()):
                if name and str(name).strip():
                    out.append((str(name).strip(), category))

        for name, category in self.extracted_names:
            n = (name or "").strip()
            if n:
                out.append((n, category or "unknown"))

        return out

    def get_glossary_names(self) -> List[Tuple[str, str]]:
        """Returns the curated glossary candidates, else every known name.

        Returns:
            The eligible candidates' ``(name, category)`` pairs, or
            :meth:`get_all_names` when no candidate is eligible.
        """
        if self.candidates:
            pairs = self.candidates.glossary_pairs()
            if pairs:
                return pairs
        return self.get_all_names()

    def to_prompt_block(
        self,
        glossary: Optional["Glossary"] = None,
        target_lang: Optional[str] = None,
        source_texts: Optional[Iterable[str]] = None,
    ) -> str:
        """Formats the world context as a concise text block for the system prompt.

        Args:
            glossary: If set, append canonical translations next to matching English names.
            target_lang: Short label for those hints (e.g. ``russian`` → ``RUS``).
            source_texts: When provided, only entities relevant to the source
                corpus are emitted (see :mod:`.relevance`), ranked and capped
                at :data:`WORLD_CONTEXT_MAX_ENTRIES` entries and about
                :data:`WORLD_CONTEXT_MAX_CHARS` characters; empty sections are
                dropped. ``None`` returns the full block.

        Returns:
            The WORLD CONTEXT block, or ``""`` when it would list nothing.
        """
        texts = None if source_texts is None else list(source_texts)
        selection = (
            None
            if texts is None
            else _Selection(
                texts, [*self.areas.values(), *self.quests.values(), *self.items.values()]
            )
        )
        label = _target_lang_label(target_lang)
        entries = glossary.entries if glossary else {}

        def gloss(name: str) -> str:
            translation = entries.get(name.strip())
            return f" [{label}: {translation}]" if translation else ""

        def name_rows(mapping: Dict[str, str]) -> List[_Row]:
            return [
                (name, tag, f"  * {name} (Tag: {tag}){gloss(name)}")
                for tag, name in sorted(mapping.items())
            ]

        npc_rows: List[_Row] = []
        for tag, npc in sorted(self.npcs.items()):
            name = npc.display_name or tag
            traits = ", ".join(trait for trait in (npc.race, npc.gender) if trait)
            line = f"  * [{tag}] {name}"
            if traits:
                line += f" ({traits})"
            if name != tag:
                line += gloss(name)
            description = (npc.description or "").strip()
            if description:
                line += f" - {description}"
            npc_rows.append((name, tag, line))

        lines = ["WORLD CONTEXT:"]
        for header, category, rows in (
            ("- KEY CHARACTERS IN THE GAME:", "character", npc_rows),
            ("- LOCATIONS:", "location", name_rows(self.areas)),
            ("- QUESTS:", "quest", name_rows(self.quests)),
            ("- KEY ITEMS:", "item", name_rows(self.items)),
        ):
            if selection is None:
                selected = [line for _name, _tag, line in rows]
            else:
                selected = selection.select(category, rows)
            if selected:
                lines.append(header)
                lines.extend(selected)

        if len(lines) == 1:
            return ""
        return "\n".join(lines)


def _target_lang_label(target_lang: Optional[str]) -> str:
    """Short label for inline glossary hints: ``RUS`` for russian, ``TL`` when unknown."""
    if not target_lang or not str(target_lang).strip():
        return "TL"
    t = str(target_lang).strip()
    if len(t) <= 4:
        return t.upper()
    return t[:3].upper()


class _Selection:
    """Relevance filter and shared budget of one WORLD CONTEXT block."""

    def __init__(self, texts: List[str], hierarchy_names: List[str]) -> None:
        self._index = SourceTokenIndex(tokenize_corpus(texts))
        self._joined = "\n".join(str(t) for t in texts if t).casefold()
        self._common = common_hierarchy_components(hierarchy_names)
        self._entries_left = WORLD_CONTEXT_MAX_ENTRIES
        self._chars_left = WORLD_CONTEXT_MAX_CHARS

    def select(self, category: str, rows: List[_Row]) -> List[str]:
        """Returns the lines of the relevant *rows*, best first, within the remaining budget.

        Args:
            category: Entity category of the section (``character``, ``location``,
                ``quest`` or ``item``).
            rows: Candidate entries of one section.

        Returns:
            The selected lines; the shared budget shrinks by what they take.
        """
        scored = [
            (self._score(name, tag, category), line)
            for name, tag, line in rows
            if self._keep(name, tag, category)
        ]
        selected: List[str] = []
        for score, line in sorted(scored, key=lambda x: (-x[0], x[1].lower())):
            if score < 0:
                continue
            if self._entries_left <= 0:
                break
            next_chars = self._chars_left - len(line) - 1
            # A section always gets its first line, even past the character budget.
            if selected and next_chars < 0:
                break
            selected.append(line)
            self._entries_left -= 1
            self._chars_left = max(0, next_chars)
        return selected

    def _keep(self, name: str, tag: str, category: str) -> bool:
        """Whether the entry is evidenced by the source corpus."""
        joined = " ".join(c for c in (name, tag) if c)
        if not joined:
            return False
        if name and is_generic_entity_label(name, category):
            # Generic labels (e.g. ``Human Female``, ``Almraiven Resident``)
            # are shared across many indistinguishable NPCs. Even when the
            # label happens to appear in the source as a descriptive phrase,
            # admitting all carriers floods the prompt with noise without
            # adding translation evidence — drop them entirely.
            return False
        if not is_relevant(joined, self._index):
            return False
        if name and not hierarchical_entry_passes(name, self._joined, self._common):
            return False
        return True

    def _score(self, name: str, tag: str, category: str) -> int:
        """Ranks a kept entry: literal name and tag hits first, deprioritized labels last."""
        decision = classify_entity_candidate(name, category).decision
        if decision == "drop":
            return -1000
        score = 0
        name_key = (name or "").casefold()
        tag_key = (tag or "").casefold()
        if name_key and name_key in self._joined:
            score += 1000
        if tag_key and tag_key in self._joined:
            score += 900
        if decision == "deprioritize":
            score -= 500
        return score


class WorldScanner:
    """Scans an extracted module directory to build a :class:`WorldContext`."""

    def scan_directory(
        self,
        extract_dir: Path,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        source_encoding: Optional[str] = None,
    ) -> WorldContext:
        """Scans the directory and build world context.

        Args:
            extract_dir: Path to directory containing extracted module files.
            gff_cache: Optional shared parse cache (same object as ModuleTranslator).
                Must be read with the same *source_encoding* everywhere it is shared.
            progress_callback: Optional progress reporter (every 20 files).
            source_encoding: Declared code page for module string bytes.

        Returns:
            Populated WorldContext. Files that fail to parse are skipped.
        """
        logger.info("Scanning module for world context...")
        context = WorldContext()
        counts = {"npcs": 0, "areas": 0, "quests": 0, "items": 0, "actors": 0}

        scannable_exts = {".utc", ".are", ".jrl", ".uti", ".git", *_DIALOG_OBJECT_KINDS}
        scan_files = [
            f for f in extract_dir.rglob("*") if f.is_file() and f.suffix.lower() in scannable_exts
        ]

        for idx, file_path in enumerate(scan_files):
            if progress_callback and idx % 20 == 0:
                progress_callback(
                    "scanning",
                    idx,
                    len(scan_files),
                    f"Scanning {file_path.name}",
                )

            ext = file_path.suffix.lower()
            resource = file_path.name
            try:
                data = read_gff(file_path, cache=gff_cache, source_encoding=source_encoding)
                if ext == ".utc":
                    counts["npcs"] += _scan_creature(context, data, resource)
                elif ext == ".jrl":
                    counts[_JOURNAL_CATEGORY.count] += sum(
                        _register_named(context, category, _JOURNAL_CATEGORY, resource)
                        for category in data.get("Categories", [])
                        if isinstance(category, dict)
                    )
                elif ext in _NAMED_BLUEPRINTS:
                    spec = _NAMED_BLUEPRINTS[ext]
                    counts[spec.count] += _register_named(context, data, spec, resource)
                elif ext == ".git":
                    counts["actors"] += _scan_placements(context, data)
                else:
                    counts["actors"] += _register_dialog_actor(
                        context, data, _DIALOG_OBJECT_KINDS[ext]
                    )
            except Exception as e:
                logger.debug("Failed to scan context from %s: %s", file_path.name, e)

        logger.info(
            "World context built: %d NPCs, %d locations, %d quests, %d items, "
            "%d other dialog actors",
            counts["npcs"],
            counts["areas"],
            counts["quests"],
            counts["items"],
            counts["actors"],
        )
        return context


def _scan_creature(context: WorldContext, data: Dict[str, Any], resource: str) -> bool:
    """Registers a creature blueprint (.utc): script owner, NPC and name candidate.

    Only creatures with a conversation, a description or a first name become
    NPCs, so the prompt is not flooded with generic monsters.

    Args:
        context: World context to populate.
        data: Parsed creature blueprint.
        resource: File name of the blueprint (candidate evidence).

    Returns:
        ``True`` when the creature was added to ``context.npcs``.
    """
    description = _local_string(data, "Description")
    npc = NPCInfo.from_creature(data, description)
    if not npc.tag:
        return False
    for script_field in UTC_SCRIPT_FIELDS:
        resref = data.get(script_field, "")
        if isinstance(resref, bytes):
            resref = resref.decode("ascii", errors="ignore")
        context.register_script_owner(resref, npc)

    if not (npc.conversation or description or npc.first_name):
        return False
    context.npcs[npc.tag] = npc
    if npc.display_name:
        context.candidates.add(
            npc.display_name,
            category="character",
            source="utc_name",
            resource=resource,
            field="FirstName/LastName",
            context=description,
            is_speaker_or_dialog_actor=bool(npc.conversation),
        )
    return True


def _register_named(
    context: WorldContext, struct: Dict[str, Any], spec: _NamedSpec, resource: str
) -> bool:
    """Registers a tagged, named entity (area, item or quest) and its name candidate.

    Args:
        context: World context to populate.
        struct: Area or item blueprint, or one journal category.
        spec: Where the entity goes and how its candidate is labelled.
        resource: File name of the struct (candidate evidence).

    Returns:
        ``True`` when the struct had both a tag and a name.
    """
    tag = struct.get("Tag", "")
    name = _local_string(struct, spec.name_field)
    if not (tag and name):
        return False
    spec.registry(context)[tag] = name
    context.candidates.add(
        name, category=spec.category, source=spec.source, resource=resource, field=spec.name_field
    )
    return True


def _scan_placements(context: WorldContext, data: Dict[str, Any]) -> int:
    """Registers the creatures, placeables and doors placed in an area (.git).

    A placed instance can rename its blueprint or give it another
    Conversation, so dialog owners are looked up among the placements too.

    Args:
        context: World context to populate.
        data: Parsed area instance file.

    Returns:
        Number of dialog actors added to the context.
    """
    added = 0
    for list_key, kind in _GIT_DIALOG_ACTOR_LISTS:
        instances = data.get(list_key)
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if isinstance(instance, dict) and _register_dialog_actor(context, instance, kind):
                added += 1
    return added


def _register_dialog_actor(context: WorldContext, data: Dict[str, Any], kind: str) -> bool:
    """Registers one creature, placeable or door struct as a dialog actor.

    Args:
        context: World context to populate.
        data: Blueprint or placed instance struct.
        kind: ``creature``, ``placeable`` or ``door``.

    Returns:
        ``True`` if the actor was new to the context.
    """
    if kind == "creature":
        actor = NPCInfo.from_creature(data)
    else:
        name = _local_string(data, "LocName") or _local_string(data, "LocalizedName")
        actor = NPCInfo(
            tag=str(data.get("Tag") or ""),
            first_name=name,
            last_name="",
            description="",
            race="",
            gender="",
            conversation=str(data.get("Conversation") or ""),
            kind=kind,
        )
    if not actor.tag and not actor.first_name.strip() and not actor.last_name.strip():
        return False
    return context.register_dialog_actor(actor)
