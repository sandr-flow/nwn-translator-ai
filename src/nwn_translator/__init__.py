"""NWN Modules Translator - AI-powered translation tool for Neverwinter Nights modules."""

from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("nwn-modules-translator")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__author__ = "Open Source Community"

from .config import ProgressCallback, TranslationConfig, create_output_path


def __getattr__(name):
    """Imports the pipeline entry points on first use.

    Keeps ``import nwn_translator`` (and ``nwn_translator.config``) light: the
    pipeline imports every extractor and the provider SDK.
    """
    if name == "translate_module":
        from .main import translate_module

        return translate_module
    if name == "ModuleTranslator":
        from .main import ModuleTranslator

        return ModuleTranslator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ProgressCallback",
    "TranslationConfig",
    "create_output_path",
    "translate_module",
    "ModuleTranslator",
]
