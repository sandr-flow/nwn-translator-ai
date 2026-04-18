"""Shared prompt building blocks for AI translation.

Centralises the translation rules that are common across line-by-line,
contextual dialog, and glossary system prompts so that updates only need
to happen in one place.

All examples are loaded from per-language modules in ``prompts.examples``
so that few-shot demonstrations match the actual target language.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from .examples import get_examples

# ---------------------------------------------------------------------------
# Static glossary-usage rules (always included in STABLE prefix so providers
# can prefix-cache the prompt; the actual entries live in the VARIABLE suffix).
# ---------------------------------------------------------------------------

_GLOSSARY_RULE_TRANSLATION = (
    "10. GLOSSARY USAGE \u2014 if a GLOSSARY section follows the rules, use it "
    "as a consistency reference, NOT as a substitution table:\n"
    "   a) Apply a glossary entry ONLY when the EXACT full proper name from "
    "the glossary appears in the source text as a capitalized name. Do NOT "
    "match partial or coincidental overlaps (e.g. 'dead Goblin Hunter' in "
    "narrative \u2260 quest name 'Dead Hunter' in glossary).\n"
    "   b) Decline/conjugate the glossary translation to fit the grammatical "
    "context (case, number, gender) ONLY if the target language grammar "
    "allows it for that particular name. If a foreign name is grammatically "
    "indeclinable in the target language, keep it in its dictionary form or "
    "rephrase the sentence.\n"
    "   c) Translate ONLY what is present in the source text. If the source "
    "says 'Danda', output only the transliteration of 'Danda' \u2014 do NOT "
    "append a surname or title from the glossary entry 'Danda Mudgrabber'.\n"
    "   d) Each glossary entry is a DISTINCT entity. NEVER replace one "
    "glossary name with another, even if they seem related or co-located in "
    "the game world. Translate ONLY the name that literally appears in the "
    "source line.\n"
    "   e) If a proper name is not listed in the glossary, follow rules 7-9.\n"
    "   f) Recurring epithets, nicknames, and hyphenated compound terms used "
    "as forms of address MUST be translated consistently across all lines.\n"
)

_GLOSSARY_RULE_DIALOG = (
    "5. GLOSSARY USAGE (if a GLOSSARY section follows the rules) \u2014 the "
    "glossary is a consistency reference, NOT a substitution table:\n"
    "   - Apply ONLY when the EXACT full proper name appears in the source "
    "as a capitalized name. Do NOT match partial or coincidental overlaps.\n"
    "   - Decline/conjugate to fit grammatical context (case, number, "
    "gender) ONLY if the target language grammar allows it for that name. "
    "If a foreign name is indeclinable, keep its dictionary form or "
    "rephrase.\n"
    "   - Translate ONLY what is in the source. If source says 'Danda', "
    "output only the transliteration of 'Danda' \u2014 do NOT add "
    "surname/title from a longer glossary entry.\n"
    "   - Each glossary entry is a DISTINCT entity. NEVER replace one name "
    "with another, even if they seem related. Translate ONLY the name "
    "literally present in the source.\n"
    "   - Recurring epithets, nicknames, and hyphenated terms used as forms "
    "of address must be translated consistently across all dialog lines.\n"
)


# ---------------------------------------------------------------------------
# Fragment builders (internal)
# ---------------------------------------------------------------------------


def _proper_names_rules(target_lang: str) -> str:
    """Rules for translating vs. transliterating proper names."""
    ex = get_examples(target_lang)
    descriptive = ex["proper_names"]
    personal = ex["personal_names"]

    desc_lines = "\n".join(
        f'      - "{eng}" -> "{good}" (GOOD) \u2014 NOT "{bad}" (BAD)'
        for eng, good, bad in descriptive
    )
    pers_lines = "\n".join(f'      - "{eng}" -> "{tr}"' for eng, tr in personal)

    declension_note = ex.get("declension_note", "")
    declension_block = f"   {declension_note}" if declension_note else ""

    return (
        "PROPER NAMES \u2014 translating vs. transliterating:\n"
        "   a) Descriptive/meaningful names: TRANSLATE the meaning. "
        "NEVER produce phonetic transliterations of English words.\n"
        f"      Examples:\n{desc_lines}\n"
        "   b) Personal names (first/last names of characters): TRANSLITERATE.\n"
        f"      Examples:\n{pers_lines}\n"
        "   When in doubt whether a name is descriptive or personal, check: does the name "
        "consist of ordinary English words with clear meaning? Then translate the meaning. "
        "Is it a made-up fantasy name? Then transliterate.\n"
        f"{declension_block}"
    )


def _speech_style_rules(target_lang: str) -> str:
    """Rules for preserving speech register (low-INT characters, etc.)."""
    ex = get_examples(target_lang)
    lines = ex["speech_low_int"]
    pattern = ex["speech_low_int_pattern"]
    counterexample = ex.get("speech_normal_counterexample", "")

    example_block = "\n".join(
        f'    - "{eng}" -> "{good}" ' f'(GOOD, broken) \u2014 NOT "{bad}" (BAD, normalized)'
        for eng, good, bad in lines
    )

    counter_block = f"\n    {counterexample}" if counterexample else ""

    return (
        "PRESERVE SPEECH STYLE AND REGISTER. This is a role-playing game with characters "
        "of different intelligence and background.\n"
        "    CRITICAL: broken/primitive style MUST be applied ONLY when the ORIGINAL "
        "English text itself has broken grammar, misspellings, or primitive syntax "
        "(low-INT characters, barbarians, goblins, etc.). "
        "If the original English text is grammatically correct — even if the speaker is "
        "a dwarf, goblin, or monster — the translation MUST be grammatically correct too. "
        "Notices, letters, signs, memos, and any other written text with proper English "
        "grammar must be translated with proper target-language grammar.\n"
        "    When broken style IS appropriate, reproduce an equally broken, primitive style. "
        'DO NOT "fix" or "correct" intentionally broken speech — that would destroy the character.\n'
        f"    Examples (English low-INT -> {target_lang} low-INT equivalent):\n"
        f"{example_block}\n\n"
        '    Key pattern: in English, low-INT speech uses "me" instead of "I", drops articles/verbs, '
        f"simplifies grammar. {pattern}\n"
        f"{counter_block}"
    )


def _player_gender_rule(gender: str) -> str:
    """One-liner for player character grammatical gender agreement."""
    agreement = "masculine" if gender == "male" else "feminine"
    return (
        f"PLAYER CHARACTER: The protagonist is {gender}. When the text addresses "
        f"or describes the player character, ALL grammatical forms (verbs, adjectives, "
        f"participles, pronouns) MUST agree with {agreement} gender.\n"
    )


def _token_preservation_rule() -> str:
    """One-liner for preserving game tokens."""
    return (
        "MANDATORY TAG/TOKEN PRESERVATION:\n"
        "- Do NOT translate, reorder, duplicate, invent, or delete placeholders like "
        "<<TOKEN_0>>, <<TOKEN_1>>, etc.\n"
        "- Preserve inline NWN markup tags exactly as in source (e.g. <StartAction>, </Start>) "
        "without renaming, rebalancing, or moving them.\n"
        "- Never output internal helper placeholders such as [[NWN_TAG_*]].\n"
        "- If uncertain, leave tags/placeholders exactly unchanged.\n"
    )


# ---------------------------------------------------------------------------
# Composite prompt builders (public API)
# ---------------------------------------------------------------------------


def build_translation_system_prompt_parts(
    target_lang: str,
    gender: str,
    glossary_block: str = "",
) -> Tuple[str, str]:
    """Return ``(stable, variable)`` halves of the line-by-line / batch system prompt.

    The *stable* half holds all rules, examples, and output instructions — it
    is byte-identical across calls in a run and can be marked as the
    cache-breakpoint boundary for providers that support prompt caching
    (Anthropic, Gemini 2.5, Grok) and also qualifies as a stable prefix for
    automatic caches (OpenAI, DeepSeek).

    The *variable* half holds the GLOSSARY section (may include per-call
    race-term hints) and is the only portion that changes between calls.
    """
    stable = (
        f"You are an elite translator for the game Neverwinter Nights. "
        f"Your task is to translate the text to {target_lang} according to "
        "Nora Gal's Golden School of Translation.\n\n"
        "RULES:\n"
        "1. Never translate word-for-word. Focus on meaning, emotion, and tone.\n"
        "2. Use natural syntax and vocabulary. Avoid bureaucratic language "
        "(Chancellery/\u041a\u0430\u043d\u0446\u0435\u043b\u044f\u0440\u0438\u0442).\n"
        "3. Identify idioms and adapt them to natural equivalents in the target language.\n"
        "4. Preserve all formatting, line breaks, and special characters.\n"
        f"5. {_token_preservation_rule()}"
        "6. The translated text MUST be grammatically correct, strictly preserving "
        "gender and case agreements. Exception: see rule 11 for intentionally "
        "broken speech.\n"
        f"7. {_proper_names_rules(target_lang)}"
        "8. When translating a creature's first name or last name field, output ONLY "
        "the translation of the given text. Do NOT add the other part of the name "
        "from context \u2014 the game engine concatenates FirstName + LastName automatically.\n"
        "9. When in doubt whether a name is descriptive or personal, check: does the name "
        "consist of ordinary English words with clear meaning? Then translate the meaning. "
        "Is it a made-up fantasy name? Then transliterate.\n"
        f"{_GLOSSARY_RULE_TRANSLATION}"
        f"11. {_player_gender_rule(gender)}"
        f"12. {_speech_style_rules(target_lang)}"
        "\nYour output MUST be strictly valid JSON. Do not use markdown code blocks.\n"
        "The JSON object must contain exactly ONE key:\n"
        '- "translation": The final translated text ONLY, perfectly formatted '
        "and ready to use in the game.\n\n"
        "Do not include any other keys, your thought process, explanations, or any "
        "markdown formatting outside the JSON object.\n"
    )
    variable = glossary_block.strip() if glossary_block and glossary_block.strip() else ""
    return stable, variable


def build_translation_system_prompt(
    target_lang: str,
    gender: str,
    glossary_block: str = "",
) -> str:
    """System prompt for line-by-line / batch translation (stable + variable concatenated)."""
    stable, variable = build_translation_system_prompt_parts(target_lang, gender, glossary_block)
    if variable:
        return f"{stable}\n\n{variable}"
    return stable


def build_dialog_system_prompt_parts(
    target_lang: str,
    gender: str,
    world_block: str,
    glossary_block: str = "",
) -> Tuple[str, str]:
    """Return ``(stable, variable)`` halves of the contextual dialog system prompt.

    ``world_block`` is treated as stable (it is built once per run from the
    scanned module) and is embedded in the stable half alongside the rules
    and the output-format example. The glossary entries are placed in the
    variable half so that glossary-filtering optimisations (Phase 2) do not
    invalidate the cached prefix.
    """
    ex = get_examples(target_lang)
    dialog_output = ex["dialog_output"]
    output_example = json.dumps(dialog_output, ensure_ascii=False, indent=2)

    stable = (
        "You are an elite translator for the game Neverwinter Nights.\n"
        f"Your task is to translate entire dialogue scripts to {target_lang} "
        "according to Nora Gal's Golden School of Translation.\n\n"
        f"{world_block}\n\n"
        "RULES:\n"
        "1. You will receive a dialogue script. Each line to translate is marked "
        "with an ID like [E0] or [R1], inside <<< >>>.\n"
        "2. Translate ONLY the text inside <<< >>>. Do NOT translate the routing "
        "hints (like '-> Player Reply').\n"
        "3. Use the WORLD CONTEXT to understand who is speaking to whom, ensuring "
        "gender and rank appropriate phrasing.\n"
        f"4. {_player_gender_rule(gender)}"
        f"{_GLOSSARY_RULE_DIALOG}"
        "6. MANDATORY TAG/TOKEN PRESERVATION:\n"
        "   - Preserve all special tokens exactly as they are (e.g., <<TOKEN_0>>).\n"
        "   - Preserve inline NWN tags exactly as in source (e.g. <StartAction>, </Start>).\n"
        "   - Never output helper placeholders like [[NWN_TAG_*]].\n"
        "   - Do not reorder, duplicate, or delete any tag/token.\n"
        "7. Maintain natural phrasing, emotion, and tone.\n"
        f"8. {_proper_names_rules(target_lang)}"
        f"9. {_speech_style_rules(target_lang)}"
        "   Normal-INT dialog lines in the SAME script must stay grammatically correct.\n\n"
        "OUTPUT FORMAT:\n"
        "You MUST return a perfectly valid JSON object mapping the node ID to its translation.\n"
        "Example:\n"
        f"{output_example}\n\n"
        "Do NOT include any markdown code blocks outside the JSON."
    )
    variable = glossary_block.strip() if glossary_block and glossary_block.strip() else ""
    return stable, variable


def build_dialog_system_prompt(
    target_lang: str,
    gender: str,
    world_block: str,
    glossary_block: str = "",
) -> str:
    """System prompt for contextual whole-dialog translation (concatenated)."""
    stable, variable = build_dialog_system_prompt_parts(
        target_lang, gender, world_block, glossary_block
    )
    if variable:
        return f"{stable}\n\n{variable}"
    return stable


def build_entity_extraction_system_prompt(source_lang: str = "English") -> str:
    """System prompt for extracting proper nouns from game texts.

    Used by :class:`~nwn_translator.context.entity_extractor.EntityExtractor`
    to find character/location/organization names that are embedded in
    dialogs, descriptions, and sign text but don't appear as standalone
    GFF fields (and are therefore missed by WorldScanner).

    Args:
        source_lang: The language of the texts being analyzed. Names must be
            returned in this language exactly as they appear in the source.
    """
    return (
        f"You are analyzing {source_lang} texts from a Neverwinter Nights game module.\n"
        "Extract ALL proper nouns and recurring nicknames you find: character names, "
        "place names, organization names, unique named objects, and recurring epithets "
        "or hyphenated terms used as forms of address (e.g. compound nicknames that "
        "characters use to refer to someone).\n\n"
        'Return a single JSON object with one key "entities" whose value is '
        'an array of entity objects with "name" and "type" fields.\n'
        'Valid types: "character", "location", "organization", "item", '
        '"nickname", "unknown".\n\n'
        "FEW-SHOT EXAMPLES (note: these illustrate the task; real inputs will be "
        f"in {source_lang}):\n\n"
        "Input:\n"
        '[0] "Leading a coach to Stout Village with farming equipment to deliver."\n'
        '[1] "Gotta hand-carry some letters to the PassGate from the castle."\n'
        '[2] "Hello! I can take you back to Penultima City, if you\'d like to leave."\n'
        "[3] \"I saw your ad posted by the Guild of Middlemen. You're looking for "
        'adventurer(s), yes?"\n'
        '[4] "Hello! I\'m the Magical Plot Fairy. Do you need a recap?"\n'
        '[5] "Contact R. Freely in Stout Village for details."\n'
        '[6] "Stay back, staff-one!"\n'
        '[7] "Must.. protect... staff-one... ghh"\n\n'
        "Output:\n"
        '{"entities": [\n'
        '  {"name": "Stout Village", "type": "location"},\n'
        '  {"name": "PassGate", "type": "location"},\n'
        '  {"name": "Penultima City", "type": "location"},\n'
        '  {"name": "Guild of Middlemen", "type": "organization"},\n'
        '  {"name": "Magical Plot Fairy", "type": "character"},\n'
        '  {"name": "R. Freely", "type": "character"},\n'
        '  {"name": "staff-one", "type": "nickname"}\n'
        "]}\n\n"
        "Rules:\n"
        "- Include proper nouns that are names of specific characters, places, "
        "organizations, or unique objects.\n"
        "- Include recurring compound nicknames or hyphenated terms used as forms "
        'of address (type: "nickname"). These must be translated consistently.\n'
        "- Do NOT include common game terms (sword, goblin, mine, chest, potion, etc.).\n"
        "- Do NOT include race or class names (dwarf, elf, wizard, halfling, etc.).\n"
        "- Do NOT include common words, adjectives, or generic phrases.\n"
        '- If uncertain about the category, use "unknown".\n'
        "- Each name should appear only once in your output (deduplicate across all input lines).\n"
        "- Preserve original spelling exactly as it appears in the text.\n"
        '- Return {"entities": []} if no proper nouns are found.\n'
        "Do not use markdown code fences."
    )


def build_glossary_system_prompt(target_lang: str) -> str:
    """System prompt for glossary proper-name translation."""
    ex = get_examples(target_lang)
    personal = ex["glossary_personal"]
    descriptive = ex["glossary_descriptive"]

    pers_ex = ", ".join(f'"{eng}" -> "{tr}"' for eng, tr in personal)
    desc_ex = ", ".join(f'"{eng}" -> "{good}" (NOT "{bad}")' for eng, good, bad in descriptive)

    return (
        f"You are preparing a translation glossary for the game Neverwinter Nights.\n"
        f"Target language: {target_lang}.\n\n"
        "Translate each proper name below into the target language.\n\n"
        "KEY RULES \u2014 translating vs transliterating:\n"
        "- Personal names (character first/last names, unique fantasy names): "
        "TRANSLITERATE into target-language script.\n"
        f"  Examples: {pers_ex}\n"
        "- Descriptive/meaningful names (locations, items, quests, titles composed of "
        "real English words with clear meaning): TRANSLATE the meaning. "
        "NEVER produce phonetic transliteration of English words.\n"
        f"  Examples: {desc_ex}\n"
        "- When in doubt: if the name consists of ordinary English words, translate the meaning. "
        "If it is a made-up fantasy word, transliterate.\n\n"
        "Return each value in nominative (dictionary) form only; "
        "the game will inflect in context later.\n\n"
        "OUTPUT: A single JSON object whose keys are the EXACT English name "
        "(WITHOUT the category hint in parentheses) and values are the translations.\n"
        'Example: the list entry "- Perin Izrick (character)" '
        'must produce key "Perin Izrick", NOT "Perin Izrick (character)".\n'
        "Do not omit keys. Do not add keys not in the list.\n"
        "Do not use markdown code fences."
    )
