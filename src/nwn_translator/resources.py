"""Registry of the translatable resource kinds.

Each file extension maps to how the resource is loaded, which extractor selects
its strings and which injector writes the translations back. A new translatable
kind is an extractor plus one entry in :data:`RESOURCE_KINDS`.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Optional

from .extractors.base import BaseExtractor
from .extractors.creature_extractor import CreatureExtractor
from .extractors.dialog_extractor import DialogExtractor
from .extractors.git_extractor import GitExtractor
from .extractors.item_extractor import ItemExtractor
from .extractors.journal_extractor import JournalExtractor
from .extractors.ncs_extractor import NcsExtractor
from .extractors.simple_extractors import (
    AreaExtractor,
    DoorExtractor,
    EncounterExtractor,
    ModuleExtractor,
    PlaceableExtractor,
    StoreExtractor,
    TriggerExtractor,
)
from .formats.gff import read_gff
from .formats.ncs import NCSParseError, parse_ncs
from .injectors.base import Injector
from .injectors.gff_injector import inject_gff
from .injectors.ncs_injector import inject_ncs

logger = logging.getLogger(__name__)

#: Per-run parse cache of GFF files: resolved path -> parsed root struct.
GffCache = Dict[Path, Dict[str, Any]]

#: ``(path, gff_cache, source_encoding) -> parsed data``, or None to skip the file.
Loader = Callable[[Path, Optional[GffCache], Optional[str]], Optional[Dict[str, Any]]]


@dataclass(frozen=True)
class ResourceKind:
    """How one resource kind is loaded, extracted and patched; GFF by default.

    Attributes:
        extractor: Selects the translatable strings of a loaded resource.
        load: Reads the resource; returns ``None`` when it must be skipped.
        inject: Writes translations back into the resource file.
    """

    extractor: BaseExtractor
    load: Loader = read_gff
    inject: Injector = inject_gff


def load_ncs(
    path: Path, _gff_cache: Optional[GffCache], source_encoding: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Parses a compiled script into the dict :class:`NcsExtractor` expects.

    Returns:
        ``{"_ncs_file": NCSFile, "_source_encoding": source_encoding}``, or ``None`` when
        the script cannot be parsed.
    """
    try:
        ncs_file = parse_ncs(path, source_encoding=source_encoding)
    except NCSParseError as e:
        logger.debug("Skipping unparseable NCS file %s: %s", path.name, e)
        return None
    return {"_ncs_file": ncs_file, "_source_encoding": source_encoding}


#: Lowercase file extension -> resource kind.
RESOURCE_KINDS: Dict[str, ResourceKind] = {
    ".dlg": ResourceKind(DialogExtractor()),
    ".jrl": ResourceKind(JournalExtractor()),
    ".uti": ResourceKind(ItemExtractor()),
    ".utc": ResourceKind(CreatureExtractor()),
    ".are": ResourceKind(AreaExtractor()),
    ".utt": ResourceKind(TriggerExtractor()),
    ".utp": ResourceKind(PlaceableExtractor()),
    ".utd": ResourceKind(DoorExtractor()),
    ".ute": ResourceKind(EncounterExtractor()),
    ".utm": ResourceKind(StoreExtractor()),
    ".git": ResourceKind(GitExtractor()),
    ".ifo": ResourceKind(ModuleExtractor()),
    ".ncs": ResourceKind(NcsExtractor(), load_ncs, inject_ncs),
}

#: Extensions of the resources the pipeline translates.
TRANSLATABLE_TYPES: FrozenSet[str] = frozenset(RESOURCE_KINDS)
