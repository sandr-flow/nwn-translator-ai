"""NCS bytecode: parsing, opcode argument sizes and string patching with jump fix-ups."""

import struct

import pytest

from nwn_translator.formats.ncs import (
    NCS_HEADER,
    OP_CONST,
    OP_CPDOWNBP,
    OP_CPDOWNSP,
    OP_CPTOPBP,
    OP_CPTOPSP,
    OP_EQUAL,
    OP_NEQUAL,
    OP_RETN,
    TYPE_INT,
    NCSParseError,
    NCSPatchError,
    parse_ncs,
    parse_ncs_bytes,
    patch_ncs_string_replacements,
)
from tests.support.ncs import action, consti, consts, jmp, jsr, retn, script, write_ncs


def _patch_texts(path, translations: dict) -> int:
    """Patch every string constant whose text is a key of *translations*."""
    replacements = [
        (instr.offset, instr.string_value, translations[instr.string_value])
        for instr in parse_ncs(path).string_constants
        if instr.string_value in translations
    ]
    return patch_ncs_string_replacements(path, replacements)


def _ee_script(body: bytes) -> bytes:
    """*body* behind the NCS banner and the NWN:EE ``0x42`` size preamble."""
    return NCS_HEADER + struct.pack(">BI", 0x42, len(NCS_HEADER) + 5 + len(body)) + body


def _size_field(data: bytes) -> int:
    """The big-endian preamble size field ``T`` at offset 9."""
    return struct.unpack_from(">I", data, 9)[0]


def _copy_op(opcode: int, offset: int = -4, size: int = 4) -> bytes:
    """CPDOWNSP/CPTOPSP/CPDOWNBP/CPTOPBP: int32 stack offset + uint16 size."""
    return struct.pack(">BBiH", opcode, 0x01, offset, size)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_header_only_and_invalid_scripts():
    assert parse_ncs_bytes(NCS_HEADER).instructions == []
    ncs = parse_ncs_bytes(script(retn()))
    assert ncs.header == NCS_HEADER
    assert [i.opcode for i in ncs.instructions] == [OP_RETN]
    with pytest.raises(NCSParseError, match="Invalid NCS header"):
        parse_ncs_bytes(b"GFF V3.2" + retn())
    with pytest.raises(NCSParseError, match="too small"):
        parse_ncs_bytes(b"NCS")


def test_instructions_decode_in_sequence():
    ncs = parse_ncs_bytes(
        script(consts("Hello, World!"), consti(42), jmp(10), action(374, 2), consts("Hi"), retn())
    )
    string, integer, jump, call, _second, _retn = ncs.instructions
    assert string.is_string_const and string.string_value == "Hello, World!"
    assert (string.offset, string.size) == (8, 4 + len("Hello, World!"))
    assert (integer.opcode, integer.type_byte, integer.is_string_const) == (
        OP_CONST,
        TYPE_INT,
        False,
    )
    assert (jump.is_jump, jump.jump_offset) == (True, 10)
    assert (call.is_action, call.action_routine, call.action_arg_count) == (True, 374, 2)
    expected_offset = 8
    for instr in ncs.instructions:
        assert instr.offset == expected_offset
        expected_offset += instr.size
    assert [s.string_value for s in ncs.string_constants] == ["Hello, World!", "Hi"]


def test_nwn_ee_size_preamble_is_skipped():
    """NWN:EE / Beamdog NCS: 0x42 + uint32 BE length after the banner (xoreos)."""
    ncs = parse_ncs_bytes(_ee_script(consts("Hello, EE!") + action(39, 1) + retn()))
    (string,) = ncs.string_constants
    assert (string.string_value, string.offset) == ("Hello, EE!", 13)


def test_parse_from_file(tmp_path):
    path = write_ncs(tmp_path, "test.ncs", consts("File test"), retn())
    assert parse_ncs(path).instructions[0].string_value == "File test"
    with pytest.raises(NCSParseError, match="not found"):
        parse_ncs(tmp_path / "nonexistent.ncs")


def test_cpdownsp_canonical_sequence_is_one_instruction():
    """``01 01 FF FF FF FC 00 04`` is one CPDOWNSP, not CPDOWNSP + garbage."""
    (instr,) = parse_ncs_bytes(NCS_HEADER + bytes.fromhex("0101FFFFFFFC0004")).instructions
    assert (instr.opcode, instr.size, instr.offset) == (OP_CPDOWNSP, 8, 8)


@pytest.mark.parametrize(
    "instruction, opcode, size",
    [
        (_copy_op(OP_CPDOWNSP), OP_CPDOWNSP, 8),
        (_copy_op(OP_CPTOPSP), OP_CPTOPSP, 8),
        (_copy_op(OP_CPDOWNBP), OP_CPDOWNBP, 8),
        (_copy_op(OP_CPTOPBP), OP_CPTOPBP, 8),
        # EQUAL/NEQUAL of two structures (type 0x24) carry a uint16 size.
        (struct.pack(">BBH", OP_EQUAL, 0x24, 8), OP_EQUAL, 4),
        (struct.pack(">BBH", OP_NEQUAL, 0x24, 8), OP_NEQUAL, 4),
        # EQUAL of non-struct types (int 0x03) has no argument bytes.
        (struct.pack(">BB", OP_EQUAL, TYPE_INT), OP_EQUAL, 2),
    ],
)
def test_opcode_argument_sizes_keep_the_next_instruction_aligned(instruction, opcode, size):
    first, second = parse_ncs_bytes(script(instruction, retn())).instructions
    assert (first.opcode, first.size) == (opcode, size)
    assert (second.opcode, second.offset) == (OP_RETN, 8 + size)


def test_string_after_an_assignment_is_at_the_right_offset():
    ncs = parse_ncs_bytes(script(_copy_op(OP_CPDOWNSP), consts("Ow."), retn()))
    (string,) = ncs.string_constants
    assert (string.string_value, string.offset) == ("Ow.", 16)  # 8 header + 8-byte CPDOWNSP


# ---------------------------------------------------------------------------
# Patcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "old, translations, new, count",
    [
        ("AAAA", {"AAAA": "BBBB"}, "BBBB", 1),
        ("Hi", {"Hi": "Hello there!"}, "Hello there!", 1),
        ("Hello there!", {"Hello there!": "Hi"}, "Hi", 1),
        ("Same", {"Same": "Same"}, "Same", 0),  # replacing a string by itself is a no-op
        ("Hello", {"Goodbye": "Au revoir"}, "Hello", 0),
    ],
)
def test_patch_resizes_the_constant(tmp_path, old, translations, new, count):
    path = write_ncs(tmp_path, "test.ncs", consts(old), retn())
    size = path.stat().st_size
    assert _patch_texts(path, translations) == count
    assert parse_ncs(path).instructions[0].string_value == new
    assert path.stat().st_size == size + len(new) - len(old)


def test_several_constants_of_one_file_are_patched(tmp_path):
    path = write_ncs(tmp_path, "test.ncs", consts("First"), consts("Second"), retn())
    assert _patch_texts(path, {"First": "Eerste", "Second": "Tweede!!!"}) == 2
    assert [i.string_value for i in parse_ncs(path).string_constants] == ["Eerste", "Tweede!!!"]


@pytest.mark.parametrize(
    "parts, jump_index, target_index, unchanged",
    [
        # [JMP] [CONSTS "AB"] [RETN]: a forward jump across the string grows.
        ((jmp(6 + 6), consts("AB"), retn()), 0, 2, False),
        # [RETN] [CONSTS "AB"] [JMP back]: a backward jump across the string grows.
        ((retn(), consts("AB"), jmp(8 - 16)), 2, 0, False),
        # [JMP] [RETN] [CONSTS "AB"] [RETN]: a jump before the string is unchanged.
        ((jmp(6), retn(), consts("AB"), retn()), 0, 1, True),
    ],
)
def test_jumps_keep_their_targets_after_a_longer_string(
    tmp_path, parts, jump_index, target_index, unchanged
):
    path = write_ncs(tmp_path, "test.ncs", *parts)
    before = parse_ncs(path).instructions[jump_index].jump_offset
    assert _patch_texts(path, {"AB": "ABCDEF"}) == 1
    instructions = parse_ncs(path).instructions
    jump, target = instructions[jump_index], instructions[target_index]
    assert jump.is_jump and target.opcode == OP_RETN
    assert jump.offset + jump.jump_offset == target.offset
    assert (jump.jump_offset == before) is unchanged


def test_subroutine_call_keeps_its_target_after_both_strings_grow(tmp_path):
    msg1 = consts("msg1")
    # JSR -> CONSTS "msg2": header + JSR + CONSTS msg1 + ACTION + RETN.
    sub_offset = 8 + 6 + len(msg1) + 5 + 2
    path = write_ncs(
        tmp_path,
        "test.ncs",
        jsr(sub_offset - 8),
        msg1,
        action(374, 2),
        retn(),
        consts("msg2"),
        action(39, 1),
        retn(),
    )
    instructions = parse_ncs(path).instructions
    assert len(instructions) == 7
    assert instructions[0].offset + instructions[0].jump_offset == instructions[4].offset

    assert (
        _patch_texts(path, {"msg1": "translated message one", "msg2": "translated message two"})
        == 2
    )

    instructions = parse_ncs(path).instructions
    assert instructions[4].string_value == "translated message two"
    assert instructions[0].offset + instructions[0].jump_offset == instructions[4].offset


def test_only_the_listed_offset_is_patched(tmp_path):
    path = write_ncs(tmp_path, "dup.ncs", consts("Same"), retn(), consts("Same"), retn())
    first, _second = parse_ncs(path).string_constants
    assert patch_ncs_string_replacements(path, [(first.offset, "Same", "FirstOnly")]) == 1
    assert [i.string_value for i in parse_ncs(path).string_constants] == ["FirstOnly", "Same"]


def test_constants_are_encoded_with_the_module_codec(tmp_path):
    """CONSTS carry a BE length and the text encoded like GFF strings (dash -> '-')."""
    path = write_ncs(tmp_path, "enc.ncs", consts("Hi"), retn())
    patch_ncs_string_replacements(path, [(8, "Hi", "Да — нет")], text_encoding="cp1251")
    assert path.read_bytes() == script(consts("Да - нет", "cp1251"), retn())


def test_invalid_patches_fail_before_writing(tmp_path):
    path = write_ncs(tmp_path, "enc.ncs", consts("Hi"), retn())
    before = path.read_bytes()
    # Only the module code pages are writable, as for GFF strings.
    with pytest.raises(NCSPatchError, match="Unsupported module text encoding: 'utf-8'"):
        patch_ncs_string_replacements(path, [(8, "Hi", "Hello")], text_encoding="utf-8")
    with pytest.raises(NCSPatchError, match="Duplicate replacement"):
        patch_ncs_string_replacements(path, [(8, "Hi", "First"), (8, "Hi", "Second")])
    assert path.read_bytes() == before


@pytest.mark.parametrize("old, new", [("Hi", "Hello there"), ("Hello there", "Hi"), ("Hi", "Yo")])
def test_preamble_size_field_tracks_the_patched_length(tmp_path, old, new):
    path = tmp_path / "ee.ncs"
    path.write_bytes(_ee_script(consts(old) + retn()))
    assert patch_ncs_string_replacements(path, [(13, old, new)], "cp1252") == 1
    out = path.read_bytes()
    assert _size_field(out) == len(out)


def test_script_without_preamble_keeps_its_instruction_bytes(tmp_path):
    """No 0x42 preamble: byte 9 is real instruction data and must be left alone."""
    path = write_ncs(tmp_path, "old.ncs", consts("Hi"), retn())
    patch_ncs_string_replacements(path, [(8, "Hi", "Hello there")], "cp1252")
    ncs = parse_ncs_bytes(path.read_bytes())
    assert [i.string_value for i in ncs.string_constants] == ["Hello there"]
