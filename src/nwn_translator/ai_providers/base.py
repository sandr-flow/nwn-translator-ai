"""Provider interface: data types, errors and the :class:`TranslationProvider` protocol."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

#: ``messages[0].content`` of a chat request: plain text, or content parts with a
#: prompt-cache breakpoint (see ``OpenRouterProvider.make_system_message_content``).
SystemContent = Union[str, List[Dict[str, Any]]]


class ProviderError(Exception):
    """Base exception for provider errors."""


class RateLimitError(ProviderError):
    """Rate limit or in-flight budget exceeded (HTTP 429 / 402).

    Attributes:
        retry_after_seconds: The gateway's ``Retry-After`` hint in seconds, if any; the
            retry policy waits at least this long.
    """

    def __init__(self, message: str = "", *, retry_after_seconds: Optional[float] = None):
        """Creates the error.

        Args:
            message: Error message.
            retry_after_seconds: The gateway's ``Retry-After`` hint, if any.
        """
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class TranslationItem:
    """A single string to translate.

    Attributes:
        original: Text to translate.
        context: Context hint for the model (speaker, dialog position, field).
        metadata: Extractor metadata; batch payloads read hints, groups and
            source excerpts from it.
    """

    original: str
    context: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TranslationResult:
    """Result of translating one string.

    Attributes:
        translated: Translated text (empty on failure).
        original: Source text.
        success: Whether the translation can be used.
        error: Failure description.
        metadata: Provider details, e.g. ``{"model": ..., "batch": True}``.
    """

    translated: str
    original: str
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class TranslationProvider(Protocol):
    """The model operations the pipeline uses.

    The five task methods keep their names and keyword arguments: the translation log
    records ``method.__name__`` and the call arguments of every request. The errors each
    method raises, and the details of its prompts, are documented on the implementation,
    :class:`~nwn_translator.ai_providers.openrouter_provider.OpenRouterProvider`.

    Attributes:
        model: Model slug sent with every request.
    """

    model: str

    def get_provider_name(self) -> str:
        """Returns the short provider id recorded in metrics."""

    def make_system_message_content(self, stable: str, variable: str = "") -> SystemContent:
        """Builds ``messages[0].content`` from a cacheable and a per-call prompt half.

        Args:
            stable: Prompt text that is byte-identical across the calls of a run.
            variable: Prompt text that may change between calls.

        Returns:
            The system message content.
        """

    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        """Translates one string.

        Args:
            text: Text to translate.
            source_lang: Source language name.
            target_lang: Target language name.
            context: Context hint for the model.
            glossary_block: GLOSSARY section of the prompt.
            content_profile: Prompt profile (``default``, ``short_label``, ``script_message``).

        Returns:
            The translation, or a failed result when no reply parses.
        """

    async def translate_batch_async(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> List[TranslationResult]:
        """Translates several strings in one request.

        Args:
            items: Items to translate.
            source_lang: Source language name.
            target_lang: Target language name.
            glossary_block: GLOSSARY section of the prompt.
            content_profile: Prompt profile of the batch.

        Returns:
            One result per item, in order.
        """

    async def classify_ncs_translate_gate_batch_async(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Decides for each NCS string candidate whether it is player-facing text.

        Args:
            entries: Candidates with unique ``key`` values.
            source_lang: Source language label.

        Returns:
            ``key -> {"translate": bool, "reason": str}`` for every entry.
        """

    async def complete_json_chat_async(
        self,
        system_prompt: SystemContent,
        user_prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        use_reasoning: bool = True,
    ) -> str:
        """Sends one JSON-mode chat request with the caller's prompts.

        Args:
            system_prompt: System message content.
            user_prompt: User message.
            max_tokens: Completion token budget.
            temperature: Sampling temperature.
            use_reasoning: ``False`` requests the lowest effort the model allows.

        Returns:
            The stripped reply text.
        """

    async def complete_glossary_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        glossary_keys: List[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Sends one glossary request; the caller retries.

        Args:
            system_prompt: Glossary system prompt.
            user_prompt: Names to translate.
            glossary_keys: Requested names.
            max_tokens: Completion token budget.
            temperature: Sampling temperature.

        Returns:
            The stripped reply text.
        """

    async def close_async_client(self) -> None:
        """Closes the HTTP client bound to the current thread's event loop."""
