"""Results belong to resource occurrences, never to matching source text."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.translators.translation_manager import TranslationManager


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_text_keeps_each_context_and_resource(reverse):
    items = [
        TranslatableItem("Commoner", "Female", "same_title", "woman.utc"),
        TranslatableItem("Commoner", "Male", "same_title", "man.utc"),
    ]
    if reverse:
        items.reverse()
    provider = Mock()

    async def batch(items, **kwargs):
        return [
            TranslationResult(
                original=i.original,
                translated=("Простолюдинка" if i.context == "Female" else "Простолюдин"),
            )
            for i in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=batch)
    manager = TranslationManager(TranslationConfig(api_key="test-key", quiet=True), provider)
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    assert result == {
        ("woman.utc", "same_title"): "Простолюдинка",
        ("man.utc", "same_title"): "Простолюдин",
    }
    assert (
        sum(len(call.kwargs["items"]) for call in provider.translate_batch_async.call_args_list)
        == 2
    )


def test_failure_is_scoped_to_one_occurrence_of_equal_text():
    female = TranslatableItem("Commoner", "Female", "title", "female.utc")
    male = TranslatableItem("Commoner", "Male", "title", "male.utc")
    provider = Mock()

    async def batch(items, **kwargs):
        return [
            TranslationResult(
                original=item.original,
                translated="Woman" if item.context == "Female" else "",
                success=item.context == "Female",
                error="rejected",
            )
            for item in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=batch)
    provider.translate_async = AsyncMock(
        return_value=TranslationResult(
            original="Commoner", translated="", success=False, error="rejected"
        )
    )
    manager = TranslationManager(TranslationConfig(api_key="test-key", quiet=True), provider)
    result = manager.translate_content(ExtractedContent("combined", [female, male], Path("module")))
    assert result == {female.key: "Woman"}
    assert manager.failed_items == {male.key}


@pytest.mark.parametrize(
    "extension, data",
    [
        (
            "utc",
            {
                "Tag": "same",
                "FirstName": {"StrRef": -1, "Value": "Shared"},
                "LastName": {"StrRef": -1, "Value": "Shared"},
            },
        ),
        (
            "dlg",
            {
                "EntryList": [
                    {"Text": {"StrRef": -1, "Value": "Shared"}},
                    {"Text": {"StrRef": -1, "Value": "Shared"}},
                ]
            },
        ),
        (
            "git",
            {
                "Creature List": [
                    {"FirstName": {"StrRef": -1, "Value": "Shared"}},
                    {"FirstName": {"StrRef": -1, "Value": "Shared"}},
                ]
            },
        ),
        (
            "jrl",
            {
                "Categories": [
                    {
                        "Name": {"StrRef": -1, "Value": "Shared"},
                        "EntryList": [{"Text": {"StrRef": -1, "Value": "Shared"}}],
                    }
                ]
            },
        ),
        (
            "uti",
            {
                "Description": {"StrRef": -1, "Value": "Shared"},
                "DescIdentified": {"StrRef": -1, "Value": "Shared"},
            },
        ),
    ],
)
def test_inject_and_rebuild_keep_identical_fields_independent(tmp_path, extension, data):
    from nwn_translator.file_handlers.gff_writer import write_gff
    from nwn_translator.pipeline.stages import (
        load_parsed_and_extracted,
        inject_translations_into_file,
    )
    from nwn_translator.main import rebuild_module

    root = tmp_path / "module"
    root.mkdir()
    path = root / f"sample.{extension}"
    write_gff(path, data, file_type=extension.upper())
    parsed, extracted = load_parsed_and_extracted(path, path.suffix, None)
    assert len(extracted.items) == 2
    first, second = extracted.items
    inject_translations_into_file(
        path, parsed, extracted, {first.key: "Alpha", second.key: "Beta"}, target_lang="english"
    )
    _, after = load_parsed_and_extracted(path, path.suffix, None)
    assert {item.key: item.text for item in after.items} == {first.key: "Alpha", second.key: "Beta"}
    # Rebuild addresses the selected occurrence using fresh offsets after resizing.
    rebuild_module(
        root,
        {path.name: {second.item_id: "Much longer edited value"}},
        tmp_path / "out.mod",
        original_mod_path=tmp_path / "missing.mod",
        target_lang="english",
    )
    _, after = load_parsed_and_extracted(path, path.suffix, None)
    assert {item.key: item.text for item in after.items} == {
        first.key: "Alpha",
        second.key: "Much longer edited value",
    }


def test_approved_script_context_is_local_and_does_not_mutate_extraction():
    provider = Mock()

    async def gate(entries, **kwargs):
        return {
            entry["key"]: {"translate": entry["text"] != "Internal debug message."}
            for entry in entries
        }

    async def batch(items, **kwargs):
        return [
            TranslationResult(original=item.original, translated="TR:" + item.original)
            for item in items
        ]

    provider.classify_ncs_translate_gate_batch_async = AsyncMock(side_effect=gate)
    provider.translate_batch_async = AsyncMock(side_effect=batch)
    items = [
        TranslatableItem(
            text,
            "script",
            f"line:{index}",
            file,
            {
                "type": "ncs_string",
                "offset": index,
                "proven_player": True,
                "ncs_hint": "SpeakString",
            },
        )
        for index, (file, text) in enumerate(
            [
                ("song.ncs", "I climbed the tower."),
                ("song.ncs", "I fell from the tower."),
                ("song.ncs", "Internal debug message."),
                ("other.ncs", "Unrelated speech here."),
            ]
        )
    ]
    config = TranslationConfig(api_key="test-key", quiet=True)
    manager = TranslationManager(config, provider)
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    assert items[2].key not in result
    sent = [
        item
        for call in provider.translate_batch_async.call_args_list
        for item in call.kwargs["items"]
    ]
    first = next(item for item in sent if item.original == items[0].text)
    assert items[1].text in first.context
    assert items[2].text not in first.context
    assert items[3].text not in first.context
    assert "not proven execution order" in first.context
    assert all(item.context == "script" for item in items)
    provider.translate_batch_async.assert_called_once()
    assert result == {item.key: "TR:" + item.text for item in items if item is not items[2]}


def test_addressed_artifact_rejects_ambiguous_old_map(tmp_path):
    from nwn_translator.pipeline.artifacts import dump_translations, load_translations

    path = tmp_path / "translations.json"
    values = {("a.utc", "name"): "First", ("b.utc", "name"): "Second"}
    dump_translations(path, values)
    assert load_translations(path) == values
    path.write_text('{"Commoner": "One answer"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_translations(path)


def test_log_connects_requests_results_and_reuse(tmp_path):
    import json

    path = tmp_path / "trace.jsonl"
    provider = Mock()
    provider.translate_batch_async = AsyncMock(
        return_value=[TranslationResult(original="Hello", translated="Greetings")]
    )
    config = TranslationConfig(api_key="test-key", quiet=True, translation_log=path)
    manager = TranslationManager(config, provider)
    items = [TranslatableItem("Hello", "same context", "name", file) for file in ("a.utc", "b.utc")]
    manager.translate_content(ExtractedContent("combined", items, Path("module")))
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    request = next(event for event in events if event.get("event") == "model_request")
    response = next(event for event in events if event.get("event") == "model_response")
    reuse = next(event for event in events if event.get("event") == "translation_reuse")
    assert request["request_id"] == response["request_id"]
    assert request["context"]["occurrences"] == [["a.utc", "name"]]
    assert reuse["occurrence"] == ["b.utc", "name"]
    assert "test-key" not in path.read_text(encoding="utf-8")
