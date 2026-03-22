"""Shared prompt building blocks for AI translation.

Centralises the translation rules that are common across line-by-line,
contextual dialog, and glossary system prompts so that updates only need
to happen in one place.
"""


def proper_names_rules(*, glossary_rule: str = "") -> str:
    """Rules for translating vs. transliterating proper names."""
    return (
        "PROPER NAMES — translating vs. transliterating:\n"
        "   a) Descriptive/meaningful names: TRANSLATE the meaning. "
        "NEVER produce phonetic transliterations of English words.\n"
        "      Examples:\n"
        '      - "Inn of the Lance" -> "Таверна Копья" (GOOD) — NOT "Инн оф зэ Ланс" (BAD)\n'
        '      - "Deadman\'s Marsh" -> "Болото Мертвецов" (GOOD) — NOT "Дэдмэнз Марш" (BAD)\n'
        '      - "Dark Ranger" -> "Тёмный Рейнджер" (GOOD) — NOT "Дарк Рейнджер" (BAD)\n'
        '      - "Horde Raven" -> "Стайный Ворон" (GOOD) — NOT "ХордРейвен" (BAD)\n'
        '      - "Fearling" -> "Страхолик" (GOOD) — NOT "Фирлинг" (BAD)\n'
        "   b) Personal names (first/last names of characters): TRANSLITERATE.\n"
        "      Examples:\n"
        '      - "Perin Izrick" -> "Перин Изрик"\n'
        '      - "Talias Allenthel" -> "Талиас Аллентел"\n'
        '      - "Drixie" -> "Дрикси"\n'
        "   When in doubt whether a name is descriptive or personal, check: does the name "
        "consist of ordinary English words with clear meaning? Then translate the meaning. "
        "Is it a made-up fantasy name? Then transliterate.\n"
        f"{glossary_rule}"
    )


def speech_style_rules(target_lang: str) -> str:
    """Rules for preserving speech register (low-INT characters, etc.)."""
    return (
        "PRESERVE SPEECH STYLE AND REGISTER. This is a role-playing game with characters "
        "of different intelligence and background. If the original text has broken grammar, "
        "primitive syntax, or childlike speech (low-INT characters, barbarians, goblins, etc.), "
        "you MUST reproduce an equally broken, primitive style in the translation. "
        'DO NOT "fix" or "correct" their speech — that would destroy the character.\n'
        "    Avoid relying exclusively on the stereotypical 'моя твоя не понимать' formula.\n"
        "    Instead, use short sentences, infinitives ('я бить'), crude vocabulary, "
        "    and missing prepositions or cases to make it sound organically primitive but literary.\n"
        f"    Examples (English low-INT -> {target_lang} low-INT equivalent):\n"
        '    - "Me no want you here no more" -> "Уходи отсюда! Я больше не хотеть тебя видеть!" '
        '(GOOD, broken) — NOT "Мне не нужен ты тут" (BAD, normalized)\n'
        '    - "Me <FullName>. Me big adventurer too." -> "Я <FullName>. Я тоже сильно большой герой." '
        '(GOOD) — NOT "Я <FullName>. Я тоже великий искатель приключений." (BAD)\n'
        '    - "You big fat liar. Me no follow you." -> "Ты толстый врун. Я с тобой не пойти." '
        '(GOOD) — NOT "Ты лживый обманщик. Я за тобой не пойду." (BAD)\n'
        '    - "Ha ha! Me no crawl. Me here to point and laugh!" -> '
        '"Ха-ха! Я не ползать. Я тут стоять, пальцем тыкать и смеяться!" '
        '(GOOD) — NOT "Я не ползаю. Я здесь, чтобы показывать на вас пальцем и смеяться!" (BAD)\n\n'
        "    Key pattern: in English, low-INT speech uses \"me\" instead of \"I\", drops articles/verbs, "
        "simplifies grammar. In Russian, the equivalent is using infinitives "
        "instead of conjugated verbs, dropping prepositions, and childlike sentence structure. "
        "Rarely use pronouns or use them incorrectly.\n"
    )


def player_gender_rule(gender: str) -> str:
    """One-liner for player character grammatical gender agreement."""
    agreement = "masculine" if gender == "male" else "feminine"
    return (
        f"PLAYER CHARACTER: The protagonist is {gender}. When the text addresses "
        f"or describes the player character, ALL grammatical forms (verbs, adjectives, "
        f"participles, pronouns) MUST agree with {agreement} gender.\n"
    )


def token_preservation_rule() -> str:
    """One-liner for preserving game tokens."""
    return "Do NOT translate or alter placeholders like <<TOKEN_0>>, <<TOKEN_1>>, etc.\n"


# ---------------------------------------------------------------------------
# Composite prompt builders
# ---------------------------------------------------------------------------

def build_translation_system_prompt(
    target_lang: str,
    gender: str,
    glossary_block: str = "",
) -> str:
    """System prompt for line-by-line / batch translation.

    Replaces ``BaseAIProvider._create_system_prompt()``.
    """
    glossary_header = ""
    glossary_rule = ""
    if glossary_block and glossary_block.strip():
        glossary_header = f"{glossary_block.strip()}\n\n"
        glossary_rule = (
            "10. GLOSSARY USAGE — the glossary is a consistency reference, NOT a substitution table:\n"
            "   a) Apply a glossary entry ONLY when the EXACT full proper name from the glossary "
            "appears in the source text as a capitalized name. Do NOT match partial or coincidental "
            "overlaps (e.g. 'dead Goblin Hunter' in narrative ≠ quest name 'Dead Hunter' in glossary).\n"
            "   b) ALWAYS decline/conjugate the glossary translation to fit the grammatical context "
            "(case, number, gender). Never paste the nominative form into oblique positions.\n"
            "   c) Translate ONLY what is present in the source text. If the source says 'Danda', "
            "output only 'Данда' — do NOT append a surname or title from the glossary entry "
            "'Danda Mudgrabber'.\n"
            "   d) If a proper name is not listed in the glossary, follow rules 7-9.\n"
        )

    return (
        f"You are an elite translator for the game Neverwinter Nights. "
        f"Your task is to translate the text to {target_lang} according to Nora Gal's Golden School of Translation.\n\n"
        f"{glossary_header}"
        f"RULES:\n"
        f"1. Never translate word-for-word. Focus on meaning, emotion, and tone.\n"
        f"2. Use natural syntax and vocabulary. Avoid bureaucratic language (Chancellery/Канцелярит).\n"
        f"3. Identify idioms and adapt them to natural equivalents in the target language.\n"
        f"4. Preserve all formatting, line breaks, and special characters.\n"
        f"5. {token_preservation_rule()}"
        f"6. The translated text MUST be grammatically correct, strictly preserving gender and case agreements "
        f"(согласование по родам и падежам). Exception: see rule 11 for intentionally broken speech.\n"
        f"7. {proper_names_rules(glossary_rule=glossary_rule)}"
        f"8. When translating a creature's first name or last name field, output ONLY "
        f"the translation of the given text. Do NOT add the other part of the name "
        f"from context — the game engine concatenates FirstName + LastName automatically.\n"
        f"9. When in doubt whether a name is descriptive or personal, check: does the name "
        f"consist of ordinary English words with clear meaning? Then translate the meaning. "
        f"Is it a made-up fantasy name? Then transliterate.\n"
        f"{glossary_rule}"
        f"11. {player_gender_rule(gender)}"
        f"12. {speech_style_rules(target_lang)}"
        f"\nYour output MUST be strictly valid JSON. Do not use markdown code blocks.\n"
        f"The JSON object must contain exactly ONE key:\n"
        f'- "translation": The final translated text ONLY, perfectly formatted and ready to use in the game.\n\n'
        f"Do not include any other keys, your thought process, explanations, or any markdown formatting outside the JSON object.\n"
    )


def build_dialog_system_prompt(
    target_lang: str,
    gender: str,
    world_block: str,
    glossary_block: str = "",
) -> str:
    """System prompt for contextual whole-dialog translation.

    Replaces ``ContextualTranslationManager._build_system_prompt()``.
    """
    glossary_section = ""
    if glossary_block and glossary_block.strip():
        glossary_section = glossary_block.strip() + "\n\n"

    return (
        f"You are an elite translator for the game Neverwinter Nights.\n"
        f"Your task is to translate entire dialogue scripts to {target_lang} "
        f"according to Nora Gal's Golden School of Translation.\n\n"
        f"{world_block}\n\n"
        f"{glossary_section}"
        f"RULES:\n"
        f"1. You will receive a dialogue script. Each line to translate is marked with an ID "
        f"like [E0] or [R1], inside <<< >>>.\n"
        f"2. Translate ONLY the text inside <<< >>>. Do NOT translate the routing hints "
        f"(like '-> Player Reply').\n"
        f"3. Use the WORLD CONTEXT to understand who is speaking to whom, ensuring gender "
        f"and rank appropriate phrasing.\n"
        f"4. {player_gender_rule(gender)}"
        f"5. GLOSSARY USAGE (if present) — the glossary is a consistency reference, NOT a "
        f"substitution table:\n"
        f"   - Apply ONLY when the EXACT full proper name appears in the source as a capitalized "
        f"name. Do NOT match partial or coincidental overlaps.\n"
        f"   - ALWAYS decline/conjugate to fit grammatical context (case, number, gender).\n"
        f"   - Translate ONLY what is in the source. If source says 'Danda', output only 'Данда' "
        f"— do NOT add surname/title from a longer glossary entry.\n"
        f"6. Preserve all special tokens exactly as they are (e.g., <<TOKEN_0>>).\n"
        f"7. Maintain natural phrasing, emotion, and tone.\n"
        f"8. {proper_names_rules()}"
        f"9. {speech_style_rules(target_lang)}"
        f"   Normal-INT dialog lines in the SAME script must stay grammatically correct.\n\n"
        f"OUTPUT FORMAT:\n"
        f"You MUST return a perfectly valid JSON object mapping the node ID to its translation.\n"
        f"Example:\n"
        f"{{\n"
        f'  "E0": "Приветствую, путник.",\n'
        f'  "R1": "Здравствуй.",\n'
        f'  "E2": "Что тебе нужно?"\n'
        f"}}\n\n"
        f"Do NOT include any markdown code blocks outside the JSON."
    )


def build_glossary_system_prompt(target_lang: str) -> str:
    """System prompt for glossary proper-name translation.

    Replaces ``GlossaryBuilder._build_system_prompt()``.
    """
    return (
        f"You are preparing a translation glossary for the game Neverwinter Nights.\n"
        f"Target language: {target_lang}.\n\n"
        "Translate each proper name below into the target language.\n\n"
        "KEY RULES — translating vs transliterating:\n"
        "- Personal names (character first/last names, unique fantasy names): "
        "TRANSLITERATE into target-language script.\n"
        '  Examples: "Perin Izrick" -> "Перин Изрик", "Drixie" -> "Дрикси"\n'
        "- Descriptive/meaningful names (locations, items, quests, titles composed of "
        "real English words with clear meaning): TRANSLATE the meaning. "
        "NEVER produce phonetic transliteration of English words.\n"
        '  Examples: "Inn of the Lance" -> "Таверна Копья" (NOT "Инн оф зэ Ланс"), '
        '"Deadman\'s Marsh" -> "Болото Мертвецов" (NOT "Дэдмэнз Марш"), '
        '"Dark Ranger" -> "Тёмный Рейнджер" (NOT "Дарк Рейнджер"), '
        '"Horde Raven" -> "Стайный Ворон" (NOT "ХордРейвен"), '
        '"Fearling" -> "Страхолик" (NOT "Фирлинг")\n'
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
