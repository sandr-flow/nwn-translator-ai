"""Portuguese prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Estalagem da Lança", "Inn of de Lens"),
        ("Deadman's Marsh", "Pântano do Morto", "Dedmens Marsh"),
        ("Dark Ranger", "Patrulheiro Sombrio", "Dark Rêinjer"),
        ("Horde Raven", "Corvo da Horda", "HordRêiven"),
        ("Fearling", "Temor", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Eu não querer tu aqui! Fora!",
            "Não quero que estejas aqui",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Eu <FullName>. Eu também grande herói.",
            "Eu sou <FullName>. Também sou um grande aventureiro.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Tu gordo mentiroso. Eu não seguir tu.",
            "Tu és um mentiroso. Não te vou seguir.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Eu não rastejar. Eu aqui apontar e rir!",
            "Não rastejo. Estou aqui para apontar para vocês e rir!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Portuguese, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'eu não querer' instead of 'eu não quero'), dropping prepositions and articles, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Saudações, viajante.",
        "R1": "Olá.",
        "E2": "Do que precisais?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Estalagem da Lança", "Inn of de Lens"),
        ("Deadman's Marsh", "Pântano do Morto", "Dedmens Marsh"),
        ("Dark Ranger", "Patrulheiro Sombrio", "Dark Rêinjer"),
        ("Horde Raven", "Corvo da Horda", "HordRêiven"),
        ("Fearling", "Temor", "Firling"),
    ],
}
