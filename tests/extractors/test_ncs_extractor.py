"""NCS string selection: text filters, the hard veto and the extracted candidates."""

import struct

import pytest

from nwn_translator.extractors.ncs_context import ACTION_SIGNATURES, PLAYER_FACING_ACTIONS
from nwn_translator.extractors.ncs_extractor import (
    _is_definitely_not_translatable,
    _is_likely_translatable,
    ncs_hard_veto_reason,
)
from nwn_translator.extractors.nss_index import classify_engine_arg
from nwn_translator.formats.ncs import OP_EQUAL, TYPE_STRING_STRING
from tests.support.ncs import action, consti, consto, consts, extract_script, movsp, retn

#: Any veto reason.
VETOED = object()


def _texts(result):
    return [item.text for item in result.items]


# ---------------------------------------------------------------------------
# Text filters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "a",
        "ab",
        "nw_c2_default1",
        "my_variable_name",
        "MY_VARIABLE",
        "NW_FLAG_HEARTBEAT",
        "door_locked",
        "npc_merchant",
        "12345",
        "3.14",
        "nw_something",
        "x2_somefile",
        "******",
        "*******",
        "***NEW TREASURE***",
        "**DESIGN***",
        "---separator---",
        "nMin = ",
        "nMax = ",
        "GetRange.nHD = ",
        "Level 1 Class Level = ",
        "blank item passed into dbCreateItemOnObject. Please report as bug to Brent.",
        "GENERIC SCRIPT DEBUG STRING ********** ",
        "Generic Generic or Specific; error: 3524",
        "USING SPAWN IN CONDITION NOW BASTARDO",
        # Waypoint tags from the corpus; they must not reach the LLM gate.
        "WP_DPR1_CloseDoor",
        "WP_CoN_Parishoner",
        "WP_MerudocRuns_01",
        "WP_DP1_GateFXCenter",
    ],
)
def test_technical_strings_are_rejected(text):
    assert _is_definitely_not_translatable(text)


@pytest.mark.parametrize(
    "text",
    [
        "Welcome, adventurer!",
        "The door is locked.",
        "You're kidding me.",
        "That's the best you can do?",
        "I'm not dead. I'm getting better.",
        "I'm okay, sir. I think.",
        "Opening in 10 seconds...",
        "Help me, please!",
        "I will not go back there.",
    ],
)
def test_player_text_is_kept(text):
    assert not _is_definitely_not_translatable(text)


@pytest.mark.parametrize(
    "text, plain, proven",
    [
        # Soft rules are waived for provably spoken strings; hard rules are not.
        ("*sniff*", True, False),
        ("Goodbye", True, False),
        ("+", True, True),
        ("3.14", True, True),
        ("nMin = ", True, True),
    ],
)
def test_proven_player_waives_only_the_soft_rules(text, plain, proven):
    assert _is_definitely_not_translatable(text) is plain
    assert _is_definitely_not_translatable(text, proven_player=True) is proven


@pytest.mark.parametrize(
    "text, likely",
    [
        ("Welcome to the tavern, stranger.", True),
        ("You don't have enough gold!", True),
        ("OK", False),
        # Three or more words without punctuation do not pass.
        ("Create Generic Martial", False),
        ("I am fighter or paladin", False),
    ],
)
def test_likely_translatable(text, likely):
    assert _is_likely_translatable(text) is likely


@pytest.mark.parametrize(
    "text, found",
    [
        ("DetermineClassToUse: This character is invalid", True),
        ("In CreateTable2Item", True),
        ("Class from determineClass ", True),
        ("Welcome to the tavern, stranger.", False),
        ("You're kidding me.", False),
    ],
)
def test_code_identifiers(text, found):
    assert (ncs_hard_veto_reason(text, is_concat=True) == "code_identifier") is found


@pytest.mark.parametrize(
    "text, kwargs, reason",
    [
        ("MY_FLAG_HEARTBEAT", {}, "upper_case_constant"),
        ("nw_c2_default1", {}, "known_internal_prefix"),
        ("Pull_K30_Monsters", {}, "underscore_identifier"),
        ("DetermineClassToUse: invalid.", {}, "code_identifier"),
        ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", {}, "alphabet_dump"),
        ("Welcome, adventurer!", {}, None),
        ("Welcome, adventurer! ", {}, None),
        ("You must wait ", {}, "sentence_fragment"),
        (" hour(s) before resting again.", {}, "sentence_fragment"),
        (" hour(s) before resting.", {}, "sentence_fragment"),
        ("*sniff*", {}, None),
        # One-word barks are vetoed only without proof; letter case proves nothing.
        ("Goodbye", {}, "resref_like_identifier"),
        ("Goodbye", {"proven_player": True}, None),
        ("Farewell", {"proven_player": True}, None),
        ("hello", {"proven_player": True}, None),
        # Identifiers stay vetoed despite proof.
        ("NRB1_Guard", {"proven_player": True}, VETOED),
        ("nw_foo", {"proven_player": True}, "known_internal_prefix"),
        ("X3_HORSE_NOMOUNT", {"proven_player": True}, VETOED),
        # A concatenation is one utterance: the fragment rule does not apply to it.
        ("Congrats to ye, ", {}, "sentence_fragment"),
        ("Congrats to ye, <VAR1>. How do ye feel?", {"is_concat": True}, None),
        ("LastOpener: GetLastOpenedBy <VAR1>", {"is_concat": True}, "code_identifier"),
    ],
)
def test_hard_veto(text, kwargs, reason):
    actual = ncs_hard_veto_reason(text, **kwargs)
    if reason is VETOED:
        assert actual is not None
    else:
        assert actual == reason


# ---------------------------------------------------------------------------
# Routine tables (verified against nwscript.nss, where routine id == prototype index)
# ---------------------------------------------------------------------------


def _internal_actions() -> set:
    """Routine ids with at least one argument classified as internal."""
    return {
        routine
        for routine, (name, params, _) in ACTION_SIGNATURES.items()
        if any(classify_engine_arg(name, arg) == "internal" for arg in range(len(params)))
    }


def test_routine_tables_pin_the_key_ids():
    """A typo in a routine id would silently reclassify strings."""
    assert {39, 221, 284, 374, 526, 554, 820, 830, 837, 858, 860, 901} == PLAYER_FACING_ACTIONS
    # GetLocal* 51-54 and SetLocal* 55-58; the wrong old ids (13-17, 29-33) let the
    # classifier scan past variable reads into a later speech call.
    assert {51, 52, 53, 54, 55, 56, 57, 58} <= _internal_actions()
    assert 417 in _internal_actions()  # SpeakOneLinerConversation: resref argument
    # 468 is EffectBlindness, 525 the StrRef variant without a string argument,
    # 761 GetStoreMaxBuyPrice: none of them displays a string.
    assert not {468, 525, 761} & PLAYER_FACING_ACTIONS
    assert not {13, 14, 15, 16, 17, 29, 32, 33} & _internal_actions()


# ---------------------------------------------------------------------------
# Extracted candidates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts, texts",
    [
        # An adjacent internal consumer drops the literal, even a sentence.
        ((consts("NPC_MERCHANT"), action(46, 1), retn()), []),  # GetObjectByTag
        ((consts("Debug: some internal message here."), action(1, 1), retn()), []),  # PrintString
        ((consts("Debug trace: entering combat round."), action(1, 1), retn()), []),
        ((consts("Goodbye"), action(53, 2), retn()), []),  # GetLocalString: a var name
        # Identifier shapes and code are rejected without any consumer, and when spoken.
        ((consts("nw_c2_default9"), retn()), []),
        ((consts("DetermineClassToUse: This character is invalid."), retn()), []),
        ((consts("nw_c2_default9"), action(221, 2), retn()), []),
        ((consts("my_var_name"), action(221, 2), retn()), []),
        ((consts("NW_FLAG_HEARTBEAT"), action(221, 2), retn()), []),
        # Concatenation operand: SpeakString("..." + IntToString(n) + "+").
        ((consts("+"), action(221, 2), retn()), []),
        # Soft rules are waived only for spoken strings.
        ((consts("*sniff*"), retn()), []),
        ((consts("Goodbye"), action(221, 2), retn()), ["Goodbye"]),
        ((consts("NOBODY MOVES AN INCH!!!"), action(221, 2), retn()), ["NOBODY MOVES AN INCH!!!"]),
        ((consts("NOBODY MOVES AN INCH!!!"), retn()), []),
        (
            (consts("You feel a chill down your spine."), action(374, 2), retn()),
            ["You feel a chill down your spine."],
        ),
        # A var name read by GetLocalInt (51) before a later SpeakString is dropped.
        (
            (
                consts("X3_HORSE_NOMOUNT"),
                action(51, 2),
                consts("Hello there, friend."),
                action(221, 2),
                retn(),
            ),
            ["Hello there, friend."],
        ),
        # A string compare before the speech call marks a dispatch key.
        (
            (
                consts("animal empathy"),
                struct.pack(">BB", OP_EQUAL, TYPE_STRING_STRING),
                action(221, 2),
                retn(),
            ),
            [],
        ),
    ],
)
def test_selected_literals(tmp_path, parts, texts):
    assert _texts(extract_script(tmp_path, *parts)) == texts


def test_player_facing_string_is_a_high_confidence_item(tmp_path):
    result = extract_script(
        tmp_path, consts("Welcome, hero!"), consto(), action(374, 2), retn(), name="test.ncs"
    )
    (item,) = result.items
    assert (item.text, item.item_id) == ("Welcome, hero!", "test:c0")
    assert item.metadata["confidence"] == "high"
    assert result.content_type == "ncs_script"


def test_only_the_consumed_string_borrows_the_speech_proof(tmp_path):
    """An unrelated stack value must not borrow a later speech call's proof."""
    result = extract_script(
        tmp_path,
        consts("First line."),
        consti(0),  # talk volume of the second line
        consts("Second line."),
        action(39, 2),  # ActionSpeakString consumes only the second line
        retn(),
    )
    assert _texts(result) == ["First line.", "Second line."]
    assert result.items[0].metadata["needs_llm_gate"] is True
    assert result.items[1].metadata["confidence"] == "high"


def test_int_compare_before_speech_is_not_a_string_dispatch(tmp_path):
    """Penultima's ``if (isay == 0) {willsay = "Ow.";} ... SpeakString(willsay);``.

    Flagging ``compare_nearby`` here would silently drop player-facing barks.
    """
    result = extract_script(
        tmp_path,
        consts("Ow."),
        consti(1),
        consti(0),
        struct.pack(">BB", OP_EQUAL, 0x20),
        movsp(-4),
        action(39, 1),  # ActionSpeakString
        retn(),
    )
    (item,) = result.items
    context = item.metadata["bytecode_context"]
    assert (context["compare_nearby"], context["consumer_proven"]) == (False, True)
    assert item.metadata["needs_llm_gate"] is True


def test_sentence_without_a_consumer_goes_to_the_gate(tmp_path):
    (item,) = extract_script(tmp_path, consts("Something happened nearby."), retn()).items
    assert (item.metadata["confidence"], item.metadata["needs_llm_gate"]) == ("medium", True)


def test_every_occurrence_of_a_literal_is_its_own_item(tmp_path):
    result = extract_script(
        tmp_path,
        consts("Duplicate text here."),
        action(374, 2),
        consts("Duplicate text here."),
        action(374, 2),
        retn(),
    )
    assert len({item.metadata["offset"] for item in result.items}) == len(result.items) == 2


@pytest.mark.parametrize(
    "source, snippet_has, confidence",
    [
        # Source context alone cannot establish a compiled consumer.
        ('void main() { PrintString("The treasure has been generated."); }', "PrintString", None),
        ('void main() { SpeakString("Greetings, adventurer!"); }', "SpeakString", "medium"),
    ],
)
def test_matching_source_is_gate_context_only(tmp_path, source, snippet_has, confidence):
    text = source.split('"')[1]
    (tmp_path / "test.nss").write_text(source, encoding="utf-8")
    (item,) = extract_script(tmp_path, consts(text), retn(), name="test.ncs").items
    assert snippet_has in item.metadata["nss_snippet"]
    assert item.metadata["proven_player"] is False
    if confidence:
        assert item.metadata["confidence"] == confidence


def test_proven_speech_and_floating_text(tmp_path):
    emote = extract_script(tmp_path, consts("*sniff*"), action(221, 2), retn())
    assert _texts(emote) == ["*sniff*"]
    assert emote.items[0].metadata["proven_player"] is True
    # FloatingTextStringOnCreature is 526; the old table had 468/525.
    (floating,) = extract_script(tmp_path, consts("Ouch!"), action(526, 2), retn()).items
    assert floating.metadata["confidence"] == "high"
    assert floating.metadata["ncs_hint"] == "FloatingTextStringOnCreature"


@pytest.mark.parametrize(
    "parts, text, confidence",
    [
        # ADwR do_bj_knock: DelayCommand intervenes before a var read; the
        # non-adjacent GetLocalInt must not bury the line. The var name,
        # adjacent to its GetLocalInt, is dropped.
        (
            (
                consts("What's she in for?"),
                action(7, 2),
                consts("BJArrested"),
                action(51, 2),
                retn(),
            ),
            "What's she in for?",
            None,
        ),
        # Almraiven trainer barks: AssignCommand intervenes and the internal
        # consumer farther on is weak evidence only.
        (
            (consts("Excellent form, Raywen!"), action(6, 2), action(51, 2), retn()),
            "Excellent form, Raywen!",
            "medium",
        ),
    ],
)
def test_speech_behind_an_unknown_call_goes_to_the_gate(tmp_path, parts, text, confidence):
    (item,) = extract_script(tmp_path, *parts).items
    assert item.text == text
    assert (item.metadata["needs_llm_gate"], item.metadata["proven_player"]) == (True, False)
    if confidence:
        assert item.metadata["confidence"] == confidence


@pytest.mark.parametrize("word", ["hello", "HELP", "oui", "OK"])
def test_displayed_words_do_not_depend_on_capitalization(tmp_path, word):
    result = extract_script(tmp_path, consti(0), consts(word), action(221, 2), retn())
    assert _texts(result) == [word]
    assert result.items[0].metadata["needs_llm_gate"] is True
