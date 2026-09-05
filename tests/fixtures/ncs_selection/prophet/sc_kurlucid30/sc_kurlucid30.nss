//::///////////////////////////////////////////////
//:: FileName sc_kurlucid30
//:://////////////////////////////////////////////
//:://////////////////////////////////////////////
//:: Created By: Script Wizard
//:: Created On: 8/16/2008 2:22:16 PM
//:://////////////////////////////////////////////
int StartingConditional()
{

    // Inspect local variables
    if(!(GetLocalInt(OBJECT_SELF, "bKnowLucid") != 1))
        return FALSE;
    if(!(GetLocalInt(GetPCSpeaker(), "NW_JOURNAL_ENTRYLucidity") >= 30))
        return FALSE;

    SetLocalInt(OBJECT_SELF, "bKnowLucid", 1);
    return TRUE;
}
