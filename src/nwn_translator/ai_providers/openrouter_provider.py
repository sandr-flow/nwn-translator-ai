"""OpenRouter provider implementation.

OpenRouter is an OpenAI-compatible API gateway that provides access to
hundreds of AI models from Anthropic, Google, Meta, DeepSeek, OpenAI, and
others through a single endpoint.

See: https://openrouter.ai/docs
"""

import json
import re
from typing import List, Optional

from openai import OpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
    RateLimitError,
)


class OpenRouterError(ProviderError):
    """OpenRouter-specific error."""
    pass


class OpenRouterProvider(BaseAIProvider):
    """AI provider implementation for OpenRouter.

    OpenRouter exposes an OpenAI-compatible REST API, so this provider
    reuses the ``openai`` SDK with a custom ``base_url``.  Any model
    available on https://openrouter.ai/models can be used by passing its
    slug (e.g. ``anthropic/claude-3.5-sonnet``) as the ``model`` argument.
    """

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    #: Default model — change via config or ``--model`` CLI flag.
    DEFAULT_MODEL = "minimax/minimax-m2.7"

    #: A curated shortlist for reference; not an exhaustive list.
    POPULAR_MODELS = [
        "minimax/minimax-m2.7",
        "openai/gpt-oss-120b",
        "openai/gpt-4o",
        "anthropic/claude-3.5-sonnet",
        "google/gemini-2.0-flash-001",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-r1",
        "meta-llama/llama-3.3-70b-instruct",
    ]

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        site_url: str = "https://github.com/nwn-modules-translator",
        site_name: str = "NWN Modules Translator",
        **kwargs,
    ):
        """Initialize the OpenRouter provider.

        Args:
            api_key: OpenRouter API key (sk-or-…).
            model: Model slug (default: minimax/minimax-m2.7).
            site_url: Your app's URL, forwarded as HTTP-Referer header.
                OpenRouter uses this for attribution / rate-limit tiers.
            site_name: Your app's name, forwarded as X-Title header.
            **kwargs: Ignored (reserved for forward compatibility).
        """
        self.site_url = site_url
        self.site_name = site_name
        super().__init__(api_key, model, **kwargs)
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": self.site_url,
                "X-Title": self.site_name,
            },
        )

    def get_default_model(self) -> str:
        """Get the default OpenRouter model.

        Returns:
            Default model slug.
        """
        return self.DEFAULT_MODEL

    def get_provider_name(self) -> str:
        """Get provider name.

        Returns:
            Provider identifier string.
        """
        return "openrouter"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError,)),
        reraise=True,
    )
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
    ) -> TranslationResult:
        """Translate text via OpenRouter.

        Args:
            text: Text to translate.
            source_lang: Source language name (e.g. "english").
            target_lang: Target language name (e.g. "russian").
            context: Optional context hint for the model.

        Returns:
            TranslationResult with translated text.

        Raises:
            RateLimitError: When the API returns HTTP 429.
            OpenRouterError: On any other API error.
        """
        if not text or not text.strip():
            return TranslationResult(translated="", original=text, success=True)

        try:
            system_prompt = self._create_system_prompt(target_lang)
            user_prompt = self._create_user_prompt(text, source_lang, context)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            raw_response = response.choices[0].message.content.strip()

            # Attempt to parse JSON
            try:
                json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = raw_response
                    
                parsed = json.loads(json_str)
                translated_text = parsed.get("translation", "")
                
                # Fallback if the model somehow returned string instead of dict
                if not isinstance(translated_text, str) or not translated_text:
                    translated_text = str(raw_response)
            except json.JSONDecodeError:
                # Fallback to raw text if JSON parsing fails
                translated_text = raw_response

            return TranslationResult(
                translated=translated_text,
                original=text,
                success=True,
                metadata={"model": self.model},
            )

        except Exception as e:
            error_msg = str(e)
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RateLimitError(
                    f"OpenRouter rate limit exceeded: {error_msg}"
                ) from e
            raise OpenRouterError(
                f"OpenRouter translation failed: {error_msg}"
            ) from e

    def translate_batch(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
    ) -> List[TranslationResult]:
        """Translate multiple items sequentially.

        Args:
            items: List of TranslationItem objects to translate.
            source_lang: Source language name.
            target_lang: Target language name.

        Returns:
            List of TranslationResult objects (one per input item).
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
