"""Polish prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Gospoda pod Lancą", "Inn of the Lance"),
        ("Deadman's Marsh", "Bagno Umarłego", "Dedmens Marsz"),
        ("Dark Ranger", "Mroczny Strażnik", "Dark Ranger"),
        ("Horde Raven", "Kruk Hordy", "HordRejwen"),
        ("Fearling", "Strachlik", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Ja nie chcieć ty tu więcej!",
            "Nie chcę cię tu widzieć",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Ja <FullName>. Ja też duży bohater.",
            "Jestem <FullName>. Jestem także wielkim poszukiwaczem przygód.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Ty gruby kłamca. Ja nie iść za tobą.",
            "Jesteś grubym kłamcą. Nie będę za tobą podążać.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Ja nie czołgać się. Ja tu stać i śmiać się!",
            "Nie czołgam się. Jestem tutaj, żeby na was wskazywać i się śmiać!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Polish, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'ja iść' instead of 'ja idę'), dropping prepositions, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Witaj, wędrowcze.",
        "R1": "Cześć.",
        "E2": "Czego potrzebujesz?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Gospoda pod Lancą", "Inn of the Lance"),
        ("Deadman's Marsh", "Bagno Umarłego", "Dedmens Marsz"),
        ("Dark Ranger", "Mroczny Strażnik", "Dark Ranger"),
        ("Horde Raven", "Kruk Hordy", "HordRejwen"),
        ("Fearling", "Strachlik", "Firling"),
    ],
}
