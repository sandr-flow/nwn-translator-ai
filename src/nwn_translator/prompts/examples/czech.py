"""Czech prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Hostinec u Kopí", "Inn of de Lens"),
        ("Deadman's Marsh", "Bažina mrtvého", "Dedmens Marš"),
        ("Dark Ranger", "Temný hraničář", "Dark Rendžer"),
        ("Horde Raven", "Havran Hordy", "HordRejven"),
        ("Fearling", "Strašidélko", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Já nechtít ty tady! Pryč!",
            "Nechci, abys tu byl",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Já <FullName>. Já taky velký hrdina.",
            "Já jsem <FullName>. Jsem také velký dobrodruh.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Ty tlustý lhář. Já nejít za tebou.",
            "Jsi tlustý lhář. Nebudu tě následovat.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Já neplazit. Já tady ukazovat a smát se!",
            "Neplazím se. Jsem tady, abych na vás ukazoval a smál se!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Czech, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'já nechtít' instead of 'já nechci'), dropping prepositions, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Buď zdráv, poutníku.",
        "R1": "Zdravím.",
        "E2": "Co potřebuješ?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Hostinec u Kopí", "Inn of de Lens"),
        ("Deadman's Marsh", "Bažina mrtvého", "Dedmens Marš"),
        ("Dark Ranger", "Temný hraničář", "Dark Rendžer"),
        ("Horde Raven", "Havran Hordy", "HordRejven"),
        ("Fearling", "Strašidélko", "Firling"),
    ],
}
