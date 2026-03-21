"""Translation layer for orchestrating content translation.

This package contains modules for managing the translation process,
including token handling and translation orchestration.
"""

from .token_handler import (
    TokenHandler,
    TokenValidator,
    SanitizedText,
    TokenReplacement,
    sanitize_text,
    restore_text,
)


def __getattr__(name):
    if name == "TranslationManager":
        from .translation_manager import TranslationManager
        return TranslationManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TokenHandler",
    "TokenValidator",
    "SanitizedText",
    "TokenReplacement",
    "sanitize_text",
    "restore_text",
    "TranslationManager",
]
