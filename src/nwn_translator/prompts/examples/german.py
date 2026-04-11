"""German prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Gasthaus zur Lanze", "Inn of se Länns"),
        ("Deadman's Marsh", "Sumpf des Toten", "Dedmäns Marsch"),
        ("Dark Ranger", "Dunkler Waldläufer", "Dark Rändscher"),
        ("Horde Raven", "Hordenrabe", "HordRäjwen"),
        ("Fearling", "Angstling", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Ich nich wollen du hier! Geh weg!",
            "Ich möchte nicht, dass du hier bist",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Ich <FullName>. Ich auch großer Held.",
            "Ich bin <FullName>. Ich bin ebenfalls ein großer Abenteurer.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Du dicker Lügner. Ich nicht gehen mit dir.",
            "Du bist ein Lügner. Ich werde dir nicht folgen.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Ich nich kriechen. Ich hier stehen, zeigen und lachen!",
            "Ich krieche nicht. Ich bin hier, um auf euch zu zeigen und zu lachen!",
        ),
    ],
    "speech_low_int_pattern": (
        "In German, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'ich gehen' instead of 'ich gehe'), dropping articles, "
        "using 'nich' instead of 'nicht', and primitive sentence structure."
    ),
    "dialog_output": {
        "E0": "Seid gegrüßt, Wanderer.",
        "R1": "Hallo.",
        "E2": "Was braucht Ihr?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Gasthaus zur Lanze", "Inn of se Länns"),
        ("Deadman's Marsh", "Sumpf des Toten", "Dedmäns Marsch"),
        ("Dark Ranger", "Dunkler Waldläufer", "Dark Rändscher"),
        ("Horde Raven", "Hordenrabe", "HordRäjwen"),
        ("Fearling", "Angstling", "Firling"),
    ],
}
