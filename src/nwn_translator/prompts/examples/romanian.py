"""Romanian prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Hanul Lăncii", "Inn of de Lens"),
        ("Deadman's Marsh", "Mlaștina Mortului", "Dedmens Marș"),
        ("Dark Ranger", "Pădurar Întunecat", "Dark Reindjer"),
        ("Horde Raven", "Corbul Hoardei", "HordReiven"),
        ("Fearling", "Spaimă", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Eu nu a vrea tu aici! Pleacă!",
            "Nu te vreau aici",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Eu <FullName>. Eu și mare erou.",
            "Eu sunt <FullName>. Sunt și eu un mare aventurier.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Tu gras mincinos. Eu nu a urma tu.",
            "Ești un mincinos. Nu te voi urma.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Eu nu a se târî. Eu aici a arăta și a râde!",
            "Nu mă târăsc. Sunt aici ca să arăt cu degetul și să râd!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Romanian, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'eu nu a vrea' instead of 'eu nu vreau'), dropping prepositions "
        "and articles, and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Salutări, călătorule.",
        "R1": "Bună.",
        "E2": "Ce-ți trebuie?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Hanul Lăncii", "Inn of de Lens"),
        ("Deadman's Marsh", "Mlaștina Mortului", "Dedmens Marș"),
        ("Dark Ranger", "Pădurar Întunecat", "Dark Reindjer"),
        ("Horde Raven", "Corbul Hoardei", "HordReiven"),
        ("Fearling", "Spaimă", "Firling"),
    ],
}
