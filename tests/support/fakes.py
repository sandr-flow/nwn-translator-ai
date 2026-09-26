"""Test doubles: run configuration, translation providers and the log writer."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union
from unittest.mock import AsyncMock, Mock

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.config import TranslationConfig


def make_config(**overrides: Any) -> TranslationConfig:
    """A run configuration: English to Russian, a test key and model, *overrides* on top."""
    values: Dict[str, Any] = dict(
        api_key="test-key",
        model="test-model",
        source_lang="english",
        target_lang="russian",
        input_file=Path("test.mod"),
    )
    values.update(overrides)
    return TranslationConfig(**values)


class RecordingWriter:
    """Translation log writer that keeps every entry.

    Attributes:
        entries: Every written entry, in order.
        fail: When true, every write raises ``OSError`` instead.
    """

    def __init__(self, fail: bool = False) -> None:
        self.entries: List[Dict[str, Any]] = []
        self.fail = fail

    def write(self, entry: Dict[str, Any]) -> None:
        if self.fail:
            raise OSError("disk full")
        self.entries.append(entry)

    def rows(self) -> List[Dict[str, Any]]:
        """Translation rows, without model request/response or diagnostic events."""
        return [entry for entry in self.entries if not entry.get("event")]

    def events(self, name: str) -> List[Dict[str, Any]]:
        """Entries of the event *name*."""
        return [entry for entry in self.entries if entry.get("event") == name]


def translation_provider(translations: Optional[Mapping[str, str]] = None) -> Mock:
    """A provider mock for the batch translation manager.

    Single and batch requests answer from *translations* (unknown text comes back
    unchanged), and the NCS gate approves every entry with the reason
    ``test_approve``. Each task method is an ``AsyncMock`` a test may replace.
    """
    answers = dict(translations or {})

    async def single(text: str, source_lang: str, target_lang: str, **_: Any) -> TranslationResult:
        return TranslationResult(translated=answers.get(text, text), original=text)

    async def batch(items: Sequence[Any], source_lang: str, target_lang: str, **_: Any) -> list:
        return [
            TranslationResult(
                translated=answers.get(i.original, i.original),
                original=i.original,
                metadata={"batch": True},
            )
            for i in items
        ]

    provider = Mock()
    provider.translate_async = AsyncMock(side_effect=single)
    provider.translate_batch_async = AsyncMock(side_effect=batch)
    provider.classify_ncs_translate_gate_batch_async = AsyncMock(
        side_effect=gate_answering(lambda entry: True, "test_approve")
    )
    provider.close_async_client = AsyncMock(return_value=None)
    return provider


def gate_answering(decide: Callable[[Dict[str, Any]], Any], reason: str = "test") -> Callable:
    """An NCS gate that answers each entry with ``decide(entry)`` and *reason*."""

    async def gate(entries: Sequence[Dict[str, Any]], *, source_lang: str) -> Dict[str, Any]:
        return {str(e["key"]): {"translate": decide(e), "reason": reason} for e in entries}

    return gate


def failing_batch(error: str = "x") -> Callable:
    """A ``translate_batch_async`` whose every result failed with *error*."""

    async def batch(items: Sequence[Any], source_lang: str, target_lang: str, **_: Any) -> list:
        return [
            TranslationResult(translated="", original=i.original, success=False, error=error)
            for i in items
        ]

    return batch


class UnexpectedLineRetry(BaseException):
    """A single-line retry the test did not expect.

    A ``BaseException``, so the manager's handlers, which catch ``Exception``,
    let it through and the test fails.
    """


Reply = Union[str, BaseException]


class DialogProvider:
    """Provider double of the dialog translator.

    JSON chats are answered from *responses*: a list is consumed in order, a
    dict answers with the reply whose marker occurs in the user prompt (which
    stays deterministic under concurrent requests). A reply that is an
    exception is raised. Single-line retries go to *translate_line*; without
    one they raise :class:`UnexpectedLineRetry`.

    Attributes:
        calls: Keyword arguments of every JSON chat request.
        line_calls: ``text``/``context``/``glossary`` of every single-line retry.
    """

    model = "fake/model"

    def __init__(
        self,
        responses: Union[Sequence[Reply], Mapping[str, Reply]],
        translate_line: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._queue = None if isinstance(responses, Mapping) else list(responses)
        self._by_marker = dict(responses) if isinstance(responses, Mapping) else {}
        self._translate_line = translate_line
        self.calls: List[Dict[str, Any]] = []
        self.line_calls: List[Dict[str, Any]] = []

    def make_system_message_content(self, stable: str, variable: str = "") -> str:
        return "\n\n".join(part for part in (stable, variable) if part)

    async def complete_json_chat_async(
        self,
        system_prompt: Any,
        user_prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        use_reasoning: bool = True,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "use_reasoning": use_reasoning,
            }
        )
        if self._queue is None:
            reply = next((r for m, r in self._by_marker.items() if m in user_prompt), None)
            if reply is None:
                raise AssertionError(f"No fake response matches prompt: {user_prompt[:120]!r}")
        elif self._queue:
            reply = self._queue.pop(0)
        else:
            raise AssertionError("No fake responses left for complete_json_chat_async")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        if self._translate_line is None:
            raise UnexpectedLineRetry(text)
        self.line_calls.append({"text": text, "context": context, "glossary": glossary_block})
        return TranslationResult(translated=self._translate_line(text), original=text)

    async def close_async_client(self) -> None:
        return None
