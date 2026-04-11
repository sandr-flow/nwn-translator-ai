"""French prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Auberge de la Lance", "Inn of ze Lance"),
        ("Deadman's Marsh", "Marais du Mort", "Dedmans Marche"),
        ("Dark Ranger", "Rôdeur des Ténèbres", "Dark Rangeur"),
        ("Horde Raven", "Corbeau de la Horde", "HordReivenn"),
        ("Fearling", "Effroyeur", "Firlingue"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Moi pas vouloir toi ici! Partir!",
            "Je ne veux plus que tu sois ici",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Moi <FullName>. Moi aussi grand héros.",
            "Je suis <FullName>. Je suis également un grand aventurier.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Toi gros menteur. Moi pas suivre toi.",
            "Tu es un menteur. Je ne te suivrai pas.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Moi pas ramper. Moi ici pour montrer doigt et rire!",
            "Je ne rampe pas. Je suis ici pour vous montrer du doigt et rire!",
        ),
    ],
    "speech_low_int_pattern": (
        "In French, the equivalent is using 'moi' instead of 'je', infinitives "
        "instead of conjugated verbs (e.g. 'moi pas vouloir' instead of 'je ne veux pas'), "
        "and dropping articles and prepositions."
    ),
    "dialog_output": {
        "E0": "Salutations, voyageur.",
        "R1": "Bonjour.",
        "E2": "Que vous faut-il?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Auberge de la Lance", "Inn of ze Lance"),
        ("Deadman's Marsh", "Marais du Mort", "Dedmans Marche"),
        ("Dark Ranger", "Rôdeur des Ténèbres", "Dark Rangeur"),
        ("Horde Raven", "Corbeau de la Horde", "HordReivenn"),
        ("Fearling", "Effroyeur", "Firlingue"),
    ],
}
