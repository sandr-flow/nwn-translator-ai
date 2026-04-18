"""POLZA.AI provider implementation.

POLZA.AI (https://polza.ai) is an OpenAI-compatible API gateway with the
same chat-completion semantics as OpenRouter, so the implementation is a
thin subclass that swaps the base URL and identifying labels. All retry,
reasoning-fallback, batch-translate, glossary and NCS-gate logic is
inherited unchanged from :class:`OpenRouterProvider`.

See: https://polza.ai/docs
"""

from typing import Dict

from .openrouter_provider import OpenRouterProvider


class PolzaProvider(OpenRouterProvider):
    """AI provider for POLZA.AI (OpenAI-compatible)."""

    BASE_URL = "https://polza.ai/api/v1"
    PROVIDER_LABEL = "POLZA.AI"
    PROVIDER_NAME = "polza"

    def _build_default_headers(self) -> Dict[str, str]:
        return {}
