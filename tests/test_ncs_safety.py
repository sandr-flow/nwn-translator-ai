"""Selection and model approval must protect individual bytecode occurrences."""

import json

import pytest

from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.file_handlers.ncs_concat import find_concat_chains, merged_text
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes
from nwn_translator.file_handlers.ncs_patcher import NCSPatchError, patch_ncs_string_replacements
from nwn_translator.injectors.ncs_injector import NcsInjector
from tests.test_ncs import _action, _add_ss, _consti, _consto, _consts, _header, _retn
from tests.test_translation_manager import _make_config, _make_provider
from nwn_translator.translators.translation_manager import TranslationManager


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}])
def test_gate_requires_json_boolean_true(value):
    provider = OpenRouterProvider(api_key="test")
    result = provider._parse_ncs_gate_raw(json.dumps({"0": {"translate": value}}), [{"key": "0"}])
    assert result["0"]["translate"] is False


@pytest.mark.parametrize("raw", ["[]", 'prefix {"0": {"translate": true}}', "{} {}"])
def test_gate_rejects_non_object_or_extra_output(raw):
    with pytest.raises(json.JSONDecodeError):
        OpenRouterProvider(api_key="test")._parse_ncs_gate_raw(raw, [{"key": "0"}])


def test_gate_accepts_explicit_approval_and_rejects_missing_entries():
    result = OpenRouterProvider(api_key="test")._parse_ncs_gate_raw(
        '```json\n{"0": {"translate": true, "reason": "speech"}}\n```',
        [{"key": "0"}, {"key": "1"}],
    )
    assert result["0"]["translate"] is True
    assert result["1"]["translate"] is False


@pytest.mark.parametrize("verdict", [False, "false", None, True])
def test_complete_path_patches_only_approved_occurrence(tmp_path, verdict):
    text = "The secret door opens."
    raw = (
        _header()
        + _consts(text)
        + _consto()
        + _action(51, 2)
        + _consti(0)
        + _consts(text)
        + _action(221, 2)
        + _retn()
    )
    path = tmp_path / "scene.ncs"
    path.write_bytes(raw)
    content = NcsExtractor().extract(path, {"_ncs_file": parse_ncs_bytes(raw)})
    provider = _make_provider({text: "Translated speech."})

    async def gate(entries, *, source_lang):
        return {e["key"]: {"translate": verdict} for e in entries}

    provider.classify_ncs_translate_gate_batch_async.side_effect = gate
    manager = TranslationManager(_make_config(target_lang="english"), provider)
    manager.translate_content(content)
    result = NcsInjector().inject(
        path,
        {},
        {},
        {
            "ncs_extracted_items": content.items,
            "ncs_translations_by_item_id": manager.ncs_translations_by_item_id,
            "module_text_encoding": "cp1252",
        },
    )
    assert result.modified is (verdict is True)
    values = [i.string_value for i in parse_ncs_bytes(path.read_bytes()).string_constants]
    assert values == [text, "Translated speech." if verdict is True else text]
    if verdict is not True:
        assert path.read_bytes() == raw
        provider.translate_async.assert_not_called()
        provider.translate_batch_async.assert_not_called()


def test_disabled_gate_rejects_unresolved_sentence(tmp_path):
    text = "This looks exactly like a spoken sentence."
    content = NcsExtractor().extract(
        tmp_path / "scene.ncs", {"_ncs_file": parse_ncs_bytes(_header() + _consts(text) + _retn())}
    )
    provider = _make_provider({text: "Must not be used."})
    manager = TranslationManager(_make_config(skip_ncs_llm_gate=True), provider)
    manager.translate_content(content)
    assert manager.ncs_translations_by_item_id == {}
    provider.translate_async.assert_not_called()
    provider.translate_batch_async.assert_not_called()


def test_void_action_does_not_invent_concat_operand():
    raw = (
        _header()
        + _consts("Left ")
        + _consts("Debug")
        + _action(1, 1)
        + _consts("right.")
        + _add_ss()
        + _action(221, 1)
        + _retn()
    )
    chains = find_concat_chains(parse_ncs_bytes(raw))
    assert [merged_text(chain) for chain in chains.values()] == ["Left right."]


def test_integer_conversion_preserves_whole_utterance(tmp_path):
    raw = (
        _header()
        + _consti(0)
        + _consts("You have ")
        + _consti(5)
        + _action(92, 1)
        + _add_ss()
        + _consts(" coins.")
        + _add_ss()
        + _action(221, 2)
        + _retn()
    )
    content = NcsExtractor().extract(tmp_path / "scene.ncs", {"_ncs_file": parse_ncs_bytes(raw)})
    assert [item.text for item in content.items] == ["You have <VAR1> coins."]
    assert content.items[0].metadata["proven_player"] is True


def test_scalar_argument_does_not_discard_pending_concat_literal():
    raw = (
        _header()
        + _consts("Left ")
        + _consti(0)
        + _consts("Speech")
        + _action(221, 2)
        + _consts("right.")
        + _add_ss()
        + _retn()
    )
    chains = find_concat_chains(parse_ncs_bytes(raw))
    assert [merged_text(chain) for chain in chains.values()] == ["Left right."]


def test_duplicate_patch_offsets_fail_before_writing(tmp_path):
    path = tmp_path / "scene.ncs"
    raw = _header() + _consts("Hello") + _retn()
    path.write_bytes(raw)
    with pytest.raises(NCSPatchError, match="Duplicate replacement"):
        patch_ncs_string_replacements(path, [(8, "Hello", "First"), (8, "Hello", "Second")])
    assert path.read_bytes() == raw
