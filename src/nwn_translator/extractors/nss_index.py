"""Engine argument roles and optional source context for NCS selection.

NSS text is supporting evidence, never proof about a compiled occurrence:
sources can be stale and identical literals can have different consumers.
"""

from pathlib import Path
from typing import Optional, Set, Tuple

PLAYER_ARG_POSITIONS: Set[Tuple[str, int]] = {
    ("SpeakString", 0),
    ("ActionSpeakString", 0),
    ("SendMessageToPC", 1),
    ("FloatingTextStringOnCreature", 0),
    ("SetCustomToken", 1),
    ("SetName", 1),
    ("SetDescription", 1),
    ("PostString", 1),
    ("PopUpDeathGUIPanel", 4),
    ("SetKeyRequiredFeedback", 1),
    ("CreateArea", 2),
    ("CopyArea", 2),
}

INTERNAL_ARG_POSITIONS: Set[Tuple[str, int]] = {
    ("PrintString", 0),
    ("WriteTimestampedLogEntry", 0),
    ("SendMessageToAllDMs", 0),
    ("ExecuteScript", 0),
    ("GetItemPossessedBy", 1),
    ("CreateItemOnObject", 0),
    ("CreateItemOnObject", 3),
    ("PlaySound", 0),
    ("GetWaypointByTag", 0),
    ("GetObjectByTag", 0),
    ("GetNearestObjectByTag", 0),
    ("CreateObject", 1),
    ("CreateObject", 4),
    ("CopyObject", 3),
    ("SpeakOneLinerConversation", 0),  # dialog resref, not display text
    ("ActionStartConversation", 1),  # dialog resref
    ("BeginConversation", 0),
    ("StartNewModule", 0),
    ("SetTag", 1),
    ("SetListenPattern", 1),
    ("TestStringAgainstPattern", 0),
    ("TestStringAgainstPattern", 1),
    ("DestroyCampaignDatabase", 0),
    ("StoreCampaignObject", 0),
    ("StoreCampaignObject", 1),
    ("RetrieveCampaignObject", 0),
    ("RetrieveCampaignObject", 1),
    ("TagItemProperty", 1),
    ("TagEffect", 1),
    ("FindSubString", 1),
    ("Get2DAString", 0),
    ("Get2DAString", 1),
    ("EffectSummonCreature", 0),
    ("EffectAreaOfEffect", 1),
    ("EffectAreaOfEffect", 2),
    ("EffectAreaOfEffect", 3),
    ("EffectSwarm", 1),
    ("EffectSwarm", 2),
    ("EffectSwarm", 3),
    ("EffectSwarm", 4),
    ("SetAreaTransitionBMP", 1),
    ("ActivatePortal", 1),
    ("ActivatePortal", 2),
    ("ActivatePortal", 3),
    ("EndGame", 0),
    ("SetLockKeyTag", 1),
    ("SetTrapKeyTag", 1),
    ("SetPortraitResRef", 1),
    ("CreateArea", 0),
    ("CreateArea", 1),
    ("CopyArea", 1),
    ("SetEventScript", 2),
    ("PostString", 9),  # custom font name, not the message
    # Journal plot IDs must match the .jrl category tag verbatim; the display
    # text lives in the .jrl and is translated there.
    ("AddJournalQuestEntry", 0),
    ("RemoveJournalQuestEntry", 0),
    ("GetJournalQuestExperience", 0),
}

# Variable-name families: GetLocalInt(oObj, "VarName") and friends take the
# var name at position 1; campaign functions take db name + var name at 0, 1.
_LOCAL_VAR_PREFIXES = ("GetLocal", "SetLocal", "DeleteLocal")
_CAMPAIGN_PREFIXES = ("GetCampaign", "SetCampaign", "DeleteCampaign")


_NSS_SNIPPET_LINES = 20
_NSS_SNIPPET_CHAR_CAP = 2000


def classify_engine_arg(func: str, arg: int) -> Optional[str]:
    """Classify a string passed as argument *arg* of engine routine *func*.

    Args:
        func: Engine routine name.
        arg: Zero-based argument position.

    Returns:
        ``"player"`` (shown to the player), ``"internal"`` (tag, resref,
        variable name, …) or None when the argument is not a known consumer.
    """
    if (func, arg) in PLAYER_ARG_POSITIONS:
        return "player"
    if (func, arg) in INTERNAL_ARG_POSITIONS:
        return "internal"
    if arg == 1 and func.startswith(_LOCAL_VAR_PREFIXES):
        return "internal"
    if arg in (0, 1) and func.startswith(_CAMPAIGN_PREFIXES):
        return "internal"
    return None


def snippet_with_position(text: str, nss_content: str) -> tuple[Optional[str], Optional[int]]:
    """Return the source lines around the first quoted occurrence of *text*.

    The excerpt gives the model gate enough source context to decide whether
    a literal is player-facing: 20 lines on each side of the hit, trimmed to
    2000 characters centred on the hit line.

    Args:
        text: Literal text, searched as ``"text"`` verbatim.
        nss_content: Script source; line endings are normalized to LF.

    Returns:
        ``(excerpt, character offset of the excerpt in the normalized
        source)``, or ``(None, None)`` when the literal is not found.
    """
    nss_content = nss_content.replace("\r\n", "\n").replace("\r", "\n")
    needle = f'"{text}"'
    idx = nss_content.find(needle)
    if idx == -1:
        return None, None

    lines = nss_content.splitlines()
    running = 0
    hit_line = 0
    for i, line in enumerate(lines):
        next_running = running + len(line) + 1  # +1 for the newline
        if running <= idx < next_running:
            hit_line = i
            break
        running = next_running

    start = max(0, hit_line - _NSS_SNIPPET_LINES)
    end = min(len(lines), hit_line + _NSS_SNIPPET_LINES + 1)
    snippet = "\n".join(lines[start:end])
    position = sum(len(line) + 1 for line in lines[:start])
    if len(snippet) > _NSS_SNIPPET_CHAR_CAP:
        # Keep the window centred on the hit line when trimming.
        hit_local = hit_line - start
        hit_offset = sum(len(lines[start + i]) + 1 for i in range(hit_local))
        half = _NSS_SNIPPET_CHAR_CAP // 2
        cut_start = max(0, hit_offset - half)
        cut_end = min(len(snippet), cut_start + _NSS_SNIPPET_CHAR_CAP)
        snippet = snippet[cut_start:cut_end]
        position += cut_start
    return snippet, position


def read_script_source(file_path: Path, encoding: str) -> str:
    """Read the ``.nss`` source next to a compiled script.

    Only the matching script is read; context is never borrowed from another
    file.

    Args:
        file_path: Path of the ``.ncs`` resource.
        encoding: Code page of the source; undecodable bytes are replaced.

    Returns:
        The source text, or ``""`` when there is no readable source.
    """
    try:
        return file_path.with_suffix(".nss").read_bytes().decode(encoding, errors="replace")
    except OSError:
        return ""
