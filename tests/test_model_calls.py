"""Model requests: batch halving, short answers and pass budgets."""

import asyncio
from unittest.mock import AsyncMock, Mock

from nwn_translator.ai_providers.base import TranslationResult
from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.translation_logging import NullTranslationLogWriter
from nwn_translator.translators import model_calls
from nwn_translator.translators.model_calls import CallLimits, ModelCaller, queued_timeout
from nwn_translator.translators.ncs_diagnostics import NcsDiagnostics, new_ncs_diagnostics
from nwn_translator.translators.token_handler import sanitize_text
from nwn_translator.translators.work_plan import WorkItem


def _caller(provider, limits: CallLimits = CallLimits()) -> ModelCaller:
    writer = NullTranslationLogWriter()
    return ModelCaller(
        TranslationConfig(api_key="k", max_concurrent_requests=1),
        provider,
        writer,
        NcsDiagnostics(new_ncs_diagnostics(), writer),
        lambda texts: None,
        limits,
    )


def _work(text: str) -> WorkItem:
    return WorkItem(TranslatableItem(text, None, text, "a.uti"), *sanitize_text(text))


def _run(coro):
    return asyncio.run(coro)


def test_queued_timeout_counts_waves_and_slack():
    assert queued_timeout(0, 120.0, 4) == 0.0
    assert queued_timeout(5, 120.0, 2) == 3 * 120.0 + 60.0
    assert queued_timeout(1, 1.0, 1) == 1.0 + 5.0


def test_failed_batch_is_halved_left_first_until_single_leaves():
    sizes = []

    async def batch(items, **kwargs):
        sizes.append(len(items))
        return [
            TranslationResult(
                translated="" if len(items) > 1 or i.original == "bad" else "ok",
                original=i.original,
                success=len(items) == 1 and i.original != "bad",
                error="failed",
            )
            for i in items
        ]

    provider = Mock()
    provider.translate_batch_async = AsyncMock(side_effect=batch)
    work = [_work(text) for text in ("a", "b", "bad", "c", "d")]
    done = []

    async def run():
        return await _caller(provider).translate_batch(asyncio.Semaphore(1), work, done.append)

    results = _run(run())

    assert sizes == [5, 2, 1, 1, 3, 1, 2, 1, 1]
    assert [r.success for r in results] == [True, True, False, True, True]
    assert done == work


def test_short_batch_answer_is_padded_with_failures():
    async def batch(items, **kwargs):
        return [TranslationResult(translated="ok", original=items[0].original)]

    provider = Mock()
    provider.translate_batch_async = AsyncMock(side_effect=batch)
    work = [_work("a"), _work("b")]

    results = _run(_caller(provider).translate_batch(asyncio.Semaphore(1), work))

    assert results[0].success
    assert (results[1].success, results[1].error) == (
        False,
        "Missing translation result in batch response",
    )
    provider.translate_batch_async.assert_called_once()


def test_batch_errors_become_failed_results():
    provider = Mock()
    provider.translate_batch_async = AsyncMock(side_effect=RuntimeError("down"))
    work = [_work("a")]

    results = _run(_caller(provider).translate_batch(asyncio.Semaphore(1), work))

    assert (results[0].success, results[0].error, results[0].original) == (False, "down", "a")


def test_pass_budget_covers_a_timeout_retry_in_every_slot(monkeypatch):
    budgets = []

    def fake_run_async(coro, *, timeout):
        coro.close()
        budgets.append(timeout)
        return [], []

    monkeypatch.setattr(model_calls, "run_async", fake_run_async)
    caller = _caller(Mock(), CallLimits(item_timeout=100.0, min_pass_timeout=0.0))
    work = [_work(text) for text in ("a", "b", "c")]

    caller.run_main_pass(work, [], None)
    caller.run_fallback_pass(work, scripts=False)
    caller.run_fallback_pass(work, scripts=True)

    # One slot, three items: each may hold it for a request and its timeout retry.
    assert budgets[0] >= 3 * 2 * 100.0
    assert budgets[1] >= 3 * 2 * 100.0
    # Script fallback requests are not retried in their slot.
    assert 3 * 100.0 <= budgets[2] < 3 * 2 * 100.0


def test_pass_budgets_add_their_pads_and_keep_their_floors(monkeypatch):
    budgets = []

    def fake_run_async(coro, *, timeout):
        coro.close()
        budgets.append(timeout)
        return [], []

    monkeypatch.setattr(model_calls, "run_async", fake_run_async)
    work = [_work(text) for text in ("a", "b", "c")]
    padded = CallLimits(
        item_timeout=100.0, min_pass_timeout=0.0, main_pass_pad=1.0, fallback_pass_pad=2.0
    )
    floored = CallLimits(item_timeout=1.0, min_pass_timeout=1000.0)

    for limits in (padded, floored):
        caller = _caller(Mock(), limits)
        caller.run_main_pass(work, [], None)
        caller.run_fallback_pass(work, scripts=False)
        caller.run_fallback_pass(work, scripts=True)

    # Three queued slots of 200 s (a request and its timeout retry) plus 60 s slack.
    assert budgets[:3] == [661.0, 662.0, 3 * 100.0 + 50.0 + 2.0]
    assert budgets[3:] == [1000.0, 500.0, 500.0]
