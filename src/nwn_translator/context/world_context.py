"""World context scanner for contextual translation.

This module provides tools to scan a module's extracted files before translation
begins, collecting a registry of NPCs, areas, items, and quests. This data is
then fed into the AI system prompt to provide world context and improve
translation coherence.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..file_handlers import read_gff
from ..file_handlers.tlk_reader import TLKFile
from ..extractors.base import BaseExtractor

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

    def to_prompt_block(self) -> str:
        """Format the world context as a concise text block for the system prompt.
        
        Returns:
            Formatted string containing necessary context.
        """
        lines = []
        lines.append("WORLD CONTEXT:")
        
        if self.npcs:
            lines.append("- KEY CHARACTERS IN THE GAME:")
            # Sort to ensure stable prompt
            for tag, npc in sorted(self.npcs.items()):
                name_parts = [npc.first_name, npc.last_name]
                full_name = " ".join(p for p in name_parts if p).strip() or tag
                
                desc_parts = []
                if npc.race:
                    desc_parts.append(npc.race)
                if npc.gender:
                    desc_parts.append(npc.gender)
                    
                traits_str = f" ({', '.join(desc_parts)})" if desc_parts else ""
                
                npc_line = f"  * [{tag}] {full_name}{traits_str}"
                
                # Truncate description if it's too long to save context window tokens
                desc = (npc.description or "").strip()
                if desc:
                    # Take first 150 chars or up to first newline
                    short_desc = desc.split("\n")[0]
                    if len(short_desc) > 150:
                        short_desc = short_desc[:147] + "..."
                    npc_line += f" - {short_desc}"
                    
                lines.append(npc_line)

        if self.areas:
            lines.append("- LOCATIONS:")
            for tag, name in sorted(self.areas.items()):
                lines.append(f"  * {name} (Tag: {tag})")

        if self.quests:
            lines.append("- QUESTS:")
            for tag, name in sorted(self.quests.items()):
                lines.append(f"  * {name} (Tag: {tag})")

        if self.items:
            lines.append("- KEY ITEMS:")
            for tag, name in sorted(self.items.items()):
                lines.append(f"  * {name} (Tag: {tag})")

        return "\n".join(lines)


class WorldScanner:
    """Scans an extracted module directory to build a WorldContext."""

    def __init__(self):
        """Initialize the scanner."""
        # Create a concrete subclass to use parser methods
        class DummyExtractor(BaseExtractor):
            def can_extract(self, t): return False
            def extract(self, p, d): return None
            
        self._extractor_helper = DummyExtractor()

    def scan_directory(
        self,
        extract_dir: Path,
        tlk: Optional[TLKFile] = None,
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]] = None,
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

        # Scan all relevant files. Order doesn't matter too much,
        # but we'll do an rglob for speed.
        for file_path in extract_dir.rglob("*"):
            if not file_path.is_file():
                continue

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
        """Helper to extract text from a CExoLocString field."""
        obj = data.get(key, {})
        return self._extractor_helper._extract_text_from_local_string(obj) or ""

    def _process_utc(
        self,
        file_path: Path,
        context: WorldContext,
        tlk: Optional[TLKFile],
        gff_cache: Optional[Dict[Tuple[Path, int], Dict[str, Any]]],
    ) -> bool:
        """Extract data from a .utc (Creature) file."""
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

        race_map = {
            0: "Dwarf", 1: "Elf", 2: "Gnome", 3: "Halfling", 4: "Half-Elf", 5: "Half-Orc", 6: "Human"
        }
        gender_map = {0: "Male", 1: "Female", 2: "Both", 3: "Other", 4: "None"}

        race_str = race_map.get(race_id, "Creature")
        gender_str = gender_map.get(gender_id, "")

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
        """Extract data from an .are (Area) file."""
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
        """Extract quest names from a .jrl (Journal) file."""
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
        """Extract data from a .uti (Item) file."""
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
