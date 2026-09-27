"""Injecting translations into compiled scripts, one bytecode occurrence at a time."""

from unittest.mock import patch

import pytest

from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.formats.ncs import NCSPatchError, parse_ncs, parse_ncs_bytes
from nwn_translator.injectors.ncs_injector import inject_ncs
from nwn_translator.translators.translation_manager import TranslationManager
from tests.support.fakes import make_config, translation_provider
from tests.support.ncs import action, add_ss, consti, consto, consts, cptopsp, jsr, retn, write_ncs

#: Keyword arguments of the pipeline for a Russian target and a detected source encoding.
_INJECT_KW = {"content_type": "ncs_script", "text_encoding": "cp1251", "source_encoding": None}


def _extract(path):
    return NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})


def _inject(path, items, answers, **kwargs):
    translations = {(path.name, item_id): text for item_id, text in answers.items()}
    return inject_ncs(path, items, translations, **{**_INJECT_KW, **kwargs})


def test_translation_is_patched_into_its_occurrence(tmp_path):
    path = write_ncs(tmp_path, "test.ncs", consts("Hello world!"), action(374, 2), retn())
    items = _extract(path).items

    result = _inject(path, items, {items[0].item_id: "Привет, мир!"})

    assert (result.modified, result.items_updated) == (True, 1)
    assert parse_ncs(path).string_constants[0].string_value == "Привет, мир!"


def test_failed_patch_is_reported_and_leaves_the_script_unchanged(tmp_path):
    path = write_ncs(tmp_path, "test.ncs", consts("Hello world!"), action(374, 2), retn())
    items = _extract(path).items

    with patch(
        "nwn_translator.injectors.ncs_injector.patch_ncs_string_replacements",
        side_effect=NCSPatchError("validation failed"),
    ):
        result = _inject(path, items, {items[0].item_id: "Translated text."})

    assert (result.modified, result.items_updated) == (False, 0)
    assert (result.metadata["ncs_patch_failed"], result.metadata["error"]) == (
        True,
        "validation failed",
    )
    assert parse_ncs(path).instructions[0].string_value == "Hello world!"


def test_translation_of_another_occurrence_changes_nothing(tmp_path):
    path = write_ncs(tmp_path, "test.ncs", consts("Hello"), retn())
    result = inject_ncs(
        path, _extract(path).items, {(path.name, "other:c0"): "Au revoir"}, **_INJECT_KW
    )
    assert (result.modified, result.items_updated) == (False, 0)
    assert result.metadata == {"type": "ncs_script"}


def test_round_trip_keeps_identifiers_and_the_subroutine_call(tmp_path):
    str1, str2, str3 = "Hello, brave adventurer!", "nw_c2_default1", "Farewell, noble traveler!"
    # JSR over: CONSTS str1 + ACTION + CONSTS str2 + ACTION + RETN.
    jsr_offset = 6 + (4 + len(str1)) + 5 + (4 + len(str2)) + 5 + 2
    path = write_ncs(
        tmp_path,
        "script.ncs",
        jsr(jsr_offset),
        consts(str1),
        action(374, 2),  # SendMessageToPC: translatable
        consts(str2),
        action(46, 1),  # GetObjectByTag: an identifier
        retn(),
        consts(str3),  # the subroutine: SpeakString
        action(39, 1),
        retn(),
    )
    extracted = _extract(path)
    assert {item.text for item in extracted.items} == {str1, str3}
    answers = {str1: "Привет, храбрый искатель приключений!", str3: "Прощай, благородный путник!"}

    result = _inject(path, extracted.items, {i.item_id: answers[i.text] for i in extracted.items})

    assert result.modified
    ncs = parse_ncs(path)
    assert [i.string_value for i in ncs.string_constants] == [answers[str1], str2, answers[str3]]
    jump, subroutine = ncs.instructions[0], ncs.string_constants[2]
    assert jump.offset + jump.jump_offset == subroutine.offset


def test_concatenation_is_split_back_into_its_literals(tmp_path):
    path = write_ncs(
        tmp_path,
        "cut.ncs",
        consts("Congrats to ye, "),
        cptopsp(-8),
        add_ss(),
        consts(". How do ye feel?"),
        add_ss(),
        action(221, 1),
        retn(),
    )
    extracted = _extract(path)
    answer = "Поздравляю тебя, <VAR1>. Как ты себя чувствуешь?"

    result = _inject(path, extracted.items, {extracted.items[0].item_id: answer})

    assert result.modified
    values = [i.string_value for i in parse_ncs(path).string_constants]
    assert values[:2] == ["Поздравляю тебя, ", ". Как ты себя чувствуешь?"]


@pytest.mark.parametrize("verdict", [False, "false", None, True])
def test_only_the_occurrence_the_gate_approves_is_patched(tmp_path, verdict):
    """Selection and model approval protect individual bytecode occurrences."""
    text = "The secret door opens."
    path = write_ncs(
        tmp_path,
        "scene.ncs",
        consts(text),
        consto(),
        action(51, 2),
        consti(0),
        consts(text),
        action(221, 2),
        retn(),
    )
    raw = path.read_bytes()
    content = _extract(path)
    provider = translation_provider({text: "Translated speech."})

    async def gate(entries, *, source_lang):
        return {e["key"]: {"translate": verdict} for e in entries}

    provider.classify_ncs_translate_gate_batch_async.side_effect = gate
    translations = TranslationManager(
        make_config(target_lang="english"), provider
    ).translate_content(content)

    result = inject_ncs(
        path,
        content.items,
        translations,
        content_type=content.content_type,
        text_encoding="cp1252",
        source_encoding=None,
    )

    assert result.modified is (verdict is True)
    values = [i.string_value for i in parse_ncs_bytes(path.read_bytes()).string_constants]
    assert values == [text, "Translated speech." if verdict is True else text]
    if verdict is not True:
        assert path.read_bytes() == raw
        provider.translate_async.assert_not_called()
        provider.translate_batch_async.assert_not_called()
