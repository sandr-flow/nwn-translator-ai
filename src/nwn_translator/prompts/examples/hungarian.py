"""Hungarian prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Lándzsás Fogadó", "Inn of de Lénsz"),
        ("Deadman's Marsh", "Halott Mocsara", "Dedmensz Márs"),
        ("Dark Ranger", "Sötét Vadász", "Dark Réndzsör"),
        ("Horde Raven", "Horda Hollója", "HordRéjven"),
        ("Fearling", "Félemény", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Én nem akarni te itt! Menj el!",
            "Nem akarom, hogy itt legyél",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Én <FullName>. Én is nagy hős.",
            "Én vagyok <FullName>. Én is nagy kalandor vagyok.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Te kövér hazug. Én nem menni utánad.",
            "Te hazug vagy. Nem foglak követni.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Én nem mászni. Én itt mutogatni és nevetni!",
            "Nem mászom. Azért vagyok itt, hogy mutogassak és nevessek!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Hungarian, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'én nem akarni' instead of 'én nem akarom'), dropping suffixes "
        "and postpositions, and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Üdvözöllek, vándor.",
        "R1": "Üdv.",
        "E2": "Mire van szükséged?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Lándzsás Fogadó", "Inn of de Lénsz"),
        ("Deadman's Marsh", "Halott Mocsara", "Dedmensz Márs"),
        ("Dark Ranger", "Sötét Vadász", "Dark Réndzsör"),
        ("Horde Raven", "Horda Hollója", "HordRéjven"),
        ("Fearling", "Félemény", "Firling"),
    ],
}
