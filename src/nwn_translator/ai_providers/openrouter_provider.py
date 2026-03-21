"""OpenRouter provider implementation.

OpenRouter is an OpenAI-compatible API gateway that provides access to
hundreds of AI models from Anthropic, Google, Meta, DeepSeek, OpenAI, and
others through a single endpoint.

See: https://openrouter.ai/docs
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, OpenAI
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

#: Exception types that should trigger automatic retry with exponential backoff.
_RETRYABLE_EXCEPTIONS = (RateLimitError, APIConnectionError, APITimeoutError)


class OpenRouterError(ProviderError):
    """OpenRouter-specific error."""
    pass


# OpenRouter structured outputs: keep schema size reasonable for providers.
_GLOSSARY_JSON_SCHEMA_MAX_KEYS = 96


def _glossary_json_schema_response_format(glossary_keys: List[str]) -> Dict[str, Any]:
    """Build OpenRouter ``response_format`` with strict JSON Schema for glossary keys."""
    properties: Dict[str, Any] = {}
    for key in glossary_keys:
        properties[key] = {
            "type": "string",
            "description": (
                "Canonical translation of this English proper name into the target language "
                "(nominative / dictionary form)."
            ),
        }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "nwn_glossary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties.keys()),
                "additionalProperties": False,
            },
        },
    }


class OpenRouterProvider(BaseAIProvider):
    """AI provider implementation for OpenRouter.

    OpenRouter exposes an OpenAI-compatible REST API, so this provider
    reuses the ``openai`` SDK with a custom ``base_url``.  Any model
    available on https://openrouter.ai/models can be used by passing its
    slug (e.g. ``anthropic/claude-3.5-sonnet``) as the ``model`` argument.
    """

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    #: Default model — change via config or ``--model`` CLI flag.
    DEFAULT_MODEL = "deepseek/deepseek-v3.2"

    #: A curated shortlist for reference; not an exhaustive list.
    POPULAR_MODELS = [
        "deepseek/deepseek-v3.2",
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
            model: Model slug (default: deepseek/deepseek-v3.2).
            site_url: Your app's URL, forwarded as HTTP-Referer header.
                OpenRouter uses this for attribution / rate-limit tiers.
            site_name: Your app's name, forwarded as X-Title header.
            **kwargs: Ignored (reserved for forward compatibility).
        """
        self.site_url = site_url
        self.site_name = site_name
        super().__init__(api_key, model, **kwargs)
        _headers = {
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name,
        }
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.OPENROUTER_BASE_URL,
            default_headers=dict(_headers),
        )
        self.async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.OPENROUTER_BASE_URL,
            default_headers=dict(_headers),
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

    @staticmethod
    def _parse_model_json_response(raw_response: str) -> str:
        """Extract translated string from model JSON (``translation`` key)."""
        try:
            json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
            json_str = json_match.group(0) if json_match else raw_response
            parsed = json.loads(json_str)
            translated_text = parsed.get("translation", "")
            if not isinstance(translated_text, str) or not translated_text:
                return str(raw_response)
            return translated_text
        except json.JSONDecodeError:
            return raw_response

    def _map_openrouter_exception(self, e: Exception) -> None:
        """Raise RateLimitError or OpenRouterError from a caught API exception."""
        error_msg = str(e)
        if "rate_limit" in error_msg.lower() or "429" in error_msg:
            raise RateLimitError(
                f"OpenRouter rate limit exceeded: {error_msg}"
            ) from e
        raise OpenRouterError(
            f"OpenRouter translation failed: {error_msg}"
        ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
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
            gb = glossary_block or ""
            system_prompt = self._create_system_prompt(target_lang, glossary_block=gb)
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

            raw_response = (response.choices[0].message.content or "").strip()
            translated_text = self._parse_model_json_response(raw_response)

            return TranslationResult(
                translated=translated_text,
                original=text,
                success=True,
                metadata={"model": self.model},
            )

        except (RateLimitError, APIConnectionError, APITimeoutError):
            raise
        except OpenRouterError:
            raise
        except Exception as e:
            self._map_openrouter_exception(e)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
    ) -> TranslationResult:
        """Async translate via OpenRouter (concurrent-friendly)."""
        if not text or not text.strip():
            return TranslationResult(translated="", original=text, success=True)

        try:
            gb = glossary_block or ""
            system_prompt = self._create_system_prompt(target_lang, glossary_block=gb)
            user_prompt = self._create_user_prompt(text, source_lang, context)

            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            raw_response = (response.choices[0].message.content or "").strip()
            translated_text = self._parse_model_json_response(raw_response)

            return TranslationResult(
                translated=translated_text,
                original=text,
                success=True,
                metadata={"model": self.model},
            )

        except (RateLimitError, APIConnectionError, APITimeoutError):
            raise
        except OpenRouterError:
            raise
        except Exception as e:
            self._map_openrouter_exception(e)

    async def _chat_completion_json_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        response_format: dict,
    ) -> str:
        """One chat completion with forced JSON-style ``response_format`` (no retries)."""
        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                stream=False,
            )
            return (response.choices[0].message.content or "").strip()
        except (RateLimitError, APIConnectionError, APITimeoutError):
            raise
        except OpenRouterError:
            raise
        except Exception as e:
            self._map_openrouter_exception(e)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def complete_json_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 16384,
        temperature: float = 0.3,
    ) -> str:
        """Single chat completion with OpenAI/OpenRouter ``json_object`` mode."""
        return await self._chat_completion_json_async(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def complete_glossary_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        glossary_keys: List[str],
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> str:
        """Glossary batch: prefer strict ``json_schema`` (structured outputs), else ``json_object``."""
        keys = sorted({str(k).strip() for k in glossary_keys if str(k).strip()})
        use_schema = 0 < len(keys) <= _GLOSSARY_JSON_SCHEMA_MAX_KEYS

        if use_schema:
            try:
                rf = _glossary_json_schema_response_format(keys)
                raw = await self._chat_completion_json_async(
                    system_prompt,
                    user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=rf,
                )
                if raw:
                    return raw
                logger.warning(
                    "Glossary structured output returned empty content; falling back to json_object."
                )
            except RateLimitError:
                raise
            except Exception as e:
                logger.warning(
                    "Glossary json_schema request failed (%s); falling back to json_object.",
                    e,
                )

        return await self.complete_json_chat_async(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

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
                    glossary_block=None,
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
