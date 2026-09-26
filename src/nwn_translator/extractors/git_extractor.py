"""Extractor for area instance files (``.git``).

Walks the instance lists declared in :mod:`~nwn_translator.extractors.git_fields`,
the inventories nested in instances, the recursive shelves of stores and the
items on the area floor. Every item keeps its field record offset, so the
shared GFF injector patches exactly the extracted fields.
"""

import json
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, NamedTuple, Optional

from ..nwn_constants import gender_label, race_label
from .base import (
    BaseExtractor,
    ExtractedContent,
    TranslatableItem,
    extract_local_string,
    list_field,
    record_offset,
)
from .creature_extractor import name_fields, name_fields_suffix
from .git_fields import (
    AREA_ITEM_LIST_KEY,
    INSTANCE_FIELDS,
    INSTANCE_NESTED_ITEM_LISTS,
    build_npc_index,
    get_module_creature_names,
    item_fields,
    should_translate_git_string,
)

_NPC_NAME_TYPES = frozenset({"creature_first_name", "creature_last_name"})
_INVENTORY = "inventory instance"
_AREA_FLOOR = "placed on the area floor"


class _FieldRef(NamedTuple):
    """A candidate CExoLocString field and how to address it."""

    struct: Dict[str, Any]
    field_name: str
    item_type: str
    context: str
    item_id: str
    group: str


def _dict_rows(struct: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    """Return the struct elements of the list *key*, skipping anything else."""
    return [row for row in list_field(struct, key) if isinstance(row, dict)]


def _item_row_fields(
    row: Dict[str, Any], id_prefix: str, group: str, where: str
) -> Iterator[_FieldRef]:
    """Yield the fields of one inventory row or area floor item."""
    for field_name, item_type, context in item_fields(row, where):
        yield _FieldRef(row, field_name, item_type, context, f"{id_prefix}_{field_name}", group)


def _store_stock_fields(
    node: Dict[str, Any], stem: str, inst_idx: int, path: str
) -> Iterator[_FieldRef]:
    """Yield a store's ``ItemList`` rows, then its nested shelves depth-first."""
    for j, row in enumerate(_dict_rows(node, "ItemList")):
        yield from _item_row_fields(
            row,
            f"{stem}_StoreList_{inst_idx}_{path}_il{j}",
            f"StoreList[{inst_idx}]{path}.ItemList[{j}]",
            _INVENTORY,
        )
    for k, child in enumerate(list_field(node, "StoreList")):
        if isinstance(child, dict):
            yield from _store_stock_fields(child, stem, inst_idx, f"{path}.StoreList[{k}]")


def _area_fields(parsed_data: Dict[str, Any], stem: str) -> Iterator[_FieldRef]:
    """Yield every candidate field of an area in extraction order.

    Each instance's own fields come first, then its nested items; the lists
    follow :data:`INSTANCE_FIELDS` order and the area floor items come last.
    """
    npc_index = build_npc_index(parsed_data)
    for list_key, fields in INSTANCE_FIELDS.items():
        for inst_idx, instance in enumerate(list_field(parsed_data, list_key)):
            if not isinstance(instance, dict):
                continue
            group = f"{list_key}[{inst_idx}]"
            for field in fields:
                yield _FieldRef(
                    instance,
                    field.name,
                    field.item_type,
                    field.context_for(instance, npc_index),
                    f"{stem}_{list_key}_{inst_idx}_{field.name}",
                    group,
                )
            if list_key == "StoreList":
                yield from _store_stock_fields(instance, stem, inst_idx, "")
            for nested_key in INSTANCE_NESTED_ITEM_LISTS.get(list_key, []):
                for j, row in enumerate(_dict_rows(instance, nested_key)):
                    yield from _item_row_fields(
                        row,
                        f"{stem}_{list_key}_{inst_idx}_{nested_key}_{j}",
                        f"{group}.{nested_key}[{j}]",
                        _INVENTORY,
                    )
    for idx, row in enumerate(_dict_rows(parsed_data, AREA_ITEM_LIST_KEY)):
        yield from _item_row_fields(
            row, f"{stem}_{AREA_ITEM_LIST_KEY}_{idx}", f"{AREA_ITEM_LIST_KEY}[{idx}]", _AREA_FLOOR
        )


def _git_item(ref: _FieldRef, known_names: FrozenSet[str]) -> Optional[TranslatableItem]:
    """Build the item for *ref*, or None when it holds no translatable text."""
    text = extract_local_string(ref.struct.get(ref.field_name))
    if text is None or not should_translate_git_string(text, ref.item_type, known_names):
        return None
    context = ref.context
    metadata: Dict[str, Any] = {
        "type": ref.item_type,
        "git_field": ref.field_name,
        "translation_group": ref.group,
    }
    if ref.item_type in _NPC_NAME_TYPES:
        names = name_fields(ref.struct)
        context += name_fields_suffix(names)
        race = race_label(ref.struct.get("Race", -1))
        gender = gender_label(ref.struct.get("Gender", -1))
        metadata.update(
            name_fields=names,
            name_field=ref.field_name,
            name_group=ref.group,
            gender=gender,
            shared_context=(
                f"NPC area instance ({race}, {gender}). Name fields: "
                + json.dumps(names, ensure_ascii=False)
            ),
            batch_context="",
        )
    metadata["record_offset"] = record_offset(ref.struct, ref.field_name)
    return TranslatableItem(text=text, context=context, item_id=ref.item_id, metadata=metadata)


class GitExtractor(BaseExtractor):
    """Area instances (``.git``): placed objects, their inventories, floor items."""

    def extract(self, file_path: Path, parsed_data: Dict[str, Any]) -> ExtractedContent:
        """Extract the visible instance strings of an area.

        Args:
            file_path: Path of the ``.git`` resource; its directory holds the
                module blueprints consulted by the name oracle.
            parsed_data: Parsed GFF root struct.

        Returns:
            The extracted items, one translation group per instance or item row.
        """
        stem = file_path.stem
        # Blueprint-name oracle: creature names from the module's .utc files
        # rescue code-like-looking real names (McGee, DeVir) from the filters.
        known_names = get_module_creature_names(file_path.parent)
        items = []
        for ref in _area_fields(parsed_data, stem):
            item = _git_item(ref, known_names)
            if item is not None:
                items.append(item)
        return ExtractedContent(
            content_type="git_instance",
            items=items,
            source_file=file_path,
            metadata={
                "type": "git_instance",
                "area_tag": parsed_data.get("Tag", stem),
                "item_count": len(items),
            },
        )
