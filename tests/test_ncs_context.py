"""Argument-specific NCS classification with real NWScript stack conventions."""

import struct

import pytest

from nwn_translator.extractors.ncs_context import TYPE_STRING_STRING, trace_string_consumer
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.extractors.nss_index import classify_engine_arg
from nwn_translator.file_handlers.ncs_parser import OP_EQUAL, OP_NEQUAL, parse_ncs_bytes
from tests.test_ncs import (
    _action,
    _consti,
    _consto,
    _consts,
    _cptopsp,
    _header,
    _jmp,
    _jsr,
    _jz,
    _movsp,
    _retn,
)


def _extract(tmp_path, *parts):
    ncs = parse_ncs_bytes(_header() + b"".join(parts))
    return NcsExtractor().extract(tmp_path / "scene.ncs", {"_ncs_file": ncs})


def test_poststring_font_is_internal_message_is_player(tmp_path):
    result = _extract(
        tmp_path,
        _consts("Custom font name"),
        _consti(0),
        _consti(0),
        _consti(0),
        struct.pack(">BBf", 0x04, 0x04, 1.0),
        _consti(0),
        _consti(0),
        _consti(0),
        _consts("The gates are open."),
        _consto(),
        _action(901, 10),
        _retn(),
    )
    assert [item.text for item in result.items] == ["The gates are open."]
    context = result.items[0].metadata["bytecode_context"]
    assert context["next_action_name"] == "PostString"
    assert context["argument_index"] == 1
    assert context["consumer_proven"] is True


@pytest.mark.parametrize("routine,prefix", [(858, [_consts("area_template")]), (860, [_consto()])])
def test_area_creation_separates_display_name_and_tag(tmp_path, routine, prefix):
    result = _extract(
        tmp_path,
        _consts("The Forgotten City"),
        _consts("Do not rename this tag."),
        *prefix,
        _action(routine, 3),
        _retn(),
    )
    assert [item.text for item in result.items] == ["The Forgotten City"]
    assert result.items[0].metadata["bytecode_context"]["argument_index"] == 2


@pytest.mark.parametrize("routine", [57, 593])
def test_stored_string_value_is_unknown_and_keys_are_internal(tmp_path, routine):
    args = [_consts("The treasure is buried here."), _consts("Stored message key.")]
    if routine == 57:
        args += [_consto()]
    else:
        args = [_consto()] + args + [_consts("Campaign database name.")]
    result = _extract(tmp_path, *args, _action(routine, len(args)), _retn())
    assert [item.text for item in result.items] == ["The treasure is buried here."]
    assert result.items[0].metadata["needs_llm_gate"] is True
    assert result.items[0].metadata["bytecode_context"]["argument_index"] == 2


def test_nested_getter_consumes_key_without_claiming_outer_message(tmp_path):
    result = _extract(
        tmp_path,
        _consts("Welcome to the city."),
        _consti(0),
        _consts("Recipient object tag."),
        _action(200, 2),
        _action(374, 2),
        _retn(),
    )
    assert [item.text for item in result.items] == ["Welcome to the city."]
    assert result.items[0].metadata["proven_player"] is True
    assert result.items[0].metadata["ncs_hint"] == "SendMessageToPC"


def test_lookup_key_is_not_the_returned_text(tmp_path):
    result = _extract(
        tmp_path,
        _consti(0),
        _consts("Stored greeting."),
        _consto(),
        _action(53, 2),
        _action(221, 2),
        _retn(),
    )
    assert result.items == []


@pytest.mark.parametrize("opcode", [OP_EQUAL, OP_NEQUAL])
def test_actual_binary_string_comparison_blocks_dispatch_literal(tmp_path, opcode):
    result = _extract(
        tmp_path,
        _consts("Open the hidden door."),
        _cptopsp(-8),
        struct.pack(">BB", opcode, TYPE_STRING_STRING),
        _jz(6),
        _consti(0),
        _consts("The door opens."),
        _action(221, 2),
        _retn(),
    )
    assert [item.text for item in result.items] == ["The door opens."]


def test_unrelated_string_comparison_does_not_veto_pending_speech():
    ncs = parse_ncs_bytes(
        _header()
        + _consts("The guard speaks.")
        + _consts("a")
        + _consts("b")
        + struct.pack(">BB", OP_EQUAL, TYPE_STRING_STRING)
        + _retn()
    )
    assert trace_string_consumer(0, ncs.instructions)["compare_nearby"] is False


@pytest.mark.parametrize(
    "barrier", [_jmp(6), _jz(6), _jsr(6), _retn(), _action(9999, 0), _cptopsp(-4)]
)
def test_uncertain_flow_cannot_borrow_later_speech_proof(tmp_path, barrier):
    result = _extract(
        tmp_path,
        _consts("An unresolved sentence."),
        barrier,
        _consti(0),
        _consts("Actual spoken text."),
        _action(221, 2),
        _retn(),
    )
    assert result.items[0].metadata["proven_player"] is False
    assert result.items[0].metadata["needs_llm_gate"] is True
    assert result.items[1].metadata["proven_player"] is True


def test_precise_internal_use_overrules_matching_source_speech(tmp_path):
    (tmp_path / "other.nss").write_text('void main() { SpeakString("A shared phrase."); }')
    result = _extract(tmp_path, _consts("A shared phrase."), _consto(), _action(51, 2), _retn())
    assert result.items == []


def test_unproven_natural_word_remains_a_gate_candidate(tmp_path):
    result = _extract(tmp_path, _consts("Good"), _action(9999, 0), _action(221, 2), _retn())
    assert [item.text for item in result.items] == ["Good"]
    assert result.items[0].metadata["player_candidate"] is True
    assert result.items[0].metadata["proven_player"] is False
    assert result.items[0].metadata["needs_llm_gate"] is True


def test_same_text_in_internal_and_spoken_slots_is_patched_selectively(tmp_path):
    from nwn_translator.file_handlers.ncs_patcher import patch_ncs_string_replacements

    text = "A shared phrase."
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
    result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs_bytes(raw)})
    assert len(result.items) == 1
    item = result.items[0]
    assert (
        patch_ncs_string_replacements(
            path, [(item.metadata["offset"], text, "Translated speech.")], "cp1252"
        )
        == 1
    )
    strings = parse_ncs_bytes(path.read_bytes()).string_constants
    assert [instr.string_value for instr in strings] == [text, "Translated speech."]


@pytest.mark.parametrize(
    "routine,argument_pushes",
    [
        (367, [_consti(0), _consti(0), _consti(0), _consto(), _consti(1)]),
        (368, [_consti(0), _consti(0), _consto()]),
        (384, []),
        (560, []),
        (255, [_consto()]),
        (417, [_consto()]),
    ],
)
def test_sentence_shaped_internal_arguments_are_skipped(tmp_path, routine, argument_pushes):
    result = _extract(
        tmp_path,
        *argument_pushes,
        _consts("A convincing natural sentence."),
        _action(routine, len(argument_pushes) + 1),
        _retn(),
    )
    assert result.items == []


@pytest.mark.parametrize(
    "routine,following",
    [
        (554, [_consti(0), _consti(1), _consti(1), _consto()]),
        (820, [_consto()]),
        (284, [_consti(100)]),
    ],
)
def test_display_arguments_are_kept(tmp_path, routine, following):
    result = _extract(
        tmp_path,
        _consts("You need the silver key."),
        *following,
        _action(routine, len(following) + 1),
        _retn(),
    )
    assert len(result.items) == 1
    assert result.items[0].metadata["proven_player"] is True


def test_unknown_engine_names_are_not_classified_by_substring():
    for func in ("GetModuleItemAcquiredBy", "PlaySpeakSoundByStrRef", "SpawnScriptDebugger"):
        assert classify_engine_arg(func, 0) is None


def _trace(*parts):
    ncs = parse_ncs_bytes(_header() + b"".join(parts))
    return trace_string_consumer(0, ncs.instructions)


def test_alias_display_does_not_hide_later_technical_use(tmp_path):
    result = _extract(
        tmp_path,
        _consts("A shared sentence."),
        _consti(0),
        _cptopsp(-8),
        _action(221, 2),
        _action(242, 0),  # GetModule must not hide the remaining key alias.
        _action(51, 2),
        _retn(),
    )
    assert result.items == []


@pytest.mark.parametrize("internal_first", [False, True])
def test_technical_branch_overrules_display_branch(internal_first):
    display = _consti(0) + _cptopsp(-8) + _action(221, 2) + _movsp(-4) + _retn()
    internal = _consto() + _action(51, 2) + _retn()
    first, second = (internal, display) if internal_first else (display, internal)
    context = _trace(_consts("A shared sentence."), _consti(1), _jz(6 + len(first)), first, second)
    assert context["role"] == "internal"


def test_overwriting_a_local_removes_the_old_alias():
    context = _trace(
        _consts("Actual speech."),
        _consti(0),
        _cptopsp(-8),
        _action(221, 2),
        _consts("New technical key."),
        struct.pack(">BBiH", 0x01, 0x01, -8, 4),  # CPDOWNSP
        _movsp(-4),
        _consto(),
        _action(51, 2),
        _retn(),
    )
    assert context["role"] == "player"


def test_known_wrapper_follows_the_actual_argument():
    context = _trace(
        _consts("McArthur"),
        _jsr(8),
        _retn(),
        _consti(0),
        _cptopsp(-8),
        _action(221, 2),
        _movsp(-4),
        _retn(),
    )
    assert context["role"] == "player"


@pytest.mark.parametrize("call", [_jsr(0), _jsr(99999), _action(9999, 0)])
def test_unknown_or_recursive_call_cannot_prove_display(call):
    context = _trace(_consts("An unresolved phrase."), call, _action(221, 1), _retn())
    assert context["consumer_proven"] is False


@pytest.mark.parametrize("offset,expected", [(0, "player"), (4, None)])
def test_destruct_tracks_only_the_retained_struct_member(offset, expected):
    context = _trace(
        _consts("Retained speech."),
        _consti(42),
        struct.pack(">BBHHH", 0x21, 0x01, 8, offset, 4),
        _action(221, 1),
        _retn(),
    )
    assert context["role"] == expected


def test_deferred_technical_use_overrules_immediate_display():
    child = _cptopsp(-4) + _consto() + _action(51, 2) + _retn()
    context = _trace(
        _consts("A shared sentence."),
        struct.pack(">BBII", 0x2C, 0x10, 0, 4),  # STORE_STATE
        _jmp(6 + len(child)),
        child,
        _consto(),
        _action(6, 2),  # Stored action consumes no ordinary stack slot.
        _action(221, 1),
        _retn(),
    )
    assert context["role"] == "internal"


def test_assign_command_does_not_pop_the_pending_string():
    context = _trace(_consts("Pending speech."), _consto(), _action(6, 2), _action(221, 1))
    assert context["role"] == "player"


@pytest.mark.parametrize("text", ["McArthur", "please report to the captain.", "oAssignedHorse"])
def test_display_evidence_overrules_word_shape_but_not_technical_use(tmp_path, text):
    shown = _extract(tmp_path, _consti(0), _consts(text), _action(221, 2), _retn())
    assert [item.text for item in shown.items] == [text]
    internal = _extract(tmp_path, _consts(text), _consto(), _action(51, 2), _retn())
    assert internal.items == []


def test_matching_source_does_not_promote_an_unresolved_identifier(tmp_path):
    (tmp_path / "scene.nss").write_text('void main() { SpeakString("Pentanar"); }')
    assert _extract(tmp_path, _consts("Pentanar"), _action(9999, 0), _retn()).items == []


def test_exhausted_budget_keeps_observed_display_unproven():
    context = _trace(
        _consts("Shared speech."),
        _consti(0),
        _cptopsp(-8),
        _action(221, 2),
        b"\x2d\x00" * 2050,  # NOPs leave a live alias beyond the exploration budget.
        _consto(),
        _action(51, 2),
        _retn(),
    )
    assert context["player_use_seen"] is True
    assert context["consumer_proven"] is False


def test_unmodeled_global_read_prevents_exclusive_display_proof():
    context = _trace(
        _consts("Shared speech."),
        struct.pack(">BBiH", 0x27, 0x01, -4, 4),  # CPTOPBP
        _movsp(-4),
        _action(221, 1),
        _retn(),
    )
    assert context["player_use_seen"] is True
    assert context["consumer_proven"] is False


def test_large_stack_discard_does_not_enumerate_untracked_slots():
    context = _trace(_consts("Discarded text."), _movsp(-(2**31)), _action(221, 1))
    assert context["consumer_proven"] is False
