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
        token_wrapped = [f"<<[{match[2:-2]}]>>" for match in TOKEN_PLACEHOLDER_RE.findall(sanitized)]
        translated = f"{inline_wrapped[0]}[Машет]{inline_wrapped[1]} Привет {token_wrapped[0]}."
        restored = restore_text(translated, handler)
        assert restored == "<StartAction>[Машет]</Start> Привет <FirstName>."


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
