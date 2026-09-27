"""Prompt texts of every model request.

:mod:`._builder` holds the content profiles, the translation prompts and the
dialog system prompt; :mod:`.dialog` the dialog user, repair and retry messages;
:mod:`.token_retry` the retry text shared by both translators; :mod:`.terminology`
the entity, curation and glossary prompts; :mod:`.ncs_gate` the NCS gate prompts;
and :mod:`.examples` the per-language few-shot examples.
"""

from ._builder import build_dialog_system_prompt_parts, build_translation_system_prompt_parts
from .terminology import build_entity_extraction_system_prompt, build_glossary_system_prompt

__all__ = [
    "build_translation_system_prompt_parts",
    "build_dialog_system_prompt_parts",
    "build_glossary_system_prompt",
    "build_entity_extraction_system_prompt",
]
