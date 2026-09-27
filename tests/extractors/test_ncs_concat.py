"""String concatenations are one translation unit with ``<VARn>`` placeholders."""

import pytest

from nwn_translator.extractors.ncs_concat import (
    ConcatLit,
    ConcatVar,
    find_concat_chains,
    merged_text,
    split_concat_translation,
)
from nwn_translator.formats.ncs import parse_ncs_bytes
from tests.support.ncs import (
    action,
    add_ss,
    consti,
    consts,
    cptopsp,
    extract_script,
    jz,
    retn,
    script,
)

#: SpeakString("Congrats to ye, " + sName + ". How do ye feel?")
CONGRATS = (
    consts("Congrats to ye, "),
    cptopsp(-8),
    add_ss(),
    consts(". How do ye feel?"),
    add_ss(),
    action(221, 1),
    retn(),
)


def _merged(*parts):
    return [
        merged_text(chain) for chain in find_concat_chains(parse_ncs_bytes(script(*parts))).values()
    ]


@pytest.mark.parametrize(
    "parts, texts",
    [
        (CONGRATS, ["Congrats to ye, <VAR1>. How do ye feel?"]),
        (
            (
                consts("This is only the beginning, we have much to do before "),
                consts("we can defeat Saris."),
                add_ss(),
                action(221, 1),
                retn(),
            ),
            ["This is only the beginning, we have much to do before we can defeat Saris."],
        ),
        (
            (cptopsp(-8), consts("is currently in your party!"), add_ss(), action(221, 1), retn()),
            ["<VAR1>is currently in your party!"],
        ),
        # A standalone constant is not a chain.
        ((consts("Welcome, hero!"), action(374, 2), retn()), []),
        # A void action between the operands does not invent an operand.
        (
            (
                consts("Left "),
                consts("Debug"),
                action(1, 1),
                consts("right."),
                add_ss(),
                action(221, 1),
                retn(),
            ),
            ["Left right."],
        ),
        # A scalar argument does not discard the pending literal.
        (
            (
                consts("Left "),
                consti(0),
                consts("Speech"),
                action(221, 2),
                consts("right."),
                add_ss(),
                retn(),
            ),
            ["Left right."],
        ),
    ],
)
def test_chains_are_detected(parts, texts):
    assert _merged(*parts) == texts


def test_chain_parts_and_branches():
    (chain,) = find_concat_chains(parse_ncs_bytes(script(*CONGRATS))).values()
    assert [type(p).__name__ for p in chain.parts] == ["ConcatLit", "ConcatVar", "ConcatLit"]
    (literal_only,) = find_concat_chains(
        parse_ncs_bytes(script(consts("a "), consts("b."), add_ss(), action(221, 1), retn()))
    ).values()
    assert len(literal_only.lits()) == 2
    # Two chains separated by a conditional jump stay two chains.
    assert set(
        _merged(
            consts("Hello, "),
            cptopsp(-8),
            add_ss(),
            action(221, 1),
            jz(4),
            consts("Goodbye, "),
            cptopsp(-8),
            add_ss(),
            action(221, 1),
            retn(),
        )
    ) == {"Hello, <VAR1>", "Goodbye, <VAR1>"}


def test_extractor_emits_one_item_per_chain(tmp_path):
    result = extract_script(tmp_path, *CONGRATS, name="openingcut1.ncs")
    (item,) = result.items
    assert item.text == "Congrats to ye, <VAR1>. How do ye feel?"
    assert item.item_id == "openingcut1:c0"
    first = parse_ncs_bytes(script(*CONGRATS)).string_constants[0]
    assert item.metadata["concat_parts"][0]["offset"] == first.offset
    assert item.metadata["concat_parts"][1] == {"var": 1}


def test_integer_conversion_keeps_the_whole_utterance(tmp_path):
    result = extract_script(
        tmp_path,
        consti(0),
        consts("You have "),
        consti(5),
        action(92, 1),  # IntToString
        add_ss(),
        consts(" coins."),
        add_ss(),
        action(221, 2),
        retn(),
    )
    (item,) = result.items
    assert item.text == "You have <VAR1> coins."
    assert item.metadata["proven_player"] is True


@pytest.mark.parametrize(
    "parts, translation, split",
    [
        (
            [
                ConcatLit(0x10, "Congrats to ye, "),
                ConcatVar(1),
                ConcatLit(0x20, ". How do ye feel?"),
            ],
            "Поздравляю тебя, <VAR1>. Как ты?",
            [
                (0x10, "Congrats to ye, ", "Поздравляю тебя, "),
                (0x20, ". How do ye feel?", ". Как ты?"),
            ],
        ),
        # Without placeholders the whole translation goes into the first literal.
        (
            [ConcatLit(0x10, "before "), ConcatLit(0x20, "after.")],
            "до после.",
            [(0x10, "before ", "до после."), (0x20, "after.", "")],
        ),
        (
            [ConcatVar(1), ConcatVar(2), ConcatLit(0x10, "end")],
            "<VAR1><VAR2>end",
            [(0x10, "end", "end")],
        ),
        # Text between adjacent placeholders has no literal to go into.
        ([ConcatVar(1), ConcatVar(2), ConcatLit(0x10, "end")], "<VAR1>oops<VAR2>end", None),
    ]
    + [
        # Missing, reordered or duplicated placeholders are rejected.
        (
            [
                ConcatLit(0x10, "a "),
                ConcatVar(1),
                ConcatLit(0x20, " b "),
                ConcatVar(2),
                ConcatLit(0x30, " c"),
            ],
            translation,
            None,
        )
        for translation in ("a <VAR1> b c", "a <VAR2> b <VAR1> c", "a <VAR1> b <VAR1> c")
    ],
)
def test_split_translation(parts, translation, split):
    assert split_concat_translation(parts, translation) == split
