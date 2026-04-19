"""Base AI provider interface and related data structures.

This module defines the abstract interface that all AI providers must implement,
ensuring a consistent API across different AI services.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from ..config import PROMPT_CACHE_BREAKPOINTS_ENABLED


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
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAIProvider(ABC):
    """Abstract base class for AI translation providers.

    All AI providers must implement this interface for translation and batch APIs.
    """

    def __init__(self, api_key: str, model: Optional[str] = None, **kwargs):
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

    async def close_async_client(self) -> None:
        """Release any provider-held async resources. Default no-op."""
        return None

    @abstractmethod
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text from source language to target language.

        Args:
            text: Text to translate
            source_lang: Source language name (e.g., "english", "french")
            target_lang: Target language name (e.g., "spanish", "german")
            context: Additional context for translation (optional)
            glossary_block: Optional GLOSSARY section for consistent proper names
            content_profile: Optional prompt profile name (``"short_label"`` for
                name/label batches, ``"default"`` otherwise). Deterministic per
                batch to keep prompt-caching cache keys stable.

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
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text asynchronously.

        Default implementation runs :meth:`translate` in a worker thread.
        Providers may override with native async HTTP for better concurrency.
        """
        return await asyncio.to_thread(
            self.translate,
            text,
            source_lang,
            target_lang,
            context,
            glossary_block,
            content_profile,
        )

    async def translate_batch_async(
        self,
        items: List["TranslationItem"],
        source_lang: str,
        target_lang: str,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
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
                content_profile=content_profile,
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
        content_profile: Optional[str] = None,
    ) -> str:
        """Create system prompt for translation (stable + variable concatenated).

        Args:
            target_lang: Target language for translation
            glossary_block: Optional GLOSSARY section (from :class:`~nwn_translator.glossary.Glossary`)
            content_profile: Prompt profile name (see :func:`build_translation_system_prompt`).
        """
        from ..prompts import build_translation_system_prompt
        from ..prompts._builder import CONTENT_PROFILE_DEFAULT

        return build_translation_system_prompt(
            target_lang,
            self.player_gender,
            glossary_block,
            content_profile=content_profile or CONTENT_PROFILE_DEFAULT,
        )

    def _create_system_prompt_parts(
        self,
        target_lang: str,
        glossary_block: str = "",
        content_profile: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Return the stable/variable halves of the line-by-line translation prompt."""
        from ..prompts import build_translation_system_prompt_parts
        from ..prompts._builder import CONTENT_PROFILE_DEFAULT

        return build_translation_system_prompt_parts(
            target_lang,
            self.player_gender,
            glossary_block,
            content_profile=content_profile or CONTENT_PROFILE_DEFAULT,
        )

    @staticmethod
    def make_system_message_content(
        stable: str,
        variable: str = "",
        *,
        stable_suffix: str = "",
    ) -> Union[str, List[Dict[str, Any]]]:
        """Build the ``messages[0].content`` payload for a chat completion call.

        When the variable half is empty the payload is returned as a plain
        string (maximally compatible with legacy OpenAI-compatible gateways).

        Otherwise, a two-part ``content`` list is returned with a
        ``cache_control: {"type": "ephemeral"}`` breakpoint on the stable
        half — this is ignored by providers that do not support prompt
        caching and honoured by Anthropic/Gemini 2.5/Grok via OpenRouter,
        while remaining a stable prefix for OpenAI/DeepSeek automatic caching.

        Args:
            stable: Prompt text that is byte-identical across calls in a run.
            variable: Prompt text that may change between calls.
            stable_suffix: Additional text appended to *stable* (e.g. BATCH MODE
                instructions) — kept inside the cached portion.
        """
        full_stable = stable if not stable_suffix else f"{stable}{stable_suffix}"
        has_variable = bool(variable and variable.strip())
        if not PROMPT_CACHE_BREAKPOINTS_ENABLED or not has_variable:
            if has_variable:
                return f"{full_stable}\n\n{variable.strip()}"
            return full_stable
        return [
            {
                "type": "text",
                "text": full_stable,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": variable.strip()},
        ]

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
