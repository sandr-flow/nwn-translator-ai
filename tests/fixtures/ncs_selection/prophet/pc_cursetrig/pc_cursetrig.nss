#include "x0_i0_partywide"

void main()
{
    object o = GetEnteringObject();
    object oMod = GetModule();

    if (o == GetLocalObject(oMod,"oOdounCurseVictim")) {
        if (GetLocalInt(OBJECT_SELF,"nOdounCount") > GetLocalInt(o,"nOdounCount")) {
            CreateItemOnObject("smalllumpoflead",o);
            if (GetLocalInt(o,"nOdounCount")>0)
                AddJournalQuestEntry("Odoun",10,o,FALSE);
            FloatingTextStringOnCreature("You feel burdened...",o,FALSE);
            if (GetLocalInt(o,"qOdoun")==0)
                SetLocalInt(o,"qOdoun",10);
            object oProphet = GetLocalObject(oMod,"oProphet");
            if (!GetLocalInt(oProphet,"bKnowChwanektu"))
                SetLocalInt(oProphet,"bAwaitingDream3",TRUE);
            }
        SetLocalInt(o,"nOdounCount",GetLocalInt(OBJECT_SELF,"nOdounCount"));
        }

}
