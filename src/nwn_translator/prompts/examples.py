"""Per-language few-shot examples of the translation, dialog and glossary prompts.

Every language translates the same English sources, so the table holds only
the target-language forms. :func:`get_examples` returns a language's examples
as the dict the prompt builders read:

- ``proper_names``: ``(english, good, bad)`` descriptive names, whose meaning
  must be translated
- ``personal_names``: ``(english, translated)`` character names, which are
  transliterated
- ``speech_low_int``: ``(english, good, bad)`` broken low-INT speech
- ``speech_low_int_pattern``: how low-INT speech looks in the target language
- ``dialog_output``: node id -> line, the dialog prompt's output example
- ``glossary_personal``: ``personal_names`` without ``Talias Allenthel``
- ``glossary_descriptive``: the ``proper_names`` examples
- ``glossary_nicknames`` (optional): ``(english, good, bad transliteration,
  bad numeral calque of "-one")``; languages without it use the English ones
- ``declension_note`` and ``speech_normal_counterexample`` (optional): extra
  rule text appended to the prompts
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

_PROPER_NAME_SOURCES = (
    "Inn of the Lance",
    "Deadman's Marsh",
    "Dark Ranger",
    "Horde Raven",
    "Fearling",
)
_PERSONAL_NAME_SOURCES = ("Perin Izrick", "Talias Allenthel", "Drixie", "Dawn", "Thrall")
_LOW_INT_SOURCES = (
    "Me no want you here no more",
    "Me <FullName>. Me big adventurer too.",
    "You big fat liar. Me no follow you.",
    "Ha ha! Me no crawl. Me here to point and laugh!",
)
_DIALOG_NODE_IDS = ("E0", "R1", "E2")
#: The glossary prompt shows the personal names without this one.
_NOT_IN_GLOSSARY = "Talias Allenthel"
_OPTIONAL_KEYS = ("glossary_nicknames", "declension_note", "speech_normal_counterexample")

#: Target-language forms, in the order of the English sources above:
#: ``(good, bad)`` pairs for proper names and low-INT speech, the translations
#: of the first personal names, and the dialog lines of ``E0``, ``R1``, ``E2``.
_FORMS: Dict[str, Dict[str, Any]] = {
    "russian": {
        "proper_names": (
            ("Таверна Копья", "Инн оф зэ Ланс"),
            ("Болото Мертвецов", "Дэдмэнз Марш"),
            ("Тёмный Рейнджер", "Дарк Рейнджер"),
            ("Стайный Ворон", "ХордРейвен"),
            ("Страхолик", "Фирлинг"),
        ),
        "personal_names": ("Перин Изрик", "Талиас Аллентел", "Дрикси", "Доун", "Тралл"),
        "speech_low_int": (
            ("Уходи отсюда! Я больше не хотеть тебя видеть!", "Мне не нужен ты тут"),
            (
                "Я <FullName>. Я тоже сильно большой герой.",
                "Я <FullName>. Я тоже великий искатель приключений.",
            ),
            ("Ты толстый врун. Я с тобой не пойти.", "Ты лживый обманщик. Я за тобой не пойду."),
            (
                "Ха-ха! Я не ползать. Я тут стоять, пальцем тыкать и смеяться!",
                "Я не ползаю. Я здесь, чтобы показывать на вас пальцем и смеяться!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Russian, the equivalent is using infinitives instead of conjugated verbs, "
            "dropping prepositions, and childlike sentence structure. Rarely use pronouns or use "
            "them incorrectly."
        ),
        "dialog_output": ("Приветствую, путник.", "Здравствуй.", "Что тебе нужно?"),
        "glossary_nicknames": (("sword-one", "ты с мечом", "Сворд-уан", "меч-один"),),
        "declension_note": (
            "DECLENSION OF FOREIGN NAMES in Russian:\n"
            "   - Foreign masculine names ending in a consonant usually decline (Перин → Перина, "
            "Перину).\n"
            "   - Foreign FEMININE names ending in a consonant (not -а/-я) are INDECLINABLE — "
            "keep the nominative form in all cases. Example: «У Кармен много друзей» (NOT «У "
            "Кармены»), «Передай привет Мишель» (NOT «Мишели»).\n"
            "   - If declining a foreign name would sound unnatural, rephrase the sentence "
            "instead.\n"
        ),
        "speech_normal_counterexample": (
            "IMPORTANT: broken speech applies ONLY when the original English text itself "
            "contains grammatical errors or primitive syntax. If the original English is "
            "grammatically correct (e.g. a notice, letter, sign, or literate character), the "
            "translation MUST also be grammatically correct.\n"
            '    Example: "this isn\'t worth 5 gold a day, we\'re out of here" -> "это не стоит 5 '
            'золотых в день, мы уходим отсюда" (GOOD, normal grammar preserved)\n'
            '    NOT: "оно не стоить 5 золотых в день, мы уходить" (BAD — original is literate, '
            "so broken translation is wrong)\n"
        ),
    },
    "english": {
        "proper_names": (
            ("Inn of the Lance", "Inn of ze Lans"),
            ("Deadman's Marsh", "Dedmans Marsh"),
            ("Dark Ranger", "Darkranger"),
            ("Horde Raven", "HordeRaven"),
            ("Fearling", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie", "Dawn", "Thrall"),
        "speech_low_int": (
            ("Me no want you here no more", "I do not want you here anymore"),
            (
                "Me <FullName>. Me big adventurer too.",
                "I am <FullName>. I am also a great adventurer.",
            ),
            (
                "You big fat liar. Me no follow you.",
                "You are a deceitful liar. I will not follow you.",
            ),
            (
                "Ha ha! Me no crawl. Me here to point and laugh!",
                "I do not crawl. I am here to point at you and laugh!",
            ),
        ),
        "speech_low_int_pattern": (
            "In English, low-INT speech uses 'me' instead of 'I', drops articles and auxiliary "
            "verbs, and simplifies grammar. Preserve these errors exactly."
        ),
        "dialog_output": ("Greetings, traveler.", "Hello.", "What do you need?"),
        "glossary_nicknames": (
            ("sword-one", "you with the sword", "Sword-uan", "sword number one"),
        ),
    },
    "ukrainian": {
        "proper_names": (
            ("Таверна Списа", "Інн оф зе Ланс"),
            ("Болото Мерця", "Дедменз Марш"),
            ("Темний Рейнджер", "Дарк Рейнджер"),
            ("Зграйний Ворон", "ХордРейвен"),
            ("Страхітник", "Фірлінг"),
        ),
        "personal_names": ("Перін Ізрік", "Таліас Аллентел", "Дріксі"),
        "speech_low_int": (
            ("Геть звідси! Я більше не хотіти тебе бачити!", "Мені не потрібен ти тут"),
            (
                "Я <FullName>. Я теж дуже великий герой.",
                "Я <FullName>. Я теж великий шукач пригод.",
            ),
            (
                "Ти товстий брехун. Я з тобою не піти.",
                "Ти підступний обманщик. Я за тобою не піду.",
            ),
            (
                "Ха-ха! Я не повзати. Я тут стояти, пальцем тикати і сміятися!",
                "Я не повзаю. Я тут, щоб показувати на вас пальцем і сміятися!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Ukrainian, the equivalent is using infinitives instead of conjugated verbs, "
            "dropping prepositions, and childlike sentence structure. Rarely use pronouns or use "
            "them incorrectly."
        ),
        "dialog_output": ("Вітаю, мандрівнику.", "Привіт.", "Що тобі потрібно?"),
    },
    "polish": {
        "proper_names": (
            ("Gospoda pod Lancą", "Inn of the Lance"),
            ("Bagno Umarłego", "Dedmens Marsz"),
            ("Mroczny Strażnik", "Dark Ranger"),
            ("Kruk Hordy", "HordRejwen"),
            ("Strachlik", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Ja nie chcieć ty tu więcej!", "Nie chcę cię tu widzieć"),
            (
                "Ja <FullName>. Ja też duży bohater.",
                "Jestem <FullName>. Jestem także wielkim poszukiwaczem przygód.",
            ),
            (
                "Ty gruby kłamca. Ja nie iść za tobą.",
                "Jesteś grubym kłamcą. Nie będę za tobą podążać.",
            ),
            (
                "Ha ha! Ja nie czołgać się. Ja tu stać i śmiać się!",
                "Nie czołgam się. Jestem tutaj, żeby na was wskazywać i się śmiać!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Polish, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'ja iść' instead of 'ja idę'), dropping prepositions, and using childlike, "
            "simplified sentence structure."
        ),
        "dialog_output": ("Witaj, wędrowcze.", "Cześć.", "Czego potrzebujesz?"),
    },
    "german": {
        "proper_names": (
            ("Gasthaus zur Lanze", "Inn of se Länns"),
            ("Sumpf des Toten", "Dedmäns Marsch"),
            ("Dunkler Waldläufer", "Dark Rändscher"),
            ("Hordenrabe", "HordRäjwen"),
            ("Angstling", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Ich nich wollen du hier! Geh weg!", "Ich möchte nicht, dass du hier bist"),
            (
                "Ich <FullName>. Ich auch großer Held.",
                "Ich bin <FullName>. Ich bin ebenfalls ein großer Abenteurer.",
            ),
            (
                "Du dicker Lügner. Ich nicht gehen mit dir.",
                "Du bist ein Lügner. Ich werde dir nicht folgen.",
            ),
            (
                "Ha ha! Ich nich kriechen. Ich hier stehen, zeigen und lachen!",
                "Ich krieche nicht. Ich bin hier, um auf euch zu zeigen und zu lachen!",
            ),
        ),
        "speech_low_int_pattern": (
            "In German, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'ich gehen' instead of 'ich gehe'), dropping articles, using 'nich' instead of "
            "'nicht', and primitive sentence structure."
        ),
        "dialog_output": ("Seid gegrüßt, Wanderer.", "Hallo.", "Was braucht Ihr?"),
    },
    "french": {
        "proper_names": (
            ("Auberge de la Lance", "Inn of ze Lance"),
            ("Marais du Mort", "Dedmans Marche"),
            ("Rôdeur des Ténèbres", "Dark Rangeur"),
            ("Corbeau de la Horde", "HordReivenn"),
            ("Effroyeur", "Firlingue"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Moi pas vouloir toi ici! Partir!", "Je ne veux plus que tu sois ici"),
            (
                "Moi <FullName>. Moi aussi grand héros.",
                "Je suis <FullName>. Je suis également un grand aventurier.",
            ),
            ("Toi gros menteur. Moi pas suivre toi.", "Tu es un menteur. Je ne te suivrai pas."),
            (
                "Ha ha! Moi pas ramper. Moi ici pour montrer doigt et rire!",
                "Je ne rampe pas. Je suis ici pour vous montrer du doigt et rire!",
            ),
        ),
        "speech_low_int_pattern": (
            "In French, the equivalent is using 'moi' instead of 'je', infinitives instead of "
            "conjugated verbs (e.g. 'moi pas vouloir' instead of 'je ne veux pas'), and dropping "
            "articles and prepositions."
        ),
        "dialog_output": ("Salutations, voyageur.", "Bonjour.", "Que vous faut-il?"),
    },
    "spanish": {
        "proper_names": (
            ("Posada de la Lanza", "Inn of de Lans"),
            ("Pantano del Muerto", "Dedmans Marsh"),
            ("Guardabosques Oscuro", "Dark Rányer"),
            ("Cuervo de la Horda", "HordRéiven"),
            ("Temorrín", "Firlin"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("¡Yo no querer tú aquí! ¡Fuera!", "No quiero que estés aquí"),
            (
                "Yo <FullName>. Yo también gran héroe.",
                "Soy <FullName>. También soy un gran aventurero.",
            ),
            ("Tú gordo mentiroso. Yo no seguir tú.", "Eres un mentiroso. No te seguiré."),
            (
                "¡Ja ja! Yo no arrastrar. ¡Yo aquí señalar y reír!",
                "No me arrastro. Estoy aquí para señalarte y reírme.",
            ),
        ),
        "speech_low_int_pattern": (
            "In Spanish, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'yo no querer' instead of 'yo no quiero'), dropping articles, and using childlike, "
            "simplified sentence structure."
        ),
        "dialog_output": ("Saludos, viajero.", "Hola.", "¿Qué necesitas?"),
    },
    "italian": {
        "proper_names": (
            ("Locanda della Lancia", "Inn of de Lens"),
            ("Palude del Morto", "Dedmens Marsh"),
            ("Ranger Oscuro", "Dark Rènger"),
            ("Corvo dell'Orda", "HordRèiven"),
            ("Spauracchio", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Io non volere te qui! Via!", "Non voglio che tu sia qui"),
            (
                "Io <FullName>. Io anche grande eroe.",
                "Sono <FullName>. Sono anche un grande avventuriero.",
            ),
            ("Tu grosso bugiardo. Io non seguire te.", "Sei un bugiardo. Non ti seguirò."),
            (
                "Ah ah! Io non strisciare. Io qui indicare e ridere!",
                "Non striscio. Sono qui per indicarvi e ridere!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Italian, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'io non volere' instead of 'io non voglio'), dropping articles, and using "
            "childlike, simplified sentence structure."
        ),
        "dialog_output": ("Salve, viaggiatore.", "Salve.", "Di cosa avete bisogno?"),
    },
    "portuguese": {
        "proper_names": (
            ("Estalagem da Lança", "Inn of de Lens"),
            ("Pântano do Morto", "Dedmens Marsh"),
            ("Patrulheiro Sombrio", "Dark Rêinjer"),
            ("Corvo da Horda", "HordRêiven"),
            ("Temor", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Eu não querer tu aqui! Fora!", "Não quero que estejas aqui"),
            (
                "Eu <FullName>. Eu também grande herói.",
                "Eu sou <FullName>. Também sou um grande aventureiro.",
            ),
            ("Tu gordo mentiroso. Eu não seguir tu.", "Tu és um mentiroso. Não te vou seguir."),
            (
                "Ha ha! Eu não rastejar. Eu aqui apontar e rir!",
                "Não rastejo. Estou aqui para apontar para vocês e rir!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Portuguese, the equivalent is using infinitives instead of conjugated verbs "
            "(e.g. 'eu não querer' instead of 'eu não quero'), dropping prepositions and "
            "articles, and using childlike, simplified sentence structure."
        ),
        "dialog_output": ("Saudações, viajante.", "Olá.", "Do que precisais?"),
    },
    "czech": {
        "proper_names": (
            ("Hostinec u Kopí", "Inn of de Lens"),
            ("Bažina mrtvého", "Dedmens Marš"),
            ("Temný hraničář", "Dark Rendžer"),
            ("Havran Hordy", "HordRejven"),
            ("Strašidélko", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Já nechtít ty tady! Pryč!", "Nechci, abys tu byl"),
            (
                "Já <FullName>. Já taky velký hrdina.",
                "Já jsem <FullName>. Jsem také velký dobrodruh.",
            ),
            ("Ty tlustý lhář. Já nejít za tebou.", "Jsi tlustý lhář. Nebudu tě následovat."),
            (
                "Ha ha! Já neplazit. Já tady ukazovat a smát se!",
                "Neplazím se. Jsem tady, abych na vás ukazoval a smál se!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Czech, the equivalent is using infinitives instead of conjugated verbs (e.g. 'já "
            "nechtít' instead of 'já nechci'), dropping prepositions, and using childlike, "
            "simplified sentence structure."
        ),
        "dialog_output": ("Buď zdráv, poutníku.", "Zdravím.", "Co potřebuješ?"),
    },
    "romanian": {
        "proper_names": (
            ("Hanul Lăncii", "Inn of de Lens"),
            ("Mlaștina Mortului", "Dedmens Marș"),
            ("Pădurar Întunecat", "Dark Reindjer"),
            ("Corbul Hoardei", "HordReiven"),
            ("Spaimă", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Eu nu a vrea tu aici! Pleacă!", "Nu te vreau aici"),
            (
                "Eu <FullName>. Eu și mare erou.",
                "Eu sunt <FullName>. Sunt și eu un mare aventurier.",
            ),
            ("Tu gras mincinos. Eu nu a urma tu.", "Ești un mincinos. Nu te voi urma."),
            (
                "Ha ha! Eu nu a se târî. Eu aici a arăta și a râde!",
                "Nu mă târăsc. Sunt aici ca să arăt cu degetul și să râd!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Romanian, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'eu nu a vrea' instead of 'eu nu vreau'), dropping prepositions and articles, and "
            "using childlike, simplified sentence structure."
        ),
        "dialog_output": ("Salutări, călătorule.", "Bună.", "Ce-ți trebuie?"),
    },
    "hungarian": {
        "proper_names": (
            ("Lándzsás Fogadó", "Inn of de Lénsz"),
            ("Halott Mocsara", "Dedmensz Márs"),
            ("Sötét Vadász", "Dark Réndzsör"),
            ("Horda Hollója", "HordRéjven"),
            ("Félemény", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Én nem akarni te itt! Menj el!", "Nem akarom, hogy itt legyél"),
            (
                "Én <FullName>. Én is nagy hős.",
                "Én vagyok <FullName>. Én is nagy kalandor vagyok.",
            ),
            ("Te kövér hazug. Én nem menni utánad.", "Te hazug vagy. Nem foglak követni."),
            (
                "Ha ha! Én nem mászni. Én itt mutogatni és nevetni!",
                "Nem mászom. Azért vagyok itt, hogy mutogassak és nevessek!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Hungarian, the equivalent is using infinitives instead of conjugated verbs (e.g. "
            "'én nem akarni' instead of 'én nem akarom'), dropping suffixes and postpositions, "
            "and using childlike, simplified sentence structure."
        ),
        "dialog_output": ("Üdvözöllek, vándor.", "Üdv.", "Mire van szükséged?"),
    },
    "dutch": {
        "proper_names": (
            ("Herberg van de Lans", "Inn of de Lens"),
            ("Moeras van de Dode", "Dedmens Marsj"),
            ("Donkere Woudloper", "Dark Reindzjer"),
            ("Raaf van de Horde", "HordReiven"),
            ("Schriksel", "Firling"),
        ),
        "personal_names": ("Perin Izrick", "Talias Allenthel", "Drixie"),
        "speech_low_int": (
            ("Ik niet willen jij hier! Wegwezen!", "Ik wil niet dat je hier bent"),
            (
                "Ik <FullName>. Ik ook grote held.",
                "Ik ben <FullName>. Ik ben ook een groot avonturier.",
            ),
            (
                "Jij dikke leugenaar. Ik niet volgen jou.",
                "Je bent een leugenaar. Ik ga je niet volgen.",
            ),
            (
                "Ha ha! Ik niet kruipen. Ik hier wijzen en lachen!",
                "Ik kruip niet. Ik ben hier om naar jullie te wijzen en te lachen!",
            ),
        ),
        "speech_low_int_pattern": (
            "In Dutch, the equivalent is using infinitives instead of conjugated verbs (e.g. 'ik "
            "niet willen' instead of 'ik wil niet'), dropping articles, and using childlike, "
            "simplified sentence structure."
        ),
        "dialog_output": ("Gegroet, reiziger.", "Hallo.", "Wat heb je nodig?"),
    },
}


def _expand(forms: Dict[str, Any]) -> Dict[str, Any]:
    """Pairs a language's forms with the English sources."""
    proper = [
        (src, good, bad) for src, (good, bad) in zip(_PROPER_NAME_SOURCES, forms["proper_names"])
    ]
    personal = list(zip(_PERSONAL_NAME_SOURCES, forms["personal_names"]))
    examples: Dict[str, Any] = {
        "proper_names": proper,
        "personal_names": personal,
        "speech_low_int": [
            (src, good, bad) for src, (good, bad) in zip(_LOW_INT_SOURCES, forms["speech_low_int"])
        ],
        "speech_low_int_pattern": forms["speech_low_int_pattern"],
        "dialog_output": dict(zip(_DIALOG_NODE_IDS, forms["dialog_output"])),
        "glossary_personal": [pair for pair in personal if pair[0] != _NOT_IN_GLOSSARY],
        "glossary_descriptive": list(proper),
    }
    for key in _OPTIONAL_KEYS:
        if key in forms:
            examples[key] = forms[key]
    return examples


_EXAMPLES: Dict[str, Dict[str, Any]] = {lang: _expand(forms) for lang, forms in _FORMS.items()}

#: Target languages with their own examples.
LANGUAGES: Tuple[str, ...] = tuple(_EXAMPLES)


def get_examples(target_lang: str) -> Dict[str, Any]:
    """Returns the examples of *target_lang*.

    Args:
        target_lang: Target language name (case and surrounding spaces ignored).

    Returns:
        The language's examples; the English ones for any other language.
    """
    return _EXAMPLES.get((target_lang or "").strip().lower(), _EXAMPLES["english"])
