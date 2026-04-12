"""Russian prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Таверна Копья", "Инн оф зэ Ланс"),
        ("Deadman's Marsh", "Болото Мертвецов", "Дэдмэнз Марш"),
        ("Dark Ranger", "Тёмный Рейнджер", "Дарк Рейнджер"),
        ("Horde Raven", "Стайный Ворон", "ХордРейвен"),
        ("Fearling", "Страхолик", "Фирлинг"),
    ],
    "personal_names": [
        ("Perin Izrick", "Перин Изрик"),
        ("Talias Allenthel", "Талиас Аллентел"),
        ("Drixie", "Дрикси"),
    ],
    "declension_note": (
        "DECLENSION OF FOREIGN NAMES in Russian:\n"
        "   - Foreign masculine names ending in a consonant usually decline "
        "(Перин → Перина, Перину).\n"
        "   - Foreign FEMININE names ending in a consonant (not -а/-я) are "
        "INDECLINABLE — keep the nominative form in all cases. "
        "Example: «У Кармен много друзей» (NOT «У Кармены»), "
        "«Передай привет Мишель» (NOT «Мишели»).\n"
        "   - If declining a foreign name would sound unnatural, rephrase the "
        "sentence instead.\n"
    ),
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Уходи отсюда! Я больше не хотеть тебя видеть!",
            "Мне не нужен ты тут",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Я <FullName>. Я тоже сильно большой герой.",
            "Я <FullName>. Я тоже великий искатель приключений.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Ты толстый врун. Я с тобой не пойти.",
            "Ты лживый обманщик. Я за тобой не пойду.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ха-ха! Я не ползать. Я тут стоять, пальцем тыкать и смеяться!",
            "Я не ползаю. Я здесь, чтобы показывать на вас пальцем и смеяться!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Russian, the equivalent is using infinitives "
        "instead of conjugated verbs, dropping prepositions, and childlike sentence structure. "
        "Rarely use pronouns or use them incorrectly."
    ),
    "speech_normal_counterexample": (
        "IMPORTANT: broken speech applies ONLY when the original English text itself "
        "contains grammatical errors or primitive syntax. If the original English is "
        "grammatically correct (e.g. a notice, letter, sign, or literate character), "
        "the translation MUST also be grammatically correct.\n"
        "    Example: \"this isn't worth 5 gold a day, we're out of here\" "
        "-> \"это не стоит 5 золотых в день, мы уходим отсюда\" "
        "(GOOD, normal grammar preserved)\n"
        "    NOT: \"оно не стоить 5 золотых в день, мы уходить\" "
        "(BAD — original is literate, so broken translation is wrong)\n"
    ),
    "dialog_output": {
        "E0": "Приветствую, путник.",
        "R1": "Здравствуй.",
        "E2": "Что тебе нужно?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Перин Изрик"),
        ("Drixie", "Дрикси"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Таверна Копья", "Инн оф зэ Ланс"),
        ("Deadman's Marsh", "Болото Мертвецов", "Дэдмэнз Марш"),
        ("Dark Ranger", "Тёмный Рейнджер", "Дарк Рейнджер"),
        ("Horde Raven", "Стайный Ворон", "ХордРейвен"),
        ("Fearling", "Страхолик", "Фирлинг"),
    ],
}
