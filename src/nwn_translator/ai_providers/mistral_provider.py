"""Mistral AI provider implementation.

This provider supports Mistral AI models for translation.
"""

import json
from typing import List, Optional

try:
    from mistralai.client import MistralClient
    from mistralai.models.chat_completion import ChatMessage
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False
    MistralClient = None
    ChatMessage = None

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base import BaseAIProvider, TranslationItem, TranslationResult, ProviderError, RateLimitError


class MistralError(ProviderError):
    """Mistral-specific error."""
    pass


class MistralProvider(BaseAIProvider):
    """AI provider implementation for Mistral AI."""

    # Available models
    MODELS = {
        "mistral-large": "mistral-large-latest",
        "mistral-medium": "mistral-mistral-large-latest",
        "mistral-small": "mistral-small-latest",
        "codestral": "codestral-latest",
    }

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        **kwargs
    ):
        """Initialize Mistral provider.

        Args:
            api_key: Mistral API key
            model: Model identifier (default: mistral-medium)
            **kwargs: Additional parameters
        """
        if not MISTRAL_AVAILABLE:
            raise MistralError(
                "mistralai library is not installed. "
                "Install it with: pip install mistralai"
            )

        super().__init__(api_key, model, **kwargs)
        self.client = MistralClient(api_key=api_key)

    def get_default_model(self) -> str:
        """Get default Mistral model."""
        return "mistral-medium"

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "mistral"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError,)),
    )
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text using Mistral.

        Args:
            text: Text to translate
            source_lang: Source language
            target_lang: Target language
            context: Additional context

        Returns:
            TranslationResult
        """
        if not text or not text.strip():
            return TranslationResult(
                translated="",
                original=text,
                success=True,
            )

        try:
            system_prompt = self._create_system_prompt(target_lang)
            user_prompt = self._create_user_prompt(text, source_lang, context)

            response = self.client.chat(
                model=self.model,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            raw_response = response.choices[0].message.content.strip()

            try:
                if raw_response.startswith("```json"):
                    raw_response = raw_response.strip("`").replace("json\n", "", 1).strip()
                elif raw_response.startswith("```"):
                    raw_response = raw_response.strip("`").strip()
                    
                parsed = json.loads(raw_response)
                translated_text = parsed.get("translation", "")
                
                if not isinstance(translated_text, str) or not translated_text:
                    translated_text = str(raw_response)
            except json.JSONDecodeError:
                translated_text = raw_response

            return TranslationResult(
                translated=translated_text,
                original=text,
                success=True,
            )

        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(f"Mistral rate limit exceeded: {error_msg}") from e
            raise MistralError(f"Mistral translation failed: {error_msg}") from e

    def translate_batch(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
    ) -> List[TranslationResult]:
        """Translate multiple items.

        Args:
            items: List of TranslationItem objects
            source_lang: Source language
            target_lang: Target language

        Returns:
            List of TranslationResult objects
        """
        results = []

        for item in items:
            try:
                result = self.translate(
                    text=item.original,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    context=item.context,
                )
                result.metadata.update(item.metadata or {})
                results.append(result)
            except Exception as e:
                results.append(
                    TranslationResult(
                        translated="",
                        original=item.original,
                        success=False,
                        error=str(e),
                        metadata=item.metadata or {},
                    )
                )

        return results
