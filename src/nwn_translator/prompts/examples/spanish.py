"""Spanish prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Posada de la Lanza", "Inn of de Lans"),
        ("Deadman's Marsh", "Pantano del Muerto", "Dedmans Marsh"),
        ("Dark Ranger", "Guardabosques Oscuro", "Dark Rányer"),
        ("Horde Raven", "Cuervo de la Horda", "HordRéiven"),
        ("Fearling", "Temorrín", "Firlin"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "¡Yo no querer tú aquí! ¡Fuera!",
            "No quiero que estés aquí",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Yo <FullName>. Yo también gran héroe.",
            "Soy <FullName>. También soy un gran aventurero.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Tú gordo mentiroso. Yo no seguir tú.",
            "Eres un mentiroso. No te seguiré.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "¡Ja ja! Yo no arrastrar. ¡Yo aquí señalar y reír!",
            "No me arrastro. Estoy aquí para señalarte y reírme.",
        ),
    ],
    "speech_low_int_pattern": (
        "In Spanish, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'yo no querer' instead of 'yo no quiero'), dropping articles, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Saludos, viajero.",
        "R1": "Hola.",
        "E2": "¿Qué necesitas?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Posada de la Lanza", "Inn of de Lans"),
        ("Deadman's Marsh", "Pantano del Muerto", "Dedmans Marsh"),
        ("Dark Ranger", "Guardabosques Oscuro", "Dark Rányer"),
        ("Horde Raven", "Cuervo de la Horda", "HordRéiven"),
        ("Fearling", "Temorrín", "Firlin"),
    ],
}
