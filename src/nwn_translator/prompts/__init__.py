"""Prompt construction for AI translation.

The package exports the system-prompt builders and the content profiles.
Translation and dialog prompts live in :mod:`._builder`, the entity, curation
and glossary prompts in :mod:`.terminology`, the NCS gate prompts in
:mod:`.ncs_gate` and the per-language few-shot examples in :mod:`.examples`.
"""

from ._builder import (
    CONTENT_PROFILE_DEFAULT,
    CONTENT_PROFILE_SCRIPT_MESSAGE,
    CONTENT_PROFILE_SHORT_LABEL,
    build_dialog_system_prompt,
    build_dialog_system_prompt_parts,
    build_translation_system_prompt_parts,
)
from .terminology import build_entity_extraction_system_prompt, build_glossary_system_prompt

__all__ = [
    "CONTENT_PROFILE_DEFAULT",
    "CONTENT_PROFILE_SCRIPT_MESSAGE",
    "CONTENT_PROFILE_SHORT_LABEL",
    "build_translation_system_prompt_parts",
    "build_dialog_system_prompt",
    "build_dialog_system_prompt_parts",
    "build_glossary_system_prompt",
    "build_entity_extraction_system_prompt",
]
