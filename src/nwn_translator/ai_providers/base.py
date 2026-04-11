"""Base AI provider interface and related data structures.

This module defines the abstract interface that all AI providers must implement,
ensuring a consistent API across different AI services.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ProviderError(Exception):
    """Base exception for provider errors."""
    pass


class RateLimitError(ProviderError):
    """Exception raised when rate limit is exceeded."""
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
        self.player_gender = kwargs.pop("player_gender", "male")
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
        glossary_block: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text from source language to target language.

        Args:
            text: Text to translate
            source_lang: Source language name (e.g., "english", "french")
            target_lang: Target language name (e.g., "spanish", "german")
            context: Additional context for translation (optional)
            glossary_block: Optional GLOSSARY section for consistent proper names

        Returns:
            TranslationResult with translated text

        Raises:
            ProviderError: If translation fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text asynchronously.

        Default implementation runs :meth:`translate` in a worker thread.
        Providers may override with native async HTTP for better concurrency.

        Args:
            text: Text to translate
            source_lang: Source language name
            target_lang: Target language name
            context: Optional context hint
            glossary_block: Optional GLOSSARY section for consistent proper names

        Returns:
            TranslationResult with translated text
        """
        return await asyncio.to_thread(
            self.translate,
            text,
            source_lang,
            target_lang,
            context,
            glossary_block,
        )

    async def translate_batch_async(
        self,
        items: List["TranslationItem"],
        source_lang: str,
        target_lang: str,
        glossary_block: Optional[str] = None,
    ) -> List["TranslationResult"]:
        """Translate a batch of short strings in a single API call.

        Default implementation falls back to individual translate_async calls.
        Providers may override with a true batch endpoint.
        """
        results = []
        for item in items:
            result = await self.translate_async(
                text=item.original,
                source_lang=source_lang,
                target_lang=target_lang,
                context=item.context,
                glossary_block=glossary_block,
            )
            results.append(result)
        return results

    async def classify_ncs_translate_gate_batch_async(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, bool]:
        """Return whether each NCS string occurrence should be translated.

        *entries* items must include at least ``key`` (batch index as str) and
        ``text``. Default: approve all (no-op gate for providers without LLM).
        """
        return {str(e["key"]): True for e in entries}

    def _validate_api_key(self) -> None:
        """Validate that the API key is present and valid.

        Raises:
            ProviderError: If API key is invalid
        """
        if not self.api_key or not self.api_key.strip():
            raise ProviderError(f"{self.get_provider_name()}: API key is required")

    def _create_system_prompt(
        self,
        target_lang: str,
        glossary_block: str = "",
    ) -> str:
        """Create system prompt for translation.

        Args:
            target_lang: Target language for translation
            glossary_block: Optional GLOSSARY section (from :class:`~nwn_translator.glossary.Glossary`)

        Returns:
            System prompt string
        """
        from ..prompts import build_translation_system_prompt
        return build_translation_system_prompt(target_lang, self.player_gender, glossary_block)

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
