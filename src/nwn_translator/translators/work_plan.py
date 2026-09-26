"""Planning of batch translation requests: what is sent alone, together or not at all.

The plan is a pure function of the prepared strings. Strings reduced to
placeholders and punctuation need no request; long strings are sent one per
request with their full context; everything else is packed, structural group by
structural group, into batch requests that stay within :class:`BatchLimits`.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

from ..ai_providers import TranslationItem
from ..ai_providers.batch_payload import batch_payload_chars
from ..extractors.base import Occurrence, TranslatableItem
from ..glossary import GLOSSARY_MAX_CHARS
from ..prompts._builder import (
    CONTENT_PROFILE_DEFAULT,
    CONTENT_PROFILE_SCRIPT_MESSAGE,
    CONTENT_PROFILE_SHORT_LABEL,
)
from .ncs_diagnostics import is_ncs_item
from .token_handler import TokenHandler, TokenMismatchReport, has_translatable_content

#: Terminology lookup: the glossary block for some texts, or None when nothing matches.
Terminology = Callable[[Iterable[Optional[str]]], Optional[str]]

#: Item types that are names or labels; a batch of only these uses the compact
#: ``short_label`` prompt.
SHORT_LABEL_TYPES = frozenset(
    {
        "creature_first_name",
        "creature_last_name",
        "item_name",
        "area_name",
        "trigger_name",
        "placeable_name",
        "door_name",
        "store_name",
        "waypoint_name",
        "waypoint_map_note",
        "journal_category_name",
    }
)


@dataclass(frozen=True)
class BatchLimits:
    """Budgets of one batch request.

    Character counts are conservative proxies for input and output size, not
    token counts; the provider still narrows answers that come back short.

    Attributes:
        max_items: Strings per batch.
        text_chars: Total sanitized characters per batch; a longer string is
            sent on its own.
        payload_chars: Serialized batch payload characters.
        ncs_item_chars: Longest script string that may join a batch; longer
            ones are sent alone with their full context.
        glossary_chars: Glossary block characters per batch.
    """

    max_items: int = 60
    text_chars: int = 6000
    payload_chars: int = 12000
    ncs_item_chars: int = 1000
    glossary_chars: int = GLOSSARY_MAX_CHARS


@dataclass
class WorkItem:
    """One string occurrence prepared for translation.

    Attributes:
        item: The occurrence (a copy whose metadata names its batch resource).
        sanitized: Text sent to the model.
        handler: Restores and validates the model's answer.
        mismatch: Validation report of the last rejected answer, if any.
    """

    item: TranslatableItem
    sanitized: str
    handler: TokenHandler
    mismatch: Optional[TokenMismatchReport] = None

    @property
    def key(self) -> Occurrence:
        """Occurrence address of the item."""
        return self.item.key

    @property
    def is_ncs(self) -> bool:
        """Whether the item is a string literal of a compiled script."""
        return is_ncs_item(self.item)

    @property
    def profile(self) -> str:
        """Content profile of a request for this item alone."""
        return content_profile([self])

    def translation_item(self) -> TranslationItem:
        """Return the batch entry for this item."""
        return TranslationItem(self.sanitized, self.item.context, self.item.metadata)


@dataclass
class WorkPlan:
    """Requests needed for a set of distinct strings.

    Attributes:
        passthrough: Strings with nothing to translate, in input order.
        singles: Strings sent one per request, in input order.
        batches: Batch requests, each in payload order.
    """

    passthrough: List[WorkItem] = field(default_factory=list)
    singles: List[WorkItem] = field(default_factory=list)
    batches: List[List[WorkItem]] = field(default_factory=list)


def content_profile(work: Sequence[WorkItem]) -> str:
    """Return the prompt profile for a request carrying *work*.

    The choice depends only on the mix of item types, so every profile keeps a
    stable prompt prefix for provider caches.

    Args:
        work: Items of one request.

    Returns:
        ``script_message`` when all are script strings, ``short_label`` when all
        are names or labels, else ``default``.
    """
    if work and all(w.is_ncs for w in work):
        return CONTENT_PROFILE_SCRIPT_MESSAGE
    if work and all(w.item.metadata.get("type", "") in SHORT_LABEL_TYPES for w in work):
        return CONTENT_PROFILE_SHORT_LABEL
    return CONTENT_PROFILE_DEFAULT


def dedup_key(work: WorkItem, terminology: Terminology) -> Tuple[Hashable, ...]:
    """Return the key under which equal requests share one answer.

    Only identical text with the same context, profile, hint and terminology may
    share an answer; every occurrence keeps its own address.

    Args:
        work: Prepared item.
        terminology: Glossary lookup of the run.

    Returns:
        A hashable request identity.
    """
    item = work.item
    return (
        work.sanitized,
        item.context or "",
        item.metadata.get("shared_context", ""),
        work.profile,
        item.metadata.get("hint") or item.metadata.get("ncs_hint") or item.metadata.get("type", ""),
        terminology([item.text, item.context]),
    )


def is_batchable(work: WorkItem, limits: BatchLimits) -> bool:
    """Return whether *work* is short enough to share a batch request."""
    limit = limits.ncs_item_chars if work.is_ncs else limits.text_chars
    return len(work.sanitized) <= limit


def batch_terminology(batch: Sequence[WorkItem], terminology: Terminology) -> Optional[str]:
    """Return the glossary block of a batch: terms of every text and context in it."""
    return terminology(text for w in batch for text in (w.sanitized, w.item.context))


def plan_work(work: Sequence[WorkItem], limits: BatchLimits, terminology: Terminology) -> WorkPlan:
    """Split distinct strings into passthrough, single and batch requests.

    Batchable strings are grouped by structural group (``translation_group``
    within a resource; ungrouped strings stand alone) in first-seen order; script
    groups are ordered by bytecode offset. Groups are packed per content profile,
    in first-seen profile order.

    Args:
        work: Distinct strings, in input order.
        limits: Batch budgets.
        terminology: Glossary lookup of the run.

    Returns:
        The requests to make.
    """
    plan = WorkPlan()
    groups: Dict[Hashable, List[WorkItem]] = {}
    for w in work:
        if not has_translatable_content(w.sanitized):
            plan.passthrough.append(w)
        elif not is_batchable(w, limits):
            plan.singles.append(w)
        else:
            group = w.item.metadata.get("translation_group")
            key = (w.key[0], "group", group) if group is not None else ("item", w.key)
            groups.setdefault(key, []).append(w)
    families: Dict[str, List[List[WorkItem]]] = {}
    for members in groups.values():
        if members[0].is_ncs:
            members.sort(key=lambda w: w.item.metadata.get("offset", 0))
        families.setdefault(content_profile(members), []).append(members)
    plan.batches = [
        batch for family in families.values() for batch in pack_groups(family, limits, terminology)
    ]
    return plan


def pack_groups(
    groups: Sequence[List[WorkItem]], limits: BatchLimits, terminology: Terminology
) -> List[List[WorkItem]]:
    """Pack structural groups into batches, keeping a group whole when it fits.

    A group that does not fit an empty batch is split into units: its name
    fields together first (an NPC's first and last name stay in one request),
    then every other field alone. A unit over budget on its own still gets a
    request of its own.

    Args:
        groups: Groups of one content profile, in order.
        limits: Batch budgets.
        terminology: Glossary lookup of the run.

    Returns:
        Batches in order; together they hold every item exactly once.
    """

    def fits(batch: List[WorkItem]) -> bool:
        return (
            len(batch) <= limits.max_items
            and sum(len(w.sanitized) for w in batch) <= limits.text_chars
            and batch_payload_chars([w.translation_item() for w in batch]) <= limits.payload_chars
            and len(batch_terminology(batch, terminology) or "") <= limits.glossary_chars
        )

    batches: List[List[WorkItem]] = []
    current: List[WorkItem] = []
    for group in groups:
        if fits(current + group):
            current.extend(group)
            continue
        if current:
            batches.append(current)
            current = []
        if fits(group):
            current = list(group)
            continue
        names = [w for w in group if w.item.metadata.get("name_fields")]
        units = ([names] if names else []) + [
            [w] for w in group if not w.item.metadata.get("name_fields")
        ]
        for unit in units:
            if current and not fits(current + unit):
                batches.append(current)
                current = []
            current.extend(unit)
    if current:
        batches.append(current)
    return batches
