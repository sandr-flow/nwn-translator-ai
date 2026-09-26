"""Request planning: passthrough, singles and packed batch groups."""

from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.translators.token_handler import sanitize_text
from nwn_translator.translators.work_plan import (
    BatchLimits,
    WorkItem,
    dedup_key,
    plan_work,
)


def _work(text: str, item_id: str, location: str = "a.uti", **meta) -> WorkItem:
    item = TranslatableItem(text, meta.pop("context", None), item_id, location, meta)
    return WorkItem(item, *sanitize_text(text))


def _no_terms(texts):
    return None


def test_plan_splits_requests_and_keeps_groups_whole_in_first_seen_order():
    work = [
        _work("<FirstName>!", "p"),
        _work("A sword.", "d", translation_group="root", type="item_description"),
        _work("Long. " * 1100, "l"),
        _work("Sword", "n", translation_group="root", type="item_name"),
        _work("Guard", "g", location="b.utc", type="creature_first_name"),
    ]

    plan = plan_work(work, BatchLimits(), _no_terms)

    assert plan.passthrough == [work[0]]
    assert plan.singles == [work[2]]
    assert plan.batches == [[work[1], work[3]], [work[4]]]


def test_script_groups_follow_offsets_and_profiles_pack_separately():
    lines = [
        _work(f"Line {offset}.", f"s:{offset}", location="s.ncs", type="ncs_string", offset=offset)
        for offset in (30, 10, 20)
    ]
    for line in lines:
        line.item.metadata["translation_group"] = "script"
    label = _work("Guard", "g", type="creature_first_name")

    plan = plan_work([label, *lines], BatchLimits(), _no_terms)

    assert plan.batches == [[label], [lines[1], lines[2], lines[0]]]


def test_dedup_key_separates_hint_context_and_terms():
    base = _work("Open", "1", type="door_name")
    same = _work("Open", "2", location="b.utd", type="door_name")
    other_hint = _work("Open", "3", type="door_name", hint="verb")
    other_context = _work("Open", "4", type="door_name", context="Door")

    keys = [dedup_key(w, _no_terms) for w in (base, same, other_hint, other_context)]

    assert keys[0] == keys[1]
    assert len({keys[0], keys[2], keys[3]}) == 3
    assert dedup_key(base, lambda texts: "GLOSSARY") != keys[0]
