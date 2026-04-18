"""Token handler for preserving NWN game tokens during translation.

This module handles the replacement and restoration of game tokens like <FirstName>,
<Class>, <Race>, etc. to prevent them from being translated or corrupted by AI.
"""

import re
import secrets
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

    # Pattern to match NWN tokens: <word>, <word:number>, or <word/word> (gender tags)
    TOKEN_PATTERN = re.compile(r"<(\w+(?:/\w+)?(?::\d+)?)>")

    # Pattern to match NWN inline action tags used in dialog text.
    # Examples: <StartAction>, </Start>, <StartSomething>
    ACTION_TAG_PATTERN = re.compile(r"</?Start[A-Za-z]*>")

    # Pattern for our placeholders: <<TOKEN_0>>, <<TOKEN_1>>, etc.
    PLACEHOLDER_PATTERN = re.compile(r"<<TOKEN_(\d+)>>")
    # Accept canonical and common model-mutated helper placeholders:
    # __NWN_TAG_x__, [[NWN_TAG_x]], <<[NWN_TAG_x]>>, <[NWN_TAG_x]>
    ACTION_PLACEHOLDER_PATTERN = re.compile(
        r"(?:__NWN_TAG_([A-Za-z0-9_]+)__|\[\[NWN_TAG_([A-Za-z0-9_]+)\]\]|<<\[NWN_TAG_([A-Za-z0-9_]+)\]>>|<\[NWN_TAG_([A-Za-z0-9_]+)\]>)"
    )
    NWN_TAG_NOISE_PATTERN = re.compile(r"[^\w\s]*NWN_TAG_[A-Za-z0-9_]+[^\w\s]*")
    RESTORED_ACTION_TAG_PATTERN = re.compile(r"</?Start[A-Za-z]*>")

    def __init__(self, preserve_standard_tokens: bool = True):
        """Initialize token handler.

        Args:
            preserve_standard_tokens: Whether to preserve standard NWN tokens
        """
        self.preserve_standard_tokens = preserve_standard_tokens
        self.token_map: Dict[str, str] = {}  # Maps placeholder to original token
        # Maps action placeholder ids to original NWN tags.
        self.action_tag_map: Dict[str, str] = {}
        self._action_nonce = secrets.token_hex(4)
        self.placeholder_counter = 0
        self.action_placeholder_counter = 0

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

        def replace_action_tag(match: re.Match) -> str:
            """Replacement function for NWN action tags."""
            original_tag = match.group(0)
            action_id = f"{self._action_nonce}_{self.action_placeholder_counter}"
            placeholder = f"__NWN_TAG_{action_id}__"
            self.action_tag_map[action_id] = original_tag
            self.action_placeholder_counter += 1
            return placeholder

        # Preserve NWN inline action tags (e.g. <StartAction>...</Start>)
        # so the model cannot rewrite tag names or closing forms.
        with_action_placeholders = self.ACTION_TAG_PATTERN.sub(replace_action_tag, text)

        # Replace all configured standard game tokens
        result.sanitized_text = self.TOKEN_PATTERN.sub(replace_token, with_action_placeholders)

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

        def restore_action_placeholder(match: re.Match) -> str:
            """Restore NWN action tags from placeholders."""
            action_id = next((g for g in match.groups() if g), None)
            if not action_id:
                return ""
            # Unknown placeholders are model artifacts and should be dropped.
            return self.action_tag_map.get(action_id, "")

        restored_tokens = self.PLACEHOLDER_PATTERN.sub(restore_placeholder, text)
        restored_actions = self.ACTION_PLACEHOLDER_PATTERN.sub(
            restore_action_placeholder, restored_tokens
        )
        # Ultra-defensive cleanup for model-mutated helper placeholders that still
        # contain NWN_TAG ids but no longer match known wrapper forms.
        restored_actions = self.NWN_TAG_NOISE_PATTERN.sub("", restored_actions)

        # Final safety net: if action tags became structurally broken,
        # strip them instead of shipping malformed markup that can break game parsing.
        if self._has_unbalanced_action_tags(restored_actions):
            return self.RESTORED_ACTION_TAG_PATTERN.sub("", restored_actions)

        return restored_actions

    @classmethod
    def _has_unbalanced_action_tags(cls, text: str) -> bool:
        """Return True when <Start...>/</Start> tags are malformed."""
        depth = 0
        for tag in cls.RESTORED_ACTION_TAG_PATTERN.findall(text):
            if tag.startswith("</"):
                depth -= 1
            else:
                depth += 1
            if depth < 0:
                return True
        return depth != 0

    def _should_preserve_token(self, token_name: str) -> bool:
        """Determine if a token should be preserved.

        Args:
            token_name: Token name without brackets (e.g., "FirstName" or "CustomToken:123")

        Returns:
            True if token should be preserved
        """
        if not self.preserve_standard_tokens:
            return False

        # Gender substitution tokens (e.g. <Brother/Sister>, <sir/madam>)
        if "/" in token_name:
            return True

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
        self.action_tag_map.clear()
        self.placeholder_counter = 0
        self.action_placeholder_counter = 0

    def get_token_count(self) -> int:
        """Get the number of tokens currently tracked.

        Returns:
            Number of unique token placeholders
        """
        return len(self.token_map) + len(self.action_tag_map)


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
