"""Ukrainian prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Таверна Списа", "Інн оф зе Ланс"),
        ("Deadman's Marsh", "Болото Мерця", "Дедменз Марш"),
        ("Dark Ranger", "Темний Рейнджер", "Дарк Рейнджер"),
        ("Horde Raven", "Зграйний Ворон", "ХордРейвен"),
        ("Fearling", "Страхітник", "Фірлінг"),
    ],
    "personal_names": [
        ("Perin Izrick", "Перін Ізрік"),
        ("Talias Allenthel", "Таліас Аллентел"),
        ("Drixie", "Дріксі"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Геть звідси! Я більше не хотіти тебе бачити!",
            "Мені не потрібен ти тут",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Я <FullName>. Я теж дуже великий герой.",
            "Я <FullName>. Я теж великий шукач пригод.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Ти товстий брехун. Я з тобою не піти.",
            "Ти підступний обманщик. Я за тобою не піду.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ха-ха! Я не повзати. Я тут стояти, пальцем тикати і сміятися!",
            "Я не повзаю. Я тут, щоб показувати на вас пальцем і сміятися!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Ukrainian, the equivalent is using infinitives "
        "instead of conjugated verbs, dropping prepositions, and childlike sentence structure. "
        "Rarely use pronouns or use them incorrectly."
    ),
    "dialog_output": {
        "E0": "Вітаю, мандрівнику.",
        "R1": "Привіт.",
        "E2": "Що тобі потрібно?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Перін Ізрік"),
        ("Drixie", "Дріксі"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Таверна Списа", "Інн оф зе Ланс"),
        ("Deadman's Marsh", "Болото Мерця", "Дедменз Марш"),
        ("Dark Ranger", "Темний Рейнджер", "Дарк Рейнджер"),
        ("Horde Raven", "Зграйний Ворон", "ХордРейвен"),
        ("Fearling", "Страхітник", "Фірлінг"),
    ],
}
