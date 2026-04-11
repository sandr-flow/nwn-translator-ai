from src.nwn_translator.file_handlers.gff_patcher import sanitize_for_module_encoding


def test_sanitize_replaces_unicode_dashes_for_cp1251():
    text = "модуль — для этого не требуется"
    sanitized = sanitize_for_module_encoding(text, "cp1251")
    assert sanitized == "модуль - для этого не требуется"


def test_sanitize_replaces_en_dash_for_cp1251():
    text = "1–2 игрока"
    sanitized = sanitize_for_module_encoding(text, "cp1251")
    assert sanitized == "1-2 игрока"
