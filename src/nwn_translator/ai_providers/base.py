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
            TranslationError: If translation fails
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
        glossary_header = ""
        glossary_rule = ""
        if glossary_block and glossary_block.strip():
            glossary_header = f"{glossary_block.strip()}\n\n"
            glossary_rule = (
                "9. Use the GLOSSARY above for every proper name it lists: keep the same "
                "translation choice everywhere; only change word form (case, number, etc.) "
                "to fit the sentence. If a proper name is not listed, follow rules 7-8.\n"
            )

        return (
            f"You are an elite translator for the game Neverwinter Nights. "
            f"Your task is to translate the text to {target_lang} according to Nora Gal's Golden School of Translation.\n\n"
            f"{glossary_header}"
            f"RULES:\n"
            f"1. Never translate word-for-word. Focus on meaning, emotion, and tone.\n"
            f"2. Use natural syntax and vocabulary. Avoid bureaucratic language (Chancellery/Канцелярит).\n"
            f"3. Identify idioms and adapt them to natural equivalents in the target language.\n"
            f"4. Preserve all formatting, line breaks, and special characters.\n"
            f"5. Do NOT translate or alter placeholders like <<TOKEN_0>>, <<TOKEN_1>>, etc.\n"
            f"6. The translated text MUST be grammatically correct, strictly preserving gender and case agreements "
            f"(согласование по родам и падежам). Exception: see rule 10 for intentionally broken speech.\n"
            f"7. PROPER NAMES — translating vs. transliterating:\n"
            f"   a) Descriptive/meaningful names: TRANSLATE the meaning. "
            f'NEVER produce phonetic transliterations of English words.\n'
            f"      Examples:\n"
            f'      - "Inn of the Lance" -> "Таверна Копья" (GOOD) — NOT "Инн оф зэ Ланс" (BAD)\n'
            f'      - "Deadman\'s Marsh" -> "Болото Мертвецов" (GOOD) — NOT "Дэдмэнз Марш" (BAD)\n'
            f'      - "Dark Ranger" -> "Тёмный Рейнджер" (GOOD) — NOT "Дарк Рейнджер" (BAD)\n'
            f'      - "Horde Raven" -> "Стайный Ворон" (GOOD) — NOT "ХордРейвен" (BAD)\n'
            f'      - "Fearling" -> "Страхолик" (GOOD) — NOT "Фирлинг" (BAD)\n'
            f"   b) Personal names (first/last names of characters): TRANSLITERATE.\n"
            f"      Examples:\n"
            f'      - "Perin Izrick" -> "Перин Изрик"\n'
            f'      - "Talias Allenthel" -> "Талиас Аллентел"\n'
            f'      - "Drixie" -> "Дрикси"\n'
            f"8. When in doubt whether a name is descriptive or personal, check: does the name "
            f"consist of ordinary English words with clear meaning? Then translate the meaning. "
            f"Is it a made-up fantasy name? Then transliterate.\n"
            f"{glossary_rule}"
            f"10. PRESERVE SPEECH STYLE AND REGISTER. This is a role-playing game with characters "
            f"of different intelligence and background. If the original text has broken grammar, "
            f"primitive syntax, or childlike speech (low-INT characters, barbarians, goblins, etc.), "
            f"you MUST reproduce an equally broken, primitive style in the translation. "
            f"DO NOT \"fix\" or \"correct\" their speech — that would destroy the character.\n"
            f"    Examples (English low-INT -> {target_lang} low-INT equivalent):\n"
            f'    - "Me no want you here no more" -> "Моя тебя тут не хотеть больше" '
            f"(GOOD, broken) — NOT \"Мне не нужен ты тут\" (BAD, normalized)\n"
            f'    - "Me <FullName>. Me big adventurer too." -> "Моя <FullName>. Моя тоже большой путешественник." '
            f"(GOOD) — NOT \"Я <FullName>. Я тоже большой искатель приключений.\" (BAD)\n"
            f'    - "You big fat liar. Me no follow you." -> "Ты толстый врун. Моя за тобой не ходить." '
            f"(GOOD) — NOT \"Ты большой жирный лгун. Я не пойду за тобой.\" (BAD)\n"
            f'    - "Ha ha! Me no crawl. Me here to point and laugh!" -> '
            f'"Ха-ха! Моя не ползать. Моя тут — пальцем тыкать и ржать!" '
            f"(GOOD) — NOT \"Я не ползаю. Я тут, чтобы показывать пальцем и смеяться!\" (BAD)\n"
            f"    Key pattern: in English, low-INT speech uses \"me\" instead of \"I\", drops articles/verbs, "
            f"simplifies grammar. In Russian, the equivalent is using \"моя\" instead of \"я\", infinitives "
            f"instead of conjugated verbs, dropping prepositions, and childlike sentence structure.\n"
            f"\nYour output MUST be strictly valid JSON. Do not use markdown code blocks.\n"
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
