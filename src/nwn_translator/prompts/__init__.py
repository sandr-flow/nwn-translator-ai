"""Prompt construction for AI translation.

Public API (re-exported for backward compatibility):
    - build_translation_system_prompt
    - build_dialog_system_prompt
    - build_glossary_system_prompt
"""

from ._builder import (
    build_dialog_system_prompt,
    build_entity_extraction_system_prompt,
    build_glossary_system_prompt,
    build_translation_system_prompt,
)

__all__ = [
    "build_translation_system_prompt",
    "build_dialog_system_prompt",
    "build_glossary_system_prompt",
    "build_entity_extraction_system_prompt",
]
