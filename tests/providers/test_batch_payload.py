"""Batch payloads: structural groups and lossless shared source windows."""

from nwn_translator.ai_providers.base import TranslationItem
from nwn_translator.ai_providers.batch_payload import build_batch_payload, source_windows


def test_source_window_union_is_lossless_and_checks_overlap():
    entries = [
        {"nss_start": 4, "nss_snippet": "efghij"},
        {"nss_start": 0, "nss_snippet": "abcdef"},
        {"nss_start": 2, "nss_snippet": "cd"},
        {"nss_start": 4, "nss_snippet": "DIFFERENT"},
        {"nss_snippet": "unknown position"},
    ]
    windows, refs = source_windows(entries)
    assert refs[0] == refs[1] == refs[2]
    assert refs[3] != refs[0]
    for entry, ref in zip(entries, refs):
        window = windows[ref]
        if entry.get("nss_start") is not None:
            start = entry["nss_start"] - window["start"]
            assert window["text"][start : start + len(entry["nss_snippet"])] == entry["nss_snippet"]
        else:
            assert window["text"] == entry["nss_snippet"]


def test_sources_never_merge_across_files_and_outputs_stay_numeric():
    items = [
        TranslationItem(
            "Hello",
            "full fallback context",
            {
                "batch_resource": file,
                "translation_group": "script",
                "batch_context": "Consumer: SpeakString argument 0",
                "nss_snippet": snippet,
                "nss_start": start,
            },
        )
        for file, snippet, start in [
            ("a.ncs", "abcdef", 0),
            ("a.ncs", "efghij", 4),
            ("b.ncs", "efghij", 4),
        ]
    ]
    payload = build_batch_payload(items)
    assert set(payload["items"]) == {"0", "1", "2"}
    assert payload["items"]["0"]["group"] == payload["items"]["1"]["group"]
    assert payload["items"]["0"]["group"] != payload["items"]["2"]["group"]
    assert payload["groups"]["0"]["matching_source_context_only"][0]["text"] == "abcdefghij"
    assert payload["groups"]["1"]["matching_source_context_only"][0]["text"] == "efghij"
    assert all(i.context == "full fallback context" for i in items)
