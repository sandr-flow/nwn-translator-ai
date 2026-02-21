"""Grok (xAI) provider implementation.

Grok is xAI's AI model. This provider uses the OpenAI SDK with a custom base URL.
"""

import json
from typing import List, Optional

from openai import OpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base import BaseAIProvider, TranslationItem, TranslationResult, ProviderError, RateLimitError


class GrokError(ProviderError):
    """Grok-specific error."""
    pass


class GrokProvider(BaseAIProvider):
    """AI provider implementation for Grok (xAI)."""

    # Grok API base URL
    BASE_URL = "https://api.x.ai/v1"

    # Available models
    MODELS = {
        "grok-2": "grok-2",
        "grok-2-vision": "grok-2-vision",
        "grok-beta": "grok-beta",
    }

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs
    ):
        """Initialize Grok provider.

        Args:
            api_key: xAI API key
            model: Model identifier (default: grok-2)
            base_url: Custom base URL (optional)
            **kwargs: Additional parameters
        """
        super().__init__(api_key, model, **kwargs)

        # Initialize OpenAI client with custom base URL for Grok
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or self.BASE_URL,
        )

    def get_default_model(self) -> str:
        """Get default Grok model."""
        return "grok-2"

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "grok"

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
        """Translate text using Grok.

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

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,  # Lower temperature for more consistent translation
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
                raise RateLimitError(f"Grok rate limit exceeded: {error_msg}") from e
            raise GrokError(f"Grok translation failed: {error_msg}") from e

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
