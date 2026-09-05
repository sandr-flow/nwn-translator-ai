//::///////////////////////////////////////////////
//:: FileName sc_checkplot_1
//:://////////////////////////////////////////////
//:://////////////////////////////////////////////
//:: Created By: Script Wizard
//:: Created On: 1/2/2005 12:55:15 AM
//:://////////////////////////////////////////////
int StartingConditional()
{
    object oMod = GetModule();
    // Inspect local variables
    if (!(GetLocalInt(oMod, "wundraquest") == 25))
       return FALSE;

    return TRUE;
}
