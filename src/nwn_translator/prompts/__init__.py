"""Prompt construction for AI translation.

The package exports the system-prompt builders. Content profiles and the
translation user messages live in :mod:`._builder`, the NCS gate prompts in
:mod:`.ncs_gate` and the per-language few-shot examples in :mod:`.examples`.
"""

from ._builder import (
    build_dialog_system_prompt,
    build_dialog_system_prompt_parts,
    build_entity_extraction_system_prompt,
    build_glossary_system_prompt,
    build_translation_system_prompt_parts,
)

__all__ = [
    "build_translation_system_prompt_parts",
    "build_dialog_system_prompt",
    "build_dialog_system_prompt_parts",
    "build_glossary_system_prompt",
    "build_entity_extraction_system_prompt",
]
