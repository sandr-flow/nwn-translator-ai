"""Italian prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Locanda della Lancia", "Inn of de Lens"),
        ("Deadman's Marsh", "Palude del Morto", "Dedmens Marsh"),
        ("Dark Ranger", "Ranger Oscuro", "Dark Rènger"),
        ("Horde Raven", "Corvo dell'Orda", "HordRèiven"),
        ("Fearling", "Spauracchio", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Io non volere te qui! Via!",
            "Non voglio che tu sia qui",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Io <FullName>. Io anche grande eroe.",
            "Sono <FullName>. Sono anche un grande avventuriero.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Tu grosso bugiardo. Io non seguire te.",
            "Sei un bugiardo. Non ti seguirò.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ah ah! Io non strisciare. Io qui indicare e ridere!",
            "Non striscio. Sono qui per indicarvi e ridere!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Italian, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'io non volere' instead of 'io non voglio'), dropping articles, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Salve, viaggiatore.",
        "R1": "Salve.",
        "E2": "Di cosa avete bisogno?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Locanda della Lancia", "Inn of de Lens"),
        ("Deadman's Marsh", "Palude del Morto", "Dedmens Marsh"),
        ("Dark Ranger", "Ranger Oscuro", "Dark Rènger"),
        ("Horde Raven", "Corvo dell'Orda", "HordRèiven"),
        ("Fearling", "Spauracchio", "Firling"),
    ],
}
