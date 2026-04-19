"""AI providers for translation (OpenRouter and POLZA.AI).

Provider selection is driven by the API key prefix: ``sk-or-...`` dispatches
to :class:`OpenRouterProvider`, ``key_...`` dispatches to
:class:`PolzaProvider`. Both share the same OpenAI-compatible request
semantics and the same default / popular model lineup.
"""

from typing import Dict, Optional, Type

from .base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
    RateLimitError,
)
from .openrouter_provider import OpenRouterProvider
from .polza_provider import PolzaProvider

#: Order matters: first matching prefix wins.
_PROVIDER_BY_PREFIX: Dict[str, Type[OpenRouterProvider]] = {
    "sk-or-": OpenRouterProvider,
    "pza": PolzaProvider,
}

#: Fallback when the key matches no known prefix.
_DEFAULT_PROVIDER_CLASS: Type[OpenRouterProvider] = OpenRouterProvider


def detect_provider_from_key(api_key: Optional[str]) -> str:
    """Return the canonical provider name inferred from *api_key*.

    Returns ``""`` for empty keys, the short name (``"openrouter"`` /
    ``"polza"``) for matching prefixes, and the default provider's name
    otherwise.
    """
    if not api_key:
        return ""
    key = api_key.strip()
    if not key:
        return ""
    for prefix, cls in _PROVIDER_BY_PREFIX.items():
        if key.startswith(prefix):
            return cls.PROVIDER_NAME
    return _DEFAULT_PROVIDER_CLASS.PROVIDER_NAME


def _provider_class_for_key(api_key: str) -> Type[OpenRouterProvider]:
    """Pick the provider class that matches *api_key*'s prefix."""
    key = (api_key or "").strip()
    for prefix, cls in _PROVIDER_BY_PREFIX.items():
        if key.startswith(prefix):
            return cls
    return _DEFAULT_PROVIDER_CLASS


def create_provider(
    api_key: str,
    model: Optional[str] = None,
    **kwargs,
) -> OpenRouterProvider:
    """Create an AI provider instance, auto-selected from the API key prefix.

    Args:
        api_key: OpenRouter (``sk-or-…``) or POLZA.AI (``key_…``) API key.
        model: Model slug (optional; uses the provider's default if ``None``).
        **kwargs: Passed through to the provider (``site_url``,
            ``site_name``, ``reasoning_effort``, ``player_gender``, …).

    Returns:
        Configured provider instance (``OpenRouterProvider`` or
        ``PolzaProvider``).
    """
    cls = _provider_class_for_key(api_key)
    return cls(api_key=api_key, model=model, **kwargs)


__all__ = [
    "BaseAIProvider",
    "TranslationItem",
    "TranslationResult",
    "ProviderError",
    "RateLimitError",
    "OpenRouterProvider",
    "PolzaProvider",
    "create_provider",
    "detect_provider_from_key",
]
