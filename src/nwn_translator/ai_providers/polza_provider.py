"""POLZA.AI provider.

POLZA.AI (https://polza.ai) is an OpenAI-compatible gateway with the same
chat-completion semantics as OpenRouter; only the base URL, the labels and the
extra headers differ. See https://polza.ai/docs
"""

from typing import Dict

from .openrouter_provider import OpenRouterProvider


class PolzaProvider(OpenRouterProvider):
    """Translation provider for POLZA.AI."""

    BASE_URL = "https://polza.ai/api/v1"
    HEADERS: Dict[str, str] = {}
    PROVIDER_LABEL = "POLZA.AI"
    PROVIDER_NAME = "polza"
