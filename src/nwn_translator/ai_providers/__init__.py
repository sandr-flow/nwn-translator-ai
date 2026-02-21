"""AI providers for translation.

This package contains implementations for various AI providers that can be used
for translating NWN module content.
"""

from .base import (
    BaseAIProvider,
    ProviderFactory,
    TranslationItem,
    TranslationResult,
    ProviderError,
    RateLimitError,
)
from .grok_provider import GrokProvider
from .openai_provider import OpenAIProvider
from .gemini_provider import GeminiProvider
from .mistral_provider import MistralProvider
from .openrouter_provider import OpenRouterProvider

# Register all providers with the factory
ProviderFactory.register("grok", GrokProvider)
ProviderFactory.register("openai", OpenAIProvider)
ProviderFactory.register("gemini", GeminiProvider)
ProviderFactory.register("mistral", MistralProvider)
ProviderFactory.register("openrouter", OpenRouterProvider)

__all__ = [
    "BaseAIProvider",
    "ProviderFactory",
    "TranslationItem",
    "TranslationResult",
    "ProviderError",
    "RateLimitError",
    "GrokProvider",
    "OpenAIProvider",
    "GeminiProvider",
    "MistralProvider",
    "OpenRouterProvider",
    "create_provider",
]


def create_provider(
    provider_name: str,
    api_key: str,
    model: str = None,
    **kwargs
) -> BaseAIProvider:
    """Create an AI provider instance.

    Args:
        provider_name: Name of the provider (grok, openai, gemini, mistral, openrouter)
        api_key: API key for the provider
        model: Model identifier (optional, uses provider default if None)
        **kwargs: Additional provider-specific parameters

    Returns:
        Instance of the requested provider

    Raises:
        ProviderError: If provider is not found

    Example:
        >>> provider = create_provider("grok", "your-api-key")
        >>> result = provider.translate("Hello", "english", "spanish")
    """
    return ProviderFactory.create(provider_name, api_key, model, **kwargs)
