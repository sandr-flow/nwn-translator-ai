"""Prompts of the terminology stages: entity extraction, candidate curation, glossary.

Every byte here reaches the model: the system prompts are pinned by the prompt
snapshot test, the user prompts by ``tests/test_terminology_requests.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Iterable, List, Mapping, Optional

from ._builder import format_nickname_examples
from .examples import get_examples

if TYPE_CHECKING:
    from ..context.entity_candidates import EntityCandidate
    from ..context.world_context import NPCInfo


def build_entity_extraction_system_prompt(source_lang: str = "English") -> str:
    """System prompt for extracting proper nouns from game texts.

    Entity extraction finds character, location and organization names embedded
    in dialogs, descriptions and sign text, which the world scan never sees as
    standalone GFF fields.

    Args:
        source_lang: Language of the analyzed texts; names must come back
            exactly as they appear in the source.

    Returns:
        The system prompt.
    """
    return (
        f"You are analyzing {source_lang} texts from a Neverwinter Nights game module.\n"
        "Extract only high-confidence proper nouns visible in natural-language game text: "
        "character names, place names, organization names, unique named objects, and "
        "recurring epithets or hyphenated terms used as forms of address. Prefer "
        "returning too few names over returning technical or uncertain labels.\n\n"
        'Return a single JSON object with one key "entities" whose value is '
        'an array of entity objects with "name" and "type" fields.\n'
        'Valid types: "character", "location", "organization", "item", '
        '"nickname", "unknown".\n\n'
        "FEW-SHOT EXAMPLES (note: these illustrate the task; real inputs will be "
        f"in {source_lang}):\n\n"
        "Input:\n"
        '[0] "Leading a coach to Stout Village with farming equipment to deliver."\n'
        '[1] "Gotta hand-carry some letters to the Western Gate from the castle."\n'
        '[2] "Hello! I can take you back to Penultima City, if you\'d like to leave."\n'
        "[3] \"I saw your ad posted by the Guild of Middlemen. You're looking for "
        'adventurer(s), yes?"\n'
        '[4] "Hello! I\'m the Magical Plot Fairy. Do you need a recap?"\n'
        '[5] "Contact R. Freely in Stout Village for details."\n'
        '[6] "Stay back, sword-one!"\n'
        '[7] "Must.. protect... sword-one... ghh"\n\n'
        "Output:\n"
        '{"entities": [\n'
        '  {"name": "Stout Village", "type": "location"},\n'
        '  {"name": "Western Gate", "type": "location"},\n'
        '  {"name": "Penultima City", "type": "location"},\n'
        '  {"name": "Guild of Middlemen", "type": "organization"},\n'
        '  {"name": "Magical Plot Fairy", "type": "character"},\n'
        '  {"name": "R. Freely", "type": "character"},\n'
        '  {"name": "sword-one", "type": "nickname"}\n'
        "]}\n\n"
        "Negative examples that MUST return no entities:\n"
        '[0] "DMFI Admin Server Wand"\n'
        '[1] "ARCH_TARGET"\n'
        '[2] "WILL_O_WISP"\n'
        '[3] "BakersPlea"\n'
        '[4] "Hello <FirstName>, choose <race>."\n'
        'Output: {"entities": []}\n\n'
        "Rules:\n"
        "- Include proper nouns that are names of specific characters, places, "
        "organizations, or unique objects.\n"
        "- Include recurring compound nicknames or hyphenated terms used as forms "
        'of address (type: "nickname"). These must be translated consistently.\n'
        "- Do NOT include placeholders or angle tokens such as <FirstName>, <FullName>, "
        "<race>, <man/woman>, or <CustomToken:123>.\n"
        "- Do NOT include acronyms, brands, system terms, engine/toolset terms, or utility "
        "labels such as NWN, DMFI, D&D, AD&D, Bioware, or ARCH_TARGET.\n"
        "- Do NOT include file names, resource references, script names, blueprint tags, "
        "or labels that look like CamelCase identifiers, snake_case identifiers, "
        "underscore constants, route labels, or wildcard patterns.\n"
        "- Do NOT include common game terms (sword, goblin, mine, chest, potion, etc.).\n"
        "- Do NOT include race or class names (dwarf, elf, wizard, halfling, etc.).\n"
        "- Do NOT include common words, adjectives, or generic phrases.\n"
        '- Use "unknown" only for a natural-language multi-word proper noun whose category '
        "is genuinely unclear; otherwise omit uncertain candidates.\n"
        "- Each name should appear only once in your output (deduplicate across all input lines).\n"
        "- Preserve original spelling exactly as it appears in the text.\n"
        '- Return {"entities": []} if no proper nouns are found.\n'
        "Do not use markdown code fences."
    )


def build_entity_extraction_user_prompt(texts: Iterable[str]) -> str:
    """User prompt listing the texts of one entity-extraction batch.

    Args:
        texts: Texts of the batch, numbered from 0 in the prompt.

    Returns:
        The user prompt; line breaks inside a text become spaces and double
        quotes become single quotes, so each text stays one quoted line.
    """
    lines = ["Extract proper nouns from these texts:", ""]
    for idx, text in enumerate(texts):
        safe = text.replace("\n", " ").replace('"', "'")
        lines.append(f'[{idx}] "{safe}"')
    return "\n".join(lines)


def build_curator_system_prompt(target_lang: str) -> str:
    """System prompt of the glossary candidate curator.

    Args:
        target_lang: Target language name, inserted as given.

    Returns:
        The system prompt.
    """
    return (
        "You curate proper-name candidates for a Neverwinter Nights translation glossary. "
        f"Target language: {target_lang}. Decide whether each candidate should be a "
        "run-wide glossary entity. Return only JSON. Valid decisions are keep, "
        "local_only, drop, alias_of. Use drop for technical labels, numbered generic "
        "placeables and route labels. Keep recurring distinctive creature types and "
        "in-world product or organization names, including their abbreviations. Use local_only "
        "for labels useful only near their resource. Use alias_of for shorter/variant "
        "names of another candidate only when the evidence identifies the same entity. "
        "The alias target must be an existing candidate name, not a new spelling. "
        "A shared word alone is not evidence: a creature type and its stronger variant "
        "remain distinct. Gendered generic titles are contextual labels, not proper names. "
        "reason is a short snake_case tag under 30 characters (for example "
        "technical_label, recurring_creature, product_name, variant_of_target, "
        "generic_title, local_label), never a sentence."
    )


def build_curator_user_prompt(records: Mapping[str, Mapping[str, object]]) -> str:
    """User prompt of one curation request.

    Args:
        records: Candidate name -> curator record, in request order.

    Returns:
        The user prompt with the records as indented JSON.
    """
    return (
        "Curate these candidates. Return a JSON object keyed by candidate name. "
        "Each value must contain decision, reason (short tag), priority, and optionally "
        "alias_of (an existing source form).\n\n"
        + json.dumps(records, ensure_ascii=False, indent=2)
    )


def build_glossary_system_prompt(target_lang: str) -> str:
    """System prompt for glossary proper-name translation.

    Args:
        target_lang: Target language name; selects the few-shot examples.

    Returns:
        The system prompt.
    """
    ex = get_examples(target_lang)
    personal = ex["glossary_personal"]
    descriptive = ex["glossary_descriptive"]

    pers_ex = ", ".join(f'"{eng}" -> "{tr}"' for eng, tr in personal)
    desc_ex = ", ".join(f'"{eng}" -> "{good}" (NOT "{bad}")' for eng, good, bad in descriptive)
    nick_ex = format_nickname_examples(target_lang, indent="  ")

    return (
        f"You are preparing a translation glossary for the game Neverwinter Nights.\n"
        f"Target language: {target_lang}.\n\n"
        "Translate each proper name below into the target language.\n\n"
        "KEY RULES — translating vs transliterating:\n"
        "- Personal names (character first/last names, unique fantasy names): "
        "TRANSLITERATE into target-language script, even when the token coincides "
        "with an ordinary English word (Dawn, Grace, Hunter as given names — "
        "NOT calques of the common nouns).\n"
        f"  Examples: {pers_ex}\n"
        "- Nicknames / vocatives (category nickname): TRANSLATE the meaning as a "
        'short natural epithet ("the one who is/has X"), not as a personal name. '
        "Fit vocative vs grammatical object to the sentence; do not freeze an "
        "English-shaped compound. Do NOT phonetic-transliterate ordinary English "
        'words. Do NOT calque the English suffix "-one" as a numeral '
        "— that suffix is speaker pidgin, not a number.\n"
        f"  Examples:\n{nick_ex}\n"
        "- Descriptive/meaningful names (locations, items, quests, titles composed of "
        "real English words with clear meaning): TRANSLATE the meaning. "
        "NEVER produce phonetic transliteration of English words.\n"
        f"  Examples: {desc_ex}\n"
        "- When in doubt: character given/family names transliterate. "
        "Nicknames built from ordinary English words translate as an epithet. "
        "Multi-word descriptive titles translate the meaning. "
        "Made-up fantasy words transliterate.\n\n"
        "Hints in parentheses may include gender (feminine/masculine) and field "
        "(FirstName) — use them; do not put those hints into JSON keys.\n\n"
        "Return personal names in nominative (dictionary) form only; the game will "
        "inflect in context later. For nicknames, store a short epithet the "
        "translator can adapt (vocative vs object), not a frozen compound.\n\n"
        "OUTPUT: A single JSON object whose keys are the EXACT English name "
        "(WITHOUT the category hint in parentheses) and values are the translations.\n"
        'Example: the list entry "- Perin Izrick (character)" '
        'must produce key "Perin Izrick", NOT "Perin Izrick (character)".\n'
        "Do not omit keys. Do not add keys not in the list.\n"
        "Do not use markdown code fences."
    )


def build_glossary_name_line(
    name: str,
    category: str,
    candidate: Optional["EntityCandidate"] = None,
    npcs: Iterable["NPCInfo"] = (),
) -> str:
    """One name of a glossary request with its hints in parentheses.

    Args:
        name: Source form to translate.
        category: Entity category (``unknown`` when empty).
        candidate: The name's entity candidate, whose alias target and source
            contexts become hints.
        npcs: Creatures carrying *name* as first, last or full name; their name
            fields and gender become hints.

    Returns:
        The line ``- name (hint, hint, ...)``.
    """
    hints: List[str] = [category or "unknown"]
    if (category or "").strip().lower() == "nickname":
        hints.append("vocative epithet; translate meaning, not a name")
    if candidate is not None:
        if candidate.alias_of:
            hints.append(f"alias of {candidate.alias_of}; preserve abbreviation or wordplay")
        hints.extend(candidate.contexts)
    for npc in npcs:
        hints.append(
            f"NPC fields: FirstName={npc.first_name!r}, LastName={npc.last_name!r}, "
            f"gender={npc.gender}"
        )
    return f"- {name} ({', '.join(hints)})"


def build_glossary_user_prompt(name_lines: Iterable[str], accepted: Mapping[str, str]) -> str:
    """User prompt of one glossary request.

    Args:
        name_lines: Lines from :func:`build_glossary_name_line`.
        accepted: Forms already accepted for this batch; a retry repeats them so
            the missing names stay consistent with their alias family.

    Returns:
        The user prompt.
    """
    prompt = (
        "Translate every name below. "
        "Keys in your JSON must be the English name only, "
        "without the parenthesized category hint:\n\n" + "\n".join(name_lines)
    )
    if accepted:
        prompt += "\n\nAlready accepted forms in this family/batch: " + json.dumps(
            accepted, ensure_ascii=False
        )
    return prompt
