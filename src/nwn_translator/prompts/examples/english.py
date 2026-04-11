"""English prompt examples (fallback / identity-translation reference)."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Inn of the Lance", "Inn of ze Lans"),
        ("Deadman's Marsh", "Deadman's Marsh", "Dedmans Marsh"),
        ("Dark Ranger", "Dark Ranger", "Darkranger"),
        ("Horde Raven", "Horde Raven", "HordeRaven"),
        ("Fearling", "Fearling", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Me no want you here no more",
            "I do not want you here anymore",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Me <FullName>. Me big adventurer too.",
            "I am <FullName>. I am also a great adventurer.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "You big fat liar. Me no follow you.",
            "You are a deceitful liar. I will not follow you.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "I do not crawl. I am here to point at you and laugh!",
        ),
    ],
    "speech_low_int_pattern": (
        "In English, low-INT speech uses 'me' instead of 'I', drops articles and auxiliary verbs, "
        "and simplifies grammar. Preserve these errors exactly."
    ),
    "dialog_output": {
        "E0": "Greetings, traveler.",
        "R1": "Hello.",
        "E2": "What do you need?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Inn of the Lance", "Inn of ze Lans"),
        ("Deadman's Marsh", "Deadman's Marsh", "Dedmans Marsh"),
        ("Dark Ranger", "Dark Ranger", "Darkranger"),
        ("Horde Raven", "Horde Raven", "HordeRaven"),
        ("Fearling", "Fearling", "Firling"),
    ],
}
