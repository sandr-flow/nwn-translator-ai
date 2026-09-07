"""Batch output contracts and bounded recovery must not create single-item storms."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from nwn_translator.ai_providers.base import TranslationItem, TranslationResult
from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from nwn_translator.async_utils import run_async
from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import ExtractedContent, TranslatableItem
from nwn_translator.prompts._builder import build_translation_system_prompt_parts
from nwn_translator.translators.translation_manager import TranslationManager


@pytest.mark.parametrize("profile", ["default", "short_label", "script_message"])
def test_batch_prompt_has_one_output_contract(profile):
    batch, _ = build_translation_system_prompt_parts(
        "russian", "male", content_profile=profile, batch_mode=True
    )
    single, _ = build_translation_system_prompt_parts("russian", "male", content_profile=profile)
    assert "exactly ONE key" not in batch
    assert '- "translation":' not in batch
    assert "flat JSON object" in batch
    assert "exactly ONE key" in single


@pytest.mark.parametrize("wrapped", [False, True])
def test_valid_translations_survive_single_item_wrapper(wrapped):
    provider = OpenRouterProvider(api_key="test")
    values = {"0": "Рабб", "1": "Хилл", "2": "Описание"}
    raw = json.dumps({"translation": values} if wrapped else values, ensure_ascii=False)
    provider._chat_completion_json_async = AsyncMock(return_value=raw)
    items = [TranslationItem(text) for text in ["Rabb", "Hill", "Description"]]
    results = run_async(provider.translate_batch_async(items, "english", "russian"))
    assert [r.translated for r in results] == list(values.values())
    assert all(r.success for r in results)
    assert provider._chat_completion_json_async.call_count == 1
    prompt = provider._chat_completion_json_async.call_args.args[0]
    assert "exactly ONE key" not in prompt


@pytest.mark.parametrize(
    "response",
    [
        {"translation": "A combined paragraph"},
        {"translation": ["First", "Second"]},
        {"translation": {"7": "Unrequested ID"}},
        {"translation": {"0": "Ambiguous"}, "items": {"0": "Different"}},
    ],
)
def test_wrapper_recovery_does_not_invent_addresses(response):
    provider = OpenRouterProvider(api_key="test")
    provider._chat_completion_json_async = AsyncMock(return_value=json.dumps(response))
    results = run_async(
        provider.translate_batch_async(
            [TranslationItem("A"), TranslationItem("B")], "english", "russian"
        )
    )
    assert len(results) == 2
    assert all(not r.success for r in results)


@pytest.mark.parametrize("successful_size", [20, 5])
def test_bulk_failure_recursively_splits_without_individual_fanout(successful_size):
    items = [
        TranslatableItem(
            f"Name {i}",
            "",
            str(i),
            "area.git",
            {
                "type": "creature_first_name",
                "translation_group": f"creature[{i}]",
            },
        )
        for i in range(40)
    ]
    provider = Mock()

    async def batch(items, **kwargs):
        success = len(items) <= successful_size
        return [
            TranslationResult(
                original=item.original,
                translated="TR:" + item.original if success else "",
                success=success,
                error="Malformed batch response",
            )
            for item in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=batch)
    manager = TranslationManager(TranslationConfig(api_key="test", quiet=True), provider)
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    sizes = [len(c.kwargs["items"]) for c in provider.translate_batch_async.call_args_list]
    assert sizes[:2] == [40, 20]
    assert sizes.count(successful_size) == 40 // successful_size
    assert min(sizes) == successful_size
    provider.translate_async.assert_not_called()
    assert result == {i.key: "TR:" + i.text for i in items}


def test_recursive_recovery_preserves_successes_and_only_retries_failed_positions():
    items = [TranslatableItem(f"Name {i}", "context", str(i), "a.utc") for i in range(6)]
    provider = Mock()

    async def batch(items, **kwargs):
        return [
            TranslationResult(
                original=item.original,
                translated="TR:" + item.original,
                success=len(items) <= 2 or item.original in {"Name 0", "Name 5"},
                error="Missing result",
            )
            for item in items
        ]

    provider.translate_batch_async = AsyncMock(side_effect=batch)
    manager = TranslationManager(TranslationConfig(api_key="test", quiet=True), provider)
    result = manager.translate_content(ExtractedContent("combined", items, Path("module")))
    sent = [
        [i.original for i in c.kwargs["items"]]
        for c in provider.translate_batch_async.call_args_list
    ]
    assert sent == [[i.text for i in items], ["Name 1", "Name 2"], ["Name 3", "Name 4"]]
    assert result == {i.key: "TR:" + i.text for i in items}
    provider.translate_async.assert_not_called()
