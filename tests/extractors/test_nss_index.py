"""Engine argument roles and NSS source context; source is never proof about bytecode."""

import pytest

from nwn_translator.extractors.nss_index import classify_engine_arg, snippet_with_position
from tests.support.ncs import action, consti, consto, consts, extract_script, retn


@pytest.mark.parametrize(
    "function, argument, role",
    [
        ("SpeakString", 0, "player"),
        ("SendMessageToPC", 1, "player"),
        ("FloatingTextStringOnCreature", 0, "player"),
        ("PrintString", 0, "internal"),
        ("GetObjectByTag", 0, "internal"),
        ("SpeakOneLinerConversation", 0, "internal"),
        # Local-variable families flag the variable name only; the value of
        # SetLocalString may be spoken later.
        ("SetLocalString", 1, "internal"),
        ("GetLocalInt", 1, "internal"),
        ("DeleteLocalObject", 1, "internal"),
        ("SetLocalString", 2, None),
        # Campaign families flag the database and variable names.
        ("SetCampaignInt", 0, "internal"),
        ("SetCampaignInt", 1, "internal"),
        ("SetCampaignString", 2, None),
        ("MyCustomThing", 0, None),
        # Unknown engine names are not classified by substring.
        ("GetModuleItemAcquiredBy", 0, None),
        ("PlaySpeakSoundByStrRef", 0, None),
        ("SpawnScriptDebugger", 0, None),
    ],
)
def test_engine_argument_roles(function, argument, role):
    assert classify_engine_arg(function, argument) == role


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_snippet_positions_recover_the_exact_capped_excerpt(newline):
    source = newline.join(["//" + "x" * 300] * 20 + ['SpeakString("Hello");'] + ["// tail"] * 8)
    text, start = snippet_with_position("Hello", source)
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    assert "SpeakString" in text
    assert normalized[start : start + len(text)] == text
    assert snippet_with_position("Missing", source) == (None, None)
    snippet, start = snippet_with_position("Hello!", 'line one\nSpeakString("Hello!");\nline 3')
    assert "SpeakString" in snippet and start == 0


@pytest.mark.parametrize(
    "source",
    [
        'void main() { SpeakString("A shared phrase."); }',
        'void main() { if (s == "A shared phrase.") return; }',
        'void main() { GetObjectByTag("A shared phrase."); }',
    ],
)
def test_other_scripts_neither_approve_nor_veto(tmp_path, source):
    (tmp_path / "other.nss").write_text(source)
    result = extract_script(tmp_path, consti(0), consts("A shared phrase."), action(221, 2), retn())
    (item,) = result.items
    assert item.metadata["proven_player"] is True
    assert item.metadata["nss_snippet"] is None


def test_matching_source_is_context_not_proof(tmp_path):
    path = tmp_path / "scene.nss"
    path.write_text('void main() { SpeakString("Farewell, my friend."); }')
    (item,) = extract_script(tmp_path, consts("Farewell, my friend."), retn()).items
    assert (item.metadata["proven_player"], item.metadata["needs_llm_gate"]) == (False, True)
    assert "SpeakString" in item.metadata["nss_snippet"]
    # Re-extraction after editing the sources does not reuse stale evidence.
    path.write_text("void main() {}")
    (item,) = extract_script(tmp_path, consts("Farewell, my friend."), retn()).items
    assert item.text == "Farewell, my friend."
    assert (item.metadata["nss_snippet"], item.metadata["proven_player"]) == (None, False)


def test_stale_source_does_not_override_internal_bytecode(tmp_path):
    (tmp_path / "scene.nss").write_text('void main() { SpeakString("A shared phrase."); }')
    parts = (consts("A shared phrase."), consto(), action(51, 2), retn())
    assert extract_script(tmp_path, *parts).items == []
