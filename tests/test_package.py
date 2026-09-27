"""The package root: run settings eagerly, pipeline entry points on first use."""

import pytest

import nwn_translator
from nwn_translator import main


def test_entry_points_resolve_from_main_on_first_use():
    assert nwn_translator.translate_module is main.translate_module
    assert nwn_translator.ModuleTranslator is main.ModuleTranslator
    assert {"TranslationConfig", "translate_module", "ModuleTranslator"} <= set(
        nwn_translator.__all__
    )
    with pytest.raises(AttributeError, match="has no attribute 'missing'"):
        getattr(nwn_translator, "missing")
