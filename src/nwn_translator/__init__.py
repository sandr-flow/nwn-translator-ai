"""NWN Modules Translator: LLM translation of Neverwinter Nights modules.

The package root exposes the run settings eagerly and the pipeline entry points
(``translate_module``, ``ModuleTranslator``) lazily.
"""

from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("nwn-modules-translator")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__author__ = "Open Source Community"

from .config import ProgressCallback, TranslationConfig, create_output_path

#: Entry points imported on first use: the pipeline imports every extractor and the
#: provider SDK, which ``import nwn_translator`` (and ``nwn_translator.config``) avoid.
_LAZY = ("translate_module", "ModuleTranslator")


def __getattr__(name):
    """Imports ``translate_module`` or ``ModuleTranslator`` from :mod:`.main` on first use.

    Args:
        name: Attribute looked up on the package.

    Returns:
        The entry point.

    Raises:
        AttributeError: If *name* is neither of them.
    """
    if name in _LAZY:
        from . import main

        return getattr(main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ProgressCallback", "TranslationConfig", "create_output_path", *_LAZY]
