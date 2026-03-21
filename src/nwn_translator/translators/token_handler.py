"""Token handler for preserving NWN game tokens during translation.

This module handles the replacement and restoration of game tokens like <FirstName>,
<Class>, <Race>, etc. to prevent them from being translated or corrupted by AI.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..config import STANDARD_TOKENS


@dataclass
class TokenReplacement:
    """Represents a single token replacement operation."""

    original: str
    placeholder: str
    position: int  # Position in original text


@dataclass
class SanitizedText:
    """Text after token sanitization with metadata for restoration."""

    sanitized_text: str
    replacements: List[TokenReplacement] = field(default_factory=list)

    def add_replacement(self, original: str, placeholder: str, position: int) -> None:
        """Add a token replacement record."""
        self.replacements.append(TokenReplacement(original, placeholder, position))


class TokenHandler:
    """Handler for managing token replacement and restoration."""

    # Pattern to match NWN tokens: <word> or <word:number>
    TOKEN_PATTERN = re.compile(r"<(\w+(?::\d+)?)>")

    # Pattern for our placeholders: <<TOKEN_0>>, <<TOKEN_1>>, etc.
    PLACEHOLDER_PATTERN = re.compile(r"<<TOKEN_(\d+)>>")

    def __init__(self, preserve_standard_tokens: bool = True):
        """Initialize token handler.

        Args:
            preserve_standard_tokens: Whether to preserve standard NWN tokens
        """
        self.preserve_standard_tokens = preserve_standard_tokens
        self.token_map: Dict[str, str] = {}  # Maps placeholder to original token
        self.placeholder_counter = 0

    def sanitize(self, text: str) -> SanitizedText:
        """Replace tokens in text with placeholders.

        Args:
            text: Text potentially containing NWN tokens

        Returns:
            SanitizedText with placeholders and replacement metadata
        """
        if not text or not isinstance(text, str):
            return SanitizedText(sanitized_text=text or "")

        result = SanitizedText(sanitized_text=text)
        offset = 0

        def replace_token(match: re.Match) -> str:
            """Replacement function for regex."""
            nonlocal offset
            original_token = match.group(0)  # Full match including brackets
            token_name = match.group(1)  # Content inside brackets

            # Check if this is a standard token we should preserve
            if not self._should_preserve_token(token_name):
                return original_token

            # Create placeholder
            placeholder = f"<<TOKEN_{self.placeholder_counter}>>"

            # Record position
            position = match.start() + offset
            result.add_replacement(original_token, placeholder, position)

            # Store mapping for restoration
            self.token_map[placeholder] = original_token

            # Update counter and offset
            self.placeholder_counter += 1
            offset += len(placeholder) - len(original_token)

            return placeholder

        # Replace all tokens
        result.sanitized_text = self.TOKEN_PATTERN.sub(replace_token, text)

        return result

    def restore(self, text: str) -> str:
        """Restore tokens from placeholders.

        Args:
            text: Text containing placeholders

        Returns:
            Text with restored tokens
        """
        if not text or not isinstance(text, str):
            return text or ""

        def restore_placeholder(match: re.Match) -> str:
            """Restoration function for regex."""
            placeholder = match.group(0)
            return self.token_map.get(placeholder, placeholder)

        return self.PLACEHOLDER_PATTERN.sub(restore_placeholder, text)

    def _should_preserve_token(self, token_name: str) -> bool:
        """Determine if a token should be preserved.

        Args:
            token_name: Token name without brackets (e.g., "FirstName" or "CustomToken:123")

        Returns:
            True if token should be preserved
        """
        if not self.preserve_standard_tokens:
            return False

        # Check if it's a standard token
        full_token = f"<{token_name}>"
        if full_token in STANDARD_TOKENS:
            return True

        # Check if it's a custom token (e.g., CustomToken:123)
        if token_name.startswith("CustomToken:"):
            return True

        # Preserve any token with a colon (likely special token)
        if ":" in token_name:
            return True

        return False

    def clear(self) -> None:
        """Clear all token mappings and reset counter."""
        self.token_map.clear()
        self.placeholder_counter = 0

    def get_token_count(self) -> int:
        """Get the number of tokens currently tracked.

        Returns:
            Number of unique token placeholders
        """
        return len(self.token_map)


class TokenValidator:
    """Validator for checking token preservation."""

    @staticmethod
    def validate_restoration(original: str, restored: str) -> bool:
        """Validate that restoration preserved all tokens correctly.

        Args:
            original: Original text with tokens
            restored: Restored text after sanitize-restore cycle

        Returns:
            True if tokens were preserved correctly
        """
        # Extract tokens from both texts
        original_tokens = set(TokenHandler.TOKEN_PATTERN.findall(original))
        restored_tokens = set(TokenHandler.TOKEN_PATTERN.findall(restored))

        return original_tokens == restored_tokens

    @staticmethod
    def find_token_mismatches(
        original: str, restored: str
    ) -> Tuple[List[str], List[str]]:
        """Find tokens that don't match between original and restored.

        Args:
            original: Original text with tokens
            restored: Restored text

        Returns:
            Tuple of (missing_tokens, extra_tokens)
        """
        original_tokens = set(TokenHandler.TOKEN_PATTERN.findall(original))
        restored_tokens = set(TokenHandler.TOKEN_PATTERN.findall(restored))

        missing = original_tokens - restored_tokens
        extra = restored_tokens - original_tokens

        return sorted(missing), sorted(extra)

    @staticmethod
    def extract_all_tokens(text: str) -> List[str]:
        """Extract all tokens from text.

        Args:
            text: Text to extract tokens from

        Returns:
            List of unique tokens found
        """
        tokens = TokenHandler.TOKEN_PATTERN.findall(text)
        return sorted(set(tokens))


# Convenience functions for simple use cases
def sanitize_text(text: str, preserve_tokens: bool = True) -> Tuple[str, TokenHandler]:
    """Sanitize text by replacing tokens with placeholders.

    Args:
        text: Text to sanitize
        preserve_tokens: Whether to preserve tokens

    Returns:
        Tuple of (sanitized_text, handler) where handler can be used for restoration
    """
    handler = TokenHandler(preserve_standard_tokens=preserve_tokens)
    result = handler.sanitize(text)
    return result.sanitized_text, handler


def restore_text(text: str, handler: TokenHandler) -> str:
    """Restore tokens from placeholders.

    Args:
        text: Text with placeholders
        handler: TokenHandler that was used for sanitization

    Returns:
        Text with restored tokens
    """
    return handler.restore(text)
