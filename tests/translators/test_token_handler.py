"""Protecting NWN tokens and inline markup from the model, and validating its answers."""

import re

import pytest

from nwn_translator.translators.token_handler import (
    TokenHandler,
    has_translatable_content,
    normalize_translated_text,
    sanitize_text,
)

INLINE_PLACEHOLDER_RE = re.compile(r"__NWN_INLINE_[A-Za-z0-9_]+__")
TOKEN_PLACEHOLDER_RE = re.compile(r"__NWN_TOKEN_[A-Za-z0-9_]+__")


def _sanitized(text: str, **kwargs):
    handler = TokenHandler(**kwargs)
    return handler, handler.sanitize(text)


def _placeholders(handler, original: str) -> list:
    return [a.placeholder for a in handler.artifacts if a.original == original]


# ---------------------------------------------------------------------------
# Sanitizing
# ---------------------------------------------------------------------------


def test_plain_text_is_unchanged():
    handler, result = _sanitized("Hello world")
    assert (result, handler.artifacts) == ("Hello world", [])


@pytest.mark.parametrize(
    "text, originals, kinds",
    [
        ("Hello <FirstName>", ["<FirstName>"], ["engine_token"]),
        ("Test <CustomToken:123>", ["<CustomToken:123>"], ["engine_token"]),
        (
            "<StartAction>[Wave]</Start> Hello <FirstName>.",
            ["<StartAction>", "</Start>", "<FirstName>"],
            ["inline_tag", "inline_tag", "engine_token"],
        ),
        (
            "<StartAction>[Wave]</Start> Hello <FirstName> <CustomToken:123>",
            ["<StartAction>", "</Start>", "<FirstName>", "<CustomToken:123>"],
            ["inline_tag", "inline_tag", "engine_token", "engine_token"],
        ),
        # A token inside a dash action marker is hidden from the model too.
        ("-glances at <FirstName>-", ["-", "<FirstName>", "-"], None),
        ("-<StartHighlight>aside</Start>-", ["-", "<StartHighlight>", "</Start>", "-"], None),
        ("<<Climb up the shaft>>", ["<<", ">>"], None),
    ],
)
def test_tokens_and_markup_become_placeholders(text, originals, kinds):
    handler, result = _sanitized(text)
    assert [a.original for a in handler.artifacts] == originals
    if kinds:
        assert [a.kind for a in handler.artifacts] == kinds
    for artifact in handler.artifacts:
        pattern = TOKEN_PLACEHOLDER_RE if artifact.kind == "engine_token" else INLINE_PLACEHOLDER_RE
        assert pattern.fullmatch(artifact.placeholder)
        assert artifact.placeholder in result
    assert not any(original in result for original in ("<FirstName>", "<Start"))


@pytest.mark.parametrize(
    "text",
    [
        # Spaced prose dashes are not an action marker.
        "Yes, yes - take it and go - your job is done pimp !",
        "And before I tell you - I've this hankering... those pointy ears - they shiver",
        "-hands over the pouch - and waits",
        "He said - wait- no more",
    ],
)
def test_prose_dashes_are_not_markers(text):
    handler, result = _sanitized(text)
    assert (result, handler.artifacts) == (text, [])


def test_disabled_token_preservation_still_hides_inline_tags():
    _, result = _sanitized("<StartAction>[Wave]</Start> Hello <FirstName>", preserve_tokens=False)
    assert "<FirstName>" in result
    assert len(INLINE_PLACEHOLDER_RE.findall(result)) == 2


def test_equal_text_sanitizes_identically():
    """Identical token-bearing text shares one request, so its placeholders must match."""
    first, _ = sanitize_text("Greetings, <FirstName>!")
    assert sanitize_text("Greetings, <FirstName>!")[0] == first
    assert TOKEN_PLACEHOLDER_RE.search(first)
    handler = TokenHandler()
    assert handler.sanitize("Hello <FirstName>") == (handler.sanitize("Hello <FirstName>"))
    assert sanitize_text("Hello <FirstName>")[0] != sanitize_text("Goodbye <FirstName>")[0]


@pytest.mark.parametrize(
    "text, translatable",
    [
        # A lone word between tags is translatable: a placeholder does not
        # match across it.
        ("<StartAction>Attack</Start>", True),
        ("<StartHighlight>Partir</Start>", True),
        ("<StartAction>Yes</Start>", True),
        ("<StartAction>Attack the guard</Start> now", True),
        ('"<Deity>!"', False),
        ("<FirstName>.", False),
        ("<CUSTOM101>", False),
        (". . .", False),
        ("...", False),
        ("########", False),
    ],
)
def test_translatable_content_after_sanitizing(text, translatable):
    assert has_translatable_content(TokenHandler().sanitize(text)) is translatable


def test_mangled_bracket_placeholders_are_not_content():
    assert not has_translatable_content("[[NWN_TOKEN_abcdef01_0]]!")
    assert not has_translatable_content("<[NWN_INLINE_deadbeef_2]>...")
    assert has_translatable_content("[[NWN_TOKEN_abcdef01_0]]Word[[NWN_TOKEN_abcdef01_1]]")


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------


def test_restore_round_trip():
    for text in (
        "Hello <FirstName>, you are a <Race> <Class>!",
        "Hello <FirstName>, welcome <StartAction>[wave]</Start>",
    ):
        sanitized, handler = sanitize_text(text)
        assert handler.restore(sanitized) == text
    handler, result = _sanitized("Hello <FirstName>, you are a skilled <Class>!")
    first, second = (a.placeholder for a in handler.artifacts)
    translated = f"¡Hola {first}, eres un {second} experto!"
    assert handler.restore(translated) == "¡Hola <FirstName>, eres un <Class> experto!"


def test_restore_accepts_wrapped_and_recased_placeholders():
    sanitized, handler = sanitize_text("<StartAction>[Wave]</Start> Hello <FirstName>.")
    inline = [f"<<[{m[2:-2]}]>>" for m in INLINE_PLACEHOLDER_RE.findall(sanitized)]
    token = [f"<<[{m[2:-2]}]>>" for m in TOKEN_PLACEHOLDER_RE.findall(sanitized)]
    wrapped = f"{inline[0]}[Машет]{inline[1]} Привет {token[0]}."
    assert handler.restore(wrapped) == "<StartAction>[Машет]</Start> Привет <FirstName>."

    sanitized, handler = sanitize_text("Hello <FirstName>.")
    placeholder = TOKEN_PLACEHOLDER_RE.search(sanitized).group(0)
    lowered = sanitized.replace(placeholder, placeholder.lower()).replace("Hello", "Привет")
    assert handler.restore(lowered) == "Привет <FirstName>."
    assert "<FirstName>" in handler.restore(sanitized.replace(placeholder, placeholder.upper()))
    assert handler.restore(f"Привет [[{placeholder[2:-2].lower()}]].") == "Привет <FirstName>."


def test_recased_answer_is_exact_on_the_first_try():
    """A case-only change must not burn a retry."""
    sanitized, handler = sanitize_text("<StartAction>[Wave]</Start> Hello <FirstName>.")
    outcome = handler.finalize_translation(sanitized.lower())
    assert outcome.exact_valid
    for original in ("<StartAction>", "</Start>", "<FirstName>"):
        assert original in outcome.final_text


@pytest.mark.parametrize(
    "answer",
    [
        "Привет __nwn_token_каракули__!",  # a mangled, re-cased prefix
        "Привет __NWN_TOKEN_deadbeef_9__.",  # an unknown well-formed core
        "Привет nwn_inline_ab12cd34_0 друг.",  # a bare re-cased marker
    ],
)
def test_placeholder_residue_never_reaches_the_output(answer):
    _, handler = sanitize_text("Hello <FirstName>.")
    restored = handler.restore(answer)
    assert "nwn_token" not in restored.lower() and "nwn_inline" not in restored.lower()
    if "друг" in answer:
        assert "друг." in restored


# ---------------------------------------------------------------------------
# Validating and finalizing answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "original, inner, final",
    [
        ("<<Climb up the shaft>>", "Ascend the shaft", "<<Ascend the shaft>>"),
        ("-end dialogue-", "end conversation", "-end conversation-"),
        # Cyrillic inner text still validates as an exact match after restoration.
        ("-more-", "далее", "-далее-"),
        ("-give him the letter-", "отдать ему письмо", "-отдать ему письмо-"),
    ],
)
def test_action_markers_keep_their_markers_and_translate_the_inner_text(original, inner, final):
    handler, result = _sanitized(original)
    first, last = (a.placeholder for a in handler.artifacts)
    assert original.strip("<>-") in result
    finalized = handler.finalize_translation(f"{first}{inner}{last}")
    assert (finalized.exact_valid, finalized.final_text) == (True, final)
    assert finalized.mismatch_report.actual_sequence == [a.original for a in handler.artifacts]


def test_nested_token_of_a_dash_marker_is_restored_and_validated():
    handler, _ = _sanitized("-glances at <FirstName>-")
    token = _placeholders(handler, "<FirstName>")[0]
    first, last = _placeholders(handler, "-")
    finalized = handler.finalize_translation(f"{first}смотрит на {token}{last}")
    assert (finalized.exact_valid, finalized.final_text) == (True, "-смотрит на <FirstName>-")
    # A dropped nested token fails exact validation.
    report = handler.validate_text(handler.restore(f"{first}смотрит{last}"))
    assert (report.is_exact_match, report.mismatch_type) == (False, "count_mismatch")
    assert (report.expected_sequence, report.actual_sequence) == (
        ["-", "<FirstName>", "-"],
        ["-", "-"],
    )


def test_prose_dashes_of_an_answer_validate_as_exact():
    handler, _ = _sanitized("Yes, yes - take it and go - your job is done pimp !")
    answer = "Да, да - бери его и иди - твоя работа сделана, сводник!"
    finalized = handler.finalize_translation(answer)
    assert (finalized.exact_valid, finalized.final_text) == (True, answer)


@pytest.mark.parametrize(
    "original, restored, mismatch, expected, actual",
    [
        (
            "<StartCheck>[Persuade]</Start> Hello <FirstName>!",
            "<StartCheck>[Убеждение]</Start> Привет <FirstName>!",
            "exact_match",
            None,
            None,
        ),
        (
            "<StartHighlight>[Shudder.]</Start>",
            "<StartAction>[Вздрогнуть.]</StartAction>",
            {"count_mismatch", "value_mismatch"},
            ["<StartHighlight>", "</Start>"],
            ["<StartAction>", "</StartAction>"],
        ),
        (
            "<FirstName><CustomToken:123>",
            "<CustomToken:123><FirstName>",
            "order_mismatch",
            None,
            None,
        ),
        (
            "<FirstName><CustomToken:123>",
            "<FirstName><BadToken>",
            "value_mismatch",
            ["<FirstName>", "<CustomToken:123>"],
            ["<FirstName>", "<BadToken>"],
        ),
        # A start tag must not replace a double-angle action marker.
        (
            "<<Walk away from the shaft>>",
            "<StartAction>Walk away from the shaft</StartAction>",
            None,
            ["<<", ">>"],
            ["<StartAction>", "</StartAction>"],
        ),
        # New pseudo-tags are rejected.
        ("Good evening, madam.", "Good evening, <sir/madam>.", None, [], ["<sir/madam>"]),
    ],
)
def test_restored_answers_are_validated_exactly(original, restored, mismatch, expected, actual):
    handler, _ = _sanitized(original)
    report = handler.validate_text(restored)
    assert report.is_exact_match is (mismatch == "exact_match")
    if isinstance(mismatch, set):
        assert report.mismatch_type in mismatch
    elif mismatch:
        assert report.mismatch_type == mismatch
    if expected is not None:
        assert (report.expected_sequence, report.actual_sequence) == (expected, actual)


@pytest.mark.parametrize(
    "original, answer, present, absent",
    [
        (
            "<StartHighlight>[Shudder.]</Start>",
            "<StartAction> [Вздрогнуть.] </StartAction>",
            ["[Вздрогнуть.]"],
            ["<Start"],
        ),
        (
            "Hello <FirstName> and <CustomToken:123>.",
            "Привет <FirstName> и <BadToken>.",
            ["<FirstName>"],
            ["<BadToken>"],
        ),
        (
            "<StartHighlight>[Success.]</Start><StartAction>[Wave]</Start> Bzzt!",
            "<StartHighlight>[Успех.][Машет]</Start> Бззт!",
            ["[Машет]"],
            ["<StartAction>"],
        ),
        ("Good evening, madam.", "Good evening, <sir/madam>.", ["Good evening"], ["<sir/madam>"]),
    ],
)
def test_cleanup_drops_only_the_mismatched_markup(original, answer, present, absent):
    handler, _ = _sanitized(original)
    result = handler.finalize_translation(answer, allow_cleanup=True)
    assert (result.exact_valid, result.used_cleanup) == (False, True)
    for text in present:
        assert text in result.final_text
    for text in absent:
        assert text not in result.final_text
    if original.startswith("<StartHighlight>[Success"):
        assert result.final_text.startswith("<StartHighlight>")


def test_cleanup_drops_unknown_helper_noise_and_mangled_cores():
    handler, result = _sanitized("<StartAction>[Wave]</Start>")
    first, last = INLINE_PLACEHOLDER_RE.findall(result)
    noisy = f"{first}[Машет]{last} [[NWN_INLINE_garbage]]"
    assert handler.finalize_translation(noisy, allow_cleanup=True).final_text.strip() == (
        "<StartAction>[Машет]</Start>"
    )

    _, handler = sanitize_text("Hello <FirstName>.")
    outcome = handler.finalize_translation("Привет __NWN_TOKEN_обрывок__", allow_cleanup=True)
    assert (outcome.exact_valid, outcome.used_cleanup) == (False, True)
    assert "nwn_token" not in outcome.final_text.lower()
    assert outcome.mismatch_report.expected_sequence == ["<FirstName>"]
    assert outcome.mismatch_report.actual_sequence == []


@pytest.mark.parametrize(
    "original",
    [
        "<StartAction>Waves a hand.",
        "<StartAction>",
        "<CUSTOM1004>(sigh)</Start>  Will 100 gold assist your memory at all?",
        "<StartAction>A</Start> and <StartCheck>B",
    ],
)
def test_unpaired_original_tags_round_trip_untouched(original):
    handler, result = _sanitized(original)
    finalized = handler.finalize_translation(result, allow_cleanup=False)
    assert (finalized.exact_valid, finalized.final_text) == (True, original)


@pytest.mark.parametrize(
    "original, dropped, cleanup",
    [
        ("<StartAction>Waves.</Start>", -1, True),
        ("<StartAction>Waves a hand.", 0, False),
    ],
)
def test_a_lost_tag_is_still_caught(original, dropped, cleanup):
    handler, result = _sanitized(original)
    lost = handler.artifacts[dropped]
    mangled = result.replace(lost.placeholder, "")
    finalized = handler.finalize_translation(mangled, allow_cleanup=cleanup)
    assert not finalized.exact_valid
    assert finalized.mismatch_report.expected_sequence == [a.original for a in handler.artifacts]
    assert lost.original not in finalized.mismatch_report.actual_sequence
    if cleanup:
        assert finalized.used_cleanup
        assert "<Start" not in finalized.final_text


@pytest.mark.parametrize(
    "text, normalized",
    [
        ("Сирани\u0301та", "Сиранита"),  # a combining acute is dropped
        # NFC composes e + U+0301 into a precomposed é instead of dropping it.
        ("cafe\u0301", "caf\u00e9"),
        ("й ё", "й ё"),
    ],
)
def test_accent_normalization(text, normalized):
    assert normalize_translated_text(text) == normalized


def test_finalizing_strips_combining_accents_and_stays_exact():
    handler, _ = _sanitized("Hello Siranita")
    result = handler.finalize_translation("Привет, Сирани\u0301та")
    assert (result.exact_valid, result.final_text) == (True, "Привет, Сиранита")


@pytest.mark.parametrize(
    "answer, cleanup, final",
    [
        ("Общество здесь не欢迎но!", False, None),
        ("Общество здесь не欢迎но!", True, "Общество здесь нено!"),
        ("Аมараст!", False, None),  # Thai
        ("Амаرаст!", True, "Амааст!"),  # Arabic
    ],
)
def test_foreign_script_in_an_answer_is_rejected(answer, cleanup, final):
    handler, _ = _sanitized(
        "The Society is not welcome here!" if "Общество" in answer else "Amarast!"
    )
    result = handler.finalize_translation(answer, allow_cleanup=cleanup)
    assert not result.exact_valid
    if cleanup:
        assert (result.used_cleanup, result.final_text) == (True, final)
    else:
        assert result.mismatch_report.mismatch_type == "foreign_script"


def test_foreign_script_of_the_original_is_allowed():
    handler, _ = _sanitized("Welcome sign reads 欢迎")
    result = handler.finalize_translation("Табличка гласит 欢迎")
    assert (result.exact_valid, result.final_text) == (True, "Табличка гласит 欢迎")


def test_foreign_script_does_not_mask_a_token_mismatch():
    handler, result = _sanitized("<FirstName>, the Society is not welcome!")
    mangled = result.replace(handler.artifacts[0].placeholder, "")
    outcome = handler.finalize_translation(mangled + " 欢迎", allow_cleanup=True)
    assert (outcome.exact_valid, outcome.used_cleanup) == (False, True)
    assert outcome.mismatch_report.mismatch_type != "foreign_script"
    assert "欢" not in outcome.final_text
