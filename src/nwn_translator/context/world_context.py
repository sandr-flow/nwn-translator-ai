"""World registry of NPCs, areas, quests and items for contextual translation.

:class:`WorldScanner` reads the module's blueprints, areas, journals and area
placements once before translation. :class:`WorldContext` holds what it found:
the glossary takes its names, dialog speaker resolution its actors, NCS
translation its script owners, and every dialog prompt the WORLD CONTEXT block
of the entities that dialog mentions.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

from ..config import ProgressCallback
from ..extractors.base import TranslatableItem, extract_local_string, list_field
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

#: Entry and approximate character budgets of a relevance-filtered WORLD CONTEXT block.
WORLD_CONTEXT_MAX_ENTRIES = 30
WORLD_CONTEXT_MAX_CHARS = 12000


class _NamedKind(NamedTuple):
    """How the world scan registers a tagged, named entity.

    Attributes:
        count_key: Key of the entity kind in the scan summary counts.
        registry: The ``WorldContext`` dict (tag -> name) the entity goes into.
        name_field: CExoLocString field holding the name.
        category: Category of the name candidate.
        source: Evidence source of the name candidate.
    """

    count_key: str
    registry: Callable[["WorldContext"], Dict[str, str]]
    name_field: str
    category: str
    source: str


#: Tagged, named entities by extension. A journal registers each of its categories.
_NAMED: Dict[str, _NamedKind] = {
    ".are": _NamedKind("areas", lambda ctx: ctx.areas, "Name", "location", "are_name"),
    ".uti": _NamedKind("items", lambda ctx: ctx.items, "LocalizedName", "item", "uti_name"),
    ".jrl": _NamedKind("quests", lambda ctx: ctx.quests, "Name", "quest", "jrl_category"),
}
#: Blueprints of non-creature objects that can own a dialog.
_DIALOG_OBJECT_KINDS = {".utp": "placeable", ".utd": "door"}
#: Area instance lists (.git) whose objects can own or speak in a dialog.
_GIT_DIALOG_ACTOR_LISTS = {
    "Creature List": "creature",
    "Placeable List": "placeable",
    "Door List": "door",
}
#: Creature event-script fields; SpeakString in them runs as the creature (OBJECT_SELF).
_UTC_SCRIPT_FIELDS = (
    "ScriptAttacked ScriptDamaged ScriptDeath ScriptDialogue ScriptDisturbed ScriptEndRound "
    "ScriptHeartbeat ScriptOnBlocked ScriptOnNotice ScriptRested ScriptSpawn ScriptSpellAt "
    "ScriptUserDefined"
).split()

#: One WORLD CONTEXT entry: ``(name, tag, rendered line)``.
_Row = Tuple[str, str, str]


def _local_string(struct: Dict[str, Any], key: str) -> str:
    """Returns the embedded text of the CExoLocString field *key*, or ``""``."""
    return extract_local_string(struct.get(key, {})) or ""


def _text_field(struct: Dict[str, Any], key: str) -> str:
    """Returns the plain field *key* as a string, ``""`` when missing."""
    return str(struct.get(key) or "")


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
    last_name: str = ""
    description: str = ""
    race: str = ""
    gender: str = ""
    conversation: str = ""
    kind: str = "creature"

    @property
    def display_name(self) -> str:
        """First and last name as stored; empty without a localized name."""
        return " ".join(p for p in (self.first_name, self.last_name) if p.strip()).strip()

    @property
    def speaker_name(self) -> str:
        """First and last name with each part stripped, or the tag without a name."""
        parts = (p.strip() for p in (self.first_name, self.last_name))
        return " ".join(p for p in parts if p) or self.tag

    @property
    def traits(self) -> str:
        """Race and gender, comma-separated; empty for placeables and doors."""
        return ", ".join(t for t in (self.race, self.gender) if t)

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
            tag=_text_field(data, "Tag"),
            first_name=_local_string(data, "FirstName"),
            last_name=_local_string(data, "LastName"),
            description=description,
            race=race_label(data.get("Race", -1)) or "Creature",
            gender=gender_label(data.get("Gender", -1)),
            conversation=_text_field(data, "Conversation"),
        )


@dataclass
class WorldContext:
    """Registry of world entities for context injection.

    Attributes:
        npcs: Creature blueprints with a conversation, a description or a first
            name, by tag.
        areas: Area name by tag.
        quests: Journal category name by tag.
        items: Item name by tag.
        extracted_names: ``(name, category)`` pairs found by entity extraction.
        candidates: Evidence-backed glossary candidates.
        script_owners: Casefolded script ResRef -> creatures running it on an event.
        dialog_actors_by_conversation: Objects that can speak in dialogs besides the
            creature blueprints in ``npcs`` (placed creatures, placeables and doors,
            placeable and door blueprints), by casefolded Conversation ResRef.
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
        """Indexes *actor* by Conversation and tag; identical placements are indexed once.

        Args:
            actor: The object.

        Returns:
            ``True`` when the actor was new.
        """
        added = False
        for index, key in (
            (self.dialog_actors_by_conversation, actor.conversation.strip().casefold()),
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
        if all(owner.tag != npc.tag for owner in owners):
            owners.append(npc)

    def speaker_hint_for_script(self, script_stem: object) -> Optional[str]:
        """Returns a compact speaker hint for the strings of the script *script_stem*.

        A script shared by many creatures (one bark script for every goblin) gets
        their common race and gender instead of their names.

        Args:
            script_stem: Script name without extension.

        Returns:
            The hint, or ``None`` when no creature runs this script.
        """
        owners = self.script_owners.get(str(script_stem or "").strip().casefold())
        if not owners:
            return None
        if len(owners) == 1:
            npc = owners[0]
            parts = [p for p in (npc.display_name, npc.race, npc.gender) if p]
            fallback = f"tag {npc.tag}" if npc.tag else "creature"
            return "Speaker (OBJECT_SELF / in-character): " + ", ".join(parts or [fallback])
        parts = ["in-character"]
        for values in ({npc.race for npc in owners}, {npc.gender for npc in owners}):
            values.discard("")
            if len(values) == 1:
                parts.extend(values)
        parts.append(f"shared by {len(owners)} creatures")
        return "Speaker (OBJECT_SELF): " + ", ".join(parts)

    def enrich_ncs_item_context(self, item: TranslatableItem) -> None:
        """Appends the speaker hint of its script to an ``ncs_string`` item's context, once.

        Args:
            item: Extracted item; other types and scripts without an owner are left as is.
        """
        if (item.metadata or {}).get("type") != "ncs_string":
            return
        hint = self.speaker_hint_for_script(Path(item.location).stem if item.location else "")
        current = (item.context or "").strip()
        if hint and hint not in current:
            item.context = f"{current} {hint}".strip() if current else hint

    def get_all_names(self) -> List[Tuple[str, str]]:
        """Collects every known name for the glossary, uncurated.

        Returns:
            ``(name, category)`` pairs: NPC full names (``character``), then areas
            (``location``), quests (``quest``) and items (``item``), each sorted by
            tag, then the extracted names.
        """
        out = [
            (npc.display_name, "character")
            for _tag, npc in sorted(self.npcs.items())
            if npc.display_name
        ]
        for mapping, category in (
            (self.areas, "location"),
            (self.quests, "quest"),
            (self.items, "item"),
        ):
            out += [
                (name.strip(), category) for _tag, name in sorted(mapping.items()) if name.strip()
            ]
        out += [
            (name.strip(), category or "unknown")
            for name, category in self.extracted_names
            if name and name.strip()
        ]
        return out

    def get_glossary_names(self) -> List[Tuple[str, str]]:
        """Returns the eligible candidates' ``(name, category)`` pairs, else every known name."""
        return (self.candidates.glossary_pairs() if self.candidates else []) or self.get_all_names()

    def to_prompt_block(
        self,
        glossary: Optional["Glossary"] = None,
        target_lang: Optional[str] = None,
        source_texts: Optional[Iterable[str]] = None,
    ) -> str:
        """Formats the world context as the WORLD CONTEXT block of the system prompt.

        Args:
            glossary: Glossary whose translations are appended to matching names.
            target_lang: Target language, shortened to the label of those
                translations (``russian`` -> ``RUS``).
            source_texts: Keep only the entities relevant to these texts (see
                :mod:`.relevance`), ranked and capped at
                :data:`WORLD_CONTEXT_MAX_ENTRIES` entries and about
                :data:`WORLD_CONTEXT_MAX_CHARS` characters; ``None`` keeps all.

        Returns:
            The block without empty sections, or ``""`` when it lists nothing.
        """
        label = _target_lang_label(target_lang)
        entries = glossary.entries if glossary else {}

        def gloss(name: str) -> str:
            """Returns the inline glossary hint of *name*, or ``""``."""
            translation = entries.get(name.strip())
            return f" [{label}: {translation}]" if translation else ""

        npc_rows: List[_Row] = []
        for tag, npc in sorted(self.npcs.items()):
            name = npc.display_name or tag
            line = f"  * [{tag}] {name}" + (f" ({npc.traits})" if npc.traits else "")
            line += gloss(name) if name != tag else ""
            description = (npc.description or "").strip()
            npc_rows.append((name, tag, line + (f" - {description}" if description else "")))
        sections = [("- KEY CHARACTERS IN THE GAME:", "character", npc_rows)]
        for header, category, mapping in (
            ("- LOCATIONS:", "location", self.areas),
            ("- QUESTS:", "quest", self.quests),
            ("- KEY ITEMS:", "item", self.items),
        ):
            rows = [
                (name, tag, f"  * {name} (Tag: {tag}){gloss(name)}")
                for tag, name in sorted(mapping.items())
            ]
            sections.append((header, category, rows))

        selection = None
        if source_texts is not None:
            names = [*self.areas.values(), *self.quests.values(), *self.items.values()]
            selection = _Selection(list(source_texts), names)
        lines = ["WORLD CONTEXT:"]
        for header, category, rows in sections:
            if selection is None:
                selected = [line for _name, _tag, line in rows]
            else:
                selected = selection.select(category, rows)
            if selected:
                lines += [header, *selected]
        return "\n".join(lines) if len(lines) > 1 else ""


def _target_lang_label(target_lang: Optional[str]) -> str:
    """Returns the label of inline glossary hints: ``RUS`` for russian, ``TL`` when unknown."""
    lang = (target_lang or "").strip()
    return (lang if len(lang) <= 4 else lang[:3]).upper() or "TL"


class _Selection:
    """Relevance filter and shared budget of one WORLD CONTEXT block."""

    def __init__(self, texts: List[str], hierarchy_names: List[str]) -> None:
        """Indexes the source *texts*; *hierarchy_names* are the area, quest and item names."""
        self._index = SourceTokenIndex(tokenize_corpus(texts))
        self._joined = "\n".join(str(t) for t in texts if t).casefold()
        self._common = common_hierarchy_components(hierarchy_names)
        self._entries_left = WORLD_CONTEXT_MAX_ENTRIES
        self._chars_left = WORLD_CONTEXT_MAX_CHARS

    def select(self, category: str, rows: List[_Row]) -> List[str]:
        """Returns the lines of the relevant *rows*, best first, within the remaining budget.

        Args:
            category: Entity category of the section.
            rows: Entries of the section.

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
        """Tells whether the corpus evidences the entry.

        A generic label (``Human Female``) is shared by many indistinguishable
        NPCs, so even a literal mention would admit all of them: it never counts.
        """
        joined = " ".join(c for c in (name, tag) if c)
        if not joined or (name and is_generic_entity_label(name, category)):
            return False
        return is_relevant(joined, self._index) and (
            not name or hierarchical_entry_passes(name, self._joined, self._common)
        )

    def _score(self, name: str, tag: str, category: str) -> int:
        """Ranks a kept entry: literal name and tag hits first, deprioritized labels last."""
        decision = classify_entity_candidate(name, category).decision
        if decision == "drop":
            return -1000
        score = 1000 if name and name.casefold() in self._joined else 0
        score += 900 if tag and tag.casefold() in self._joined else 0
        return score - 500 if decision == "deprioritize" else score


class WorldScanner:
    """Scanner that builds a :class:`WorldContext` from an extracted module directory."""

    def scan_directory(
        self,
        extract_dir: Path,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        source_encoding: Optional[str] = None,
    ) -> WorldContext:
        """Scans the directory and builds the world context.

        Args:
            extract_dir: Directory of the extracted module files.
            gff_cache: Parse cache shared by the run's stages, if any; every user
                must read with the same *source_encoding*.
            progress_callback: Optional progress reporter (every 20 files).
            source_encoding: Declared code page for module string bytes.

        Returns:
            The world context; files that fail to parse are skipped.
        """
        logger.info("Scanning module for world context...")
        context = WorldContext()
        counts = dict.fromkeys(("npcs", "areas", "quests", "items", "actors"), 0)
        exts = {".utc", ".git", *_NAMED, *_DIALOG_OBJECT_KINDS}
        files = [f for f in extract_dir.rglob("*") if f.is_file() and f.suffix.lower() in exts]
        for idx, file_path in enumerate(files):
            if progress_callback and idx % 20 == 0:
                progress_callback("scanning", idx, len(files), f"Scanning {file_path.name}")
            ext = file_path.suffix.lower()
            try:
                data = read_gff(file_path, cache=gff_cache, source_encoding=source_encoding)
                if ext == ".utc":
                    counts["npcs"] += _scan_creature(context, data, file_path.name)
                elif ext == ".git":
                    # A placement can rename its blueprint or give it another Conversation.
                    counts["actors"] += sum(
                        _register_dialog_actor(context, instance, kind)
                        for list_key, kind in _GIT_DIALOG_ACTOR_LISTS.items()
                        for instance in list_field(data, list_key)
                        if isinstance(instance, dict)
                    )
                elif ext in _DIALOG_OBJECT_KINDS:
                    kind = _DIALOG_OBJECT_KINDS[ext]
                    counts["actors"] += _register_dialog_actor(context, data, kind)
                else:
                    structs = list_field(data, "Categories") if ext == ".jrl" else [data]
                    counts[_NAMED[ext].count_key] += sum(
                        _register_named(context, struct, ext, file_path.name)
                        for struct in structs
                        if isinstance(struct, dict)
                    )
            except Exception as e:
                logger.debug("Failed to scan context from %s: %s", file_path.name, e)

        logger.info(
            "World context built: %d NPCs, %d locations, %d quests, %d items, "
            "%d other dialog actors",
            *counts.values(),
        )
        return context


def _scan_creature(context: WorldContext, data: Dict[str, Any], resource: str) -> bool:
    """Registers a creature blueprint: script owner, NPC and name candidate.

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
    for script_field in _UTC_SCRIPT_FIELDS:
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


def _register_named(context: WorldContext, struct: Dict[str, Any], ext: str, resource: str) -> bool:
    """Registers a tagged, named entity (see :data:`_NAMED`) and its name candidate.

    Args:
        context: World context to populate.
        struct: Area or item blueprint, or one journal category.
        ext: Extension of the resource, the key of its :data:`_NAMED` entry.
        resource: File name of the struct (candidate evidence).

    Returns:
        ``True`` when the struct had both a tag and a name.
    """
    kind = _NAMED[ext]
    tag = struct.get("Tag", "")
    name = _local_string(struct, kind.name_field)
    if not (tag and name):
        return False
    kind.registry(context)[tag] = name
    context.candidates.add(
        name, category=kind.category, source=kind.source, resource=resource, field=kind.name_field
    )
    return True


def _register_dialog_actor(context: WorldContext, data: Dict[str, Any], kind: str) -> bool:
    """Registers a creature, placeable or door struct as a dialog actor.

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
        conversation = _text_field(data, "Conversation")
        actor = NPCInfo(_text_field(data, "Tag"), name, conversation=conversation, kind=kind)
    if not (actor.tag or actor.first_name.strip() or actor.last_name.strip()):
        return False
    return context.register_dialog_actor(actor)
