"""Gemini (Google) provider implementation.

This provider supports Google's Gemini AI models for translation.
"""

import json
from typing import List, Optional

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base import BaseAIProvider, TranslationItem, TranslationResult, ProviderError, RateLimitError


class GeminiError(ProviderError):
    """Gemini-specific error."""
    pass


class GeminiProvider(BaseAIProvider):
    """AI provider implementation for Google Gemini."""

    # Available models
    MODELS = {
        "gemini-pro": "gemini-pro",
        "gemini-1.5-pro": "gemini-1.5-pro",
        "gemini-1.5-flash": "gemini-1.5-flash",
    }

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        **kwargs
    ):
        """Initialize Gemini provider.

        Args:
            api_key: Google API key
            model: Model identifier (default: gemini-pro)
            **kwargs: Additional parameters
        """
        if not GEMINI_AVAILABLE:
            raise GeminiError(
                "google-generativeai library is not installed. "
                "Install it with: pip install google-generativeai"
            )

        super().__init__(api_key, model, **kwargs)

        # Configure Gemini
        genai.configure(api_key=api_key)
        self._client = None

    @property
    def client(self):
        """Lazy initialization of Gemini client."""
        if self._client is None:
            self._client = genai.GenerativeModel(self.model)
        return self._client

    def get_default_model(self) -> str:
        """Get default Gemini model."""
        return "gemini-pro"

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "gemini"

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
        """Translate text using Gemini.

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

            # Combine prompts for Gemini (doesn't have separate system messages)
            full_prompt = f"{system_prompt}\n\n{user_prompt}"

            response = self.client.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )

            raw_response = response.text.strip()

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
            if "quota" in error_msg.lower() or "rate limit" in error_msg.lower():
                raise RateLimitError(f"Gemini rate limit exceeded: {error_msg}") from e
            raise GeminiError(f"Gemini translation failed: {error_msg}") from e

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
