"""Bounded stack tracing of NCS string consumers.

Signatures are checked against NWN:EE's ``ovr/nwscript.nss``; argument roles
come from ``nss_index`` and the NWN Lexicon function reference. This is a
bounded value-flow analysis, not a VM: copies, branches and known calls are
tracked; unsupported instructions prevent proof. Any observed technical use
overrides display use. Inconclusive values still require the translation gate.
"""

import struct
from typing import Any, Dict, List, Set, Tuple

from .nss_index import classify_engine_arg
from ..file_handlers.ncs_parser import (
    NCSInstruction,
    OP_ADD,
    OP_CONST,
    OP_CPTOPBP,
    OP_CPTOPSP,
    OP_EQUAL,
    OP_NEQUAL,
    OP_NOP,
    OP_RSADD,
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_OBJECT,
    TYPE_STRING,
)

# Binary string/string qualifier; TYPE_STRING (0x05) is unary, used by CONSTS.
TYPE_STRING_STRING = 0x23

# Routine id -> (name, parameter types, return stack slots). Parameters are
# in declaration order. Each takes one 4-byte slot, except vector (v): three and stored action (a): zero.
# Only known signatures can be crossed, even for calls without string args.
# Sources: game nwscript.nss; https://nwnlexicon.com/<function name>.
ACTION_SIGNATURES: Dict[int, Tuple[str, str, int]] = {
    1: ("PrintString", "s", 0),
    3: ("FloatToString", "fii", 1),
    6: ("AssignCommand", "oa", 0),
    7: ("DelayCommand", "fa", 0),
    8: ("ExecuteScript", "so", 0),
    9: ("ClearAllActions", "io", 0),
    24: ("GetArea", "o", 1),
    25: ("GetEnteringObject", "", 1),
    28: ("GetFacing", "o", 1),
    30: ("GetItemPossessedBy", "os", 1),
    31: ("CreateItemOnObject", "sois", 1),
    33: ("ActionUnequipItem", "o", 0),
    39: ("ActionSpeakString", "si", 0),
    40: ("ActionPlayAnimation", "iff", 0),
    42: ("GetIsObjectValid", "o", 1),
    45: ("SetCameraFacing", "fffi", 0),
    46: ("PlaySound", "s", 0),
    51: ("GetLocalInt", "os", 1),
    52: ("GetLocalFloat", "os", 1),
    53: ("GetLocalString", "os", 1),
    54: ("GetLocalObject", "os", 1),
    55: ("SetLocalInt", "osi", 0),
    56: ("SetLocalFloat", "osf", 0),
    57: ("SetLocalString", "oss", 0),
    58: ("SetLocalObject", "oso", 0),
    66: ("FindSubString", "ssi", 1),
    83: ("EffectSummonCreature", "sifiio", 1),
    92: ("IntToString", "i", 1),
    152: ("SetLocalLocation", "osl", 0),
    153: ("GetLocalLocation", "os", 1),
    155: ("GetItemInSlot", "io", 1),
    171: ("EffectAreaOfEffect", "isss", 1),
    176: ("SetListenPattern", "osi", 0),
    177: ("TestStringAgainstPattern", "ss", 1),
    197: ("GetWaypointByTag", "s", 1),
    200: ("GetObjectByTag", "si", 1),
    202: ("ActionWait", "f", 0),
    203: ("SetAreaTransitionBMP", "is", 0),
    204: ("ActionStartConversation", "osii", 0),
    221: ("SpeakString", "si", 0),
    229: ("GetNearestObjectByTag", "soi", 1),
    238: ("GetPCSpeaker", "", 1),
    242: ("GetModule", "", 1),
    243: ("CreateObject", "islis", 1),
    253: ("GetName", "oi", 1),
    255: ("BeginConversation", "so", 1),
    265: ("DeleteLocalInt", "os", 0),
    266: ("DeleteLocalFloat", "os", 0),
    267: ("DeleteLocalString", "os", 0),
    268: ("DeleteLocalObject", "os", 0),
    269: ("DeleteLocalLocation", "os", 0),
    284: ("SetCustomToken", "is", 0),
    330: ("GetLastUsedBy", "", 1),
    358: ("GetGender", "o", 1),
    367: ("AddJournalQuestEntry", "sioiii", 0),
    368: ("RemoveJournalQuestEntry", "soii", 0),
    374: ("SendMessageToPC", "os", 0),
    384: ("GetJournalQuestExperience", "s", 1),
    393: ("GiveXPToCreature", "oi", 0),
    417: ("SpeakOneLinerConversation", "so", 0),
    474: ("ActivatePortal", "osssi", 0),
    504: ("SetCameraMode", "oi", 0),
    509: ("StartNewModule", "s", 0),
    510: ("EffectSwarm", "issss", 1),
    526: ("FloatingTextStringOnCreature", "soii", 0),
    548: ("GetFirstPC", "", 1),
    554: ("PopUpDeathGUIPanel", "oiiis", 0),
    560: ("WriteTimestampedLogEntry", "s", 0),
    563: ("SendMessageToAllDMs", "s", 0),
    564: ("EndGame", "s", 0),
    589: ("SetCampaignFloat", "ssfo", 0),
    590: ("SetCampaignInt", "ssio", 0),
    591: ("SetCampaignVector", "ssvo", 0),
    592: ("SetCampaignLocation", "sslo", 0),
    593: ("SetCampaignString", "ssso", 0),
    594: ("DestroyCampaignDatabase", "s", 0),
    595: ("GetCampaignFloat", "sso", 1),
    596: ("GetCampaignInt", "sso", 1),
    597: ("GetCampaignVector", "sso", 3),
    598: ("GetCampaignLocation", "sso", 1),
    599: ("GetCampaignString", "sso", 1),
    600: ("CopyObject", "olosi", 1),
    601: ("DeleteCampaignVariable", "sso", 0),
    602: ("StoreCampaignObject", "ssooi", 1),
    603: ("RetrieveCampaignObject", "sslooi", 1),
    695: ("FadeFromBlack", "of", 0),
    710: ("Get2DAString", "ssi", 1),
    799: ("SetLockKeyTag", "os", 0),
    806: ("SetTrapKeyTag", "os", 0),
    820: ("SetKeyRequiredFeedback", "os", 0),
    830: ("SetName", "os", 0),
    834: ("SetPortraitResRef", "os", 0),
    837: ("SetDescription", "osi", 0),
    848: ("SetTag", "os", 0),
    850: ("TagEffect", "es", 1),
    855: ("TagItemProperty", "ps", 1),
    858: ("CreateArea", "sss", 1),
    860: ("CopyArea", "oss", 1),
    886: ("SetEventScript", "ois", 1),
    901: ("PostString", "osiiifiiis", 0),
}

_TRACE_WINDOW = 64
_SCALAR_TYPES = {TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_OBJECT}

# A routine can have both player and internal arguments (PostString/CreateArea).
# These sets describe the routine; proof still requires a particular argument.
PLAYER_FACING_ACTIONS: Set[int] = {
    routine
    for routine, (name, params, _) in ACTION_SIGNATURES.items()
    if any(classify_engine_arg(name, arg) == "player" for arg in range(len(params)))
}
NON_PLAYER_ACTIONS: Set[int] = {
    routine
    for routine, (name, params, _) in ACTION_SIGNATURES.items()
    if any(classify_engine_arg(name, arg) == "internal" for arg in range(len(params)))
}


def trace_string_consumer(instr_index: int, instructions: List[NCSInstruction]) -> Dict[str, Any]:
    """Follow copies through bounded control flow; any technical use wins.

    Stack positions are relative to the initial string. Unknown instructions,
    escaped values and exhausted budgets prevent a player-only proof. Engine
    return values never inherit the identity of an argument. NSS is not used.
    """
    from ..file_handlers import ncs_parser as op

    context: Dict[str, Any] = {
        "next_action": None,
        "next_action_name": None,
        "argument_index": None,
        "consumer_proven": False,
        "compare_nearby": False,
        "distance": None,
        "role": None,
        "player_use_seen": False,
        "player_action_nearby": any(
            i.action_routine in PLAYER_FACING_ACTIONS
            for i in instructions[instr_index + 1 : instr_index + _TRACE_WINDOW + 1]
            if i.is_action
        ),
    }
    by_offset = {i.offset: n for n, i in enumerate(instructions)}
    # Next instruction, stack top (exclusive, bytes), tracked slots, return stack.
    pending: list[tuple[int, int, frozenset[int], tuple[int, ...]]] = [
        (instr_index + 1, 0, frozenset({-4}), ())
    ]
    seen: set[tuple[int, int, frozenset[int], tuple[int, ...]]] = set()
    incomplete = False
    player = False
    while pending and len(seen) < 2048:
        idx, sp, frozen, returns = pending.pop()
        state = (idx, sp, frozen, returns)
        if state in seen or not frozen:
            continue
        seen.add(state)
        if not 0 <= idx < len(instructions) or len(returns) > 16:
            incomplete = True
            continue
        instr = instructions[idx]
        tokens = set(frozen)
        next_idx = idx + 1

        def pop(size: int) -> bool:
            nonlocal sp
            removed = {pos for pos in tokens if sp - size <= pos < sp}
            tokens.difference_update(removed)
            sp -= size
            return bool(removed)

        if instr.opcode in (OP_CONST, OP_RSADD) and (
            instr.type_byte in _SCALAR_TYPES or 0x10 <= instr.type_byte <= 0x17
        ):
            if idx == instr_index:
                tokens.add(sp)
            sp += 4
        elif instr.opcode in (OP_CPTOPSP, op.OP_CPDOWNSP):
            offset, size = struct.unpack(">iH", instr.args)
            if not size or size % 4 or offset % 4 or offset + size > 0:
                incomplete = True
                continue
            source = sp + offset if instr.opcode == OP_CPTOPSP else sp - size
            dest = sp if instr.opcode == OP_CPTOPSP else sp + offset
            copied = {dest + pos - source for pos in tokens if source <= pos < source + size}
            tokens.difference_update(range(dest, dest + size, 4))
            tokens.update(copied)
            if instr.opcode == OP_CPTOPSP:
                sp += size
        elif instr.opcode == OP_CPTOPBP:
            # BP is not modeled. Continue seeking technical consumers, but do
            # not claim exclusive display use across an untracked global read.
            incomplete = True
            offset, size = struct.unpack(">iH", instr.args)
            if not size or size % 4 or offset % 4 or offset + size > 0:
                incomplete = True
                continue
            sp += size
        elif instr.opcode == op.OP_MOVSP:
            amount = struct.unpack(">i", instr.args)[0]
            if amount % 4 or amount > 0:
                incomplete = True
                continue
            pop(-amount)
        elif instr.opcode == OP_ADD and instr.type_byte == TYPE_STRING_STRING:
            touched = pop(8)
            if touched:
                tokens.add(sp)
            sp += 4
        elif instr.opcode in (OP_EQUAL, OP_NEQUAL) and instr.type_byte == TYPE_STRING_STRING:
            if pop(8):
                context.update(
                    role="compare",
                    compare_nearby=True,
                    consumer_proven=True,
                    distance=idx - instr_index,
                )
                return context
            sp += 4
        elif op.OP_LOGAND <= instr.opcode <= op.OP_MOD and instr.type_byte in (
            0x20,
            0x21,
            0x25,
            0x26,
        ):
            if pop(8):
                incomplete = True
                continue
            sp += 4
        elif instr.opcode in (op.OP_NEG, op.OP_COMP, op.OP_NOT):
            if sp - 4 in tokens or instr.type_byte not in (TYPE_INT, TYPE_FLOAT):
                incomplete = True
                continue
        elif instr.opcode == op.OP_DESTRUCT:
            size, offset, kept = struct.unpack(">HHH", instr.args)
            if any(n % 4 for n in (size, offset, kept)) or offset + kept > size:
                incomplete = True
                continue
            copied = {
                pos - offset
                for pos in tokens
                if sp - size + offset <= pos < sp - size + offset + kept
            }
            pop(size)
            tokens.update(copied)
            sp += kept
        elif instr.is_action:
            signature = ACTION_SIGNATURES.get(instr.action_routine or -1)
            argc = instr.action_arg_count
            if signature is None or argc is None or argc > len(signature[1]):
                incomplete = True
                continue
            name, params, result_slots = signature
            for arg, param in enumerate(params[:argc]):
                width = 0 if param == "a" else 12 if param == "v" else 4
                if pop(width):
                    role = classify_engine_arg(name, arg) if param == "s" else None
                    context.update(
                        next_action=instr.action_routine,
                        next_action_name=name,
                        argument_index=arg,
                        distance=idx - instr_index,
                    )
                    if role == "internal":
                        context.update(role=role, consumer_proven=True)
                        return context
                    if role == "player":
                        player = True
                    else:
                        incomplete = True
            sp += result_slots * 4
        elif instr.opcode in (op.OP_JMP, op.OP_JSR, op.OP_JZ, op.OP_JNZ):
            target = by_offset.get(instr.offset + (instr.jump_offset or 0))
            if target is None:
                incomplete = True
                continue
            if instr.opcode in (op.OP_JZ, op.OP_JNZ):
                if pop(4):
                    incomplete = True
                    continue
                pending.append((next_idx, sp, frozenset(tokens), returns))
            elif instr.opcode == op.OP_JSR:
                returns = returns + (next_idx,)
            next_idx = target
        elif instr.opcode == op.OP_RETN:
            if not returns:
                incomplete |= bool(tokens)
                continue
            next_idx, returns = returns[-1], returns[:-1]
        elif instr.opcode == op.OP_STORE_STATE:
            bp_size, sp_size = struct.unpack(">II", instr.args)
            target = by_offset.get(instr.offset + instr.type_byte)
            if target is None or bp_size % 4 or sp_size % 4:
                incomplete = True
                continue
            # Deferred action copies the top sp_size bytes. Its relative SP
            # positions are unchanged; its return stack is independent.
            captured = frozenset(pos for pos in tokens if sp - sp_size <= pos < sp)
            pending.append((target, sp, captured, ()))
        elif instr.opcode != OP_NOP:
            incomplete = True
            continue
        pending.append((next_idx, sp, frozenset(tokens), returns))
    context["player_use_seen"] = player
    if player and not incomplete and not pending:
        context.update(role="player", consumer_proven=True)
    return context
