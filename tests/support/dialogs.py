"""Builders of parsed ``.dlg`` structs."""

from typing import Dict, List

from nwn_translator.context.dialog_formatter import iter_nodes
from nwn_translator.extractors.base import DialogNode


def dlg(*roots: DialogNode) -> dict:
    """Parsed ``.dlg`` struct whose conversation tree is *roots*.

    Node ids are list indices; unused indices get empty nodes no link reaches.
    """
    tables: Dict[bool, Dict[int, dict]] = {True: {}, False: {}}
    for _key, node in iter_nodes(list(roots)):
        links = [{"Index": child.node_id} for child in node.replies]
        struct = {"Text": {"StrRef": -1, "Value": node.text}}
        if node.is_entry:
            struct.update(Speaker=node.speaker or "", RepliesList=links)
        else:
            struct.update(EntriesList=links)
        tables[node.is_entry][node.node_id] = struct

    def as_list(table: Dict[int, dict]) -> List[dict]:
        return [
            table.get(i, {"Text": {"StrRef": -1, "Value": ""}})
            for i in range(max(table, default=-1) + 1)
        ]

    return {
        "StructType": "DLG",
        "EntryList": as_list(tables[True]),
        "ReplyList": as_list(tables[False]),
        "StartingList": [{"Index": root.node_id} for root in roots],
    }


def deep_chain(n: int) -> dict:
    """Parsed ``.dlg`` with *n* entry/reply alternations (depth ``2 * n``)."""
    return {
        "StructType": "DLG",
        "EntryList": [
            {"Text": {"StrRef": -1, "Value": f"E{i}"}, "Speaker": "", "RepliesList": [{"Index": i}]}
            for i in range(n)
        ],
        "ReplyList": [
            {
                "Text": {"StrRef": -1, "Value": f"R{i}"},
                "EntriesList": [{"Index": i + 1}] if i + 1 < n else [],
            }
            for i in range(n)
        ],
        "StartingList": [{"Index": 0}],
    }
