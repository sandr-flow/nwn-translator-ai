"""Tests for token and inline-tag preservation."""

import re

from src.nwn_translator.translators.token_handler import (
    TokenHandler,
    TokenValidator,
    restore_text,
    sanitize_text,
)

INLINE_PLACEHOLDER_RE = re.compile(r"__NWN_INLINE_[A-Za-z0-9_]+__")
TOKEN_PLACEHOLDER_RE = re.compile(r"__NWN_TOKEN_[A-Za-z0-9_]+__")


class TestTokenHandler:
    """Tests for TokenHandler class."""

    def test_sanitize_simple_text(self):
        handler = TokenHandler()
        result = handler.sanitize("Hello world")
        assert result.sanitized_text == "Hello world"
        assert len(result.replacements) == 0
        assert handler.get_token_count() == 0

    def test_sanitize_with_first_name_token(self):
        handler = TokenHandler()
        result = handler.sanitize("Hello <FirstName>")
        assert TOKEN_PLACEHOLDER_RE.fullmatch(result.sanitized_text.split()[-1])
        assert len(result.replacements) == 1
        assert result.artifacts[0].kind == "engine_token"

    def test_sanitize_with_custom_token(self):
        handler = TokenHandler()
        result = handler.sanitize("Test <CustomToken:123>")
        assert TOKEN_PLACEHOLDER_RE.fullmatch(result.sanitized_text.split()[-1])
        assert len(result.replacements) == 1
        assert result.artifacts[0].original == "<CustomToken:123>"

    def test_sanitize_preserves_inline_tags_and_engine_tokens(self):
        handler = TokenHandler()
        result = handler.sanitize("<StartAction>[Wave]</Start> Hello <FirstName>.")
        inline_placeholders = INLINE_PLACEHOLDER_RE.findall(result.sanitized_text)
        token_placeholders = TOKEN_PLACEHOLDER_RE.findall(result.sanitized_text)
        assert len(inline_placeholders) == 2
        assert len(token_placeholders) == 1
        assert result.artifacts[0].kind == "inline_tag"
        assert result.artifacts[1].kind == "inline_tag"
        assert result.artifacts[2].kind == "engine_token"

    def test_sanitize_restore_roundtrip(self):
        handler = TokenHandler()
        original = "Hello <FirstName>, you are a <Race> <Class>!"
        sanitized = handler.sanitize(original)
        restored = handler.restore(sanitized.sanitized_text)
        assert restored == original

    def test_preserve_tokens_disabled_keeps_engine_tokens_but_still_hides_inline_tags(self):
        handler = TokenHandler(preserve_standard_tokens=False)
        result = handler.sanitize("<StartAction>[Wave]</Start> Hello <FirstName>")
        assert "<FirstName>" in result.sanitized_text
        assert len(INLINE_PLACEHOLDER_RE.findall(result.sanitized_text)) == 2

    def test_restore_accepts_wrapped_placeholders(self):
        source = "<StartAction>[Wave]</Start> Hello <FirstName>."
        sanitized, handler = sanitize_text(source)
        inline_wrapped = [
            f"<<[{match[2:-2]}]>>" for match in INLINE_PLACEHOLDER_RE.findall(sanitized)
        ]
        token_wrapped = [
            f"<<[{match[2:-2]}]>>" for match in TOKEN_PLACEHOLDER_RE.findall(sanitized)
        ]
        translated = f"{inline_wrapped[0]}[Машет]{inline_wrapped[1]} Привет {token_wrapped[0]}."
        restored = restore_text(translated, handler)
        assert restored == "<StartAction>[Машет]</Start> Привет <FirstName>."

    def test_double_angle_dialog_action_preserves_markers_but_translates_inner_text(self):
        handler = TokenHandler()
        result = handler.sanitize("<<Climb up the shaft>>")
        inline_placeholders = INLINE_PLACEHOLDER_RE.findall(result.sanitized_text)
        assert len(inline_placeholders) == 2
        assert "Climb up the shaft" in result.sanitized_text
        assert result.artifacts[0].original == "<<"
        assert result.artifacts[1].original == ">>"

        finalized = handler.finalize_translation(
            f"{inline_placeholders[0]}Ascend the shaft{inline_placeholders[1]}"
        )
        assert finalized.exact_valid
        assert finalized.final_text == "<<Ascend the shaft>>"

    def test_dash_dialog_action_preserves_markers_but_translates_inner_text(self):
        handler = TokenHandler()
        result = handler.sanitize("-end dialogue-")
        inline_placeholders = INLINE_PLACEHOLDER_RE.findall(result.sanitized_text)
        assert len(inline_placeholders) == 2
        assert "end dialogue" in result.sanitized_text

        finalized = handler.finalize_translation(
            f"{inline_placeholders[0]}end conversation{inline_placeholders[1]}"
        )
        assert finalized.exact_valid
        assert finalized.final_text == "-end conversation-"

    def test_dash_dialog_action_validates_with_cyrillic_inner_text(self):
        """Regression: -more- → -далее- must still validate as exact match.

        Previously the dash pattern required [A-Za-z] inside, so after
        restoration the translated Cyrillic inner body did not re-match
        and every Ravenloft dash-action node failed validation.
        """
        handler = TokenHandler()
        result = handler.sanitize("-more-")
        inline_placeholders = [r.placeholder for r in result.replacements]
        assert len(inline_placeholders) == 2

        finalized = handler.finalize_translation(
            f"{inline_placeholders[0]}далее{inline_placeholders[1]}"
        )
        assert finalized.exact_valid
        assert finalized.final_text == "-далее-"
        assert finalized.mismatch_report.actual_sequence == ["-", "-"]

    def test_dash_dialog_action_validates_multiword_cyrillic_inner(self):
        handler = TokenHandler()
        result = handler.sanitize("-give him the letter-")
        inline_placeholders = [r.placeholder for r in result.replacements]
        assert len(inline_placeholders) == 2

        finalized = handler.finalize_translation(
            f"{inline_placeholders[0]}отдать ему письмо{inline_placeholders[1]}"
        )
        assert finalized.exact_valid
        assert finalized.final_text == "-отдать ему письмо-"


class TestTokenValidator:
    """Tests for exact preserved-artifact validation."""

    def test_validate_restoration_success(self):
        original = "<StartCheck>[Persuade]</Start> Hello <FirstName>!"
        restored = "<StartCheck>[Убеждение]</Start> Привет <FirstName>!"
        assert TokenValidator.validate_restoration(original, restored)

    def test_validate_restoration_fails_on_tag_type_change(self):
        original = "<StartHighlight>[Shudder.]</Start>"
        restored = "<StartAction>[Вздрогнуть.]</StartAction>"
        report = TokenValidator.validate_exact_texts(original, restored)
        assert not report.is_exact_match
        assert report.mismatch_type in {"count_mismatch", "value_mismatch"}
        assert report.expected_sequence == ["<StartHighlight>", "</Start>"]
        assert report.actual_sequence == ["<StartAction>", "</StartAction>"]

    def test_validate_restoration_fails_on_order_change(self):
        original = "<FirstName><CustomToken:123>"
        restored = "<CustomToken:123><FirstName>"
        report = TokenValidator.validate_exact_texts(original, restored)
        assert not report.is_exact_match
        assert report.mismatch_type == "order_mismatch"

    def test_find_token_mismatches_reports_missing_and_extra(self):
        original = "<FirstName><CustomToken:123>"
        restored = "<FirstName><BadToken>"
        missing, extra = TokenValidator.find_token_mismatches(original, restored)
        assert missing == ["<CustomToken:123>"]
        assert extra == ["<BadToken>"]

    def test_validate_rejects_start_tag_replacement_for_double_angle_action(self):
        original = "<<Walk away from the shaft>>"
        restored = "<StartAction>Walk away from the shaft</StartAction>"
        report = TokenValidator.validate_exact_texts(original, restored)
        assert not report.is_exact_match
        assert report.expected_sequence == ["<<", ">>"]
        assert report.actual_sequence == ["<StartAction>", "</StartAction>"]

    def test_validate_rejects_new_pseudo_angle_tags(self):
        original = "Good evening, madam."
        restored = "Good evening, <sir/madam>."
        report = TokenValidator.validate_exact_texts(original, restored)
        assert not report.is_exact_match
        assert report.expected_sequence == []
        assert report.actual_sequence == ["<sir/madam>"]

    def test_extract_all_tokens_ignores_inline_tags(self):
        text = "<StartAction>[Wave]</Start> Hello <FirstName> <CustomToken:123>"
        tokens = TokenValidator.extract_all_tokens(text)
        assert set(tokens) == {"CustomToken:123", "FirstName"}


class TestCleanupPath:
    """Tests for cleanup-only acceptance after retries are exhausted."""

    def test_finalize_cleanup_removes_mismatched_start_tags_but_keeps_bracket_text(self):
        handler = TokenHandler()
        handler.sanitize("<StartHighlight>[Shudder.]</Start>")
        result = handler.finalize_translation(
            "<StartAction> [Вздрогнуть.] </StartAction>",
            allow_cleanup=True,
        )
        assert not result.exact_valid
        assert result.used_cleanup
        assert "<Start" not in result.final_text
        assert "[Вздрогнуть.]" in result.final_text

    def test_finalize_cleanup_removes_only_bad_engine_token(self):
        handler = TokenHandler()
        handler.sanitize("Hello <FirstName> and <CustomToken:123>.")
        result = handler.finalize_translation(
            "Привет <FirstName> и <BadToken>.",
            allow_cleanup=True,
        )
        assert not result.exact_valid
        assert result.used_cleanup
        assert "<FirstName>" in result.final_text
        assert "<BadToken>" not in result.final_text

    def test_cleanup_keeps_remaining_valid_tag_pair_and_plain_bracket_text(self):
        handler = TokenHandler()
        handler.sanitize("<StartHighlight>[Success.]</Start><StartAction>[Wave]</Start> Bzzt!")
        result = handler.finalize_translation(
            "<StartHighlight>[Успех.][Машет]</Start> Бззт!",
            allow_cleanup=True,
        )
        assert not result.exact_valid
        assert result.used_cleanup
        assert result.final_text.startswith("<StartHighlight>")
        assert "[Машет]" in result.final_text
        assert "<StartAction>" not in result.final_text

    def test_cleanup_drops_unknown_helper_noise(self):
        handler = TokenHandler()
        sanitized = handler.sanitize("<StartAction>[Wave]</Start>").sanitized_text
        inline_placeholder = INLINE_PLACEHOLDER_RE.findall(sanitized)
        translated = f"{inline_placeholder[0]}[Машет]{inline_placeholder[1]} [[NWN_INLINE_garbage]]"
        result = handler.finalize_translation(translated, allow_cleanup=True)
        assert result.final_text.strip() == "<StartAction>[Машет]</Start>"

    def test_cleanup_drops_new_pseudo_angle_tag(self):
        handler = TokenHandler()
        handler.sanitize("Good evening, madam.")
        result = handler.finalize_translation(
            "Good evening, <sir/madam>.",
            allow_cleanup=True,
        )
        assert not result.exact_valid
        assert result.used_cleanup
        assert "<sir/madam>" not in result.final_text
        assert "Good evening" in result.final_text


class TestDeterministicNonce:
    """Identical token-bearing text must sanitize identically (cache/dedup reuse)."""

    def test_same_text_with_token_sanitizes_identically(self):
        text = "Greetings, <FirstName>!"
        a, _ = sanitize_text(text)
        b, _ = sanitize_text(text)
        assert a == b
        assert TOKEN_PLACEHOLDER_RE.search(a)

    def test_same_handler_reused_is_deterministic(self):
        handler = TokenHandler()
        first = handler.sanitize("Hello <FirstName>").sanitized_text
        second = handler.sanitize("Hello <FirstName>").sanitized_text
        assert first == second

    def test_different_text_gets_different_nonce(self):
        a, _ = sanitize_text("Hello <FirstName>")
        b, _ = sanitize_text("Goodbye <FirstName>")
        assert a != b

    def test_roundtrip_still_restores(self):
        text = "Hello <FirstName>, welcome <StartAction>[wave]</Start>"
        sanitized, handler = sanitize_text(text)
        assert restore_text(sanitized, handler) == text
