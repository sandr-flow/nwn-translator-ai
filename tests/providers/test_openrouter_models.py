"""Reasoning metadata of the OpenRouter model catalog."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from nwn_translator.ai_providers.openrouter_models import (
    FALLBACK,
    ModelReasoning,
    allowed_efforts,
    get_known_reasoning,
    is_valid_model_slug,
    lookup_model_reasoning,
    refresh_catalog,
    reset_catalog_cache,
    resolve_reasoning_effort,
)

FLASH = "google/gemini-3.8-flash"


@pytest.fixture(autouse=True)
def _clear_catalog():
    reset_catalog_cache()
    yield
    reset_catalog_cache()


def _catalog_client(*, payload=None, error=None):
    """Patch ``httpx.Client`` with a context manager whose ``get`` answers or raises."""
    client = MagicMock()
    if error is not None:
        client.get.side_effect = error
    else:
        client.get.return_value.json.return_value = payload
        client.get.return_value.raise_for_status.return_value = None
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client, patch(
        "nwn_translator.ai_providers.openrouter_models.httpx.Client", return_value=client
    )


def test_slug_validation():
    assert is_valid_model_slug(FLASH)
    assert is_valid_model_slug("meta-llama/llama-3.3-70b-instruct:free")
    for slug in ("noslash", "../etc/passwd", "a/b with spaces"):
        assert not is_valid_model_slug(slug)


def test_allowed_efforts_of_the_fallback_models():
    flash = FALLBACK[FLASH]
    assert flash.mandatory is True
    assert allowed_efforts(flash) == ["low", "medium", "high"]  # no "none"
    assert allowed_efforts(FALLBACK["openai/gpt-5.6-luna"])[0] == "none"
    unrestricted = ModelReasoning(supported=True, mandatory=True, supported_efforts=None)
    assert "none" not in allowed_efforts(unrestricted)
    assert allowed_efforts(unrestricted)[0] == "minimal"


@pytest.mark.parametrize(
    "slug, requested, resolved",
    [
        (FLASH, "none", "low"),
        (FLASH, None, "low"),
        (FLASH, "medium", "medium"),
        ("google/gemini-3.1-flash-lite", "none", "minimal"),
        # Unknown slugs pass through unclamped.
        ("vendor/custom-model", "none", "none"),
        ("vendor/custom-model", "high", "high"),
    ],
)
def test_requested_effort_is_clamped_to_the_model(slug, requested, resolved):
    assert resolve_reasoning_effort(slug, requested) == resolved


def test_known_reasoning_uses_the_fallback_before_a_live_fetch():
    assert get_known_reasoning(FLASH) is FALLBACK[FLASH]
    assert get_known_reasoning("vendor/unknown") is None


def test_refresh_parses_the_live_catalog():
    payload = {
        "data": [
            {
                "id": FLASH,
                "reasoning": {
                    "mandatory": True,
                    "default_effort": "medium",
                    "supported_efforts": ["high", "medium", "low"],
                },
            },
            {"id": "tencent/hy-mt2-1.8b"},
        ]
    }
    _client, patched = _catalog_client(payload=payload)
    with patched:
        catalog = refresh_catalog(force=True)
    assert catalog[FLASH].mandatory is True
    assert catalog["tencent/hy-mt2-1.8b"].supported is False
    found, info = lookup_model_reasoning("tencent/hy-mt2-1.8b")
    assert found is True and info is not None and info.supported is False


def test_unreachable_catalog_falls_back_and_is_fetched_once_per_lookup():
    client, patched = _catalog_client(error=httpx.ConnectError("nope"))
    with patched:
        assert refresh_catalog(force=True)[FLASH] == FALLBACK[FLASH]
        reset_catalog_cache()
        client.get.reset_mock()
        assert lookup_model_reasoning("vendor/unknown") == (False, None)
        found, info = lookup_model_reasoning(FLASH)
    assert client.get.call_count == 2  # one fetch per lookup, no immediate retry
    assert found is True and info == FALLBACK[FLASH]
