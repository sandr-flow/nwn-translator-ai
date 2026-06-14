"""Deterministic mock translation provider for the V2.4 round-trip test.

It prepends a fixed marker to every non-empty string and otherwise echoes the
input. Token placeholders inserted by ``token_handler`` survive untouched (the
marker is added in front), so restore/validation behave exactly as in a real
run — only the network call is replaced.
"""

from __future__ import annotations

from typing import Optional

from nwn_translator.ai_providers.base import BaseAIProvider, TranslationResult

#: Marker injected in front of every translated string; ASCII so it encodes in
#: every supported module code page.
MARKER = "[MT]"


class MockTranslateProvider(BaseAIProvider):
    """Marks strings deterministically without any API call."""

    def __init__(self) -> None:
        super().__init__(api_key="mock-key", model="mock/echo")

    def get_default_model(self) -> str:
        return "mock/echo"

    def get_provider_name(self) -> str:
        return "mock"

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        translated = MARKER + text if text else text
        return TranslationResult(translated=translated, original=text, success=True)
