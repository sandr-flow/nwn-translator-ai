"""World context scanner for contextual translation.

This module provides tools to scan a module's extracted files before translation
begins, collecting a registry of NPCs, areas, items, and quests. This data is
then fed into the AI system prompt to provide world context and improve
translation coherence.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from ..file_handlers import read_gff
from ..extractors.base import extract_local_string
from ..nwn_constants import race_label, gender_label
from .entity_candidates import EntityCandidateRegistry
from .string_filters import classify_entity_candidate, is_generic_entity_label

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)

WORLD_CONTEXT_MAX_ENTRIES = 30
WORLD_CONTEXT_MAX_CHARS = 12000


@dataclass
class NPCInfo:
    """Information about a specific NPC."""

    tag: str
    first_name: str
    last_name: str
    description: str
    race: str
    gender: str
    conversation: str


@dataclass
class WorldContext:
    """Registry of world entities for context injection."""

    npcs: Dict[str, NPCInfo] = field(default_factory=dict)
    areas: Dict[str, str] = field(default_factory=dict)
    quests: Dict[str, str] = field(default_factory=dict)
    items: Dict[str, str] = field(default_factory=dict)
    #: Proper nouns discovered by :class:`EntityExtractor` in text bodies.
    #: Populated after Phase A, before glossary build.
    extracted_names: List[Tuple[str, str]] = field(default_factory=list)
    candidates: EntityCandidateRegistry = field(default_factory=EntityCandidateRegistry)

    def get_all_names(self) -> List[Tuple[str, str]]:
        """Collect (name, category) pairs for glossary pre-translation.

        Categories: ``character``, ``location``, ``quest``, ``item``,
        ``organization``, ``unknown``.
        """
        out: List[Tuple[str, str]] = []

        for _tag, npc in sorted(self.npcs.items()):
            parts = [p for p in (npc.first_name, npc.last_name) if p and str(p).strip()]
            full = " ".join(parts).strip()
            if full:
                out.append((full, "character"))
            elif npc.first_name and str(npc.first_name).strip():
                out.append((str(npc.first_name).strip(), "character"))

        for _tag, name in sorted(self.areas.items()):
            if name and str(name).strip():
                out.append((str(name).strip(), "location"))

        for _tag, name in sorted(self.quests.items()):
            if name and str(name).strip():
                out.append((str(name).strip(), "quest"))

        for _tag, name in sorted(self.items.items()):
            if name and str(name).strip():
                out.append((str(name).strip(), "item"))

        for name, category in self.extracted_names:
            n = (name or "").strip()
            if n:
                out.append((n, category or "unknown"))

        return out

    def get_glossary_names(self) -> List[Tuple[str, str]]:
        """Return curated/evidence-backed glossary candidates when available."""
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
        """Format the world context as a concise text block for the system prompt.

        Args:
            glossary: If set, append canonical translations next to matching English names.
            target_lang: Short label for those hints (e.g. ``russian`` → ``RUS``).
            source_texts: When provided, only entities whose name or tag is
                relevant (exact / prefix>=4 / Damerau-Levenshtein<=1 on
                tokens>=6 chars) to the source corpus are emitted.  Empty
                category sections are dropped entirely.  ``None`` returns
                the full block (used by glossary build and other callers
                that need a complete view).

        Returns:
            Formatted string containing necessary context.
        """
        from .relevance import (
            common_hierarchy_components,
            hierarchical_entry_passes,
            is_relevant,
            tokenize_corpus,
        )

        source_tokens = tokenize_corpus(source_texts) if source_texts is not None else None
        source_joined = "\n".join(str(t) for t in source_texts or [] if t).casefold()

        all_hierarchy_names: List[str] = []
        all_hierarchy_names.extend(self.areas.values())
        all_hierarchy_names.extend(self.quests.values())
        all_hierarchy_names.extend(self.items.values())
        common_components = (
            common_hierarchy_components(all_hierarchy_names) if source_tokens is not None else set()
        )

        def _keep(*candidates: str, category: str = "") -> bool:
            if source_tokens is None:
                return True
            joined = " ".join(c for c in candidates if c)
            if not joined:
                return False
            primary = candidates[0] if candidates else ""
            if primary and is_generic_entity_label(primary, category):
                # Generic labels (e.g. ``Human Female``, ``Almraiven Resident``)
                # are shared across many indistinguishable NPCs. Even when the
                # label happens to appear in the source as a descriptive phrase,
                # admitting all carriers floods the prompt with noise without
                # adding translation evidence — drop them entirely.
                return False
            if not is_relevant(joined, source_tokens):
                return False
            if primary:
                if not hierarchical_entry_passes(
                    primary, source_joined, source_tokens, common_components
                ):
                    return False
            return True

        def _score(name: str, tag: str, category: str) -> int:
            if source_tokens is None:
                return 0
            filter_result = classify_entity_candidate(name, category)
            if filter_result.decision == "drop":
                return -1000
            name_key = (name or "").casefold()
            tag_key = (tag or "").casefold()
            generic = is_generic_entity_label(name, category)
            if generic:
                if not (name_key and name_key in source_joined):
                    return -1000
                # Tag/speaker-id matches do not count as evidence for generics.
                return 100
            score = 0
            if name_key and name_key in source_joined:
                score += 1000
            if tag_key and tag_key in source_joined:
                score += 900
            if filter_result.decision == "deprioritize":
                score -= 500
            return score

        budget_remaining = {
            "entries": WORLD_CONTEXT_MAX_ENTRIES,
            "chars": WORLD_CONTEXT_MAX_CHARS,
        }

        def _budget_lines(scored_lines: List[Tuple[int, str]]) -> List[str]:
            if source_tokens is None:
                return [line for _score_value, line in scored_lines]
            selected: List[str] = []
            for _score_value, line in sorted(scored_lines, key=lambda x: (-x[0], x[1].lower())):
                if _score_value < 0:
                    continue
                if budget_remaining["entries"] <= 0:
                    break
                next_chars = budget_remaining["chars"] - len(line) - 1
                if selected and next_chars < 0:
                    break
                selected.append(line)
                budget_remaining["entries"] -= 1
                budget_remaining["chars"] = max(0, next_chars)
            return selected

        lines = []
        lines.append("WORLD CONTEXT:")
        lang_lbl = self._label_for_target_lang(target_lang)

        def _gloss_suffix(en_name: str) -> str:
            if not glossary or not glossary.entries:
                return ""
            tr = glossary.entries.get(en_name.strip())
            if not tr:
                return ""
            return f" [{lang_lbl}: {tr}]"

        npc_scored_lines: List[Tuple[int, str]] = []
        for tag, npc in sorted(self.npcs.items()):
            name_parts = [npc.first_name, npc.last_name]
            full_name = " ".join(p for p in name_parts if p).strip() or tag
            if not _keep(full_name, tag, category="character"):
                continue
            gloss = ""
            if full_name != tag:
                gloss = _gloss_suffix(full_name)

            desc_parts = []
            if npc.race:
                desc_parts.append(npc.race)
            if npc.gender:
                desc_parts.append(npc.gender)

            traits_str = f" ({', '.join(desc_parts)})" if desc_parts else ""

            npc_line = f"  * [{tag}] {full_name}{traits_str}{gloss}"

            desc = (npc.description or "").strip()
            if desc:
                npc_line += f" - {desc}"

            npc_scored_lines.append((_score(full_name, tag, "character"), npc_line))
        npc_lines = _budget_lines(npc_scored_lines)
        if npc_lines:
            lines.append("- KEY CHARACTERS IN THE GAME:")
            lines.extend(npc_lines)

        area_lines = _budget_lines(
            [
                (_score(name, tag, "location"), f"  * {name} (Tag: {tag}){_gloss_suffix(name)}")
                for tag, name in sorted(self.areas.items())
                if _keep(name, tag, category="location")
            ]
        )
        if area_lines:
            lines.append("- LOCATIONS:")
            lines.extend(area_lines)

        quest_lines = _budget_lines(
            [
                (_score(name, tag, "quest"), f"  * {name} (Tag: {tag}){_gloss_suffix(name)}")
                for tag, name in sorted(self.quests.items())
                if _keep(name, tag, category="quest")
            ]
        )
        if quest_lines:
            lines.append("- QUESTS:")
            lines.extend(quest_lines)

        item_lines = _budget_lines(
            [
                (_score(name, tag, "item"), f"  * {name} (Tag: {tag}){_gloss_suffix(name)}")
                for tag, name in sorted(self.items.items())
                if _keep(name, tag, category="item")
            ]
        )
        if item_lines:
            lines.append("- KEY ITEMS:")
            lines.extend(item_lines)

        # If filtering produced no entries at all, suppress the lone header.
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    @staticmethod
    def _label_for_target_lang(target_lang: Optional[str]) -> str:
        """Short label for inline glossary hints (e.g. RUS, ENG)."""
        if not target_lang or not str(target_lang).strip():
            return "TL"
        t = str(target_lang).strip()
        if len(t) <= 4:
            return t.upper()
        return t[:3].upper()


class WorldScanner:
    """Scans an extracted module directory to build a WorldContext."""

    def __init__(self):
        """Initialize the scanner."""

    def scan_directory(
        self,
        extract_dir: Path,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]] = None,
        progress_callback=None,
    ) -> WorldContext:
        """Scan the directory and build world context.

        Args:
            extract_dir: Path to directory containing extracted module files.
            gff_cache: Optional shared parse cache (same object as ModuleTranslator).

        Returns:
            Populated WorldContext.
        """
        logger.info("Scanning module for world context...")
        context = WorldContext()

        count_npcs = 0
        count_areas = 0
        count_quests = 0
        count_items = 0

        # Collect scannable files first for progress reporting
        scannable_exts = {".utc", ".are", ".jrl", ".uti"}
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

            try:
                if ext == ".utc":
                    if self._process_utc(file_path, context, gff_cache):
                        count_npcs += 1
                elif ext == ".are":
                    if self._process_are(file_path, context, gff_cache):
                        count_areas += 1
                elif ext == ".jrl":
                    count_quests += self._process_jrl(file_path, context, gff_cache)
                elif ext == ".uti":
                    if self._process_uti(file_path, context, gff_cache):
                        count_items += 1
            except Exception as e:
                logger.debug("Failed to scan context from %s: %s", file_path.name, e)

        logger.info(
            "World context built: %d NPCs, %d locations, %d quests, %d items",
            count_npcs,
            count_areas,
            count_quests,
            count_items,
        )
        return context

    def _get_local_string(self, data: Dict[str, Any], key: str) -> str:
        """Extract text from a CExoLocString field in parsed GFF data.

        Args:
            data: Parsed GFF struct dict.
            key: Field name (e.g. ``"FirstName"``, ``"LocalizedName"``).

        Returns:
            Extracted string, or empty string if not found.
        """
        obj = data.get(key, {})
        return extract_local_string(obj) or ""

    def _process_utc(
        self,
        file_path: Path,
        context: WorldContext,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    ) -> bool:
        """Extract NPC data from a .utc (Creature) file into *context*.

        Args:
            file_path: Path to the .utc file.
            context: World context to populate.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the NPC was added to the context.
        """
        data = read_gff(file_path, cache=gff_cache)
        tag = data.get("Tag", "")
        if not tag:
            return False

        first_name = self._get_local_string(data, "FirstName")
        last_name = self._get_local_string(data, "LastName")
        desc = self._get_local_string(data, "Description")

        # In NWN: Race and Gender are IDs. We could map them to strings,
        # but for prompt context, just capturing them if available is good.
        # These are usually ints. Let's do a basic mapping for common ones.
        race_id = data.get("Race", -1)
        gender_id = data.get("Gender", -1)
        conversation = data.get("Conversation", "")

        race_str = race_label(race_id) or "Creature"
        gender_str = gender_label(gender_id)

        # Only add to context if it has a conversation or a description,
        # otherwise we might fill context window with generic monsters.
        # But for unique names, it's also worth keeping.
        if conversation or desc or first_name:
            context.npcs[tag] = NPCInfo(
                tag=tag,
                first_name=first_name,
                last_name=last_name,
                description=desc,
                race=race_str,
                gender=gender_str,
                conversation=conversation,
            )
            full_name = " ".join(p for p in (first_name, last_name) if p).strip()
            if full_name:
                context.candidates.add(
                    full_name,
                    category="character",
                    source="utc_name",
                    resource=file_path.name,
                    field="FirstName/LastName",
                    context=desc,
                    is_speaker_or_dialog_actor=bool(conversation),
                )
            return True
        return False

    def _process_are(
        self,
        file_path: Path,
        context: WorldContext,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    ) -> bool:
        """Extract area name from an .are (Area) file into *context*.

        Args:
            file_path: Path to the .are file.
            context: World context to populate.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the area was added to the context.
        """
        data = read_gff(file_path, cache=gff_cache)
        tag = data.get("Tag", "")
        name = self._get_local_string(data, "Name")

        if tag and name:
            context.areas[tag] = name
            context.candidates.add(
                name,
                category="location",
                source="are_name",
                resource=file_path.name,
                field="Name",
            )
            return True
        return False

    def _process_jrl(
        self,
        file_path: Path,
        context: WorldContext,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    ) -> int:
        """Extract quest names from a .jrl (Journal) file into *context*.

        Args:
            file_path: Path to the .jrl file.
            context: World context to populate.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            Number of quests added to the context.
        """
        data = read_gff(file_path, cache=gff_cache)
        categories = data.get("Categories", [])

        added = 0
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            tag = cat.get("Tag", "")
            name = self._get_local_string(cat, "Name")
            if tag and name:
                context.quests[tag] = name
                context.candidates.add(
                    name,
                    category="quest",
                    source="jrl_category",
                    resource=file_path.name,
                    field="Name",
                )
                added += 1

        return added

    def _process_uti(
        self,
        file_path: Path,
        context: WorldContext,
        gff_cache: Optional[Dict[Path, Dict[str, Any]]],
    ) -> bool:
        """Extract item name from a .uti (Item) file into *context*.

        Args:
            file_path: Path to the .uti file.
            context: World context to populate.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the item was added to the context.
        """
        data = read_gff(file_path, cache=gff_cache)
        tag = data.get("Tag", "")
        name = self._get_local_string(data, "LocalizedName")

        # Only add uniquely-tagged items or items with descriptions
        # to avoid blowing up the context window with generic items.
        # For simplicity, we filter out common ones or generic tags if needed.
        # For now, if it has a LocalizedName and Tag, add it.
        if tag and name:
            context.items[tag] = name
            context.candidates.add(
                name,
                category="item",
                source="uti_name",
                resource=file_path.name,
                field="LocalizedName",
            )
            return True
        return False
