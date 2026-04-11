"""Turkish prompt examples."""

EXAMPLES = {
    "proper_names": [
        ("Inn of the Lance", "Mızrak Hanı", "Inn of dı Lens"),
        ("Deadman's Marsh", "Ölünün Bataklığı", "Dedmens Marş"),
        ("Dark Ranger", "Karanlık Korucu", "Dark Reyncır"),
        ("Horde Raven", "Ordu Kuzgunu", "HordReyven"),
        ("Fearling", "Korkuncuk", "Firling"),
    ],
    "personal_names": [
        ("Perin Izrick", "Perin Izrick"),
        ("Talias Allenthel", "Talias Allenthel"),
        ("Drixie", "Drixie"),
    ],
    "speech_low_int": [
        (
            "Me no want you here no more",
            "Ben seni burada istememek! Git!",
            "Seni burada istemiyorum",
        ),
        (
            "Me <FullName>. Me big adventurer too.",
            "Ben <FullName>. Ben de büyük kahraman.",
            "Ben <FullName>. Ben de büyük bir maceracıyım.",
        ),
        (
            "You big fat liar. Me no follow you.",
            "Sen şişko yalancı. Ben seni takip etmemek.",
            "Sen bir yalancısın. Seni takip etmeyeceğim.",
        ),
        (
            "Ha ha! Me no crawl. Me here to point and laugh!",
            "Ha ha! Ben sürünmemek. Ben burada göstermek ve gülmek!",
            "Sürünmüyorum. Sizi göstermek ve gülmek için buradayım!",
        ),
    ],
    "speech_low_int_pattern": (
        "In Turkish, the equivalent is using infinitives instead of conjugated verbs "
        "(e.g. 'ben istememek' instead of 'ben istemiyorum'), dropping suffixes, "
        "and using childlike, simplified sentence structure."
    ),
    "dialog_output": {
        "E0": "Selam, yolcu.",
        "R1": "Merhaba.",
        "E2": "Neye ihtiyacın var?",
    },
    "glossary_personal": [
        ("Perin Izrick", "Perin Izrick"),
        ("Drixie", "Drixie"),
    ],
    "glossary_descriptive": [
        ("Inn of the Lance", "Mızrak Hanı", "Inn of dı Lens"),
        ("Deadman's Marsh", "Ölünün Bataklığı", "Dedmens Marş"),
        ("Dark Ranger", "Karanlık Korucu", "Dark Reyncır"),
        ("Horde Raven", "Ordu Kuzgunu", "HordReyven"),
        ("Fearling", "Korkuncuk", "Firling"),
    ],
}
