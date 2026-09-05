"""Source context must not become proof about a bytecode occurrence."""

import pytest

from nwn_translator.extractors.nss_index import classify_engine_arg, snippet_for_text
from tests.test_ncs_context import _extract
from tests.test_ncs import _consts, _consti, _consto, _action, _retn


class TestEngineArgTable:
    def test_player_positions(self):
        assert classify_engine_arg("SpeakString", 0) == "player"
        assert classify_engine_arg("SendMessageToPC", 1) == "player"
        assert classify_engine_arg("FloatingTextStringOnCreature", 0) == "player"

    def test_internal_positions(self):
        assert classify_engine_arg("PrintString", 0) == "internal"
        assert classify_engine_arg("GetObjectByTag", 0) == "internal"
        assert classify_engine_arg("SpeakOneLinerConversation", 0) == "internal"

    def test_local_var_families_flag_var_name_only(self):
        assert classify_engine_arg("SetLocalString", 1) == "internal"
        assert classify_engine_arg("GetLocalInt", 1) == "internal"
        assert classify_engine_arg("DeleteLocalObject", 1) == "internal"
        # The *value* argument of SetLocalString may be spoken later.
        assert classify_engine_arg("SetLocalString", 2) is None

    def test_campaign_family_flags_db_and_var_names(self):
        assert classify_engine_arg("SetCampaignInt", 0) == "internal"
        assert classify_engine_arg("SetCampaignInt", 1) == "internal"
        assert classify_engine_arg("SetCampaignString", 2) is None

    def test_unknown_function(self):
        assert classify_engine_arg("MyCustomThing", 0) is None


def test_snippet_found_and_missing():
    source = 'line one\nSpeakString("Hello!");\nline three'
    assert "SpeakString" in snippet_for_text("Hello!", source)
    assert snippet_for_text("absent", source) is None


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
    result = _extract(tmp_path, _consti(0), _consts("A shared phrase."), _action(221, 2), _retn())
    assert len(result.items) == 1
    assert result.items[0].metadata["proven_player"] is True
    assert result.items[0].metadata["nss_snippet"] is None


def test_matching_source_is_context_not_proof(tmp_path):
    path = tmp_path / "scene.nss"
    path.write_text('void main() { SpeakString("Farewell, my friend."); }')
    result = _extract(tmp_path, _consts("Farewell, my friend."), _retn())
    assert len(result.items) == 1
    meta = result.items[0].metadata
    assert meta["proven_player"] is False
    assert meta["needs_llm_gate"] is True
    assert "SpeakString" in meta["nss_snippet"]
    # Re-extraction after editing sources must not reuse stale cached evidence.
    path.write_text("void main() {}")
    without_source = _extract(tmp_path, _consts("Farewell, my friend."), _retn())
    assert [item.text for item in without_source.items] == ["Farewell, my friend."]
    assert without_source.items[0].metadata["nss_snippet"] is None
    assert without_source.items[0].metadata["proven_player"] is False


def test_stale_source_does_not_override_internal_bytecode(tmp_path):
    (tmp_path / "scene.nss").write_text('void main() { SpeakString("A shared phrase."); }')
    assert (
        _extract(tmp_path, _consts("A shared phrase."), _consto(), _action(51, 2), _retn()).items
        == []
    )
