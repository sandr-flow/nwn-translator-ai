"""OpenRouter provider: the OpenAI-compatible chat transport and the model tasks.

OpenRouter is an OpenAI-compatible gateway to models from Anthropic, Google,
Meta, DeepSeek, OpenAI and others. See https://openrouter.ai/docs
"""

import asyncio
import functools
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, cast

from openai import APITimeoutError, AsyncOpenAI, BadRequestError, Timeout

from ..config import (
    DEFAULT_MODEL,
    NCS_GATE_TEMPERATURE,
    PROMPT_CACHE_BREAKPOINTS_ENABLED,
    TRANSLATION_MAX_TOKENS,
    TRANSLATION_TEMPERATURE,
    parse_reasoning_effort,
)
from ..json_utils import load_first_json_object
from ..prompts._builder import (
    CONTENT_PROFILE_DEFAULT,
    build_batch_user_prompt,
    build_single_user_prompt,
    build_translation_system_prompt_parts,
)
from ..prompts.ncs_gate import NCS_GATE_SYSTEM_PROMPT
from ..race_dictionary import match_race_terms
from ..telemetry import (
    LLMRequestMetric,
    RunMetricsRecorder,
    current_llm_phase,
    estimate_tokens,
    split_system_prompt_chars,
    usage_tokens,
)
from .base import ProviderError, SystemContent, TranslationItem, TranslationResult
from .batch_payload import serialize_batch_payload
from .errors import TRANSIENT_ERRORS, TRANSIENT_RETRY, is_reasoning_rejection, map_api_error
from .ncs_gate import Verdict, classify_with_recovery
from .openrouter_models import FALLBACK, resolve_reasoning_effort

logger = logging.getLogger(__name__)

#: HTTP timeouts in seconds; generation may take minutes, connecting may not. The
#: client's own ``Timeout`` type is used because the SDK may pin another httpx.
_HTTP_TIMEOUT = Timeout(connect=10, read=180, write=10, pool=10)

#: Requests per single-string translation: an unparseable reply is asked once more.
_SINGLE_JSON_ATTEMPTS = 2


def parse_single_translation(raw: str) -> str:
    """Extracts the ``translation`` value of a single-string reply.

    Args:
        raw: Model reply; text around the first JSON object is ignored.

    Returns:
        The translation, or ``""`` when the reply has no JSON object, does not
        decode, or lacks a non-empty string ``translation``.
    """
    try:
        translated = load_first_json_object(raw).get("translation", "")
    except json.JSONDecodeError:
        logger.warning(
            "No valid JSON object in model response. Raw (first 200 chars): %s", raw[:200]
        )
        return ""
    if not isinstance(translated, str) or not translated:
        logger.warning("JSON parsed but 'translation' key missing or empty")
        return ""
    return translated


def parse_batch_results(
    raw: str, items: List[TranslationItem], model: str
) -> List[TranslationResult]:
    """Turns a batch reply into one result per item, addressed by position.

    A ``{"translation": {...}}`` wrapper around the ID map is unwrapped; positions
    are never inferred from lists, group ids or a combined string.

    Args:
        raw: Model reply.
        items: The requested items, in payload order.
        model: Model slug recorded in the result metadata.

    Returns:
        One result per item. Every item fails with ``Batch JSON parse error: ...``
        when the reply does not decode, and individually when its key is missing
        or empty.
    """
    try:
        parsed = load_first_json_object(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Batch JSON parse failed: %s", exc)
        return [
            TranslationResult(
                translated="",
                original=item.original,
                success=False,
                error=f"Batch JSON parse error: {exc}",
            )
            for item in items
        ]
    wrapped = parsed.get("translation")
    if set(parsed) == {"translation"} and isinstance(wrapped, dict):
        parsed = wrapped
    results = []
    for i, item in enumerate(items):
        translated = parsed.get(str(i), "")
        ok = isinstance(translated, str) and bool(translated)
        results.append(
            TranslationResult(
                translated=translated if ok else "",
                original=item.original,
                success=ok,
                error=None if ok else "Missing or empty translation in batch response",
                metadata={"model": model, "batch": True},
            )
        )
    return results


class OpenRouterProvider:
    """Translation provider for OpenRouter and other OpenAI-compatible gateways.

    Every request goes through :meth:`_complete_once`: JSON response format,
    catalog-clamped reasoning effort, error mapping and one request metric per
    attempt. Every task except the glossary request sends it through
    :meth:`_complete`, which retries transient errors. Any slug listed on
    https://openrouter.ai/models can be used as the model.

    Attributes:
        BASE_URL: API base URL; subclasses target another gateway.
        HEADERS: Extra headers sent with every request.
        PROVIDER_LABEL: Human-readable name used in error messages.
        PROVIDER_NAME: Short id returned by :meth:`get_provider_name`.
        DEFAULT_MODEL: Model used when none is given.
        POPULAR_MODELS: Curated model shortlist for the web UI.
        api_key: API key.
        model: Model slug sent with every request.
        player_gender: Grammatical gender of the player in translation prompts.
        metrics_recorder: Receives one metric per request attempt, if set.
    """

    BASE_URL = "https://openrouter.ai/api/v1"
    #: OpenRouter attributes traffic (and rate-limit tiers) to the calling app.
    HEADERS: Dict[str, str] = {
        "HTTP-Referer": "https://github.com/nwn-modules-translator",
        "X-Title": "NWN Modules Translator",
    }
    PROVIDER_LABEL = "OpenRouter"
    PROVIDER_NAME = "openrouter"
    DEFAULT_MODEL = DEFAULT_MODEL
    POPULAR_MODELS = list(FALLBACK)

    #: Distinguishes "no client cached yet" from a client cached for loop ``None``.
    _NO_LOOP_CACHED = object()

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        *,
        player_gender: str = "male",
        reasoning_effort: Optional[str] = None,
        metrics_recorder: Optional[RunMetricsRecorder] = None,
    ) -> None:
        """Creates a provider; no network access happens here.

        Args:
            api_key: Gateway API key.
            model: Model slug; :attr:`DEFAULT_MODEL` when ``None`` or empty.
            player_gender: ``"male"`` or ``"female"``.
            reasoning_effort: Requested ``reasoning.effort`` (see
                :func:`~nwn_translator.config.parse_reasoning_effort`).
            metrics_recorder: Receives one metric per request attempt.

        Raises:
            ProviderError: If *api_key* is blank.
            ValueError: If *reasoning_effort* is not a known effort.
        """
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.player_gender = player_gender
        self.metrics_recorder = metrics_recorder
        if not api_key or not api_key.strip():
            raise ProviderError(f"{self.PROVIDER_NAME}: API key is required")
        self._reasoning_effort = parse_reasoning_effort(reasoning_effort)
        #: Set by the first "reasoning not supported" 400 so the rest of the session
        #: skips the doomed reasoning request instead of repeating it for every call.
        self._reasoning_unsupported = False
        self._thread_local = threading.local()

    def __repr__(self) -> str:
        return f"{self.get_provider_name()}(model={self.model})"

    def get_provider_name(self) -> str:
        """Returns the short provider id recorded in metrics.

        Returns:
            :attr:`PROVIDER_NAME`.
        """
        return self.PROVIDER_NAME

    @property
    def async_client(self) -> AsyncOpenAI:
        """The ``AsyncOpenAI`` client bound to the current thread's running loop.

        The cache key is the loop object itself (a strong reference is kept):
        comparing ``id(loop)`` values would false-hit when a garbage-collected
        loop's address is reused by a new one. With the persistent loop of
        ``run_async`` the same client serves every call of a run.
        """
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if getattr(self._thread_local, "client_loop", self._NO_LOOP_CACHED) is not loop:
            self._thread_local.client_loop = loop
            self._thread_local.async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.BASE_URL,
                default_headers=dict(self.HEADERS),
                timeout=_HTTP_TIMEOUT,
                max_retries=0,
            )
        return cast(AsyncOpenAI, self._thread_local.async_client)

    async def close_async_client(self) -> None:
        """Closes this thread's client; called before the event loop shuts down.

        A failing close is logged at debug level and otherwise ignored: the run's
        results do not depend on it.
        """
        client = getattr(self._thread_local, "async_client", None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("Closing the %s client failed", self.PROVIDER_LABEL, exc_info=True)
            self._thread_local.async_client = None
            self._thread_local.client_loop = self._NO_LOOP_CACHED

    @staticmethod
    def make_system_message_content(stable: str, variable: str = "") -> SystemContent:
        """Builds ``messages[0].content`` from a cacheable and a per-call prompt half.

        Without a variable half the content is plain text (maximally compatible
        with OpenAI-compatible gateways). Otherwise the stable half carries a
        ``cache_control: ephemeral`` breakpoint: honoured by Anthropic, Gemini and
        Grok through OpenRouter, ignored by providers that cache prefixes on their
        own. :data:`~nwn_translator.config.PROMPT_CACHE_BREAKPOINTS_ENABLED` off
        joins both halves into one string instead.

        Args:
            stable: Prompt text that is byte-identical across the calls of a run.
            variable: Prompt text that may change between calls.

        Returns:
            A string, or two text parts of which the first is cacheable.
        """
        variable = variable.strip() if variable else ""
        if not variable:
            return stable
        if not PROMPT_CACHE_BREAKPOINTS_ENABLED:
            return f"{stable}\n\n{variable}"
        return [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": variable},
        ]

    async def _create(self, kwargs: Dict[str, Any], *, use_reasoning: bool) -> Any:
        """Sends one ``chat.completions.create`` with the catalog-clamped effort.

        A model that rejects the reasoning field gets the request once more without
        it, and later requests of this provider omit it.
        """
        effort = None
        if not self._reasoning_unsupported:
            requested = self._reasoning_effort if use_reasoning else "none"
            effort = resolve_reasoning_effort(self.model, requested)
        call_kwargs = (
            {**kwargs, "reasoning_effort": effort, "extra_body": {"reasoning": {"effort": effort}}}
            if effort
            else kwargs
        )
        try:
            return await self.async_client.chat.completions.create(**call_kwargs)
        except BadRequestError as error:
            if not is_reasoning_rejection(error):
                raise
            logger.warning(
                "%s rejected the reasoning field (HTTP 400); disabling reasoning for this session",
                self.PROVIDER_LABEL,
            )
            self._reasoning_unsupported = True
            return await self.async_client.chat.completions.create(**kwargs)

    async def _complete_once(
        self,
        system: SystemContent,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        phase: str,
        batch_size: int = 1,
        glossary_chars: int = 0,
        use_reasoning: bool = True,
        stream: Optional[bool] = False,
    ) -> str:
        """Sends one JSON-mode chat request and returns the stripped reply text.

        Every attempt, failed or not, is recorded as one request metric.
        :meth:`_complete` is the same request retried on transient errors.

        Args:
            system: System message content.
            user: User message.
            max_tokens: Completion token budget (hidden reasoning included).
            temperature: Sampling temperature.
            phase: Metric phase label.
            batch_size: Items answered by this request (metrics).
            glossary_chars: Glossary characters in the system prompt (metrics).
            use_reasoning: ``False`` requests the lowest effort the model allows.
            stream: Value of the ``stream`` field; ``None`` omits it.

        Returns:
            The reply text, stripped.

        Raises:
            RateLimitError: HTTP 429/402 or budget exhaustion.
            OpenRouterError: Any other non-transient API error.
            APIConnectionError: Connection failure or timeout (transient).
            InternalServerError: HTTP >= 500 (transient).
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if stream is not None:
            kwargs["stream"] = stream
        record = functools.partial(
            self._record_metric,
            phase=phase,
            system=system,
            user=user,
            batch_size=batch_size,
            glossary_chars=glossary_chars,
            started=time.monotonic(),
        )
        try:
            response = await self._create(kwargs, use_reasoning=use_reasoning)
        except Exception as exc:
            record(error=exc)
            if isinstance(exc, TRANSIENT_ERRORS):
                raise
            raise map_api_error(exc, self.PROVIDER_LABEL) from exc
        try:
            reply = (response.choices[0].message.content or "").strip()
        except (AttributeError, IndexError, TypeError) as exc:  # a reply without choices
            record(response=response, error=exc)
            raise map_api_error(exc, self.PROVIDER_LABEL) from exc
        record(response=response, reply=reply)
        return reply

    #: :meth:`_complete_once` retried on transient errors. The retry repeats only the
    #: failed request, never the requests a task already completed (JSON attempts,
    #: NCS gate halves).
    _complete = TRANSIENT_RETRY(_complete_once)

    def _record_metric(
        self,
        *,
        phase: str,
        system: SystemContent,
        user: str,
        batch_size: int,
        glossary_chars: int,
        started: float,
        response: Any = None,
        reply: str = "",
        error: Optional[BaseException] = None,
    ) -> None:
        """Records one request attempt when a metrics recorder is configured."""
        recorder = self.metrics_recorder
        if recorder is None:
            return
        stable_chars, variable_chars = split_system_prompt_chars(system)
        user_chars = len(user or "")
        prompt_chars = stable_chars + variable_chars + user_chars
        usage_in, usage_out = usage_tokens(response)
        recorder.record(
            LLMRequestMetric(
                request_id=recorder.next_request_id(),
                phase=phase,
                provider=self.get_provider_name(),
                model=self.model,
                batch_size=batch_size,
                stable_chars=stable_chars,
                variable_chars=variable_chars,
                user_chars=user_chars,
                glossary_chars=glossary_chars,
                prompt_chars=prompt_chars,
                estimated_input_tokens=(
                    usage_in if usage_in is not None else estimate_tokens(prompt_chars)
                ),
                estimated_output_tokens=(
                    usage_out if usage_out is not None else estimate_tokens(len(reply))
                ),
                usage_input_tokens=usage_in,
                usage_output_tokens=usage_out,
                latency_ms=int((time.monotonic() - started) * 1000),
                timeout=isinstance(error, APITimeoutError),
                success=error is None,
                error=str(error) if error is not None else None,
            )
        )

    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
        *,
        json_attempts: int = _SINGLE_JSON_ATTEMPTS,
    ) -> TranslationResult:
        """Translates one string.

        Race terms found in *text* are added to the prompt when no glossary block
        is given. An unparseable reply is requested again, up to *json_attempts*
        requests in total.

        Args:
            text: Text to translate; blank text returns an empty success.
            source_lang: Source language name.
            target_lang: Target language name.
            context: Context hint for the model.
            glossary_block: GLOSSARY section for the variable prompt half.
            content_profile: Prompt profile (``default``, ``short_label``,
                ``script_message``).
            json_attempts: Requests to send until a reply parses. The web
                connection check sends one, so a bad key costs one request.

        Returns:
            The translation, or a failed result when no reply parses.

        Raises:
            RateLimitError: Rate limit or budget exhausted after retries.
            OpenRouterError: Non-transient API error.
            APIConnectionError: Connection failure or timeout, after retries.
            InternalServerError: HTTP >= 500, after retries.
        """
        if not text or not text.strip():
            return TranslationResult(translated="", original=text, success=True)
        glossary = glossary_block or match_race_terms(text, target_lang)
        stable, variable = build_translation_system_prompt_parts(
            target_lang,
            self.player_gender,
            glossary,
            content_profile=content_profile or CONTENT_PROFILE_DEFAULT,
        )
        system = self.make_system_message_content(stable, variable)
        user = build_single_user_prompt(text, source_lang, context)
        for attempt in range(json_attempts):
            raw = await self._complete(
                system,
                user,
                max_tokens=TRANSLATION_MAX_TOKENS,
                temperature=TRANSLATION_TEMPERATURE,
                phase=current_llm_phase("generic_single"),
                glossary_chars=len(glossary),
                stream=None,
            )
            translated = parse_single_translation(raw)
            if translated:
                return TranslationResult(
                    translated=translated,
                    original=text,
                    success=True,
                    metadata={"model": self.model},
                )
            if attempt + 1 < json_attempts:
                logger.warning(
                    "Unparseable or empty JSON from model, retrying once. "
                    "Raw (first 200 chars): %s",
                    raw[:200],
                )
        return TranslationResult(
            translated="",
            original=text,
            success=False,
            error="Model returned empty or unparseable JSON",
            metadata={"model": self.model},
        )

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
            items: Items to translate; see
                :func:`~nwn_translator.ai_providers.batch_payload.build_batch_payload`.
            source_lang: Source language name.
            target_lang: Target language name.
            glossary_block: GLOSSARY section; race terms of the items are used when
                it is empty.
            content_profile: Prompt profile; it must depend only on the batch's
                content-type mix so the stable prompt prefix stays cacheable.

        Returns:
            One result per item, in order (see :func:`parse_batch_results`).

        Raises:
            RateLimitError: Rate limit or budget exhausted after retries.
            OpenRouterError: Non-transient API error.
            APIConnectionError: Connection failure or timeout, after retries.
            InternalServerError: HTTP >= 500, after retries.
        """
        if not items:
            return []
        glossary = glossary_block or match_race_terms(
            " ".join(item.original for item in items if item.original), target_lang
        )
        stable, variable = build_translation_system_prompt_parts(
            target_lang,
            self.player_gender,
            glossary,
            content_profile=content_profile or CONTENT_PROFILE_DEFAULT,
            batch_mode=True,
        )
        raw = await self._complete(
            self.make_system_message_content(stable, variable),
            build_batch_user_prompt(source_lang, serialize_batch_payload(items)),
            max_tokens=TRANSLATION_MAX_TOKENS,
            temperature=TRANSLATION_TEMPERATURE,
            phase=current_llm_phase("generic_batch"),
            batch_size=len(items),
            glossary_chars=len(glossary),
        )
        return parse_batch_results(raw, items, self.model)

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
            system_prompt: System content, plain or from
                :meth:`make_system_message_content`.
            user_prompt: User message.
            max_tokens: Completion token budget.
            temperature: Sampling temperature.
            use_reasoning: ``False`` requests the lowest effort the model allows.

        Returns:
            The stripped reply text.

        Raises:
            RateLimitError: Rate limit or budget exhausted after retries.
            OpenRouterError: Non-transient API error.
            APIConnectionError: Connection failure or timeout, after retries.
            InternalServerError: HTTP >= 500, after retries.
        """
        return await self._complete(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            phase=current_llm_phase("generic_batch"),
            use_reasoning=use_reasoning,
        )

    async def complete_glossary_chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        glossary_keys: List[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Sends one glossary request, without retries, at the lowest effort the model allows.

        ``json_object`` mode is used rather than a strict ``json_schema``: OpenRouter's
        constrained decoding hangs on models without native support (DeepSeek, Qwen).
        The glossary builder retries and merges partial results itself.

        Args:
            system_prompt: Glossary system prompt.
            user_prompt: Names to translate.
            glossary_keys: Requested names (metrics batch size).
            max_tokens: Completion token budget.
            temperature: Sampling temperature.

        Returns:
            The stripped reply text.

        Raises:
            RateLimitError: Rate limit or budget exhausted.
            OpenRouterError: Non-transient API error.
            APIConnectionError: Connection failure or timeout.
            InternalServerError: HTTP >= 500.
        """
        return await self._complete_once(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            phase=current_llm_phase("glossary"),
            batch_size=len(glossary_keys),
            use_reasoning=False,
        )

    async def classify_ncs_translate_gate_batch_async(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, Verdict]:
        """Decides for each NCS string occurrence whether it is player-facing text.

        Args:
            entries: Candidates with unique ``key`` values (see
                :func:`~nwn_translator.ai_providers.ncs_gate.gate_user_prompt`).
            source_lang: Source language label.

        Returns:
            ``key -> {"translate": bool, "reason": str}`` for every entry (see
            :func:`~nwn_translator.ai_providers.ncs_gate.classify_with_recovery`).

        Raises:
            RateLimitError: Rate limit or budget exhausted after retries.
            OpenRouterError: Non-transient API error.
            APIConnectionError: Connection failure or timeout, after retries.
            InternalServerError: HTTP >= 500, after retries.
        """

        async def request(user_prompt: str, max_tokens: int, batch_size: int) -> str:
            return await self._complete(
                NCS_GATE_SYSTEM_PROMPT,
                user_prompt,
                max_tokens=max_tokens,
                temperature=NCS_GATE_TEMPERATURE,
                phase="ncs_gate",
                batch_size=batch_size,
            )

        return await classify_with_recovery(request, entries, source_lang)
