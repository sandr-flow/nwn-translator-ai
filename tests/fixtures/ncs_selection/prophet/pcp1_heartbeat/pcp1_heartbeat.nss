#include "nw_i0_generic"
#include "x0_i0_partywide"
#include "pc_include"

void main()
{
    object oMod = GetModule();
    object o;

    // Count days
    if (GetIsDay() && !GetLocalInt(oMod,"bDay")) {
        SetLocalInt(oMod,"bDay",TRUE);
        SetLocalInt(oMod,"nDays",GetLocalInt(oMod,"nDays")+1);
        }
    SetLocalInt(oMod,"bDay",GetIsDay());

    // Player specific stuff
    object oPC = GetFirstPC();
    while (GetIsObjectValid(oPC)) {

        if (GetLocalInt(GetArea(oPC),"bDreamArea") && !GetCutsceneMode(oPC)) {
            // Player is stuck within a dream - make sure he/she gets back
            AssignCommand(oPC,JumpToLocation(GetLocalLocation(oPC,"lRestLoc")));
            }

        // Make sure player is awake
        if (!GetIsResting(oPC) && GetLocalInt(oPC,"bSleepingBlindDeaf")) {
            SetLocalInt(oPC,"bSleepingBlindDeaf",FALSE);
            effect e = GetFirstEffect(oPC);
            while (GetIsEffectValid(e)) {
                if (GetEffectType(e)==EFFECT_TYPE_BLINDNESS || GetEffectType(e)==EFFECT_TYPE_DEAF)
                    RemoveEffect(oPC,e);
                e = GetNextEffect(oPC);
                }
            }

        if (GetLocalInt(oPC,"bRespawnedAsGhost")) {
            object oCarrier = GetLocalObject(oPC,"oFollow");
            if (GetIsObjectValid(oCarrier) && GetArea(oPC)!=GetArea(oCarrier)) {
                SetCommandable(TRUE,oPC);
                AssignCommand(oPC,ClearAllActions());
                AssignCommand(oPC,JumpToLocation(GetLocation(oCarrier)));
                AssignCommand(oPC,ActionForceFollowObject(oCarrier));
                AssignCommand(oPC,ActionDoCommand(SetCommandable(FALSE,oPC)));
                }
            }

        /* Store current XP */
        SetLocalInt(oPC,"nXP",GetXP(oPC));

        oPC = GetNextPC();
        }

    // If a prophet copy exists, make sure it fights
    o = GetLocalObject(oMod,"oProphetCopy");
    if (GetIsObjectValid(o)) {
        if (!GetIsDead(o)) {
            if (GetCurrentAction(o)==ACTION_INVALID)
                AssignCommand(o,DetermineCombatRound(GetNearestSeenOrHeardEnemy(o)));
            }
        else
            DeleteLocalObject(oMod,"oProphetCopy");
        }

    // Handle the curse of the Odoun
    o = GetLocalObject(oMod,"oOdounCurseVictim");
    if (GetIsObjectValid(o)) {
        object oChw = GetObjectByTag("Chwanektu");
        if (GetLocalInt(oMod,"bDMLiftedCurse") || (GetIsObjectValid(oChw) && GetIsDead(oChw))) {
            // Chwanektu exists and was killed - lift the curse
            object oItem = GetFirstItemInInventory(o);
            while (GetIsObjectValid(oItem)) {
                if (GetTag(oItem)=="Smalllumpoflead")
                    DestroyObject(oItem);
                oItem = GetNextItemInInventory(o);
                }
            DeleteLocalObject(oMod,"oOdounCurseVictim");
            SetLocalInt(oMod,"bDMLiftedCurse",FALSE);
            SetLocalInt(o,"bOdounDisturbed",FALSE);
            AddJournalQuestEntry("Odoun",1100,o,TRUE);
            SetLocalInt(o,"qOdoun",1100);
            GiveXPToAll(o,GetJournalQuestExperience("Odoun"));
            SetLocalInt(oMod,"bOdounFinished",TRUE);
            }
        else if (GetLocalInt(o,"nOdounCount")>=10) { // After PC moves away by 10 triggers, the curse continues on heartbeats
            int n = GetLocalInt(oMod,"nOdounCurseCount")+1;
            AddJournalQuestEntry("Odoun",50,o,FALSE);
            if (n==30) {
                CreateItemOnObject("smalllumpoflead",o);
                AddJournalQuestEntry("Odoun",10,o,FALSE);
                FloatingTextStringOnCreature("You feel burdened...",o,FALSE);
                if (GetLocalInt(o,"qOdoun")==0)
                    SetLocalInt(o,"qOdoun",10);
                object oProphet = GetLocalObject(oMod,"oProphet");
                if (!GetLocalInt(oProphet,"bKnowChwanektu"))
                    SetLocalInt(oProphet,"bAwaitingDream3",TRUE);
                n=0;
                }
            SetLocalInt(oMod,"nOdounCurseCount",n);
            }
        }

}
