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
    extract_tokens,
)
from .context_builder import ContextBuilder

# Lazy import for translation_manager which has external dependencies
def __getattr__(name):
    if name == "TranslationManager":
        from .translation_manager import TranslationManager
        return TranslationManager
    if name == "translate_file":
        from .translation_manager import translate_file
        return translate_file
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "TokenHandler",
    "TokenValidator",
    "SanitizedText",
    "TokenReplacement",
    "sanitize_text",
    "restore_text",
    "extract_tokens",
    "ContextBuilder",
    "TranslationManager",
    "translate_file",
]
