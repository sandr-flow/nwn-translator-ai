//::///////////////////////////////////////////////
//:: FileName at_attaque
//:://////////////////////////////////////////////
//:://////////////////////////////////////////////
//:: Created By: Script Wizard
//:: Created On: 12/11/2002 16:34:51
//:://////////////////////////////////////////////
#include "nw_i0_generic"

void main()
{

    // Set the faction to hate the player, then attack the player
    //AdjustReputation(GetPCSpeaker(), OBJECT_SELF, -100);
    ChangeFaction(OBJECT_SELF, GetObjectByTag("mechant"));
    DetermineCombatRound(GetFirstPC());
    DelayCommand(0.1, AssignCommand(OBJECT_SELF, DetermineCombatRound()));
}
