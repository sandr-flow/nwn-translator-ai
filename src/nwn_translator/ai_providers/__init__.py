"""AI providers for translation (OpenRouter and POLZA.AI).

The API key prefix selects the provider: ``sk-or-...`` goes to
:class:`OpenRouterProvider`, ``pza...`` to :class:`PolzaProvider`, anything else
to OpenRouter. Both share the same OpenAI-compatible request semantics and the
same default and popular models.
"""

from typing import Any, Dict, Optional, Type

from ..config import TranslationConfig
from ..telemetry import RunMetricsRecorder
from .base import TranslationItem, TranslationProvider, TranslationResult
from .openrouter_provider import OpenRouterProvider
from .polza_provider import PolzaProvider

#: Provider class by API-key prefix; the first matching prefix wins.
_PROVIDER_BY_PREFIX: Dict[str, Type[OpenRouterProvider]] = {
    "sk-or-": OpenRouterProvider,
    "pza": PolzaProvider,
}


def _provider_class_for_key(api_key: Optional[str]) -> Type[OpenRouterProvider]:
    """Picks the provider class for *api_key*; OpenRouter when no prefix matches."""
    key = (api_key or "").strip()
    matches = (cls for prefix, cls in _PROVIDER_BY_PREFIX.items() if key.startswith(prefix))
    return next(matches, OpenRouterProvider)


def detect_provider_from_key(api_key: Optional[str]) -> str:
    """Returns ``"openrouter"`` or ``"polza"`` for an API key, ``""`` for a blank one."""
    return _provider_class_for_key(api_key).PROVIDER_NAME if (api_key or "").strip() else ""


def provider_label(name: str) -> str:
    """Returns ``"OpenRouter"`` or ``"POLZA.AI"`` for a provider name, ``""`` if unknown."""
    labels = (
        cls.PROVIDER_LABEL for cls in _PROVIDER_BY_PREFIX.values() if cls.PROVIDER_NAME == name
    )
    return next(labels, "")


def create_provider(api_key: str, model: Optional[str] = None, **kwargs: Any) -> OpenRouterProvider:
    """Creates the provider that matches the API key prefix.

    Args:
        api_key: OpenRouter (``sk-or-...``) or POLZA.AI (``pza...``) API key.
        model: Model slug; the provider's default when ``None``.
        **kwargs: Keyword arguments of :class:`OpenRouterProvider`.
    """
    return _provider_class_for_key(api_key)(api_key, model, **kwargs)


def create_provider_for_config(
    config: TranslationConfig, metrics_recorder: Optional[RunMetricsRecorder] = None
) -> OpenRouterProvider:
    """Creates the provider for the API key, model and prompt settings of a run.

    Args:
        config: Run settings (API key, model, player gender, reasoning effort).
        metrics_recorder: Receives one metric per request attempt, if set.
    """
    return create_provider(
        config.api_key,
        config.model,
        player_gender=config.player_gender,
        reasoning_effort=config.reasoning_effort,
        metrics_recorder=metrics_recorder,
    )
