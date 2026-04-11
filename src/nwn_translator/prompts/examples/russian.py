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
