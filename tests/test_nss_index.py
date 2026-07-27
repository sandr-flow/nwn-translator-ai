"""Tests for the module-wide .nss literal index (NSS-first NCS classification)."""

import struct

import pytest

from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.extractors.nss_index import (
    NssModuleIndex,
    classify_engine_arg,
    clear_index_cache,
    get_module_index,
    snippet_for_text,
    strip_comments,
)
from nwn_translator.file_handlers.ncs_parser import (
    NCS_HEADER,
    OP_ACTION,
    OP_CONST,
    TYPE_INT,
    TYPE_STRING,
    parse_ncs,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_index_cache()
    yield
    clear_index_cache()


def _build(tmp_path, **files):
    for name, content in files.items():
        (tmp_path / f"{name}.nss").write_text(content, encoding="cp1252")
    return NssModuleIndex.build(tmp_path)


# ---------------------------------------------------------------------------
# Engine argument tables
# ---------------------------------------------------------------------------


class TestEngineArgTable:
    def test_player_positions(self):
        assert classify_engine_arg("SpeakString", 0) == "player"
        assert classify_engine_arg("SendMessageToPC", 1) == "player"
        assert classify_engine_arg("FloatingTextStringOnCreature", 0) == "player"

    def test_internal_positions(self):
        assert classify_engine_arg("PrintString", 0) == "internal"
        assert classify_engine_arg("GetObjectByTag", 0) == "internal"
        assert classify_engine_arg("SpeakOneLinerConversation", 0) == "internal"

    def test_local_var_families_flag_var_name_only(self):
        assert classify_engine_arg("SetLocalString", 1) == "internal"
        assert classify_engine_arg("GetLocalInt", 1) == "internal"
        assert classify_engine_arg("DeleteLocalObject", 1) == "internal"
        # The *value* argument of SetLocalString may be spoken later.
        assert classify_engine_arg("SetLocalString", 2) is None

    def test_campaign_family_flags_db_and_var_names(self):
        assert classify_engine_arg("SetCampaignInt", 0) == "internal"
        assert classify_engine_arg("SetCampaignInt", 1) == "internal"
        assert classify_engine_arg("SetCampaignString", 2) is None

    def test_unknown_function(self):
        assert classify_engine_arg("MyCustomThing", 0) is None


# ---------------------------------------------------------------------------
# Lexical layer
# ---------------------------------------------------------------------------


class TestLexical:
    def test_strip_comments_preserves_literals(self):
        src = 'SpeakString("keep // this"); // drop this\n/* and this */ int x;'
        out = strip_comments(src)
        assert '"keep // this"' in out
        assert "drop this" not in out
        assert "and this" not in out

    def test_snippet_found_and_missing(self):
        content = 'line one\nSpeakString("Hello!");\nline three'
        snippet = snippet_for_text("Hello!", content)
        assert snippet is not None and "SpeakString" in snippet
        assert snippet_for_text("absent", content) is None


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_player_via_direct_call(self, tmp_path):
        index = _build(tmp_path, a='void main() { SpeakString("Hello, traveler!"); }')
        assert index.verdict("Hello, traveler!") == "player"
        assert index.player_consumer("Hello, traveler!") == "SpeakString"

    def test_parens_inside_literal(self, tmp_path):
        index = _build(tmp_path, a='void main() { SendMessageToPC(oPC, "Hello (Player)!"); }')
        assert index.verdict("Hello (Player)!") == "player"

    def test_internal_via_debug_call(self, tmp_path):
        index = _build(tmp_path, a='void main() { PrintString("nMin = " + IntToString(nMin)); }')
        assert index.verdict("nMin = ") == "internal"

    def test_var_name_internal_value_unknown(self, tmp_path):
        index = _build(
            tmp_path, a='void main() { SetLocalString(oPC, "MyVar", "Stored words here."); }'
        )
        assert index.verdict("MyVar") == "internal"
        assert index.verdict("Stored words here.") == "unknown"

    def test_assignment_only_is_unknown(self, tmp_path):
        index = _build(tmp_path, a='void main() { string sTag = "sometag"; }')
        assert index.verdict("sometag") == "unknown"

    def test_absent_literal(self, tmp_path):
        index = _build(tmp_path, a="void main() { }")
        assert index.verdict("never seen") == "absent"

    def test_compare_beats_player(self, tmp_path):
        index = _build(
            tmp_path,
            a='void main() { if (sChat == "animal empathy") SpeakString("animal empathy"); }',
        )
        assert index.verdict("animal empathy") == "compare"

    def test_compare_detected_across_files(self, tmp_path):
        index = _build(
            tmp_path,
            speak='void main() { SpeakString("open sesame"); }',
            check='void main() { if ("open sesame" != GetLocalString(oPC, "pw")) return; }',
        )
        assert index.verdict("open sesame") == "compare"

    def test_mixed_role_across_files(self, tmp_path):
        index = _build(
            tmp_path,
            speak='void main() { SpeakString("Oh yeah!"); }',
            tag='void main() { object o = GetObjectByTag("Oh yeah!"); }',
        )
        assert index.verdict("Oh yeah!") == "mixed"

    def test_nested_call_positions(self, tmp_path):
        # The literal inside IntToString must not inherit SendMessageToPC's slot 1.
        index = _build(
            tmp_path,
            a='void main() { SendMessageToPC(GetObjectByTag("HENCH_WP"), "Gold: " + IntToString(n)); }',
        )
        assert index.verdict("HENCH_WP") == "internal"
        assert index.verdict("Gold: ") == "player"

    def test_concatenated_literals_share_call_position(self, tmp_path):
        index = _build(
            tmp_path,
            a='void main() { SpeakString("It comes to " + IntToString(n) + " dinars."); }',
        )
        assert index.verdict("It comes to ") == "player"
        assert index.verdict(" dinars.") == "player"


# ---------------------------------------------------------------------------
# Local-variable flow
# ---------------------------------------------------------------------------


class TestVariableFlow:
    def test_random_bark_table_from_corpus(self, tmp_path):
        # Verbatim pattern from Almraiven (commoner_blk_inn.nss): assignments
        # in an if-chain, one SpeakString(sSpeak) at the bottom.
        src = """
void main()
{
    int iRandSpeak = d10();
    string sSpeak;
    if (iRandSpeak == 1)
        sSpeak = "Ye blasted drunken fool, watch where ye be goin'!";
    if (iRandSpeak == 2)
        sSpeak = "I be hearin' there bein' a storm brewin' this evenin'";
    SpeakString(sSpeak);
}
"""
        index = _build(tmp_path, commoner=src)
        assert index.verdict("I be hearin' there bein' a storm brewin' this evenin'") == "player"
        assert index.verdict("Ye blasted drunken fool, watch where ye be goin'!") == "player"

    def test_var_into_internal_consumer(self, tmp_path):
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    string sTag = "SPAWN_POINT_A";\n'
                "    object o = GetObjectByTag(sTag);\n"
                "}"
            ),
        )
        assert index.verdict("SPAWN_POINT_A") == "internal"

    def test_var_with_mixed_consumers(self, tmp_path):
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    string s = "Both ways";\n'
                "    SpeakString(s);\n"
                "    object o = GetObjectByTag(s);\n"
                "}"
            ),
        )
        assert index.verdict("Both ways") == "mixed"

    def test_concat_assignment_collects_all_literals(self, tmp_path):
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    string s = "The pot is " + IntToString(n) + " coins.";\n'
                "    SpeakString(s);\n"
                "}"
            ),
        )
        assert index.verdict("The pot is ") == "player"
        assert index.verdict(" coins.") == "player"

    def test_augmented_assignment(self, tmp_path):
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    string s = "Start. ";\n'
                '    s += "And more.";\n'
                "    SpeakString(s);\n"
                "}"
            ),
        )
        assert index.verdict("Start. ") == "player"
        assert index.verdict("And more.") == "player"

    def test_comparison_is_not_an_assignment(self, tmp_path):
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    if (sChat == "not assigned") return;\n'
                "    SpeakString(sChat);\n"
                "}"
            ),
        )
        # `==` must not register as an assignment into sChat; the literal is
        # a comparison target and stays blocked.
        assert index.verdict("not assigned") == "compare"

    def test_unconsumed_var_stays_unknown(self, tmp_path):
        index = _build(
            tmp_path,
            a='void main() { string s = "Nobody reads this."; }',
        )
        assert index.verdict("Nobody reads this.") == "unknown"

    def test_var_consumed_inside_concat_expression(self, tmp_path):
        # DMFI XP labels: sFloating = "Roleplaying Bonus"; ...
        # FloatingTextStringOnCreature(sFloating + " +3%", oTarget);
        index = _build(
            tmp_path,
            a=(
                "void main() {\n"
                '    string sFloating = "Roleplaying Bonus";\n'
                '    FloatingTextStringOnCreature(sFloating + " +3%", oTarget);\n'
                "}"
            ),
        )
        assert index.verdict("Roleplaying Bonus") == "player"

    def test_journal_plot_id_is_internal(self, tmp_path):
        index = _build(
            tmp_path,
            a='void main() { AddJournalQuestEntry("LA TOMBE DE VOTRE MERE.", 5, oPC); }',
        )
        assert index.verdict("LA TOMBE DE VOTRE MERE.") == "internal"


# ---------------------------------------------------------------------------
# Wrapper resolution
# ---------------------------------------------------------------------------


class TestWrappers:
    def test_speak_wrapper_from_corpus(self, tmp_path):
        # Verbatim pattern from A Dance with Rogues (do_bj_knock.nss).
        src = """
void Speak (object who, string text, int anim, float duration)
{
    AssignCommand (who, ActionSpeakString (text));
    DelayCommand (0.2f, AssignCommand (who, ActionPlayAnimation (anim, 1.0f, duration)));
}
void main()
{
    Speak (GetObjectByTag ("BJ1_Guard"), "What's she in for?", ANIMATION_NONE, 0.0f);
}
"""
        index = _build(tmp_path, do_bj_knock=src)
        assert index.verdict("What's she in for?") == "player"
        assert index.verdict("BJ1_Guard") == "internal"

    def test_wrapper_across_files(self, tmp_path):
        index = _build(
            tmp_path,
            inc="void Bark(string sText) { SpeakString(sText); }",
            user='void main() { Bark("Halt! Who goes there?"); }',
        )
        assert index.verdict("Halt! Who goes there?") == "player"

    def test_internal_wrapper(self, tmp_path):
        index = _build(
            tmp_path,
            inc="void Debug(string sMsg) { PrintString(sMsg); }",
            user='void main() { Debug("Reached waypoint 3"); }',
        )
        assert index.verdict("Reached waypoint 3") == "internal"

    def test_transitive_wrapper_chain(self, tmp_path):
        index = _build(
            tmp_path,
            inc=(
                "void Inner(string s) { FloatingTextStringOnCreature(s, GetFirstPC()); }\n"
                "void Outer(string sMessage) { Inner(sMessage); }"
            ),
            user='void main() { Outer("A quest has been updated."); }',
        )
        assert index.verdict("A quest has been updated.") == "player"

    def test_mixed_wrapper(self, tmp_path):
        index = _build(
            tmp_path,
            inc=(
                "void Remember(string sWhat) {\n"
                "    SetLocalString(GetFirstPC(), sWhat, sWhat);\n"
                "    SpeakString(sWhat);\n"
                "}"
            ),
            user='void main() { Remember("The password"); }',
        )
        assert index.verdict("The password") == "mixed"

    def test_unresolved_wrapper_stays_unknown(self, tmp_path):
        index = _build(
            tmp_path,
            inc="void Mystery(string s) { int n = GetStringLength(s); }",
            user='void main() { Mystery("Some plain sentence here."); }',
        )
        assert index.verdict("Some plain sentence here.") == "unknown"

    def test_recursive_wrapper_terminates(self, tmp_path):
        index = _build(
            tmp_path,
            inc="void Echo(string s) { Echo(s); }",
            user='void main() { Echo("loop forever"); }',
        )
        assert index.verdict("loop forever") == "unknown"


# ---------------------------------------------------------------------------
# Extractor integration (index as primary oracle, bytecode as fallback)
# ---------------------------------------------------------------------------


def _write_ncs(tmp_path, name, *chunks):
    path = tmp_path / name
    path.write_bytes(NCS_HEADER + b"".join(chunks))
    return path


def _consts(text: str) -> bytes:
    encoded = text.encode("cp1252")
    return struct.pack(">BB", OP_CONST, TYPE_STRING) + struct.pack(">H", len(encoded)) + encoded


def _action(routine: int, argc: int) -> bytes:
    return struct.pack(">BB", OP_ACTION, 0x00) + struct.pack(">H", routine) + bytes([argc])


def _retn() -> bytes:
    return b"\x20\x00"


class TestExtractorWithSources:
    def test_source_player_overrides_adjacent_internal_bytecode(self, tmp_path):
        """DelayCommand pattern: bytecode sees GetLocalInt first, source knows better."""
        path = _write_ncs(
            tmp_path,
            "scene.ncs",
            _consts("Get on your knees, worm!"),
            _action(51, 2),  # GetLocalInt — would be a strong internal verdict
            _retn(),
        )
        (tmp_path / "scene.nss").write_text(
            "void Speak(object who, string text) { AssignCommand(who, ActionSpeakString(text)); }\n"
            'void main() { Speak(oGuard, "Get on your knees, worm!"); }',
            encoding="cp1252",
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert [i.text for i in result.items] == ["Get on your knees, worm!"]
        assert result.items[0].metadata["confidence"] == "high"
        assert result.items[0].metadata["proven_player"] is True
        assert result.items[0].metadata["needs_llm_gate"] is False

    def test_source_internal_drops_despite_player_bytecode_window(self, tmp_path):
        """A tag pushed before a distant SpeakString must not ride the window."""
        path = _write_ncs(
            tmp_path,
            "scene.ncs",
            _consts("HENCH_SPAWN_WP"),
            _consts("Follow me!"),
            _action(221, 1),  # SpeakString
            _retn(),
        )
        (tmp_path / "scene.nss").write_text(
            "void main() {\n"
            '    object oWP = GetWaypointByTag("HENCH_SPAWN_WP");\n'
            '    SpeakString("Follow me!");\n'
            "}",
            encoding="cp1252",
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert [i.text for i in result.items] == ["Follow me!"]

    def test_source_compare_drops_spoken_elsewhere(self, tmp_path):
        path = _write_ncs(
            tmp_path,
            "scene.ncs",
            _consts("open sesame"),
            _action(221, 1),
            _retn(),
        )
        (tmp_path / "scene.nss").write_text(
            'void main() { SpeakString("open sesame"); }', encoding="cp1252"
        )
        (tmp_path / "door.nss").write_text(
            'void main() { if (GetLocalString(oPC, "pw") == "open sesame") OpenDoor(); }',
            encoding="cp1252",
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert result.items == []

    def test_mixed_role_goes_to_gate(self, tmp_path):
        path = _write_ncs(
            tmp_path,
            "scene.ncs",
            _consts("Oh yeah! That is the spot."),
            _retn(),
        )
        (tmp_path / "scene.nss").write_text(
            'void main() { SpeakString("Oh yeah! That is the spot."); }', encoding="cp1252"
        )
        (tmp_path / "tagged.nss").write_text(
            'void main() { object o = GetObjectByTag("Oh yeah! That is the spot."); }',
            encoding="cp1252",
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert len(result.items) == 1
        meta = result.items[0].metadata
        assert meta["needs_llm_gate"] is True
        assert meta["confidence"] == "low"
        assert meta["source_class"] == "mixed"

    def test_no_sources_falls_back_to_bytecode(self, tmp_path):
        path = _write_ncs(
            tmp_path,
            "orphan.ncs",
            _consts("Welcome, hero!"),
            _action(374, 2),  # SendMessageToPC
            _retn(),
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert [i.text for i in result.items] == ["Welcome, hero!"]
        assert result.items[0].metadata["source_class"] == "absent"

    def test_absent_from_sources_still_uses_bytecode(self, tmp_path):
        """Sources exist but lack the literal (stale .nss) — bytecode decides."""
        path = _write_ncs(
            tmp_path,
            "scene.ncs",
            _consts("You cannot rest here."),
            _action(526, 2),  # FloatingTextStringOnCreature
            _retn(),
        )
        (tmp_path / "other.nss").write_text("void main() { int n = 1; }", encoding="cp1252")
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert [i.text for i in result.items] == ["You cannot rest here."]

    def test_emote_survives_with_source_proof(self, tmp_path):
        """Soft rejection rules are waived for source-proven speech."""
        path = _write_ncs(tmp_path, "scene.ncs", _consts("*sniff*"), _retn())
        (tmp_path / "scene.nss").write_text(
            'void main() { AssignCommand(oNPC, ActionSpeakString("*sniff*")); }',
            encoding="cp1252",
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert [i.text for i in result.items] == ["*sniff*"]

    def test_gate_items_carry_module_snippet(self, tmp_path):
        path = _write_ncs(tmp_path, "scene.ncs", _consts("Some plain sentence here."), _retn())
        (tmp_path / "scene.nss").write_text(
            'void main() { string s = "Some plain sentence here."; }', encoding="cp1252"
        )
        result = NcsExtractor().extract(path, {"_ncs_file": parse_ncs(path)})
        assert len(result.items) == 1
        meta = result.items[0].metadata
        assert meta["needs_llm_gate"] is True
        assert meta["nss_snippet"] is not None
        assert "Some plain sentence here." in meta["nss_snippet"]

    def test_index_cache_reused_across_files_in_module(self, tmp_path):
        (tmp_path / "one.nss").write_text('void main() { SpeakString("Hi."); }', "cp1252")
        first = get_module_index(tmp_path)
        second = get_module_index(tmp_path)
        assert first is second
