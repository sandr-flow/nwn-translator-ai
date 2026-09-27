"""What the system prompts say, per target language, content profile and prompt half.

``test_prompt_snapshots`` pins every prompt byte for byte; these tests state the
rules the prompts must keep when they are changed on purpose.
"""

import json

import pytest

from nwn_translator.prompts import (
    build_dialog_system_prompt_parts,
    build_entity_extraction_system_prompt,
    build_glossary_system_prompt,
    build_translation_system_prompt_parts,
)
from nwn_translator.prompts._builder import (
    CONTENT_PROFILE_DEFAULT,
    CONTENT_PROFILE_SCRIPT_MESSAGE,
    CONTENT_PROFILE_SHORT_LABEL,
    build_batch_user_prompt,
    build_single_user_prompt,
)
from nwn_translator.prompts.examples import LANGUAGES, get_examples

#: A phrase that appears only in the examples of its language.
MARKERS = {
    "russian": "Таверна Копья",
    "ukrainian": "Таверна Списа",
    "polish": "Gospoda pod",
    "german": "Gasthaus zur Lanze",
    "french": "Auberge de la Lance",
    "spanish": "Posada de la Lanza",
    "italian": "Locanda della Lancia",
    "portuguese": "Estalagem da Lança",
    "czech": "Hostinec u Kopí",
    "romanian": "Hanul Lăncii",
    "hungarian": "Lándzsás Fogadó",
    "dutch": "Herberg van de Lans",
}
RUSSIAN_EXAMPLES = ["Таверна Копья", "Болото Мертвецов", "Перин Изрик", "Приветствую, путник"]


def _stable(lang="russian", gender="male", **kwargs) -> str:
    return build_translation_system_prompt_parts(lang, gender, **kwargs)[0]


def _dialog(lang: str, world_block: str) -> str:
    """The dialog system prompt as sent: the stable half, then the variable half."""
    parts = build_dialog_system_prompt_parts(lang, "male", world_block)
    return "\n\n".join(part for part in parts if part)


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_every_language_has_its_own_examples_and_no_other(lang):
    examples = get_examples(lang)
    assert len(examples["proper_names"]) >= 3
    assert all(len(entry) == 3 for entry in examples["proper_names"])  # (eng, good, bad)
    assert len(examples["personal_names"]) >= 2
    assert all(len(entry) == 2 for entry in examples["personal_names"])  # (eng, translated)
    assert len(examples["speech_low_int"]) >= 3
    assert all(len(entry) == 3 for entry in examples["speech_low_int"])
    assert "speech_low_int_pattern" in examples
    assert isinstance(examples["dialog_output"], dict) and len(examples["dialog_output"]) >= 2
    assert len(examples["glossary_personal"]) >= 2
    assert len(examples["glossary_nicknames"]) >= 1

    glossary = "GLOSSARY:\n- Dark Ranger = Test Ranger\n"
    translation, variable = build_translation_system_prompt_parts(
        lang, "male", glossary_block=glossary
    )
    dialog = _dialog(lang, "WORLD: test")
    glossary_prompt = build_glossary_system_prompt(lang)
    assert lang in translation.lower()
    assert variable == glossary.strip()
    for prompt in (translation, dialog, glossary_prompt):
        assert len(prompt) > 100
        if lang in MARKERS:
            assert MARKERS[lang] in prompt
        for other, marker in MARKERS.items():
            if other != lang:
                assert marker not in prompt, f"{other} example leaked into {lang}"
        if lang != "russian":
            assert not any(example in prompt for example in RUSSIAN_EXAMPLES)
            assert "Дрикси" not in prompt
    # The output example of the dialog prompt is valid JSON.
    start = dialog.find("Example:\n")
    end = dialog.find("\n\nDo NOT include", start)
    assert start != -1 and end != -1
    assert "E0" in json.loads(dialog[start + len("Example:\n") : end].strip())


def test_unknown_language_falls_back_to_english_examples():
    assert get_examples("klingon") is get_examples("english")


@pytest.mark.parametrize("gender", ["male", "female"])
def test_player_gender_is_named(gender):
    assert gender in _stable("polish", gender)


def test_translation_output_rules():
    prompt = _stable("english")
    assert "Never return an empty translation" in prompt
    assert "escape line breaks as \\n" in prompt


def test_script_message_profile_keeps_code_untouched():
    stable, variable = build_translation_system_prompt_parts(
        "russian", "male", content_profile=CONTENT_PROFILE_SCRIPT_MESSAGE
    )
    assert variable == ""
    for rule in (
        "player-visible script messages",
        "Never translate, rename, or rewrite identifiers",
        "debug logs",
        "<VAR1>",
    ):
        assert rule in stable


def test_short_label_profile_drops_speech_and_gender_rules():
    default = _stable(content_profile=CONTENT_PROFILE_DEFAULT)
    short = _stable(content_profile=CONTENT_PROFILE_SHORT_LABEL)
    assert 1.0 - len(short) / len(default) >= 0.1
    for dropped in ("PRESERVE SPEECH STYLE", "low-INT", "PLAYER CHARACTER"):
        assert dropped not in short
    for kept in ("TAG/TOKEN PRESERVATION", "PROPER NAMES", "GLOSSARY USAGE"):
        assert kept in short
    # No profile and unknown profiles are the default one.
    assert build_translation_system_prompt_parts("russian", "male") == (
        build_translation_system_prompt_parts("russian", "male", content_profile="default")
    )
    assert _stable(content_profile="nonsense") == default


@pytest.mark.parametrize("profile", [CONTENT_PROFILE_DEFAULT, CONTENT_PROFILE_SHORT_LABEL])
def test_glossary_goes_only_into_the_variable_half(profile):
    """The stable prefix must be byte-identical across calls for prompt caching."""
    stable_a, var_a = build_translation_system_prompt_parts(
        "russian",
        "male",
        glossary_block='GLOSSARY:\n* "Zephirax" -> Зефиракс',
        content_profile=profile,
    )
    stable_b, var_b = build_translation_system_prompt_parts(
        "russian",
        "male",
        glossary_block='GLOSSARY:\n* "Qartheel" -> Картил\n* "Vastwood" -> Просторолесье',
        content_profile=profile,
    )
    assert stable_a == stable_b and len(stable_a) > 500
    assert var_a == 'GLOSSARY:\n* "Zephirax" -> Зефиракс'
    assert "Qartheel" in var_b
    assert "Zephirax" not in stable_a and "Qartheel" not in stable_b
    assert "GLOSSARY USAGE" in stable_a  # how to use a glossary stays stable
    _, empty = build_translation_system_prompt_parts("russian", "male", "", content_profile=profile)
    assert empty == ""


def test_dialog_world_block_is_in_the_variable_half():
    stable, variable = build_dialog_system_prompt_parts(
        "russian", "male", "WORLD CONTEXT: NPCs...", glossary_block="GLOSSARY: Zephirax"
    )
    assert "WORLD CONTEXT: NPCs..." in variable and "Zephirax" in variable
    assert "WORLD CONTEXT: NPCs..." not in stable and "Zephirax" not in stable


def test_dialog_prompt_rules():
    prompt = _dialog("english", "WORLD: test")
    for rule in (
        "<StartAction>",
        "<StartCheck>",
        "<StartHighlight>",
        "__NWN_TOKEN_ABC__",
        "END DIALOG",
        "Never return an empty translation",
    ):
        assert rule in prompt


@pytest.mark.parametrize(
    "profile",
    [CONTENT_PROFILE_DEFAULT, CONTENT_PROFILE_SHORT_LABEL, CONTENT_PROFILE_SCRIPT_MESSAGE],
)
def test_batch_prompt_has_one_output_contract(profile):
    batch = _stable(content_profile=profile, batch_mode=True)
    assert "exactly ONE key" not in batch
    assert '- "translation":' not in batch
    assert "flat JSON object" in batch
    assert "exactly ONE key" in _stable(content_profile=profile)


def test_batch_rules_follow_the_single_item_rules_on_the_cached_side():
    stable, variable = build_translation_system_prompt_parts(
        "russian", "male", "GLOSSARY: X", batch_mode=True
    )
    single = _stable(glossary_block="GLOSSARY: X")
    assert stable.startswith(single.split("The JSON object must contain", 1)[0])
    assert "\nBATCH MODE: Input items have numeric IDs." in stable
    assert stable.endswith("Do NOT wrap in markdown. Output ONLY the JSON object.\n")
    assert variable == "GLOSSARY: X"


def test_user_prompts():
    assert build_single_user_prompt("Hi", "english") == "Text to translate from english:\n\nHi"
    assert build_single_user_prompt("Hi", "english", "Greeting") == (
        "Context Hint: Greeting\n\nText to translate from english:\n\nHi"
    )
    assert build_batch_user_prompt("french", '{"0":"a"}') == (
        "Translate the items from french. Return only a flat object of numeric item IDs "
        'and translated strings. Shared groups are context only.\n\n{"0":"a"}'
    )


def test_entity_extraction_prompt_rejects_technical_candidates():
    prompt = build_entity_extraction_system_prompt("English")
    for rule in (
        "high-confidence proper nouns",
        "placeholders",
        "acronyms",
        "CamelCase identifiers",
        "snake_case identifiers",
        "ARCH_TARGET",
        "WILL_O_WISP",
        "BakersPlea",
        'Output: {"entities": []}',
        "Western Gate",
        "sword-one",
    ):
        assert rule in prompt
    assert "PassGate" not in prompt and "staff-one" not in prompt


def test_nicknames_translate_as_epithets():
    glossary = build_glossary_system_prompt("russian")
    for part in ("Dawn", "Thrall", "sword-one", "ты с мечом", "меч-один", "Сворд-уан"):
        assert part in glossary
    assert "nickname" in glossary.lower()
    assert "Nicknames built from ordinary English words translate as an epithet" in glossary
    assert "staff-one" not in glossary
    assert "transliterate as a form of address" not in glossary.lower()
    translation = _stable()
    assert "sword-one" in translation and "ты с мечом" in translation
    assert "staff-one" not in translation
