"""Choosing the provider from the API key."""

from unittest.mock import MagicMock

import pytest

from nwn_translator.ai_providers import (
    create_provider,
    create_provider_for_config,
    detect_provider_from_key,
    openrouter_provider,
    provider_label,
)
from nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from nwn_translator.ai_providers.polza_provider import PolzaProvider
from nwn_translator.async_utils import run_async
from nwn_translator.telemetry import RunMetricsRecorder
from tests.support.fakes import make_config


@pytest.mark.parametrize(
    "key, provider",
    [
        ("", ""),
        (None, ""),
        ("   ", ""),
        ("sk-or-v1-abc", "openrouter"),
        ("pza-abcdef1234567890", "polza"),
        ("pza_abcdef1234567890", "polza"),
        ("sk-abc-123", "openrouter"),  # unknown prefixes fall back to OpenRouter
    ],
)
def test_detect_provider_from_key(key, provider):
    assert detect_provider_from_key(key) == provider


def test_provider_labels():
    assert [provider_label(name) for name in ("openrouter", "polza", "", "unknown")] == [
        "OpenRouter",
        "POLZA.AI",
        "",
        "",
    ]


def test_create_provider_by_key_prefix(monkeypatch):
    provider = create_provider("sk-or-test", model="openai/gpt-4o", player_gender="female")
    assert type(provider) is OpenRouterProvider
    assert (provider.model, provider.player_gender) == ("openai/gpt-4o", "female")
    assert type(create_provider("just-random-chars", model="openai/gpt-4o")) is OpenRouterProvider
    with pytest.raises(TypeError):
        create_provider("sk-or-test", site_name="typo")

    # ``pza…`` keys route to POLZA.AI: its base URL and no extra headers.
    client_cls = MagicMock()
    monkeypatch.setattr(openrouter_provider, "AsyncOpenAI", client_cls)
    polza = create_provider("pza-abcdef1234567890", model="openai/gpt-4o")

    async def touch_client():
        return polza.async_client

    run_async(touch_client(), timeout=5.0)
    assert isinstance(polza, PolzaProvider)
    assert polza.get_provider_name() == "polza"
    assert client_cls.call_args.kwargs["base_url"] == "https://polza.ai/api/v1"
    assert client_cls.call_args.kwargs["default_headers"] == {}


def test_provider_for_config_takes_the_key_model_and_prompt_settings(tmp_path):
    config = make_config(
        api_key="pza-abcdef1234567890",
        model="openai/gpt-4o",
        input_file=tmp_path / "m.mod",
        player_gender="female",
    )
    recorder = RunMetricsRecorder()

    provider = create_provider_for_config(config, recorder)

    assert isinstance(provider, PolzaProvider)
    assert (provider.model, provider.player_gender) == ("openai/gpt-4o", "female")
    assert provider.metrics_recorder is recorder
