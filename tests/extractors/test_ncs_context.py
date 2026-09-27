"""Argument-specific NCS classification with real NWScript stack conventions."""

import struct

import pytest

from nwn_translator.extractors.ncs_context import TYPE_STRING_STRING, trace_string_consumer
from nwn_translator.formats.ncs import (
    OP_EQUAL,
    OP_NEQUAL,
    parse_ncs_bytes,
    patch_ncs_string_replacements,
)
from tests.support.ncs import (
    action,
    consti,
    consto,
    consts,
    cptopsp,
    extract_script,
    jmp,
    jsr,
    jz,
    movsp,
    retn,
    script,
    write_ncs,
)

#: Stored code that reads the string as a local variable name.
_DEFERRED_KEY_USE = cptopsp(-4) + consto() + action(51, 2) + retn()


def _trace(*parts):
    return trace_string_consumer(0, parse_ncs_bytes(script(*parts)).instructions)


def _texts(result):
    return [item.text for item in result.items]


@pytest.mark.parametrize(
    "parts",
    [
        # GetLocalString reads a key; its value is not the text PrintString shows.
        (consti(0), consts("Stored greeting."), consto(), action(53, 2), action(221, 2), retn()),
        # A precise internal use beats a matching source line of another script.
        (consts("A shared phrase."), consto(), action(51, 2), retn()),
        # A later technical use of the alias beats an earlier display (GetModule between).
        (
            consts("A shared sentence."),
            consti(0),
            cptopsp(-8),
            action(221, 2),
            action(242, 0),
            action(51, 2),
            retn(),
        ),
    ]
    # Sentence-shaped arguments that name things rather than show text.
    + [
        (
            *pushes,
            consts("A convincing natural sentence."),
            action(routine, len(pushes) + 1),
            retn(),
        )
        for routine, pushes in [
            (367, [consti(0), consti(0), consti(0), consto(), consti(1)]),
            (368, [consti(0), consti(0), consto()]),
            (384, []),
            (560, []),
            (255, [consto()]),
            (417, [consto()]),
        ]
    ],
)
def test_internal_arguments_are_not_extracted(tmp_path, parts):
    # Another script speaking the same phrase proves nothing about this one.
    (tmp_path / "other.nss").write_text('void main() { SpeakString("A shared phrase."); }')
    assert extract_script(tmp_path, *parts).items == []


@pytest.mark.parametrize(
    "parts, index, action_name",
    [
        (
            (
                consts("Custom font name"),
                *[consti(0)] * 3,
                struct.pack(">BBf", 0x04, 0x04, 1.0),
                *[consti(0)] * 3,
                consts("The gates are open."),
                consto(),
                action(901, 10),
                retn(),
            ),
            1,
            "PostString",
        ),
    ]
    + [
        # Area creation separates the display name from the tag.
        (
            (
                consts("The Forgotten City"),
                consts("Do not rename this tag."),
                *prefix,
                action(routine, 3),
                retn(),
            ),
            2,
            None,
        )
        for routine, prefix in [(858, [consts("area_template")]), (860, [consto()])]
    ],
)
def test_only_the_display_argument_of_a_call_is_extracted(tmp_path, parts, index, action_name):
    (item,) = extract_script(tmp_path, *parts).items
    context = item.metadata["bytecode_context"]
    assert context["argument_index"] == index
    if action_name:
        assert item.text == "The gates are open."
        assert (context["next_action_name"], context["consumer_proven"]) == (action_name, True)
    else:
        assert item.text == "The Forgotten City"


@pytest.mark.parametrize("routine", [57, 593])
def test_stored_string_value_is_unknown_and_its_keys_are_internal(tmp_path, routine):
    args = [consts("The treasure is buried here."), consts("Stored message key.")]
    if routine == 57:
        args += [consto()]
    else:
        args = [consto()] + args + [consts("Campaign database name.")]
    (item,) = extract_script(tmp_path, *args, action(routine, len(args)), retn()).items
    assert item.text == "The treasure is buried here."
    assert item.metadata["needs_llm_gate"] is True
    assert item.metadata["bytecode_context"]["argument_index"] == 2


def test_nested_getter_consumes_its_key_without_claiming_the_message(tmp_path):
    result = extract_script(
        tmp_path,
        consts("Welcome to the city."),
        consti(0),
        consts("Recipient object tag."),
        action(200, 2),
        action(374, 2),
        retn(),
    )
    (item,) = result.items
    assert item.text == "Welcome to the city."
    assert (item.metadata["proven_player"], item.metadata["ncs_hint"]) == (True, "SendMessageToPC")


@pytest.mark.parametrize("opcode", [OP_EQUAL, OP_NEQUAL])
def test_binary_string_comparison_blocks_the_dispatch_literal(tmp_path, opcode):
    result = extract_script(
        tmp_path,
        consts("Open the hidden door."),
        cptopsp(-8),
        struct.pack(">BB", opcode, TYPE_STRING_STRING),
        jz(6),
        consti(0),
        consts("The door opens."),
        action(221, 2),
        retn(),
    )
    assert _texts(result) == ["The door opens."]


def test_unrelated_string_comparison_does_not_veto_pending_speech():
    context = _trace(
        consts("The guard speaks."),
        consts("a"),
        consts("b"),
        struct.pack(">BB", OP_EQUAL, TYPE_STRING_STRING),
        retn(),
    )
    assert context["compare_nearby"] is False


@pytest.mark.parametrize("barrier", [jmp(6), jz(6), jsr(6), retn(), action(9999, 0), cptopsp(-4)])
def test_uncertain_flow_cannot_borrow_later_speech_proof(tmp_path, barrier):
    result = extract_script(
        tmp_path,
        consts("An unresolved sentence."),
        barrier,
        consti(0),
        consts("Actual spoken text."),
        action(221, 2),
        retn(),
    )
    first, second = result.items
    assert (first.metadata["proven_player"], first.metadata["needs_llm_gate"]) == (False, True)
    assert second.metadata["proven_player"] is True


def test_unproven_natural_word_remains_a_gate_candidate(tmp_path):
    (item,) = extract_script(
        tmp_path, consts("Good"), action(9999, 0), action(221, 2), retn()
    ).items
    assert item.text == "Good"
    assert (item.metadata["player_candidate"], item.metadata["proven_player"]) == (True, False)
    assert item.metadata["needs_llm_gate"] is True


def test_same_text_in_internal_and_spoken_slots_is_patched_selectively(tmp_path):
    text = "A shared phrase."
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
    result = extract_script(
        tmp_path,
        consts(text),
        consto(),
        action(51, 2),
        consti(0),
        consts(text),
        action(221, 2),
        retn(),
    )
    (item,) = result.items
    replacement = [(item.metadata["offset"], text, "Translated speech.")]
    assert patch_ncs_string_replacements(path, replacement, "cp1252") == 1
    strings = parse_ncs_bytes(path.read_bytes()).string_constants
    assert [instr.string_value for instr in strings] == [text, "Translated speech."]


@pytest.mark.parametrize(
    "routine, following",
    [
        (554, [consti(0), consti(1), consti(1), consto()]),
        (820, [consto()]),
        (284, [consti(100)]),
    ],
)
def test_display_arguments_are_kept(tmp_path, routine, following):
    result = extract_script(
        tmp_path,
        consts("You need the silver key."),
        *following,
        action(routine, len(following) + 1),
        retn(),
    )
    (item,) = result.items
    assert item.metadata["proven_player"] is True


@pytest.mark.parametrize("text", ["McArthur", "please report to the captain.", "oAssignedHorse"])
def test_display_evidence_overrules_word_shape_but_not_technical_use(tmp_path, text):
    shown = extract_script(tmp_path, consti(0), consts(text), action(221, 2), retn())
    assert _texts(shown) == [text]
    assert extract_script(tmp_path, consts(text), consto(), action(51, 2), retn()).items == []


def test_matching_source_does_not_promote_an_unresolved_identifier(tmp_path):
    (tmp_path / "scene.nss").write_text('void main() { SpeakString("Pentanar"); }')
    assert extract_script(tmp_path, consts("Pentanar"), action(9999, 0), retn()).items == []


# ---------------------------------------------------------------------------
# Consumer tracing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("internal_first", [False, True])
def test_technical_branch_overrules_display_branch(internal_first):
    display = consti(0) + cptopsp(-8) + action(221, 2) + movsp(-4) + retn()
    internal = consto() + action(51, 2) + retn()
    first, second = (internal, display) if internal_first else (display, internal)
    context = _trace(consts("A shared sentence."), consti(1), jz(6 + len(first)), first, second)
    assert context["role"] == "internal"


@pytest.mark.parametrize(
    "parts, role",
    [
        # Overwriting a local removes the old alias.
        (
            (
                consts("Actual speech."),
                consti(0),
                cptopsp(-8),
                action(221, 2),
                consts("New technical key."),
                struct.pack(">BBiH", 0x01, 0x01, -8, 4),  # CPDOWNSP
                movsp(-4),
                consto(),
                action(51, 2),
                retn(),
            ),
            "player",
        ),
        # A known wrapper follows the actual argument.
        (
            (
                consts("McArthur"),
                jsr(8),
                retn(),
                consti(0),
                cptopsp(-8),
                action(221, 2),
                movsp(-4),
                retn(),
            ),
            "player",
        ),
        # DESTRUCT keeps only the retained struct member.
        (
            (
                consts("Retained speech."),
                consti(42),
                struct.pack(">BBHHH", 0x21, 0x01, 8, 0, 4),
                action(221, 1),
                retn(),
            ),
            "player",
        ),
        (
            (
                consts("Retained speech."),
                consti(42),
                struct.pack(">BBHHH", 0x21, 0x01, 8, 4, 4),
                action(221, 1),
                retn(),
            ),
            None,
        ),
        # A deferred technical use overrules an immediate display.
        (
            (
                consts("A shared sentence."),
                struct.pack(">BBII", 0x2C, 0x10, 0, 4),  # STORE_STATE
                jmp(6 + len(_DEFERRED_KEY_USE)),
                _DEFERRED_KEY_USE,
                consto(),
                action(6, 2),  # the stored action consumes no ordinary stack slot
                action(221, 1),
                retn(),
            ),
            "internal",
        ),
        # AssignCommand does not pop the pending string.
        ((consts("Pending speech."), consto(), action(6, 2), action(221, 1)), "player"),
    ],
)
def test_consumer_role(parts, role):
    assert _trace(*parts)["role"] == role


@pytest.mark.parametrize(
    "parts, display_seen",
    [
        # An unknown or recursive call cannot prove display.
        ((consts("An unresolved phrase."), jsr(0), action(221, 1), retn()), None),
        ((consts("An unresolved phrase."), jsr(99999), action(221, 1), retn()), None),
        ((consts("An unresolved phrase."), action(9999, 0), action(221, 1), retn()), None),
        # NOPs leave a live alias beyond the exploration budget.
        (
            (
                consts("Shared speech."),
                consti(0),
                cptopsp(-8),
                action(221, 2),
                b"\x2d\x00" * 2050,
                consto(),
                action(51, 2),
                retn(),
            ),
            True,
        ),
        # An unmodeled global read prevents an exclusive display proof.
        (
            (
                consts("Shared speech."),
                struct.pack(">BBiH", 0x27, 0x01, -4, 4),  # CPTOPBP
                movsp(-4),
                action(221, 1),
                retn(),
            ),
            True,
        ),
        # A large stack discard does not enumerate untracked slots.
        ((consts("Discarded text."), movsp(-(2**31)), action(221, 1)), None),
    ],
)
def test_display_is_not_proven(parts, display_seen):
    context = _trace(*parts)
    assert context["consumer_proven"] is False
    if display_seen is not None:
        assert context["player_use_seen"] is display_seen
