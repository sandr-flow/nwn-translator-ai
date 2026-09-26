"""Run configuration: environment overrides, languages, encodings and output names."""

from pathlib import Path

import pytest

from nwn_translator import config
from nwn_translator.config import (
    _LANG_TO_WINDOWS_ENCODING,
    DEFAULT_MODEL,
    TranslationConfig,
    _env_number,
    create_output_path,
    lang_suffix,
    max_concurrent_from_environment,
    module_string_encoding_for_target_lang,
    parse_reasoning_effort,
    sanitized_mod_stem,
    source_string_encoding,
    target_lang_supported_for_nwn_injection,
)
from nwn_translator.formats.text_codec import MODULE_ENCODINGS


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
    run = TranslationConfig(api_key="k", input_file=Path("test.mod"), target_lang="spanish")
    assert run.model == DEFAULT_MODEL == "google/gemini-3.8-flash"
    assert (run.target_lang, run.preserve_tokens) == ("spanish", True)


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


@pytest.mark.parametrize(
    "lang, supported",
    [
        ("korean", False),
        ("Chinese", False),
        ("japanese", False),
        # NWN:EE offers only cp1250/cp1251/cp1252: cp1254 text cannot be shown in game.
        ("turkish", False),
        ("Turkish", False),
        ("russian", True),
        ("german", True),
    ],
)
def test_target_languages_the_game_can_display(lang, supported):
    assert target_lang_supported_for_nwn_injection(lang) is supported


@pytest.mark.parametrize(
    "lang, encoding",
    [
        ("russian", "cp1251"),
        ("German", "cp1252"),
        ("polish", "cp1250"),
        ("unknown-lang", "cp1252"),
        (None, "cp1251"),
        ("", "cp1251"),
    ],
)
def test_module_encoding_by_target_language(lang, encoding):
    assert module_string_encoding_for_target_lang(lang) == encoding


@pytest.mark.parametrize(
    "lang, encoding",
    [
        ("russian", "cp1251"),
        ("French", "cp1252"),
        ("german", "cp1252"),
        ("polish", "cp1250"),
        # "auto", empty and unknown languages leave the encoding to detection.
        ("auto", None),
        ("AUTO", None),
        ("", None),
        (None, None),
        ("klingon", None),
    ],
)
def test_source_encoding_by_source_language(lang, encoding):
    assert source_string_encoding(lang) == encoding


def test_target_code_pages_are_the_writable_module_encodings():
    """Every offered language maps to a page the patchers accept, and back."""
    assert set(_LANG_TO_WINDOWS_ENCODING.values()) == MODULE_ENCODINGS


def test_output_names_use_hyphens_not_underscores():
    assert (lang_suffix("russian"), lang_suffix("de")) == ("-rus", "-de")
    assert not lang_suffix("english").startswith("_")
    assert "_" not in lang_suffix("french")
    assert sanitized_mod_stem("foo_bar") == "foo-bar"
    assert "_" not in sanitized_mod_stem("a_b_c")
    source = Path("in") / "my_mod_name.mod"
    assert create_output_path(source, "russian").name == "my-mod-name-rus.mod"
    assert create_output_path(source, "russian", output_dir=Path("out")) == (
        Path("out") / "my-mod-name-rus.mod"
    )
