"""Tests for NCS bytecode parser, patcher, extractor, and injector."""

import struct
import tempfile
from pathlib import Path

import pytest

from nwn_translator.file_handlers.ncs_parser import (
    NCS_HEADER,
    NCSFile,
    NCSInstruction,
    NCSParseError,
    OP_ACTION,
    OP_CONST,
    OP_JMP,
    OP_JSR,
    OP_JZ,
    OP_JNZ,
    OP_RETN,
    OP_MOVSP,
    TYPE_INT,
    TYPE_STRING,
    parse_ncs,
    parse_ncs_bytes,
)
from nwn_translator.file_handlers.ncs_patcher import (
    NCSPatchError,
    patch_ncs_string_replacements,
    patch_ncs_strings,
)
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.extractors.ncs_extractor import (
    NcsExtractor,
    _is_definitely_not_translatable,
    _is_likely_translatable,
    _contains_code_identifiers,
    _classify_from_source,
)
from nwn_translator.injectors.ncs_injector import NcsInjector

# ---------------------------------------------------------------------------
# Bytecode builder helpers
# ---------------------------------------------------------------------------


def _header() -> bytes:
    return NCS_HEADER


def _consts(text: str, encoding: str = "cp1252") -> bytes:
    """Build a CONSTS (string) instruction."""
    encoded = text.encode(encoding)
    return struct.pack(">BB", OP_CONST, TYPE_STRING) + struct.pack(">H", len(encoded)) + encoded


def _consti(value: int) -> bytes:
    """Build a CONSTI (integer) instruction."""
    return struct.pack(">BB", OP_CONST, TYPE_INT) + struct.pack(">i", value)


def _jmp(offset: int) -> bytes:
    """Build a JMP instruction with the given relative offset."""
    return struct.pack(">BB", OP_JMP, 0x00) + struct.pack(">i", offset)


def _jz(offset: int) -> bytes:
    """Build a JZ instruction."""
    return struct.pack(">BB", OP_JZ, 0x00) + struct.pack(">i", offset)


def _jnz(offset: int) -> bytes:
    """Build a JNZ instruction."""
    return struct.pack(">BB", OP_JNZ, 0x00) + struct.pack(">i", offset)


def _jsr(offset: int) -> bytes:
    """Build a JSR instruction."""
    return struct.pack(">BB", OP_JSR, 0x00) + struct.pack(">i", offset)


def _retn() -> bytes:
    """Build a RETN instruction."""
    return struct.pack(">BB", OP_RETN, 0x00)


def _action(routine: int, arg_count: int = 1) -> bytes:
    """Build an ACTION instruction."""
    return (
        struct.pack(">BB", OP_ACTION, 0x00)
        + struct.pack(">H", routine)
        + struct.pack(">B", arg_count)
    )


def _movsp(displacement: int) -> bytes:
    """Build a MOVSP instruction."""
    return struct.pack(">BB", OP_MOVSP, 0x00) + struct.pack(">i", displacement)


def _write_ncs(tmp_dir: Path, name: str, *parts: bytes) -> Path:
    """Write an NCS file from header + instruction parts."""
    data = _header() + b"".join(parts)
    path = tmp_dir / name
    path.write_bytes(data)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# Parser tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNCSParser:
    """Tests for ncs_parser.py."""

    def test_valid_header(self):
        data = _header() + _retn()
        ncs = parse_ncs_bytes(data)
        assert ncs.header == NCS_HEADER
        assert len(ncs.instructions) == 1
        assert ncs.instructions[0].opcode == OP_RETN

    def test_invalid_header(self):
        with pytest.raises(NCSParseError, match="Invalid NCS header"):
            parse_ncs_bytes(b"GFF V3.2" + _retn())

    def test_too_short(self):
        with pytest.raises(NCSParseError, match="too small"):
            parse_ncs_bytes(b"NCS")

    def test_empty_script(self):
        """Header-only file with no instructions."""
        ncs = parse_ncs_bytes(_header())
        assert len(ncs.instructions) == 0

    def test_parse_consts(self):
        data = _header() + _consts("Hello, World!")
        ncs = parse_ncs_bytes(data)
        assert len(ncs.instructions) == 1
        instr = ncs.instructions[0]
        assert instr.is_string_const
        assert instr.string_value == "Hello, World!"
        assert instr.offset == 8
        assert instr.size == 4 + len("Hello, World!")

    def test_parse_consti(self):
        data = _header() + _consti(42)
        ncs = parse_ncs_bytes(data)
        assert len(ncs.instructions) == 1
        instr = ncs.instructions[0]
        assert instr.opcode == OP_CONST
        assert instr.type_byte == TYPE_INT
        assert not instr.is_string_const

    def test_parse_jump(self):
        data = _header() + _jmp(10)
        ncs = parse_ncs_bytes(data)
        instr = ncs.instructions[0]
        assert instr.is_jump
        assert instr.jump_offset == 10

    def test_parse_action(self):
        data = _header() + _action(374, 2)
        ncs = parse_ncs_bytes(data)
        instr = ncs.instructions[0]
        assert instr.is_action
        assert instr.action_routine == 374
        assert instr.action_arg_count == 2

    def test_parse_nwn_ee_script_size_prefix(self):
        """NWN:EE / Beamdog NCS: 0x42 + uint32 BE length after banner (xoreos)."""
        body = _consts("Hello, EE!") + _action(39, 1) + _retn()
        total_len = len(NCS_HEADER) + 5 + len(body)
        data = NCS_HEADER + struct.pack(">BI", 0x42, total_len) + body
        ncs = parse_ncs_bytes(data)
        consts = [i for i in ncs.instructions if i.is_string_const]
        assert len(consts) == 1
        assert consts[0].string_value == "Hello, EE!"
        assert consts[0].offset == 13

    def test_multiple_instructions(self):
        data = _header() + _consts("Test") + _consti(1) + _jmp(6) + _retn()
        ncs = parse_ncs_bytes(data)
        assert len(ncs.instructions) == 4
        # Verify offsets are sequential
        expected_offset = 8
        for instr in ncs.instructions:
            assert instr.offset == expected_offset
            expected_offset += instr.size

    def test_parse_from_file(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _consts("File test"), _retn())
        ncs = parse_ncs(path)
        assert ncs.instructions[0].string_value == "File test"

    def test_parse_file_not_found(self, tmp_path):
        with pytest.raises(NCSParseError, match="not found"):
            parse_ncs(tmp_path / "nonexistent.ncs")

    def test_string_constants_property(self):
        data = _header() + _consts("Hello") + _consti(42) + _consts("World") + _retn()
        ncs = parse_ncs_bytes(data)
        strings = ncs.string_constants
        assert len(strings) == 2
        assert strings[0].string_value == "Hello"
        assert strings[1].string_value == "World"


# ═══════════════════════════════════════════════════════════════════════════
# Patcher tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNCSPatcher:
    """Tests for ncs_patcher.py."""

    def test_no_matching_strings(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _consts("Hello"), _retn())
        count = patch_ncs_strings(path, {"Goodbye": "Au revoir"})
        assert count == 0

    def test_same_length_replacement(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _consts("AAAA"), _retn())
        count = patch_ncs_strings(path, {"AAAA": "BBBB"})
        assert count == 1
        ncs = parse_ncs(path)
        assert ncs.instructions[0].string_value == "BBBB"

    def test_longer_replacement(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _consts("Hi"), _retn())
        original_size = path.stat().st_size
        count = patch_ncs_strings(path, {"Hi": "Hello there!"})
        assert count == 1
        ncs = parse_ncs(path)
        assert ncs.instructions[0].string_value == "Hello there!"
        assert path.stat().st_size == original_size + (len("Hello there!") - len("Hi"))

    def test_shorter_replacement(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _consts("Hello there!"), _retn())
        count = patch_ncs_strings(path, {"Hello there!": "Hi"})
        assert count == 1
        ncs = parse_ncs(path)
        assert ncs.instructions[0].string_value == "Hi"

    def test_forward_jump_adjustment(self, tmp_path):
        """JMP before CONSTS, target after CONSTS -- offset must increase."""
        # Layout: [JMP +offset] [CONSTS "AB"] [RETN]
        # JMP should land on RETN.
        jmp_size = 6
        consts_size = 4 + 2  # "AB" is 2 bytes
        jmp_offset = jmp_size + consts_size  # jump from JMP to RETN
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _jmp(jmp_offset),
            _consts("AB"),
            _retn(),
        )
        # Replace "AB" with "ABCDEF" (+4 bytes)
        count = patch_ncs_strings(path, {"AB": "ABCDEF"})
        assert count == 1
        ncs = parse_ncs(path)
        # JMP should now have offset increased by 4
        jmp_instr = ncs.instructions[0]
        assert jmp_instr.is_jump
        # The target should still be RETN
        retn_instr = ncs.instructions[2]
        assert retn_instr.opcode == OP_RETN
        assert jmp_instr.offset + jmp_instr.jump_offset == retn_instr.offset

    def test_backward_jump_adjustment(self, tmp_path):
        """JMP after CONSTS, target before CONSTS -- offset must adjust."""
        # Layout: [RETN] [CONSTS "AB"] [JMP back to RETN]
        retn_bytes = _retn()  # 2 bytes at offset 8
        consts_bytes = _consts("AB")  # 6 bytes at offset 10
        # JMP at offset 16, target is RETN at offset 8
        jmp_target_offset = 8 - 16  # -8
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            retn_bytes,
            consts_bytes,
            _jmp(jmp_target_offset),
        )
        # Replace "AB" with "ABCDEF" (+4 bytes)
        count = patch_ncs_strings(path, {"AB": "ABCDEF"})
        assert count == 1
        ncs = parse_ncs(path)
        jmp_instr = ncs.instructions[2]
        retn_instr = ncs.instructions[0]
        # Target should still be RETN
        assert jmp_instr.offset + jmp_instr.jump_offset == retn_instr.offset

    def test_jump_not_crossing_unchanged(self, tmp_path):
        """JMP and target both before CONSTS -- offset unchanged."""
        # Layout: [JMP to RETN] [RETN] [CONSTS "AB"] [RETN]
        jmp_offset = 6  # JMP(6 bytes) -> RETN at offset 14
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _jmp(jmp_offset),
            _retn(),
            _consts("AB"),
            _retn(),
        )
        ncs_before = parse_ncs(path)
        jmp_before = ncs_before.instructions[0].jump_offset

        count = patch_ncs_strings(path, {"AB": "ABCDEF"})
        assert count == 1
        ncs_after = parse_ncs(path)
        # JMP offset should be unchanged (both source and target before CONSTS)
        assert ncs_after.instructions[0].jump_offset == jmp_before

    def test_multiple_patches(self, tmp_path):
        """Replace two strings in the same file."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("First"),
            _consts("Second"),
            _retn(),
        )
        count = patch_ncs_strings(
            path,
            {
                "First": "Eerste",
                "Second": "Tweede!!!",
            },
        )
        assert count == 2
        ncs = parse_ncs(path)
        assert ncs.instructions[0].string_value == "Eerste"
        assert ncs.instructions[1].string_value == "Tweede!!!"

    def test_identity_replacement_skipped(self, tmp_path):
        """Replacing string with itself should be a no-op."""
        path = _write_ncs(tmp_path, "test.ncs", _consts("Same"), _retn())
        count = patch_ncs_strings(path, {"Same": "Same"})
        assert count == 0

    def test_complex_jump_scenario(self, tmp_path):
        """Multiple jumps and strings -- verify all targets remain correct."""
        # Layout:
        # [0] JSR -> subroutine (forward)
        # [1] CONSTS "msg1"
        # [2] ACTION SendMessageToPC(374)
        # [3] RETN
        # [4] CONSTS "msg2"   <-- subroutine start
        # [5] ACTION SpeakString(39)
        # [6] RETN

        # Calculate offsets manually
        jsr_size = 6
        consts1 = _consts("msg1")
        consts1_size = len(consts1)
        action1_size = 5
        retn_size = 2
        consts2 = _consts("msg2")
        consts2_size = len(consts2)

        # JSR target: offset of CONSTS "msg2" = header + jsr + consts1 + action1 + retn
        sub_offset = 8 + jsr_size + consts1_size + action1_size + retn_size
        jsr_offset_value = sub_offset - 8  # relative to JSR at offset 8

        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _jsr(jsr_offset_value),
            consts1,
            _action(374, 2),
            _retn(),
            consts2,
            _action(39, 1),
            _retn(),
        )

        # Verify initial parse
        ncs = parse_ncs(path)
        assert len(ncs.instructions) == 7
        jsr_instr = ncs.instructions[0]
        sub_instr = ncs.instructions[4]
        assert jsr_instr.offset + jsr_instr.jump_offset == sub_instr.offset

        # Patch both strings (longer replacements)
        count = patch_ncs_strings(
            path,
            {
                "msg1": "translated message one",
                "msg2": "translated message two",
            },
        )
        assert count == 2

        # Verify JSR still targets the subroutine CONSTS
        ncs = parse_ncs(path)
        jsr_instr = ncs.instructions[0]
        sub_instr = ncs.instructions[4]
        assert sub_instr.string_value == "translated message two"
        assert jsr_instr.offset + jsr_instr.jump_offset == sub_instr.offset

    def test_patch_ncs_string_replacements_selective_offset(self, tmp_path):
        """Only the listed offset is patched when the same literal appears twice."""
        path = _write_ncs(
            tmp_path,
            "dup.ncs",
            _consts("Same"),
            _retn(),
            _consts("Same"),
            _retn(),
        )
        ncs = parse_ncs(path)
        consts = [i for i in ncs.instructions if i.is_string_const]
        assert len(consts) == 2
        count = patch_ncs_string_replacements(
            path,
            [(consts[0].offset, "Same", "FirstOnly")],
        )
        assert count == 1
        ncs2 = parse_ncs(path)
        vals = [i.string_value for i in ncs2.instructions if i.is_string_const]
        assert vals.count("FirstOnly") == 1
        assert vals.count("Same") == 1


# ═══════════════════════════════════════════════════════════════════════════
# String filter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStringFilter:
    """Tests for string classification heuristics."""

    def test_reject_empty(self):
        assert _is_definitely_not_translatable("")
        assert _is_definitely_not_translatable("   ")

    def test_reject_short(self):
        assert _is_definitely_not_translatable("a")
        assert _is_definitely_not_translatable("ab")

    def test_reject_snake_case(self):
        assert _is_definitely_not_translatable("nw_c2_default1")
        assert _is_definitely_not_translatable("my_variable_name")

    def test_reject_upper_constant(self):
        assert _is_definitely_not_translatable("MY_VARIABLE")
        assert _is_definitely_not_translatable("NW_FLAG_HEARTBEAT")

    def test_reject_resref(self):
        assert _is_definitely_not_translatable("door_locked")
        assert _is_definitely_not_translatable("npc_merchant")

    def test_reject_numeric(self):
        assert _is_definitely_not_translatable("12345")
        assert _is_definitely_not_translatable("3.14")

    def test_reject_known_prefix(self):
        assert _is_definitely_not_translatable("nw_something")
        assert _is_definitely_not_translatable("x2_somefile")

    def test_accept_sentence(self):
        assert not _is_definitely_not_translatable("Welcome, adventurer!")
        assert not _is_definitely_not_translatable("The door is locked.")

    def test_likely_translatable_sentence(self):
        assert _is_likely_translatable("Welcome to the tavern, stranger.")
        assert _is_likely_translatable("You don't have enough gold!")

    def test_not_likely_without_punctuation_short(self):
        assert not _is_likely_translatable("OK")

    def test_not_likely_words_only_no_punctuation(self):
        """3+ words without punctuation should NOT pass anymore."""
        assert not _is_likely_translatable("Create Generic Martial")
        assert not _is_likely_translatable("I am fighter or paladin")

    def test_reject_separator_lines(self):
        assert _is_definitely_not_translatable("******")
        assert _is_definitely_not_translatable("*******")
        assert _is_definitely_not_translatable("***NEW TREASURE***")
        assert _is_definitely_not_translatable("**DESIGN***")
        assert _is_definitely_not_translatable("---separator---")

    def test_reject_variable_dump(self):
        assert _is_definitely_not_translatable("nMin = ")
        assert _is_definitely_not_translatable("nMax = ")
        assert _is_definitely_not_translatable("GetRange.nHD = ")
        assert _is_definitely_not_translatable("Level 1 Class Level = ")

    def test_reject_developer_messages(self):
        assert _is_definitely_not_translatable(
            "blank item passed into dbCreateItemOnObject. Please report as bug to Brent."
        )
        assert _is_definitely_not_translatable("GENERIC SCRIPT DEBUG STRING ********** ")
        assert _is_definitely_not_translatable("Generic Generic or Specific; error: 3524")

    def test_reject_all_caps_sentences(self):
        assert _is_definitely_not_translatable("USING SPAWN IN CONDITION NOW BASTARDO")

    def test_accept_normal_player_text(self):
        """Genuine player-facing strings must NOT be rejected."""
        assert not _is_definitely_not_translatable("You're kidding me.")
        assert not _is_definitely_not_translatable("That's the best you can do?")
        assert not _is_definitely_not_translatable("I'm not dead. I'm getting better.")
        assert not _is_definitely_not_translatable("I'm okay, sir. I think.")
        assert not _is_definitely_not_translatable("Opening in 10 seconds...")

    def test_contains_code_identifiers(self):
        assert _contains_code_identifiers("DetermineClassToUse: This character is invalid")
        assert _contains_code_identifiers("In CreateTable2Item")
        assert _contains_code_identifiers("Class from determineClass ")
        assert not _contains_code_identifiers("Welcome to the tavern, stranger.")
        assert not _contains_code_identifiers("You're kidding me.")

    def test_classify_from_source_player(self):
        nss = 'SpeakString("Hello, traveler!");'
        assert _classify_from_source("Hello, traveler!", nss) == "player"

    def test_classify_from_source_debug(self):
        nss = 'PrintString("nMin = " + IntToString(nMin));'
        assert _classify_from_source("nMin = ", nss) == "debug"

    def test_classify_from_source_unknown(self):
        nss = 'string sTag = "sometag";'
        assert _classify_from_source("sometag", nss) is None

    def test_classify_from_source_parens_in_string(self):
        """Regex must handle parentheses inside the string literal."""
        nss = 'SendMessageToPC(oPC, "Hello (Player)!");'
        assert _classify_from_source("Hello (Player)!", nss) == "player"


# ═══════════════════════════════════════════════════════════════════════════
# Extractor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNcsExtractor:
    """Tests for NcsExtractor."""

    def test_extract_player_facing_string(self, tmp_path):
        """String followed by SendMessageToPC ACTION should be extracted."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Welcome, hero!"),
            _action(374, 2),  # SendMessageToPC
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 1
        assert result.items[0].text == "Welcome, hero!"
        assert result.items[0].metadata["confidence"] == "high"

    def test_two_strings_before_player_action_both_high(self, tmp_path):
        """Multiple CONSTS pushed before one SpeakString — both classify as player."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("First line."),
            _consts("Second line."),
            _action(39, 2),  # SpeakString, 2 args on stack
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        texts = {it.text for it in result.items}
        assert texts == {"First line.", "Second line."}
        assert all(it.metadata["confidence"] == "high" for it in result.items)

    def test_skip_internal_string(self, tmp_path):
        """String followed by GetObjectByTag ACTION should be skipped."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("NPC_MERCHANT"),
            _action(46, 1),  # GetObjectByTag
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 0

    def test_skip_identifier_string(self, tmp_path):
        """snake_case string should be skipped even without ACTION context."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("nw_c2_default9"),
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 0

    def test_extract_ambiguous_string(self, tmp_path):
        """A sentence-like string without ACTION context goes through LLM gate."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Something happened nearby."),
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 1
        assert result.items[0].metadata["confidence"] == "medium"
        assert result.items[0].metadata.get("needs_llm_gate") is True

    def test_per_occurrence_extraction(self, tmp_path):
        """Same literal at two offsets produces two items (independent patching)."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Duplicate text here."),
            _action(374, 2),
            _consts("Duplicate text here."),
            _action(374, 2),
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 2
        offs = {it.metadata["offset"] for it in result.items}
        assert len(offs) == 2

    def test_no_ncs_data(self, tmp_path):
        """Missing _ncs_file key should return empty result."""
        extractor = NcsExtractor()
        result = extractor.extract(tmp_path / "fake.ncs", {})
        assert len(result.items) == 0

    def test_content_type(self, tmp_path):
        path = _write_ncs(tmp_path, "test.ncs", _retn())
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert result.content_type == "ncs_script"

    def test_skip_print_string(self, tmp_path):
        """PrintString (ACTION #1) should now be classified as internal."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Debug: some internal message here."),
            _action(1, 1),  # PrintString
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 0

    def test_no_cross_file_dedup(self, tmp_path):
        """Same string in different files must be extracted for each file."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("You feel a chill down your spine."),
            _action(374, 2),
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        # Even if another file had the same string, extractor must still return it
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 1

    def test_nss_source_skips_debug(self, tmp_path):
        """When .nss source shows PrintString, string should be skipped."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Generate Treasure nSpecific here."),
            _retn(),
        )
        # Create corresponding .nss file
        nss_path = tmp_path / "test.nss"
        nss_path.write_text(
            'void main() { PrintString("Generate Treasure nSpecific here."); }',
            encoding="utf-8",
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 0

    def test_nss_source_keeps_player(self, tmp_path):
        """When .nss source shows SpeakString, string should be extracted."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Greetings, adventurer!"),
            _retn(),
        )
        nss_path = tmp_path / "test.nss"
        nss_path.write_text(
            'void main() { SpeakString("Greetings, adventurer!"); }',
            encoding="utf-8",
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 1
        assert result.items[0].metadata["confidence"] == "high"

    def test_code_identifiers_downgrade_confidence(self, tmp_path):
        """Strings with CamelCase get low confidence and require LLM gate."""
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("DetermineClassToUse: This character is invalid."),
            _retn(),
        )
        ncs = parse_ncs(path)
        extractor = NcsExtractor()
        result = extractor.extract(path, {"_ncs_file": ncs})
        assert len(result.items) == 1
        assert result.items[0].metadata["confidence"] == "low"
        assert result.items[0].metadata.get("needs_llm_gate") is True


# ═══════════════════════════════════════════════════════════════════════════
# Injector tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNcsInjector:
    """Tests for NcsInjector."""

    def test_inject_success(self, tmp_path):
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Hello world!"),
            _retn(),
        )
        ncs = parse_ncs(path)
        const_instr = next(i for i in ncs.instructions if i.is_string_const)
        item = TranslatableItem(
            text="Hello world!",
            item_id="test:inject",
            location=str(path),
            metadata={
                "type": "ncs_string",
                "offset": const_instr.offset,
            },
        )
        injector = NcsInjector()
        result = injector.inject(
            path,
            {},
            {},
            {
                "ncs_extracted_items": [item],
                "ncs_translations_by_item_id": {item.item_id: "Привет, мир!"},
            },
        )
        assert result.modified
        assert result.items_updated == 1

    def test_inject_no_match(self, tmp_path):
        path = _write_ncs(
            tmp_path,
            "test.ncs",
            _consts("Hello"),
            _retn(),
        )
        injector = NcsInjector()
        result = injector.inject(path, {}, {"Goodbye": "Au revoir"}, None)
        assert not result.modified
        assert result.items_updated == 0

    def test_supported_types(self):
        injector = NcsInjector()
        assert injector.can_inject("ncs_script")
        assert not injector.can_inject("dialog")


# ═══════════════════════════════════════════════════════════════════════════
# Integration test
# ═══════════════════════════════════════════════════════════════════════════


class TestNCSIntegration:
    """End-to-end: extract -> (mock translate) -> inject -> verify."""

    def test_round_trip(self, tmp_path):
        """Full cycle: parse, extract, inject translations, verify."""
        # Build a realistic NCS file with:
        # - 2 translatable strings (with player-facing ACTIONs)
        # - 1 non-translatable identifier
        # - Jump instructions crossing the strings
        str1 = "Hello, brave adventurer!"  # 24 bytes, player-facing
        str2 = "nw_c2_default1"  # 14 bytes, identifier
        str3 = "Farewell, noble traveler!"  # 25 bytes, player-facing

        # Calculate sizes: CONSTS = 4 + len(string)
        s1 = 4 + len(str1.encode("cp1252"))  # 28
        s2 = 4 + len(str2.encode("cp1252"))  # 18
        # JSR(6) + CONSTS_str1(s1) + ACTION(5) + CONSTS_str2(s2) + ACTION(5) + RETN(2)
        jsr_offset = 6 + s1 + 5 + s2 + 5 + 2

        path = _write_ncs(
            tmp_path,
            "script.ncs",
            _jsr(jsr_offset),  # JSR to subroutine
            _consts(str1),  # translatable (SendMessageToPC)
            _action(374, 2),
            _consts(str2),  # NOT translatable (identifier)
            _action(46, 1),
            _retn(),
            # subroutine:
            _consts(str3),  # translatable (SpeakString)
            _action(39, 1),
            _retn(),
        )

        # Step 1: Parse
        ncs = parse_ncs(path)
        assert len(ncs.string_constants) == 3

        # Step 2: Extract
        extractor = NcsExtractor()
        extracted = extractor.extract(path, {"_ncs_file": ncs})
        texts = {item.text for item in extracted.items}
        assert str1 in texts
        assert str3 in texts
        assert str2 not in texts

        # Step 3: Inject translations
        trans1 = "Привет, храбрый искатель приключений!"
        trans3 = "Прощай, благородный путник!"
        by_item_id = {item.item_id: trans1 for item in extracted.items if item.text == str1}
        by_item_id.update({item.item_id: trans3 for item in extracted.items if item.text == str3})
        translations = {str1: trans1, str3: trans3}
        injector = NcsInjector()
        result = injector.inject(
            path,
            {},
            translations,
            {
                "ncs_extracted_items": extracted.items,
                "ncs_translations_by_item_id": by_item_id,
            },
        )
        assert result.modified

        # Step 4: Verify patched file
        ncs2 = parse_ncs(path)
        strings = {i.string_value for i in ncs2.string_constants}
        assert str2 in strings  # identifier should remain unchanged

        # Step 5: Verify JSR still targets the subroutine
        jsr = ncs2.instructions[0]
        # The subroutine's first instruction is the translated CONSTS
        sub_consts = [
            i
            for i in ncs2.instructions
            if i.is_string_const and i.string_value != str2 and i.offset > 20
        ]  # after the main block
        assert len(sub_consts) == 1
        assert jsr.offset + jsr.jump_offset == sub_consts[0].offset
