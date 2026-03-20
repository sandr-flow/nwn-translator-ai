"""Base AI provider interface and related data structures.

This module defines the abstract interface that all AI providers must implement,
ensuring a consistent API across different AI services.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ProviderError(Exception):
    """Base exception for provider errors."""
    pass


class RateLimitError(ProviderError):
    """Exception raised when rate limit is exceeded."""
    pass


class TranslationError(ProviderError):
    """Exception raised when translation fails."""
    pass


@dataclass
class TranslationItem:
    """Represents a single item to be translated.

    Attributes:
        original: Original text to translate
        context: Additional context for the translation (e.g., speaker name, dialog position)
        metadata: Additional information about the item
    """
    original: str
    context: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class TranslationResult:
    """Result of a translation operation.

    Attributes:
        translated: Translated text
        original: Original text (for reference)
        success: Whether translation was successful
        error: Error message if translation failed
        metadata: Additional metadata from the translation
    """
    translated: str
    original: str
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseAIProvider(ABC):
    """Abstract base class for AI translation providers.

    All AI providers must implement this interface for translation and batch APIs.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        **kwargs
    ):
        """Initialize the AI provider.

        Args:
            api_key: API key for authentication
            model: Model identifier (uses provider default if None)
            **kwargs: Additional provider-specific parameters
        """
        self.api_key = api_key
        self.model = model or self.get_default_model()
        self._validate_api_key()

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider.

        Returns:
            Default model identifier
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Get the provider name.

        Returns:
            Provider name (e.g., "grok", "openai")
        """
        pass

    @abstractmethod
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text from source language to target language.

        Args:
            text: Text to translate
            source_lang: Source language name (e.g., "english", "french")
            target_lang: Target language name (e.g., "spanish", "german")
            context: Additional context for translation (optional)

        Returns:
            TranslationResult with translated text

        Raises:
            TranslationError: If translation fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    @abstractmethod
    def translate_batch(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
    ) -> List[TranslationResult]:
        """Translate multiple items in batch.

        Args:
            items: List of TranslationItem objects to translate
            source_lang: Source language name
            target_lang: Target language name

        Returns:
            List of TranslationResult objects (one per input item)

        Raises:
            TranslationError: If batch translation fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    def _validate_api_key(self) -> None:
        """Validate that the API key is present and valid.

        Raises:
            ProviderError: If API key is invalid
        """
        if not self.api_key or not self.api_key.strip():
            raise ProviderError(f"{self.get_provider_name()}: API key is required")

    def _create_system_prompt(self, target_lang: str) -> str:
        """Create system prompt for translation.

        Args:
            target_lang: Target language for translation

        Returns:
            System prompt string
        """
        return (
            f"You are an elite translator for the game Neverwinter Nights. "
            f"Your task is to translate the text to {target_lang} according to Nora Gal's Golden School of Translation.\n\n"
            f"RULES:\n"
            f"1. Never translate word-for-word. Focus on meaning, emotion, and tone.\n"
            f"2. Use natural syntax and vocabulary. Avoid bureaucratic language (Chancellery/Канцелярит).\n"
            f"3. Identify idioms and adapt them to natural equivalents in the target language.\n"
            f"4. Preserve all formatting, line breaks, and special characters.\n"
            f"5. Do NOT translate or alter placeholders like <<TOKEN_0>>, <<TOKEN_1>>, etc.\n"
            f"6. The translated text MUST be grammatically correct, strictly preserving gender and case agreements (согласование по родам и падежам).\n"
            f"7. If the text is a proper noun (e.g., character name, unique location), transliterate it into the target language rather than translating the meaning or leaving it unchanged.\n\n"
            f"Your output MUST be strictly valid JSON. Do not use markdown code blocks.\n"
            f"The JSON object must contain exactly ONE key:\n"
            f'- "translation": The final translated text ONLY, perfectly formatted and ready to use in the game.\n\n'
            f"Do not include any other keys, your thought process, explanations, or any markdown formatting outside the JSON object.\n"
        )

    def _create_user_prompt(
        self,
        text: str,
        source_lang: str,
        context: Optional[str] = None,
    ) -> str:
        """Create user prompt for translation.

        Args:
            text: Text to translate
            source_lang: Source language
            context: Additional context

        Returns:
            User prompt string
        """
        prompt = f"Text to translate from {source_lang}:\n\n{text}"

        if context:
            prompt = f"Context Hint: {context}\n\n{prompt}"

        return prompt

    def __repr__(self) -> str:
        """String representation of the provider."""
        return f"{self.get_provider_name()}(model={self.model})"
