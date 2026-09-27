"""The package root: run settings eagerly, pipeline entry points on first use."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import nwn_translator
from nwn_translator import main


def test_importing_the_package_leaves_the_pipeline_unloaded():
    # A fresh interpreter: this test session has imported the pipeline already.
    src = Path(nwn_translator.__file__).resolve().parents[1]
    probe = "import sys, nwn_translator; print('nwn_translator.main' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_entry_points_resolve_from_main():
    assert nwn_translator.translate_module is main.translate_module
    assert nwn_translator.ModuleTranslator is main.ModuleTranslator
    assert {"TranslationConfig", "translate_module", "ModuleTranslator"} <= set(
        nwn_translator.__all__
    )
    with pytest.raises(AttributeError, match="has no attribute 'missing'"):
        getattr(nwn_translator, "missing")
