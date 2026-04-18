"""Tests for token handling functionality."""

import re

import pytest

from src.nwn_translator.translators.token_handler import (
    TokenHandler,
    TokenValidator,
    sanitize_text,
    restore_text,
)


class TestTokenHandler:
    """Tests for TokenHandler class."""

    def test_sanitize_simple_text(self):
        """Test sanitizing text without tokens."""
        handler = TokenHandler()
        result = handler.sanitize("Hello world")
        assert result.sanitized_text == "Hello world"
        assert len(result.replacements) == 0

    def test_sanitize_with_first_name_token(self):
        """Test sanitizing text with <FirstName> token."""
        handler = TokenHandler()
        result = handler.sanitize("Hello <FirstName>")
        assert result.sanitized_text == "Hello <<TOKEN_0>>"
        assert len(result.replacements) == 1

    def test_sanitize_with_multiple_tokens(self):
        """Test sanitizing text with multiple tokens."""
        handler = TokenHandler()
        result = handler.sanitize("Hello <FirstName>, you are a <Class>!")
        assert result.sanitized_text == "Hello <<TOKEN_0>>, you are a <<TOKEN_1>>!"
        assert len(result.replacements) == 2

    def test_sanitize_custom_token(self):
        """Test sanitizing text with custom token."""
        handler = TokenHandler()
        result = handler.sanitize("Test <CustomToken:123>")
        assert result.sanitized_text == "Test <<TOKEN_0>>"
        assert len(result.replacements) == 1

    def test_restore_simple_text(self):
        """Test restoring text without tokens."""
        handler = TokenHandler()
        result = handler.restore("Hello world")
        assert result == "Hello world"

    def test_restore_with_placeholders(self):
        """Test restoring text with placeholders."""
        handler = TokenHandler()
        handler.token_map = {"<<TOKEN_0>>": "<FirstName>", "<<TOKEN_1>>": "<Class>"}
        result = handler.restore("Hello <<TOKEN_0>>, you are a <<TOKEN_1>>!")
        assert result == "Hello <FirstName>, you are a <Class>!"

    def test_sanitize_restore_roundtrip(self):
        """Test that sanitize and restore preserve tokens."""
        handler = TokenHandler()
        original = "Hello <FirstName>, you are a <Race> <Class>!"
        sanitized = handler.sanitize(original)
        restored = handler.restore(sanitized.sanitized_text)
        assert restored == original

    def test_preserve_tokens_disabled(self):
        """Test with token preservation disabled."""
        handler = TokenHandler(preserve_standard_tokens=False)
        result = handler.sanitize("Hello <FirstName>")
        assert result.sanitized_text == "Hello <FirstName>"
        assert len(result.replacements) == 0


class TestTokenValidator:
    """Tests for TokenValidator class."""

    def test_validate_restoration_success(self):
        """Test validating successful restoration."""
        original = "Hello <FirstName>, you are a <Class>!"
        restored = "Hello <FirstName>, you are a <Class>!"
        assert TokenValidator.validate_restoration(original, restored)

    def test_validate_restoration_failure(self):
        """Test validating failed restoration."""
        original = "Hello <FirstName>, you are a <Class>!"
        restored = "Hola <FirstName>, eres un <Class>!"
        # Tokens should still match even if text changed
        assert TokenValidator.validate_restoration(original, restored)

    def test_extract_tokens(self):
        """Test extracting tokens from text."""
        text = "Hello <FirstName>, you are a <Race> <Class>!"
        tokens = TokenValidator.extract_all_tokens(text)
        assert set(tokens) == {"FirstName", "Race", "Class"}


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_sanitize_text_function(self):
        """Test sanitize_text convenience function."""
        text = "Hello <FirstName>"
        sanitized, handler = sanitize_text(text, preserve_tokens=True)
        assert sanitized == "Hello <<TOKEN_0>>"

    def test_restore_text_function(self):
        """Test restore_text convenience function."""
        text = "Hello <FirstName>"
        sanitized, handler = sanitize_text(text)
        restored = restore_text(sanitized, handler)
        assert restored == text

    def test_extract_tokens_via_validator(self):
        """Test TokenValidator.extract_all_tokens convenience method."""
        text = "Hello <FirstName>, you are a <Class>!"
        tokens = TokenValidator.extract_all_tokens(text)
        assert set(tokens) == {"FirstName", "Class"}


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_string(self):
        """Test with empty string."""
        handler = TokenHandler()
        result = handler.sanitize("")
        assert result.sanitized_text == ""

    def test_none_input(self):
        """Test with None input."""
        handler = TokenHandler()
        result = handler.sanitize(None)
        assert result.sanitized_text == ""

    def test_nested_brackets(self):
        """Test with nested brackets (shouldn't happen but handle gracefully)."""
        handler = TokenHandler()
        result = handler.sanitize("Test <<NotAToken>>")
        # This should not be treated as a token
        assert result.sanitized_text == "Test <<NotAToken>>"

    def test_token_at_start_and_end(self):
        """Test tokens at start and end of string."""
        handler = TokenHandler()
        result = handler.sanitize("<FirstName> text <Class>")
        assert result.sanitized_text == "<<TOKEN_0>> text <<TOKEN_1>>"

    def test_consecutive_tokens(self):
        """Test consecutive tokens."""
        handler = TokenHandler()
        result = handler.sanitize("<FirstName><LastName>")
        assert result.sanitized_text == "<<TOKEN_0>><<TOKEN_1>>"

    def test_sanitize_preserves_nwn_action_tags(self):
        """NWN action tags should be protected from model rewrites."""
        handler = TokenHandler()
        result = handler.sanitize("<StartAction>[Wave]</Start> Hello <FirstName>.")
        assert result.sanitized_text.endswith(" Hello <<TOKEN_0>>.")
        placeholders = re.findall(r"__NWN_TAG_[A-Za-z0-9_]+__", result.sanitized_text)
        assert len(placeholders) == 2

    def test_restore_preserves_nwn_action_tags(self):
        """Roundtrip should restore original Start/StartAction tags."""
        handler = TokenHandler()
        source = "<StartAction>[Wave]</Start> Hello <FirstName>."
        sanitized = handler.sanitize(source)
        placeholders = re.findall(
            r"__NWN_TAG_[A-Za-z0-9_]+__", sanitized.sanitized_text
        )
        assert len(placeholders) == 2
        translated = f"Привет {placeholders[0]}[машет]{placeholders[1]}, <<TOKEN_0>>."
        restored = handler.restore(translated)
        assert restored == "Привет <StartAction>[машет]</Start>, <FirstName>."

    def test_sanitize_preserves_closing_start_tag(self):
        """Even closing-only malformed tag fragments must remain unchanged."""
        handler = TokenHandler()
        result = handler.sanitize("[Felkram begins to scream.]</Start>")
        assert result.sanitized_text.startswith("[Felkram begins to scream.]")
        placeholders = re.findall(r"__NWN_TAG_[A-Za-z0-9_]+__", result.sanitized_text)
        assert len(placeholders) == 1

    def test_restore_drops_unknown_nwn_placeholders(self):
        """Unknown placeholder artifacts from the model must be removed."""
        handler = TokenHandler()
        source = "<StartAction>[Wave]</Start>"
        sanitized = handler.sanitize(source)
        placeholders = re.findall(
            r"__NWN_TAG_[A-Za-z0-9_]+__", sanitized.sanitized_text
        )
        translated = f"{placeholders[0]}[машет]{placeholders[1]}[[NWN_TAG_999999]]"
        restored = handler.restore(translated)
        assert restored == "<StartAction>[машет]</Start>"

    def test_restore_accepts_wrapped_nwn_placeholders(self):
        """Restore should tolerate model-wrapped helper placeholders."""
        handler = TokenHandler()
        source = "<StartAction>[Wave]</Start>"
        sanitized = handler.sanitize(source)
        placeholders = re.findall(
            r"__NWN_TAG_[A-Za-z0-9_]+__", sanitized.sanitized_text
        )
        # Simulate model mutation seen in logs: __NWN_TAG_x__ -> <<[NWN_TAG_x]>>
        wrapped = []
        for p in placeholders:
            inner = p[len("__") : -len("__")]
            wrapped.append(f"<<[{inner}]>>")
        translated = f"{wrapped[0]}[машет]{wrapped[1]}"
        restored = handler.restore(translated)
        assert restored == "<StartAction>[машет]</Start>"

    def test_restore_scrubs_generic_nwn_tag_noise(self):
        """Any leftover NWN_TAG artifacts should be removed as noise."""
        handler = TokenHandler()
        source = "<StartAction>[Wave]</Start>"
        sanitized = handler.sanitize(source)
        placeholders = re.findall(
            r"__NWN_TAG_[A-Za-z0-9_]+__", sanitized.sanitized_text
        )
        translated = f"{placeholders[0]}[машет]{placeholders[1]} «NWN_TAG_garbage_42»"
        restored = handler.restore(translated)
        assert restored == "<StartAction>[машет]</Start> "

    def test_restore_strips_unbalanced_action_tags(self):
        """Malformed Start-tags should be stripped to avoid broken game markup."""
        handler = TokenHandler()
        handler.sanitize("<StartAction>[Wave]</Start>")
        restored = handler.restore("<StartAction>[машет]")
        assert restored == "[машет]"

    def test_clear(self):
        """Test clearing handler state."""
        handler = TokenHandler()
        handler.sanitize("Hello <FirstName>")
        assert handler.get_token_count() > 0
        handler.clear()
        assert handler.get_token_count() == 0
