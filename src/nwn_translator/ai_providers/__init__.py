"""AI providers for translation (OpenRouter only)."""

from typing import Optional

from .base import (
    BaseAIProvider,
    TranslationItem,
    TranslationResult,
    ProviderError,
    RateLimitError,
)
from .openrouter_provider import OpenRouterProvider


def create_provider(
    api_key: str,
    model: Optional[str] = None,
    **kwargs,
) -> OpenRouterProvider:
    """Create the OpenRouter AI provider instance.

    Args:
        api_key: OpenRouter API key.
        model: Model slug (optional; uses OpenRouter default if None).
        **kwargs: Passed to ``OpenRouterProvider`` (e.g. ``site_url``, ``site_name``).

    Returns:
        Configured ``OpenRouterProvider`` instance.
    """
    return OpenRouterProvider(api_key=api_key, model=model, **kwargs)


__all__ = [
    "BaseAIProvider",
    "TranslationItem",
    "TranslationResult",
    "ProviderError",
    "RateLimitError",
    "OpenRouterProvider",
    "create_provider",
]
