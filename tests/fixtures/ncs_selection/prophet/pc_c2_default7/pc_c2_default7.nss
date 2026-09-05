//:://////////////////////////////////////////////////
//:: NW_C2_DEFAULT7
/*
  Default OnDeath event handler for NPCs.

  Adjusts killer's alignment if appropriate and
  alerts allies to our death.
 */
//:://////////////////////////////////////////////////
//:: Copyright (c) 2002 Floodgate Entertainment
//:: Created By: Naomi Novik
//:: Created On: 12/22/2002
//:://////////////////////////////////////////////////

#include "x2_inc_compon"
#include "x0_i0_spawncond"
#include "pc_include"

void Resurrect(string sTemplate, location lLoc)
{
    CreateObject(OBJECT_TYPE_CREATURE,sTemplate,lLoc);
}

void main()
{
    if (GetTag(OBJECT_SELF)=="WhiteDragon") {
        AddJournalQuestEntry("GoblinKing",9100,GetLastKiller(),TRUE,TRUE);
        }
    else if (GetTag(OBJECT_SELF)=="Arilthil") {
        AddJournalQuestEntry("Sigris",8900,GetFirstPC(),TRUE,TRUE);
        }

    if (GetLocalInt(OBJECT_SELF,"bDieAsGhost")) {
        ExecuteScript("pc_ghostdeath",OBJECT_SELF);
        return;
        }

    int nClass = GetLevelByClass(CLASS_TYPE_COMMONER);
    int nAlign = GetAlignmentGoodEvil(OBJECT_SELF);
    object oKiller = GetLastKiller();

    //SetIsDestroyable(FALSE,TRUE,TRUE);
    //DelayCommand(120.0,DropItems());

    // If we're a good/neutral commoner,
    // adjust the killer's alignment evil
    if (nClass > 0 && (nAlign == ALIGNMENT_GOOD || nAlign == ALIGNMENT_NEUTRAL)
        && !GetFactionEqual(GetLocalObject(GetModule(),"oFactHostile")))
    {
        AdjustAlignment(oKiller, ALIGNMENT_EVIL, 5);
    }

    // Call to allies to let them know we're dead
    SpeakString("NW_I_AM_DEAD", TALKVOLUME_SILENT_TALK);
    SpeakString("PC_SHOUT_IMDEAD", TALKVOLUME_SILENT_SHOUT);

    //Shout Attack my target, only works with the On Spawn In setup
    SpeakString("NW_ATTACK_MY_TARGET", TALKVOLUME_SILENT_TALK);

    // NOTE: the OnDeath user-defined event does not
    // trigger reliably and should probably be removed
    if(GetSpawnInCondition(NW_FLAG_DEATH_EVENT))
    {
         SignalEvent(OBJECT_SELF, EventUserDefined(1007));
    }
    craft_drop_items(oKiller);

    if (!GetLocalInt(OBJECT_SELF,"bDoNotLeaveCorpse"))
        DieAndLeaveCorpse();

}
