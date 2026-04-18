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
from typing import Any, Dict, List, Optional, Union

import httpx

logger = logging.getLogger(__name__)

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    OpenAI,
)
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
    parse_reasoning_effort,
)
from ..race_dictionary import match_race_terms

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

    #: API base URL. Subclasses override to target an OpenAI-compatible gateway.
    BASE_URL = "https://openrouter.ai/api/v1"
    #: Backwards-compat alias; external code may reference ``OPENROUTER_BASE_URL``.
    OPENROUTER_BASE_URL = BASE_URL
    #: Human-readable provider label used in error messages.
    PROVIDER_LABEL = "OpenRouter"
    #: Short identifier returned from :meth:`get_provider_name`.
    PROVIDER_NAME = "openrouter"

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
            **kwargs: Passed to base (e.g. ``player_gender``); ``reasoning_effort`` is consumed here for OpenRouter's ``reasoning`` request field.
        """
        reasoning_raw = kwargs.pop("reasoning_effort", None)
        self.site_url = site_url
        self.site_name = site_name
        super().__init__(api_key, model, **kwargs)
        self._reasoning_effort = parse_reasoning_effort(reasoning_raw)
        _timeout = httpx.Timeout(connect=10, read=180, write=10, pool=10)
        self._headers = self._build_default_headers()
        self._timeout = _timeout
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.BASE_URL,
            default_headers=self._headers,
            timeout=self._timeout,
            max_retries=0,
        )
        self._thread_local = threading.local()

    def _build_default_headers(self) -> Dict[str, str]:
        """Extra headers appended to every request. Subclasses may override."""
        return {
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name,
        }

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
                base_url=self.BASE_URL,
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
        return self.PROVIDER_NAME

    def _reasoning_extra_body(self) -> Optional[Dict[str, Any]]:
        """OpenRouter ``extra_body`` fragment for ``reasoning``, or ``None``."""
        if not self._reasoning_effort:
            return None
        return {"reasoning": {"effort": self._reasoning_effort}}

    def _chat_completions_create_sync(self, *, use_reasoning: bool = True, **kwargs: Any):
        """``chat.completions.create`` with optional ``reasoning``; one 400 retry without it."""
        reasoning_extra = self._reasoning_extra_body() if use_reasoning else None
        if reasoning_extra:
            call_kw = {**kwargs, "extra_body": reasoning_extra}
            try:
                return self.client.chat.completions.create(**call_kw)
            except BadRequestError:
                logger.warning(
                    "OpenRouter returned HTTP 400 with reasoning enabled; retrying without reasoning"
                )
                return self.client.chat.completions.create(**kwargs)
        return self.client.chat.completions.create(**kwargs)

    async def _chat_completions_create_async(
        self,
        *,
        use_reasoning: bool = True,
        **kwargs: Any,
    ):
        """Async ``chat.completions.create`` with optional ``reasoning``; one 400 retry without it."""
        reasoning_extra = self._reasoning_extra_body() if use_reasoning else None
        if reasoning_extra:
            call_kw = {**kwargs, "extra_body": reasoning_extra}
            try:
                return await self.async_client.chat.completions.create(**call_kw)
            except BadRequestError:
                logger.warning(
                    "OpenRouter returned HTTP 400 with reasoning enabled; retrying without reasoning"
                )
                return await self.async_client.chat.completions.create(**kwargs)
        return await self.async_client.chat.completions.create(**kwargs)

    @staticmethod
    def _parse_model_json_response(raw_response: str) -> str:
        """Extract translated string from model JSON (``translation`` key).

        Returns the translated text, or ``""`` when the response is
        truncated / unparseable JSON (caller should treat as failure).
        """
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
                return ""
            return translated_text
        except json.JSONDecodeError:
            # Response looks like truncated JSON — return empty to trigger retry
            logger.warning(
                "Truncated/invalid JSON in model response, will retry. "
                "Raw (first 200 chars): %s",
                (raw_response or "")[:200],
            )
            return ""

    def _map_openrouter_exception(self, e: Exception) -> None:
        """Raise RateLimitError or OpenRouterError from a caught API exception."""
        error_msg = str(e)
        if "rate_limit" in error_msg.lower() or "429" in error_msg:
            raise RateLimitError(f"{self.PROVIDER_LABEL} rate limit exceeded: {error_msg}") from e
        raise OpenRouterError(f"{self.PROVIDER_LABEL} translation failed: {error_msg}") from e

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
            race_block = match_race_terms(text, target_lang)
            if race_block:
                gb = gb + "\n\n" + race_block if gb else race_block
            stable, variable = self._create_system_prompt_parts(target_lang, glossary_block=gb)
            system_content = self.make_system_message_content(stable, variable)
            user_prompt = self._create_user_prompt(text, source_lang, context)

            response = self._chat_completions_create_sync(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=TRANSLATION_TEMPERATURE,
                response_format={"type": "json_object"},
            )

            raw_response = (response.choices[0].message.content or "").strip()
            translated_text = self._parse_model_json_response(raw_response)

            if not translated_text:
                return TranslationResult(
                    translated="",
                    original=text,
                    success=False,
                    error="Model returned empty or unparseable JSON",
                    metadata={"model": self.model},
                )

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
            race_block = match_race_terms(text, target_lang)
            if race_block:
                gb = gb + "\n\n" + race_block if gb else race_block
            stable, variable = self._create_system_prompt_parts(target_lang, glossary_block=gb)
            system_content = self.make_system_message_content(stable, variable)
            user_prompt = self._create_user_prompt(text, source_lang, context)

            response = await self._chat_completions_create_async(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=TRANSLATION_TEMPERATURE,
                response_format={"type": "json_object"},
            )

            raw_response = (response.choices[0].message.content or "").strip()
            translated_text = self._parse_model_json_response(raw_response)

            if not translated_text:
                return TranslationResult(
                    translated="",
                    original=text,
                    success=False,
                    error="Model returned empty or unparseable JSON",
                    metadata={"model": self.model},
                )

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
        system_prompt: Any,
        user_prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        response_format: dict,
        use_reasoning: bool = True,
    ) -> str:
        """One chat completion with forced JSON-style ``response_format`` (no retries).

        ``system_prompt`` may be a plain string or a list of content parts
        (the latter produced by :meth:`BaseAIProvider.make_system_message_content`
        when prompt caching is in effect).
        """
        try:
            response = await self._chat_completions_create_async(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                stream=False,
                use_reasoning=use_reasoning,
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
        system_prompt: Any,
        user_prompt: str,
        *,
        max_tokens: int = TRANSLATION_MAX_TOKENS,
        temperature: float = TRANSLATION_TEMPERATURE,
        use_reasoning: bool = True,
    ) -> str:
        """Single chat completion with OpenAI/OpenRouter ``json_object`` mode.

        ``system_prompt`` accepts either a plain string or a content-parts
        list (see :meth:`BaseAIProvider.make_system_message_content`).
        """
        return await self._chat_completion_json_async(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
            use_reasoning=use_reasoning,
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
            use_reasoning=False,
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
        combined_text = " ".join(item.original for item in items if item.original)
        race_block = match_race_terms(combined_text, target_lang)
        if race_block:
            gb = gb + "\n\n" + race_block if gb else race_block
        stable, variable = self._create_system_prompt_parts(target_lang, glossary_block=gb)
        # BATCH MODE instructions are identical for every batch call — keep
        # them inside the cached stable half so the prompt prefix is stable.
        batch_mode_suffix = (
            "\nBATCH MODE: You will receive a JSON object mapping numeric IDs "
            "to items. Each item is either a plain string or an object "
            '{"text": "...", "hint": "..."}. '
            'The hint (e.g. "item_name", "creature_first_name", "store_name") '
            "tells you what kind of game entity this is — use it to decide "
            "whether to translate the meaning or transliterate. "
            "Return a JSON object with the EXACT SAME numeric keys, where each "
            "value is the translated string (NOT an object). "
            "Do NOT rename, add, or remove keys. "
            "Do NOT wrap in markdown. Output ONLY the JSON object.\n"
        )
        system_content = self.make_system_message_content(
            stable, variable, stable_suffix=batch_mode_suffix
        )

        # Build the batch payload with optional type hints from metadata
        batch_input: dict = {}
        for i, item in enumerate(items):
            hint = (item.metadata or {}).get("type", "")
            if hint:
                batch_input[str(i)] = {"text": item.original, "hint": hint}
            else:
                batch_input[str(i)] = item.original

        user_prompt = f"Translate each value from {source_lang}.\n\n" + json.dumps(
            batch_input, ensure_ascii=False
        )

        try:
            raw = await self._chat_completion_json_async(
                system_content,
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
                    results.append(
                        TranslationResult(
                            translated=translated,
                            original=item.original,
                            success=True,
                            metadata={"model": self.model, "batch": True},
                        )
                    )
                else:
                    results.append(
                        TranslationResult(
                            translated="",
                            original=item.original,
                            success=False,
                            error="Missing or empty translation in batch response",
                            metadata={"model": self.model, "batch": True},
                        )
                    )
            return results

        except json.JSONDecodeError as e:
            logger.warning("Batch JSON parse failed: %s", e)
            return [
                TranslationResult(
                    translated="",
                    original=item.original,
                    success=False,
                    error=f"Batch JSON parse error: {e}",
                )
                for item in items
            ]
        except (RateLimitError, APIConnectionError, APITimeoutError):
            raise
        except Exception as e:
            self._map_openrouter_exception(e)

    @staticmethod
    def _ncs_gate_system_prompt() -> str:
        return (
            "You classify strings from Neverwinter Nights (NWN) compiled NWScript. "
            "For each item, decide if it should be translated for players — text "
            "shown as dialog, floating text, SendMessageToPC, SpeakString, etc.\n"
            'Return ONLY a JSON object. Keys are "0", "1", … matching the input. '
            'Each value must be an object: {"translate": true} or {"translate": false}. '
            "Use false for script names, tags, resrefs, variable names, debug logs, "
            "identifiers, and code-like fragments. "
            'Short NPC lines (e.g. "Mommy.", "I\'m okay, sir. I think.", "Help!") '
            "are usually real dialogue — use translate: true unless it is obviously a token. "
            "Informal or broken English in-character lines still count as dialogue. "
            "Use true for natural language the player reads.\n"
        )

    def _ncs_gate_build_user_prompt(
        self,
        *,
        source_lang: str,
        entries: List[Dict[str, Any]],
    ) -> str:
        user_payload: Dict[str, Dict[str, str]] = {}
        for e in entries:
            user_payload[str(e["key"])] = {
                "text": e.get("text", ""),
                "file": str(e.get("file", "")),
                "offset": str(e.get("offset", "")),
                "hint": str(e.get("hint", "")),
            }
        return f"Source language label: {source_lang}. Classify each entry.\n\n" + json.dumps(
            user_payload, ensure_ascii=False
        )

    def _parse_ncs_gate_raw(
        self,
        raw: str,
        entries: List[Dict[str, Any]],
    ) -> Dict[str, bool]:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        decoder = json.JSONDecoder()
        idx = cleaned.find("{")
        if idx == -1:
            raise json.JSONDecodeError("No JSON object", cleaned, 0)
        parsed, _ = decoder.raw_decode(cleaned, idx)

        out: Dict[str, bool] = {}
        for e in entries:
            k = str(e["key"])
            cell: Union[Dict[str, Any], bool, None] = parsed.get(k)
            if isinstance(cell, dict):
                out[k] = bool(cell.get("translate", False))
            elif isinstance(cell, bool):
                out[k] = cell
            else:
                out[k] = False
        return out

    async def _ncs_gate_batch_with_recovery(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, bool]:
        """Parse gate JSON with token bump retries, then split batch on failure."""
        if not entries:
            return {}

        max_tok = min(8192, TRANSLATION_MAX_TOKENS)
        last_err: Optional[json.JSONDecodeError] = None
        for attempt in range(2):
            try:
                raw = await self._chat_completion_json_async(
                    self._ncs_gate_system_prompt(),
                    self._ncs_gate_build_user_prompt(source_lang=source_lang, entries=entries),
                    max_tokens=max_tok,
                    temperature=0.15,
                    response_format={"type": "json_object"},
                )
                return self._parse_ncs_gate_raw(raw, entries)
            except json.JSONDecodeError as err:
                last_err = err
                logger.warning(
                    "NCS gate JSON parse failed (attempt %d/2, %d entries): %s",
                    attempt + 1,
                    len(entries),
                    err,
                )
                max_tok = min(TRANSLATION_MAX_TOKENS, max(max_tok * 2, 4096))

        if len(entries) <= 1:
            logger.warning(
                "NCS gate giving up on batch; defaulting to translate=false: %s",
                last_err,
            )
            return {str(e["key"]): False for e in entries}

        mid = len(entries) // 2
        left = entries[:mid]
        right = entries[mid:]
        left_rekeyed = [{**e, "key": str(i)} for i, e in enumerate(left)]
        right_rekeyed = [{**e, "key": str(i)} for i, e in enumerate(right)]
        left_out = await self._ncs_gate_batch_with_recovery(left_rekeyed, source_lang=source_lang)
        right_out = await self._ncs_gate_batch_with_recovery(right_rekeyed, source_lang=source_lang)
        merged: Dict[str, bool] = {}
        for i, e in enumerate(left):
            merged[str(e["key"])] = left_out[str(i)]
        for i, e in enumerate(right):
            merged[str(e["key"])] = right_out[str(i)]
        return merged

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def classify_ncs_translate_gate_batch_async(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, bool]:
        """LLM gate: whether each NCS string occurrence is player-facing."""
        return await self._ncs_gate_batch_with_recovery(entries, source_lang=source_lang)
