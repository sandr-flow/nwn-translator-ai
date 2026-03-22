"""OpenRouter provider implementation.

OpenRouter is an OpenAI-compatible API gateway that provides access to
hundreds of AI models from Anthropic, Google, Meta, DeepSeek, OpenAI, and
others through a single endpoint.

See: https://openrouter.ai/docs
"""

import json
import logging
import re
import threading
import asyncio
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, OpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
    RateLimitError,
)
from ..config import (
    TRANSLATION_TEMPERATURE,
    TRANSLATION_MAX_TOKENS,
    GLOSSARY_TEMPERATURE,
    GLOSSARY_MAX_TOKENS,
)

#: Exception types that should trigger automatic retry with exponential backoff.
_RETRYABLE_EXCEPTIONS = (RateLimitError, APIConnectionError, APITimeoutError)


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
    DEFAULT_MODEL = "google/gemini-3.1-flash-lite-preview"

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
        _timeout = httpx.Timeout(connect=10, read=180, write=10, pool=10)
        self._headers = dict(_headers)
        self._timeout = _timeout
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.OPENROUTER_BASE_URL,
            default_headers=self._headers,
            timeout=self._timeout,
            max_retries=0,
        )
        self._thread_local = threading.local()

    @property
    def async_client(self) -> AsyncOpenAI:
        """Get or create an AsyncOpenAI client bound to the current event loop.

        This prevents httpx connection pool errors when using a ThreadPoolExecutor
        where each thread runs its own asyncio event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = None

        if getattr(self._thread_local, "last_loop_id", None) != loop_id:
            self._thread_local.last_loop_id = loop_id
            self._thread_local.async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.OPENROUTER_BASE_URL,
                default_headers=self._headers,
                timeout=self._timeout,
                max_retries=0,
            )
        return self._thread_local.async_client

    async def close_async_client(self) -> None:
        """Explicitly close the thread-local async client (call before loop shutdown)."""
        client = getattr(self._thread_local, "async_client", None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
            self._thread_local.async_client = None
            self._thread_local.last_loop_id = None

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
            # Strip markdown code fences that some models wrap around JSON
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_response.strip())
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)

            # Use raw_decode for precise extraction of the first valid JSON object
            decoder = json.JSONDecoder()
            # Find the first '{' and decode from there
            idx = cleaned.find("{")
            if idx == -1:
                logger.warning("No JSON object found in model response, using raw text")
                return raw_response
            parsed, _ = decoder.raw_decode(cleaned, idx)
            translated_text = parsed.get("translation", "")
            if not isinstance(translated_text, str) or not translated_text:
                logger.warning("JSON parsed but 'translation' key missing or empty")
                return str(raw_response)
            return translated_text
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from model response, using raw text")
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
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
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
                temperature=TRANSLATION_TEMPERATURE,
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
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
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
                temperature=TRANSLATION_TEMPERATURE,
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
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def complete_json_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = TRANSLATION_MAX_TOKENS,
        temperature: float = TRANSLATION_TEMPERATURE,
    ) -> str:
        """Single chat completion with OpenAI/OpenRouter ``json_object`` mode."""
        return await self._chat_completion_json_async(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    async def complete_glossary_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        glossary_keys: List[str],
        max_tokens: int = GLOSSARY_MAX_TOKENS,
        temperature: float = GLOSSARY_TEMPERATURE,
    ) -> str:
        """Glossary batch via ``json_object`` mode (no retries — caller retries).

        Uses ``json_object`` response format for maximum model compatibility.
        Structured outputs (``json_schema`` with ``strict: true``) cause
        timeouts/hangs on models without native support (DeepSeek, Qwen, etc.)
        because OpenRouter's constrained-decoding wrapper is extremely slow.

        Callers (``GlossaryBuilder._translate_batch_async``) handle retries
        and partial-result merging, so no tenacity decorator here.
        """
        return await self._chat_completion_json_async(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def translate_batch_async(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
        glossary_block: Optional[str] = None,
    ) -> List[TranslationResult]:
        """Translate a batch of short strings in a single API call.

        Sends up to ~30 short items as a JSON mapping and parses the
        response back into individual TranslationResult objects.

        Args:
            items: List of TranslationItem objects (should be short strings).
            source_lang: Source language name.
            target_lang: Target language name.
            glossary_block: Optional glossary prompt block.

        Returns:
            List of TranslationResult, one per input item.
        """
        if not items:
            return []

        gb = glossary_block or ""
        system_prompt = self._create_system_prompt(target_lang, glossary_block=gb)
        # Override the JSON output instruction for batch mode
        system_prompt += (
            "\nBATCH MODE: You will receive a JSON object mapping numeric IDs "
            "(\"0\", \"1\", \"2\", ...) to short strings. "
            "Return a JSON object with the EXACT SAME numeric keys, where each "
            "value is the translated string. Do NOT rename, add, or remove keys. "
            "Do NOT wrap in markdown. Output ONLY the JSON object.\n"
        )

        # Build the batch payload: {"0": "text", "1": "text", ...}
        batch_input = {}
        for i, item in enumerate(items):
            batch_input[str(i)] = item.original

        user_prompt = (
            f"Translate each value from {source_lang}.\n\n"
            + json.dumps(batch_input, ensure_ascii=False)
        )

        try:
            raw = await self._chat_completion_json_async(
                system_prompt,
                user_prompt,
                max_tokens=TRANSLATION_MAX_TOKENS,
                temperature=TRANSLATION_TEMPERATURE,
                response_format={"type": "json_object"},
            )

            # Strip markdown fences if present
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)

            # Use raw_decode to handle trailing junk
            decoder = json.JSONDecoder()
            idx = cleaned.find("{")
            if idx == -1:
                raise json.JSONDecodeError("No JSON object found", cleaned, 0)
            parsed, _ = decoder.raw_decode(cleaned, idx)

            results = []
            for i, item in enumerate(items):
                key = str(i)
                translated = parsed.get(key, "")
                if isinstance(translated, str) and translated:
                    results.append(TranslationResult(
                        translated=translated,
                        original=item.original,
                        success=True,
                        metadata={"model": self.model, "batch": True},
                    ))
                else:
                    results.append(TranslationResult(
                        translated="",
                        original=item.original,
                        success=False,
                        error="Missing or empty translation in batch response",
                        metadata={"model": self.model, "batch": True},
                    ))
            return results

        except json.JSONDecodeError as e:
            logger.warning("Batch JSON parse failed: %s", e)
            return [
                TranslationResult(
                    translated="", original=item.original,
                    success=False, error=f"Batch JSON parse error: {e}",
                )
                for item in items
            ]
        except (RateLimitError, APIConnectionError, APITimeoutError):
            raise
        except Exception as e:
            self._map_openrouter_exception(e)

