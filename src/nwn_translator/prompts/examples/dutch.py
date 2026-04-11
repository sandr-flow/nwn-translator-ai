"""Dutch prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Herberg van de Lans", "Inn of de Lens"),
        ("Deadman's Marsh", "Moeras van de Dode", "Dedmens Marsj"),
        ("Dark Ranger", "Donkere Woudloper", "Dark Reindzjer"),
        ("Horde Raven", "Raaf van de Horde", "HordReiven"),
        ("Fearling", "Schriksel", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Ik niet willen jij hier! Wegwezen!",
            "Ik wil niet dat je hier bent",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Ik <FullName>. Ik ook grote held.",
            "Ik ben <FullName>. Ik ben ook een groot avonturier.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Jij dikke leugenaar. Ik niet volgen jou.",
            "Je bent een leugenaar. Ik ga je niet volgen.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Ik niet kruipen. Ik hier wijzen en lachen!",
            "Ik kruip niet. Ik ben hier om naar jullie te wijzen en te lachen!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Dutch, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'ik niet willen' instead of 'ik wil niet'), dropping articles, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Gegroet, reiziger.",
        "R1": "Hallo.",
        "E2": "Wat heb je nodig?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Herberg van de Lans", "Inn of de Lens"),
        ("Deadman's Marsh", "Moeras van de Dode", "Dedmens Marsj"),
        ("Dark Ranger", "Donkere Woudloper", "Dark Reindzjer"),
        ("Horde Raven", "Raaf van de Horde", "HordReiven"),
        ("Fearling", "Schriksel", "Firling"),
    ],
}
