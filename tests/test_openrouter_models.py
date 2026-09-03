"""Tests for OpenRouter model-catalog reasoning metadata."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.nwn_translator.ai_providers.openrouter_models import (
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


@pytest.fixture(autouse=True)
def _clear_catalog():
    reset_catalog_cache()
    yield
    reset_catalog_cache()


def test_slug_validation():
    assert is_valid_model_slug("google/gemini-3.8-flash")
    assert is_valid_model_slug("meta-llama/llama-3.3-70b-instruct:free")
    assert not is_valid_model_slug("noslash")
    assert not is_valid_model_slug("../etc/passwd")
    assert not is_valid_model_slug("a/b with spaces")


def test_gemini_38_fallback_has_no_off():
    info = FALLBACK["google/gemini-3.8-flash"]
    assert info.mandatory is True
    assert allowed_efforts(info) == ["low", "medium", "high"]


def test_luna_fallback_allows_none():
    info = FALLBACK["openai/gpt-5.6-luna"]
    assert "none" in allowed_efforts(info)
    assert allowed_efforts(info)[0] == "none"


def test_resolve_clamps_38_off_to_low():
    assert resolve_reasoning_effort("google/gemini-3.8-flash", "none") == "low"
    assert resolve_reasoning_effort("google/gemini-3.8-flash", None) == "low"
    assert resolve_reasoning_effort("google/gemini-3.8-flash", "medium") == "medium"


def test_resolve_unknown_slug_passthrough():
    assert resolve_reasoning_effort("vendor/custom-model", "none") == "none"
    assert resolve_reasoning_effort("vendor/custom-model", "high") == "high"


def test_resolve_31_off_to_minimal():
    assert resolve_reasoning_effort("google/gemini-3.1-flash-lite", "none") == "minimal"


def test_get_known_reasoning_uses_fallback_before_live_fetch():
    assert get_known_reasoning("google/gemini-3.8-flash") is FALLBACK["google/gemini-3.8-flash"]
    assert get_known_reasoning("vendor/unknown") is None


def test_refresh_catalog_parses_live_payload():
    payload = {
        "data": [
            {
                "id": "google/gemini-3.8-flash",
                "reasoning": {
                    "mandatory": True,
                    "default_effort": "medium",
                    "supported_efforts": ["high", "medium", "low"],
                },
            },
            {"id": "tencent/hy-mt2-1.8b"},
        ]
    }
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    mock_client = MagicMock()
    mock_client.get.return_value = response
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    with patch(
        "src.nwn_translator.ai_providers.openrouter_models.httpx.Client",
        return_value=mock_client,
    ):
        catalog = refresh_catalog(force=True)
    assert catalog["google/gemini-3.8-flash"].mandatory is True
    assert catalog["tencent/hy-mt2-1.8b"].supported is False
    found, info = lookup_model_reasoning("tencent/hy-mt2-1.8b")
    assert found is True
    assert info is not None and info.supported is False


def test_refresh_catalog_falls_back_on_http_error():
    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.ConnectError("nope")
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    with patch(
        "src.nwn_translator.ai_providers.openrouter_models.httpx.Client",
        return_value=mock_client,
    ):
        catalog = refresh_catalog(force=True)
    assert catalog["google/gemini-3.8-flash"] == FALLBACK["google/gemini-3.8-flash"]


def test_unrestricted_mandatory_drops_none():
    info = ModelReasoning(supported=True, mandatory=True, supported_efforts=None)
    assert "none" not in allowed_efforts(info)
    assert allowed_efforts(info)[0] == "minimal"
