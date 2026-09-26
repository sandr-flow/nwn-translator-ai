"""Editor row model: how the translation rows of a task become editable rows.

A dialog line keeps its own row, because the same text can have different
speakers. Elsewhere identical lines of one file share a row unless they got
different translations; an edit of such a row applies to every line it stands
for. All functions are pure and work on rows as returned by
:func:`~nwn_translator.web.database.get_translations_by_task`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .schemas import DialogSpeaker, RebuildEdit, TranslationFileGroup, TranslationItem

#: One stored translation row.
Row = Dict[str, Any]
#: Identity of an editor row within its file.
RowKey = Tuple[str, ...]

#: Context the dialog extractor gives a line with an explicit ``Speaker`` tag.
_TAGGED_DIALOG_LINE_RE = re.compile(r"\(speaker: (.+)\)$")


def _is_dialog(filename: str) -> bool:
    """Whether *filename* is a dialog resource."""
    return filename.lower().endswith(".dlg")


def dialog_speaker(row: Row) -> Optional[DialogSpeaker]:
    """Returns the speaker label of a dialog row.

    Rows stored before speakers were recorded have only the extractor's context
    string, which still tells player replies, tagged lines and owner lines apart.

    Args:
        row: Translation row of a ``.dlg`` file.

    Returns:
        The speaker, or ``None`` when neither the row nor its context names one.
    """
    if row.get("speaker"):
        return DialogSpeaker.model_validate(row["speaker"])
    context = row.get("context") or ""
    if context.startswith("Player reply in "):
        return DialogSpeaker(kind="player")
    tagged = _TAGGED_DIALOG_LINE_RE.search(context)
    if tagged:
        return DialogSpeaker(kind="npc", tag=tagged.group(1))
    if context.startswith("NPC dialog line in "):
        return DialogSpeaker(kind="owner_unknown")
    return None


def row_key(filename: str, row: Row) -> RowKey:
    """Returns the editor row a translation belongs to within its file.

    Args:
        filename: Resource file of the row.
        row: Translation row.

    Returns:
        ``("node", item_id)`` for a dialog line with an ``item_id``, otherwise
        ``("text", original, translated)``.
    """
    item_id = row.get("item_id") or ""
    if _is_dialog(filename) and item_id:
        return ("node", item_id)
    return ("text", row["original"], row["translated"])


def group_rows(rows: Iterable[Row]) -> List[TranslationFileGroup]:
    """Groups translation rows into editor rows, per file in first-seen order.

    A row that stands for several identical lines lists the other ``item_id``
    values in ``duplicate_item_ids`` and is marked failed when any of them
    failed. ``shared_with`` names the other files that contain the same original.
    Rows without an original text are skipped.

    Args:
        rows: Translation rows of one task.

    Returns:
        One group per file.
    """
    groups: Dict[str, Dict[RowKey, TranslationItem]] = {}
    files_by_text: Dict[str, List[str]] = {}
    for row in rows:
        original = row["original"]
        if not original:
            continue
        filename = row.get("file") or "unknown"
        item_id = row.get("item_id") or ""
        failed = row.get("success", 1) in (0, False, "0")
        file_rows = groups.setdefault(filename, {})
        key = row_key(filename, row)
        item = file_rows.get(key)
        if item is not None:
            if item_id and item_id != item.item_id and item_id not in item.duplicate_item_ids:
                item.duplicate_item_ids.append(item_id)
            item.failed = item.failed or failed
            continue
        file_rows[key] = TranslationItem(
            original=original,
            translated=row["translated"],
            item_id=item_id,
            failed=failed,
            speaker=dialog_speaker(row) if _is_dialog(filename) else None,
        )
        files = files_by_text.setdefault(original, [])
        if filename not in files:
            files.append(filename)

    for filename, file_rows in groups.items():
        for item in file_rows.values():
            files = files_by_text[item.original]
            if len(files) > 1:
                item.shared_with = [f for f in files if f != filename]
    return [
        TranslationFileGroup(filename=filename, items=list(file_rows.values()))
        for filename, file_rows in groups.items()
    ]


def expand_edits(rows: Iterable[Row], edits: Sequence[RebuildEdit]) -> Dict[Tuple[str, str], str]:
    """Maps each edit to every ``(file, item_id)`` its editor row stands for.

    An edit whose ``(file, item_id)`` has no stored row applies to that item alone.

    Args:
        rows: Translation rows of the task.
        edits: Edits from the editor, each addressing one row by an item of it.

    Returns:
        ``{(file, item_id): translated}``; a later edit of the same item wins.
    """
    items_of_row: Dict[Tuple[str, RowKey], List[str]] = {}
    row_of_item: Dict[Tuple[str, str], Tuple[str, RowKey]] = {}
    for row in rows:
        filename = row.get("file") or "unknown"
        item_id = row.get("item_id") or ""
        if not row["original"] or not item_id:
            continue
        editor_row = (filename, row_key(filename, row))
        items_of_row.setdefault(editor_row, []).append(item_id)
        row_of_item[(filename, item_id)] = editor_row

    expanded: Dict[Tuple[str, str], str] = {}
    for edit in edits:
        edited_row = row_of_item.get((edit.file, edit.item_id))
        for item_id in items_of_row[edited_row] if edited_row is not None else [edit.item_id]:
            expanded[(edit.file, item_id)] = edit.translated
    return expanded
