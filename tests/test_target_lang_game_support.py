"""Target languages compatible with NWN string injection (legacy Windows pages)."""

from nwn_translator.config import (
    module_string_encoding_for_target_lang,
    target_lang_supported_for_nwn_injection,
)


def test_cjk_marked_unsupported() -> None:
    assert not target_lang_supported_for_nwn_injection("korean")
    assert not target_lang_supported_for_nwn_injection("Chinese")
    assert not target_lang_supported_for_nwn_injection("japanese")


def test_european_languages_supported() -> None:
    assert target_lang_supported_for_nwn_injection("russian")
    assert target_lang_supported_for_nwn_injection("german")
    assert target_lang_supported_for_nwn_injection("turkish")


def test_module_encoding_by_language() -> None:
    assert module_string_encoding_for_target_lang("russian") == "cp1251"
    assert module_string_encoding_for_target_lang("German") == "cp1252"
    assert module_string_encoding_for_target_lang("polish") == "cp1250"
    assert module_string_encoding_for_target_lang("turkish") == "cp1254"
    assert module_string_encoding_for_target_lang("unknown-lang") == "cp1252"
    assert module_string_encoding_for_target_lang(None) == "cp1251"
    assert module_string_encoding_for_target_lang("") == "cp1251"
