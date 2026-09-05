#include "x0_i0_partywide"
#include "nw_i0_generic"

void main()
{
    object o;
    object oSelf = OBJECT_SELF;
    o = GetLastKiller();
    while (GetIsObjectValid(GetMaster(o)))
        o = GetMaster(o);
    if (GetTag(OBJECT_SELF)=="Atelkhan2") {
        AddJournalQuestEntry("Hierathanum",1200,o,TRUE);
        }
    else if (GetTag(GetArea(OBJECT_SELF))=="TheLodge" && !GetLocalInt(GetModule(),"bRebelsDead")) {
        AdjustReputation(o, OBJECT_SELF, -100);
        int n=1;
        o = GetNearestCreature(CREATURE_TYPE_IS_ALIVE,TRUE,OBJECT_SELF,n);
        int bAllDead = TRUE;
        while (bAllDead && GetIsObjectValid(o)) {
            if (GetFactionEqual(o))
                bAllDead = FALSE;
            o = GetNearestCreature(CREATURE_TYPE_IS_ALIVE,TRUE,OBJECT_SELF,++n);
            }
        if (bAllDead) {
            // Rebels in the lodge have all been killed
            o = GetFirstPC();
            SetLocalInt(GetModule(),"bRebelsDead",TRUE);
            if (GetLocalInt(o,"NW_JOURNAL_ENTRYRebels")>0)
                AddJournalQuestEntry("Rebels",1000,o,TRUE);
            if (GetLocalInt(o,"NW_JOURNAL_ENTRYKillRebels")>0) {
                AddJournalQuestEntry("KillRebels",99,o,TRUE);
                GiveXPToAll(o,GetJournalQuestExperience("KillRebels"));
                }
            }
        else {
            // Hazur comes in to the fight
            o = GetObjectByTag("Hazur");
            if (GetArea(o)!=GetArea(OBJECT_SELF) && !GetLocalInt(o,"bFighting")) {
                DestroyObject(GetObjectByTag("Jamro"));
                AssignCommand(o,ClearAllActions());
                AssignCommand(o,ActionJumpToObject(GetWaypointByTag("wpRebels")));
                AssignCommand(o,ActionMoveToObject(oSelf,TRUE));
                ChangeFaction(o,oSelf);
                SetLocalInt(o,"bNotWorking",TRUE);
                SetWalkCondition(NW_WALK_FLAG_CONSTANT,FALSE,o);
                ExecuteScript("pc_openrebportal",o);
                }
            }
        }
    ExecuteScript("x2_def_ondeath",OBJECT_SELF);
}
