"""Regression tests for the 0.1002 beta hotfixes."""

from __future__ import annotations

from pathlib import Path

import pytest

from nwn_translator.ai_providers.base import RateLimitError
from nwn_translator.ai_providers.openrouter_provider import (
    OpenRouterError,
    OpenRouterProvider,
    _extract_retry_after_seconds,
    _is_rate_or_budget_error,
    _wait_with_retry_after,
)
from nwn_translator.context.entity_extractor import _select_texts
from nwn_translator.context.world_context import NPCInfo, WorldContext
from nwn_translator.extractors.base import TranslatableItem
from nwn_translator.extractors.ncs_extractor import ncs_hard_veto_reason
from nwn_translator.glossary import GlossaryBuilder
from nwn_translator.prompts import (
    build_glossary_system_prompt,
    build_translation_system_prompt_parts,
)
from nwn_translator.translators.translation_manager import _unescape_literal_newlines
from nwn_translator.web.database import compact_stats_for_api


class TestCompactStatsForApi:
    def test_truncates_errors_and_keeps_total(self) -> None:
        stats = {
            "total_errors": 12,
            "errors": [f"e{i}" for i in range(12)],
            "metrics": {"requests": [{"id": 1}], "failed_requests": 0},
        }
        out = compact_stats_for_api(stats)
        assert out is not None
        assert out["total_errors"] == 12
        assert out["errors"] == [f"e{i}" for i in range(5)]
        assert "requests" not in out["metrics"]
        assert stats["errors"] == [f"e{i}" for i in range(12)]


class TestOpenRouterBudgetRetry:
    def test_402_budget_is_rate_limit(self) -> None:
        msg = "Error code: 402 - in_flight_budget_exhausted Retry-After: 120"
        assert _is_rate_or_budget_error(msg, Exception(msg))
        assert _extract_retry_after_seconds(Exception(msg)) == 120.0

    def test_map_402_raises_rate_limit_error(self) -> None:
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        provider.PROVIDER_LABEL = "OpenRouter"
        with pytest.raises(RateLimitError) as exc_info:
            provider._map_openrouter_exception(
                Exception("402 in_flight_budget_exhausted Retry-After: 120")
            )
        assert exc_info.value.retry_after_seconds == 120.0

    def test_map_other_errors_stay_openrouter(self) -> None:
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        provider.PROVIDER_LABEL = "OpenRouter"
        with pytest.raises(OpenRouterError):
            provider._map_openrouter_exception(Exception("permission denied"))

    def test_wait_floors_at_retry_after(self) -> None:
        class _Outcome:
            failed = True

            def exception(self):
                return RateLimitError("budget", retry_after_seconds=120)

        class _State:
            outcome = _Outcome()
            attempt_number = 1

        waited = _wait_with_retry_after(_State())
        assert waited >= 120.0


class TestNcsSpeakerMeta:
    def test_single_owner_includes_name_and_traits(self) -> None:
        ctx = WorldContext()
        npc = NPCInfo(
            tag="dawn01",
            first_name="Dawn",
            last_name="Ioza",
            description="",
            race="Human",
            gender="Female",
            conversation="dawnchat",
        )
        ctx.register_script_owner("kneeltozim", npc)
        hint = ctx.speaker_hint_for_script("kneeltozim")
        assert hint is not None
        assert "Dawn Ioza" in hint
        assert "Human" in hint
        assert "Female" in hint

    def test_shared_script_summarizes_race(self) -> None:
        ctx = WorldContext()
        for i in range(3):
            ctx.register_script_owner(
                "whineprotect",
                NPCInfo(
                    tag=f"gob{i}",
                    first_name="",
                    last_name="",
                    description="",
                    race="Goblin",
                    gender="Male",
                    conversation="",
                ),
            )
        hint = ctx.speaker_hint_for_script("whineprotect")
        assert hint is not None
        assert "Goblin" in hint
        assert "shared by 3" in hint
        assert "gob0" not in hint

    def test_enrich_appends_speaker_once(self) -> None:
        ctx = WorldContext()
        ctx.register_script_owner(
            "bark",
            NPCInfo("t", "Bob", "", "", "Human", "Male", ""),
        )
        item = TranslatableItem(
            text="Hi!",
            context="Script text shown to player via SpeakString in bark.ncs.",
            item_id="bark:off_10",
            location=str(Path("extract") / "bark.ncs"),
            metadata={"type": "ncs_string"},
        )
        ctx.enrich_ncs_item_context(item)
        assert "Speaker" in (item.context or "")
        assert "Bob" in (item.context or "")
        before = item.context
        ctx.enrich_ncs_item_context(item)
        assert item.context == before


class TestEntitySelectShortNcs:
    def test_short_proven_player_ncs_is_selected(self) -> None:
        items = [
            TranslatableItem(
                text="Stay back, staff-one!",
                context="ncs",
                metadata={"type": "ncs_string", "proven_player": True},
            ),
            TranslatableItem(
                text="short dlg",
                context="dlg",
                metadata={"type": "entry"},
            ),
        ]
        selected = _select_texts(items)
        assert "Stay back, staff-one!" in selected
        assert "short dlg" not in selected


class TestGlossaryPersonalPolicy:
    def test_glossary_prompt_prefers_character_translit(self) -> None:
        prompt = build_glossary_system_prompt("russian")
        assert "nickname" in prompt.lower()
        assert "Dawn" in prompt
        assert "Thrall" in prompt
        assert "sword-one" in prompt
        assert "ты с мечом" in prompt
        assert "меч-один" in prompt
        assert "Сворд-уан" in prompt
        assert "staff-one" not in prompt
        assert "transliterate as a form of address" not in prompt.lower()
        assert "Nicknames built from ordinary English words translate as an epithet" in prompt

    def test_translation_prompt_uses_nickname_fewshot(self) -> None:
        stable, _ = build_translation_system_prompt_parts("russian", "male")
        assert "sword-one" in stable
        assert "ты с мечом" in stable
        assert "staff-one" not in stable

    def test_format_line_includes_gender_and_firstname(self) -> None:
        ctx = WorldContext()
        ctx.npcs["g"] = NPCInfo("g", "Dawn", "Ioza", "", "Human", "Female", "dlg")
        line = GlossaryBuilder._format_glossary_name_line("Dawn", "character", ctx)
        assert "Female" in line or "female" in line.lower()
        assert "FirstName" in line

    def test_seed_character_name_parts(self) -> None:
        entries = {"Dawn Ioza": "Доун Иоза"}
        GlossaryBuilder._seed_character_name_parts(entries, {"Dawn Ioza": "character"})
        assert entries["Dawn"] == "Доун"
        assert entries["Ioza"] == "Иоза"

    def test_nickname_line_asks_for_meaning(self) -> None:
        line = GlossaryBuilder._format_glossary_name_line("sword-one", "nickname")
        assert "epithet" in line
        assert "translate meaning" in line


class TestAcceptFixes:
    def test_unescape_literal_newlines(self) -> None:
        original = "Mine\nStaff only"
        assert _unescape_literal_newlines(original, "Шахта\\nТолько") == "Шахта\nТолько"
        assert _unescape_literal_newlines("No break", "a\\nb") == "a\\nb"

    def test_restore_wrapping_quotes(self) -> None:
        assert (
            GlossaryBuilder._restore_wrapping_quotes('"Welcome!"', "Добро пожаловать!")
            == '"Добро пожаловать!"'
        )

    def test_ncs_fragment_veto(self) -> None:
        assert ncs_hard_veto_reason("You must wait ") == "sentence_fragment"
        assert ncs_hard_veto_reason(" hour(s) before resting.") == "sentence_fragment"
        assert ncs_hard_veto_reason("Welcome, adventurer!") is None
        assert ncs_hard_veto_reason("Welcome, adventurer! ") is None
