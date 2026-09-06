"""Deterministic mock translation provider for the V2.4 round-trip test.

It prepends a fixed marker to every non-empty string and otherwise echoes the
input. Token placeholders inserted by ``token_handler`` survive untouched (the
marker is added in front), so restore/validation behave exactly as in a real
run — only the network call is replaced.
"""

from __future__ import annotations

from typing import Optional

from nwn_translator.ai_providers.base import BaseAIProvider, TranslationResult
from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider

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


class MockContextProvider(OpenRouterProvider):
    """Exercise the context pipeline with deterministic in-memory model responses."""

    def __init__(self):
        BaseAIProvider.__init__(self, api_key="mock-key", model="mock/context")
        self.requests = []

    def translate(
        self,
        text,
        source_lang,
        target_lang,
        context=None,
        glossary_block=None,
        content_profile=None,
    ):
        self.requests.append({"text": text, "context": context, "glossary": glossary_block})
        # Different answers expose cross-context result reuse in the real archive.
        if text == "Commoner":
            text_out = "female title" if "Female" in (context or "") else "male title"
        elif text == "Jade" and "Female" in (context or ""):
            text_out = "female Jade"
        elif text in {"Shadow", "Shadow Lord"}:
            text_out = {"Shadow": "shade", "Shadow Lord": "lord"}[text]
        else:
            text_out = text
        return TranslationResult(original=text, translated=MARKER + text_out)

    translate_async = BaseAIProvider.translate_async
    translate_batch_async = BaseAIProvider.translate_batch_async
    close_async_client = BaseAIProvider.close_async_client

    async def classify_ncs_translate_gate_batch_async(self, entries, **kwargs):
        return {
            entry["key"]: {
                "translate": True,
                "reason": "mock approval",
            }
            for entry in entries
        }

    async def complete_glossary_chat_async(
        self, system_prompt, user_prompt, *, glossary_keys, **kwargs
    ):
        import json

        return json.dumps({name: name for name in glossary_keys})

    async def complete_json_chat_async(self, system_prompt, user_prompt, **kwargs):
        import json
        import re

        def nodes(script):
            return {
                key: MARKER + text
                for key, text in re.findall(
                    r"^\[([ER]\d+)\] \[[^\n]*\]:\n<<<(.*?)>>>\s*(?:\n|$)",
                    script,
                    re.MULTILINE | re.DOTALL,
                )
            }

        files = re.split(r"^=== FILE: ([^\n]+) ===\n", user_prompt, flags=re.MULTILINE)
        if len(files) > 1:
            return json.dumps({files[i]: nodes(files[i + 1]) for i in range(1, len(files), 2)})
        # Discovery/curation may return no additions. Real extraction and glossary building run.
        return json.dumps(nodes(user_prompt))
