"""Environment overrides and defaults of nwn_translator.config."""

import pytest

from nwn_translator import config
from nwn_translator.config import (
    DEFAULT_MODEL,
    TranslationConfig,
    _env_number,
    max_concurrent_from_environment,
    parse_reasoning_effort,
)


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 12), ("20", 20), (" 3 ", 3), ("0", 1), ("-5", 1), ("abc", 12), ("", 12), ("2.5", 12)],
)
def test_max_concurrent_from_environment(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("NWN_TRANSLATE_MAX_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("NWN_TRANSLATE_MAX_CONCURRENT", raw)
    assert max_concurrent_from_environment() == expected


@pytest.mark.parametrize(
    "raw,expected", [(None, 300.0), ("45.5", 45.5), ("10", 30.0), ("x", 300.0)]
)
def test_float_override_is_clamped(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("NWN_TEST_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("NWN_TEST_TIMEOUT", raw)
    assert _env_number("NWN_TEST_TIMEOUT", 300.0, 30.0, float) == expected


def test_defaults_without_environment():
    assert config.GLOSSARY_LLM_TIMEOUT >= 30.0
    assert config.GLOSSARY_RUN_TIMEOUT >= 60.0
    assert TranslationConfig(api_key="k").model == DEFAULT_MODEL == "google/gemini-3.8-flash"


def test_parse_reasoning_effort():
    assert parse_reasoning_effort(None) is None
    assert parse_reasoning_effort("  ") is None
    assert parse_reasoning_effort(" XHigh ") == "xhigh"
    with pytest.raises(ValueError) as exc_info:
        parse_reasoning_effort("ultra")
    assert str(exc_info.value) == (
        "Invalid reasoning_effort 'ultra'; expected one of "
        "['high', 'low', 'max', 'medium', 'minimal', 'none', 'xhigh']"
    )


def test_missing_api_key_raises():
    with pytest.raises(ValueError, match="API key is required"):
        TranslationConfig(api_key="").get_api_key()
