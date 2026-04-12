"""World context scanner for contextual translation.

This module provides tools to scan a module's extracted files before translation
begins, collecting a registry of NPCs, areas, items, and quests. This data is
then fed into the AI system prompt to provide world context and improve
translation coherence.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..file_handlers import read_gff
from ..file_handlers.tlk_reader import TLKFile
from ..extractors.base import extract_local_string
from ..nwn_constants import race_label, gender_label

if TYPE_CHECKING:
    from ..glossary import Glossary

logger = logging.getLogger(__name__)


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

    def to_prompt_block(
        self,
        glossary: Optional["Glossary"] = None,
        target_lang: Optional[str] = None,
    ) -> str:
        """Format the world context as a concise text block for the system prompt.

        Args:
            glossary: If set, append canonical translations next to matching English names.
            target_lang: Short label for those hints (e.g. ``russian`` → ``RUS``).

        Returns:
            Formatted string containing necessary context.
        """
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

        if self.npcs:
            lines.append("- KEY CHARACTERS IN THE GAME:")
            # Sort to ensure stable prompt
            for tag, npc in sorted(self.npcs.items()):
                name_parts = [npc.first_name, npc.last_name]
                full_name = " ".join(p for p in name_parts if p).strip() or tag
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
                    
                lines.append(npc_line)

        if self.areas:
            lines.append("- LOCATIONS:")
            for tag, name in sorted(self.areas.items()):
                lines.append(
                    f"  * {name} (Tag: {tag}){_gloss_suffix(name)}"
                )

        if self.quests:
            lines.append("- QUESTS:")
            for tag, name in sorted(self.quests.items()):
                lines.append(
                    f"  * {name} (Tag: {tag}){_gloss_suffix(name)}"
                )

        if self.items:
            lines.append("- KEY ITEMS:")
            for tag, name in sorted(self.items.items()):
                lines.append(
                    f"  * {name} (Tag: {tag}){_gloss_suffix(name)}"
                )

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
        tlk: Optional[TLKFile] = None,
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]] = None,
        progress_callback=None,
    ) -> WorldContext:
        """Scan the directory and build world context.

        Args:
            extract_dir: Path to directory containing extracted module files.
            tlk: Optional TLK for StrRef resolution (should match translation reads).
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
            f for f in extract_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in scannable_exts
        ]

        for idx, file_path in enumerate(scan_files):
            if progress_callback and idx % 20 == 0:
                progress_callback(
                    "scanning", idx, len(scan_files),
                    f"Scanning {file_path.name}",
                )

            ext = file_path.suffix.lower()

            try:
                if ext == ".utc":
                    if self._process_utc(file_path, context, tlk, gff_cache):
                        count_npcs += 1
                elif ext == ".are":
                    if self._process_are(file_path, context, tlk, gff_cache):
                        count_areas += 1
                elif ext == ".jrl":
                    count_quests += self._process_jrl(file_path, context, tlk, gff_cache)
                elif ext == ".uti":
                    if self._process_uti(file_path, context, tlk, gff_cache):
                        count_items += 1
            except Exception as e:
                logger.debug("Failed to scan context from %s: %s", file_path.name, e)

        logger.info(
            "World context built: %d NPCs, %d locations, %d quests, %d items",
            count_npcs, count_areas, count_quests, count_items
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
        tlk: Optional[TLKFile],
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
    ) -> bool:
        """Extract NPC data from a .utc (Creature) file into *context*.

        Args:
            file_path: Path to the .utc file.
            context: World context to populate.
            tlk: Optional TLK for StrRef resolution.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the NPC was added to the context.
        """
        data = read_gff(file_path, tlk=tlk, cache=gff_cache)
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
                conversation=conversation
            )
            return True
        return False

    def _process_are(
        self,
        file_path: Path,
        context: WorldContext,
        tlk: Optional[TLKFile],
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
    ) -> bool:
        """Extract area name from an .are (Area) file into *context*.

        Args:
            file_path: Path to the .are file.
            context: World context to populate.
            tlk: Optional TLK for StrRef resolution.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the area was added to the context.
        """
        data = read_gff(file_path, tlk=tlk, cache=gff_cache)
        tag = data.get("Tag", "")
        name = self._get_local_string(data, "Name")
        
        if tag and name:
            context.areas[tag] = name
            return True
        return False

    def _process_jrl(
        self,
        file_path: Path,
        context: WorldContext,
        tlk: Optional[TLKFile],
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
    ) -> int:
        """Extract quest names from a .jrl (Journal) file into *context*.

        Args:
            file_path: Path to the .jrl file.
            context: World context to populate.
            tlk: Optional TLK for StrRef resolution.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            Number of quests added to the context.
        """
        data = read_gff(file_path, tlk=tlk, cache=gff_cache)
        categories = data.get("Categories", [])
        
        added = 0
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            tag = cat.get("Tag", "")
            name = self._get_local_string(cat, "Name")
            if tag and name:
                context.quests[tag] = name
                added += 1
                
        return added

    def _process_uti(
        self,
        file_path: Path,
        context: WorldContext,
        tlk: Optional[TLKFile],
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
    ) -> bool:
        """Extract item name from a .uti (Item) file into *context*.

        Args:
            file_path: Path to the .uti file.
            context: World context to populate.
            tlk: Optional TLK for StrRef resolution.
            gff_cache: Optional shared GFF parse cache.

        Returns:
            ``True`` if the item was added to the context.
        """
        data = read_gff(file_path, tlk=tlk, cache=gff_cache)
        tag = data.get("Tag", "")
        name = self._get_local_string(data, "LocalizedName")
        
        # Only add uniquely-tagged items or items with descriptions 
        # to avoid blowing up the context window with generic items.
        # For simplicity, we filter out common ones or generic tags if needed.
        # For now, if it has a LocalizedName and Tag, add it.
        if tag and name:
            context.items[tag] = name
            return True
        return False
