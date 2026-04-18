"""Tests for language-specific prompt generation.

Verifies that each supported target language:
- Has its own examples module
- Produces prompts with examples in the correct language (no Russian leakage)
- All three prompt builders work with every language
"""

import json
import pytest

from src.nwn_translator.prompts import (
    build_dialog_system_prompt,
    build_dialog_system_prompt_parts,
    build_glossary_system_prompt,
    build_translation_system_prompt,
    build_translation_system_prompt_parts,
)
from src.nwn_translator.prompts.examples import get_examples, _LANG_MODULE_MAP

ALL_LANGS = list(_LANG_MODULE_MAP.keys())
NON_RUSSIAN_LANGS = [lang for lang in ALL_LANGS if lang != "russian"]


class TestExamplesModules:
    """Every language has a well-formed examples module."""

    REQUIRED_KEYS = {
        "proper_names",
        "personal_names",
        "speech_low_int",
        "speech_low_int_pattern",
        "dialog_output",
        "glossary_personal",
        "glossary_descriptive",
    }

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_examples_loadable(self, lang: str):
        ex = get_examples(lang)
        assert isinstance(ex, dict)

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_examples_have_required_keys(self, lang: str):
        ex = get_examples(lang)
        missing = self.REQUIRED_KEYS - set(ex.keys())
        assert not missing, f"{lang} examples missing keys: {missing}"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_proper_names_non_empty(self, lang: str):
        ex = get_examples(lang)
        assert len(ex["proper_names"]) >= 3, f"{lang}: too few proper_names examples"
        for entry in ex["proper_names"]:
            assert len(entry) == 3, f"{lang}: proper_names entry should be (eng, good, bad)"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_personal_names_non_empty(self, lang: str):
        ex = get_examples(lang)
        assert len(ex["personal_names"]) >= 2
        for entry in ex["personal_names"]:
            assert len(entry) == 2, f"{lang}: personal_names entry should be (eng, translated)"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_speech_examples_non_empty(self, lang: str):
        ex = get_examples(lang)
        assert len(ex["speech_low_int"]) >= 3
        for entry in ex["speech_low_int"]:
            assert len(entry) == 3, f"{lang}: speech_low_int entry should be (eng, good, bad)"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_dialog_output_non_empty(self, lang: str):
        ex = get_examples(lang)
        assert isinstance(ex["dialog_output"], dict)
        assert len(ex["dialog_output"]) >= 2

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_glossary_examples_non_empty(self, lang: str):
        ex = get_examples(lang)
        assert len(ex["glossary_personal"]) >= 2
        assert len(ex["glossary_descriptive"]) >= 3

    def test_unknown_lang_falls_back_to_english(self):
        ex = get_examples("klingon")
        english = get_examples("english")
        assert ex is english


# Distinctive markers per language to verify the correct examples are loaded.
# Each marker is a substring that appears ONLY in that language's examples.
_LANG_MARKERS = {
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
    "turkish": "Mızrak Hanı",
}


class TestTranslationPrompt:
    """build_translation_system_prompt uses the right language examples."""

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_prompt_builds_without_error(self, lang: str):
        prompt = build_translation_system_prompt(lang, "male")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_prompt_mentions_target_lang(self, lang: str):
        prompt = build_translation_system_prompt(lang, "male")
        assert lang in prompt.lower()

    @pytest.mark.parametrize("lang,marker", list(_LANG_MARKERS.items()))
    def test_prompt_contains_own_marker(self, lang: str, marker: str):
        prompt = build_translation_system_prompt(lang, "male")
        assert marker in prompt, f"{lang} prompt missing its marker '{marker}'"

    @pytest.mark.parametrize("lang", NON_RUSSIAN_LANGS)
    def test_no_russian_leakage(self, lang: str):
        prompt = build_translation_system_prompt(lang, "male")
        # Check that Russian-specific examples don't appear
        assert "Таверна Копья" not in prompt, f"Russian example leaked into {lang} prompt"
        assert "Болото Мертвецов" not in prompt, f"Russian example leaked into {lang} prompt"
        assert "Перин Изрик" not in prompt, f"Russian example leaked into {lang} prompt"
        assert "Приветствую, путник" not in prompt, f"Russian example leaked into {lang} prompt"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_prompt_with_glossary(self, lang: str):
        glossary = "GLOSSARY:\n- Dark Ranger = Test Ranger\n"
        prompt = build_translation_system_prompt(lang, "male", glossary_block=glossary)
        assert "GLOSSARY" in prompt
        assert "Dark Ranger" in prompt

    @pytest.mark.parametrize("gender", ["male", "female"])
    def test_gender_in_prompt(self, gender: str):
        prompt = build_translation_system_prompt("polish", gender)
        assert gender in prompt


class TestDialogPrompt:
    """build_dialog_system_prompt uses the right language examples."""

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_dialog_prompt_builds(self, lang: str):
        prompt = build_dialog_system_prompt(lang, "male", "WORLD: test world")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    @pytest.mark.parametrize("lang,marker", list(_LANG_MARKERS.items()))
    def test_dialog_prompt_contains_own_marker(self, lang: str, marker: str):
        prompt = build_dialog_system_prompt(lang, "male", "WORLD: test")
        assert marker in prompt, f"{lang} dialog prompt missing its marker '{marker}'"

    @pytest.mark.parametrize("lang", NON_RUSSIAN_LANGS)
    def test_dialog_no_russian_leakage(self, lang: str):
        prompt = build_dialog_system_prompt(lang, "male", "WORLD: test")
        assert "Приветствую, путник" not in prompt, f"Russian dialog leaked into {lang}"

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_dialog_output_example_is_valid_json(self, lang: str):
        """The output example in the dialog prompt should be parseable JSON."""
        prompt = build_dialog_system_prompt(lang, "male", "WORLD: test")
        # The JSON example is between "Example:\n" and "\n\nDo NOT"
        ex_start = prompt.find("Example:\n")
        ex_end = prompt.find("\n\nDo NOT include", ex_start)
        assert ex_start != -1 and ex_end != -1
        json_str = prompt[ex_start + len("Example:\n") : ex_end].strip()
        parsed = json.loads(json_str)
        assert "E0" in parsed


class TestGlossaryPrompt:
    """build_glossary_system_prompt uses the right language examples."""

    @pytest.mark.parametrize("lang", ALL_LANGS)
    def test_glossary_prompt_builds(self, lang: str):
        prompt = build_glossary_system_prompt(lang)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    @pytest.mark.parametrize("lang,marker", list(_LANG_MARKERS.items()))
    def test_glossary_prompt_contains_own_marker(self, lang: str, marker: str):
        prompt = build_glossary_system_prompt(lang)
        assert marker in prompt, f"{lang} glossary prompt missing its marker '{marker}'"

    @pytest.mark.parametrize("lang", NON_RUSSIAN_LANGS)
    def test_glossary_no_russian_leakage(self, lang: str):
        prompt = build_glossary_system_prompt(lang)
        assert "Перин Изрик" not in prompt, f"Russian glossary leaked into {lang}"
        assert "Дрикси" not in prompt, f"Russian glossary leaked into {lang}"


class TestStableVariableSplit:
    """Stable/variable split is a prerequisite for provider-agnostic prompt caching."""

    def test_translation_stable_identical_across_glossary_content(self):
        """Changing only the glossary contents must not change the stable prefix."""
        stable_a, var_a = build_translation_system_prompt_parts(
            "russian", "male", glossary_block='GLOSSARY:\n* "Zephirax" -> Зефиракс'
        )
        stable_b, var_b = build_translation_system_prompt_parts(
            "russian",
            "male",
            glossary_block='GLOSSARY:\n* "Qartheel" -> Картил\n* "Vastwood" -> Просторолесье',
        )
        assert stable_a == stable_b, "stable prefix must be byte-identical between calls"
        assert var_a != var_b, "variable half should hold the glossary entries"
        assert "Zephirax" in var_a and "Qartheel" in var_b
        assert "Zephirax" not in stable_a and "Qartheel" not in stable_b

    def test_translation_stable_contains_glossary_usage_rules(self):
        """The instructions on HOW to use a glossary stay in the stable prefix."""
        stable, _ = build_translation_system_prompt_parts("russian", "male")
        assert "GLOSSARY USAGE" in stable

    def test_dialog_stable_includes_world_block(self):
        """World context is run-stable and must live in the cached prefix."""
        stable, var = build_dialog_system_prompt_parts(
            "russian",
            "male",
            "WORLD CONTEXT: NPCs...",
            glossary_block="GLOSSARY: Zephirax",
        )
        assert "WORLD CONTEXT: NPCs..." in stable
        assert "Zephirax" in var
        assert "Zephirax" not in stable

    def test_translation_empty_glossary_yields_empty_variable(self):
        stable, var = build_translation_system_prompt_parts("russian", "male", "")
        assert var == ""
        assert len(stable) > 500


class TestSystemMessageContent:
    """Verify the cache_control breakpoint emitted by make_system_message_content."""

    def _make_provider(self):
        from unittest.mock import patch

        with patch(
            "src.nwn_translator.ai_providers.base.BaseAIProvider._validate_api_key",
            lambda self: None,
        ):
            from src.nwn_translator.ai_providers.openrouter_provider import (
                OpenRouterProvider,
            )

            return OpenRouterProvider(api_key="sk-or-test")

    def test_content_parts_with_breakpoint_when_variable_present(self):
        from src.nwn_translator.ai_providers.base import BaseAIProvider

        content = BaseAIProvider.make_system_message_content("STABLE", "VARIABLE")
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["text"] == "STABLE"
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert content[1]["text"] == "VARIABLE"
        assert "cache_control" not in content[1]

    def test_string_when_variable_empty(self):
        from src.nwn_translator.ai_providers.base import BaseAIProvider

        content = BaseAIProvider.make_system_message_content("STABLE", "")
        assert isinstance(content, str)
        assert content == "STABLE"

    def test_stable_suffix_is_cached_side(self):
        from src.nwn_translator.ai_providers.base import BaseAIProvider

        content = BaseAIProvider.make_system_message_content(
            "STABLE", "VAR", stable_suffix="\nBATCH MODE: ..."
        )
        assert isinstance(content, list)
        assert content[0]["text"] == "STABLE\nBATCH MODE: ..."
        assert content[1]["text"] == "VAR"

    def test_env_flag_disables_breakpoint(self, monkeypatch):
        """When NWN_TRANSLATE_PROMPT_CACHE=0 the helper falls back to a plain string."""
        from src.nwn_translator.ai_providers import base as base_mod

        monkeypatch.setattr(base_mod, "PROMPT_CACHE_BREAKPOINTS_ENABLED", False)
        content = base_mod.BaseAIProvider.make_system_message_content("STABLE", "VAR")
        assert isinstance(content, str)
        assert content == "STABLE\n\nVAR"


class TestCrossLanguageIsolation:
    """No language's examples bleed into another language's prompt."""

    def test_all_markers_unique(self):
        """Each marker appears in exactly one language's prompt."""
        for lang, marker in _LANG_MARKERS.items():
            for other_lang in ALL_LANGS:
                if other_lang == lang:
                    continue
                other_prompt = build_translation_system_prompt(other_lang, "male")
                assert (
                    marker not in other_prompt
                ), f"Marker '{marker}' from {lang} found in {other_lang} prompt"
