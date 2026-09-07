"""Serialize structural translation groups without repeating shared context."""

import json
from typing import Any, Dict, List

from .base import TranslationItem


def source_windows(entries: List[dict]) -> tuple[List[dict], List[int]]:
    """Merge verified overlapping character ranges from one matching source file.

    Unknown positions are deduplicated only when their complete text is equal.
    Every input excerpt remains recoverable through its window reference.
    """
    windows: List[dict] = []
    refs = [-1] * len(entries)
    ordered = sorted(
        enumerate(entries),
        key=lambda pair: (
            int(pair[1]["nss_start"]) if isinstance(pair[1].get("nss_start"), int) else -1
        ),
    )
    for index, entry in ordered:
        text = entry.get("nss_snippet") or ""
        if not text:
            continue
        start = entry.get("nss_start")
        for number, window in enumerate(windows):
            old_start = window.get("start")
            if isinstance(start, int) and isinstance(old_start, int):
                offset = start - old_start
                overlap = len(window["text"]) - offset
                if (
                    offset >= 0
                    and overlap > 0
                    and (
                        window["text"][offset : offset + min(overlap, len(text))] == text[:overlap]
                    )
                ):
                    window["text"] += text[overlap:]
                    refs[index] = number
                    break
            elif window["text"] == text:
                refs[index] = number
                break
        else:
            refs[index] = len(windows)
            windows.append({"start": start, "text": text})
    return windows, refs


def build_batch_payload(items: List[TranslationItem]) -> Dict[str, Any]:
    """Keep numeric output addresses flat; scope context to resource-local groups."""
    cells: Dict[str, Any] = {}
    groups: Dict[str, Any] = {}
    members: Dict[tuple, List[int]] = {}
    for i, item in enumerate(items):
        meta = item.metadata or {}
        group = meta.get("translation_group") or meta.get("name_group")
        resource = meta.get("batch_resource")
        if group is not None and resource:
            members.setdefault((resource, group), []).append(i)
        hint = meta.get("hint") or meta.get("ncs_hint") or meta.get("type", "")
        context = (
            meta.get("batch_context", item.context)
            if group is not None and resource
            else item.context
        ) or ""
        cell: Dict[str, Any] = {"text": item.original}
        if hint:
            cell["hint"] = hint
        if context.strip():
            cell["context"] = context.strip()
        cells[str(i)] = cell if len(cell) > 1 else item.original

    for (resource, group), indices in members.items():
        group_id = str(len(groups))
        shared: Dict[str, Any] = {"resource": resource, "structure": group}
        contexts = list(
            dict.fromkeys(
                items[i].metadata["shared_context"]
                for i in indices
                if items[i].metadata.get("shared_context")
            )
        )
        if contexts:
            shared["context"] = contexts
        windows, refs = source_windows([items[i].metadata for i in indices])
        if windows:
            shared["matching_source_context_only"] = windows
        targets = {items[i].original for i in indices}
        speech = list(
            dict.fromkeys(
                text
                for i in indices
                for text in items[i].metadata.get("approved_neighbors", [])
                if text not in targets
            )
        )
        if speech:
            shared["approved_speech_context_only"] = speech
        for i, ref in zip(indices, refs):
            if isinstance(cells[str(i)], str):
                cells[str(i)] = {"text": cells[str(i)]}
            cell = cells[str(i)]
            cell["group"] = group_id
            if ref >= 0:
                cell["source_window"] = ref
            # Deduplicate identical complete field contexts within this group.
            context = cell.pop("context", None)
            if context:
                shared.setdefault("field_contexts", [])
                if context not in shared["field_contexts"]:
                    shared["field_contexts"].append(context)
                cell["context_ref"] = shared["field_contexts"].index(context)
        groups[group_id] = shared
    return {"groups": groups, "items": cells} if groups else cells


def batch_payload_chars(items: List[TranslationItem]) -> int:
    """Character budget proxy for the exact serialized input, not a token count."""
    return len(json.dumps(build_batch_payload(items), ensure_ascii=False, separators=(",", ":")))
