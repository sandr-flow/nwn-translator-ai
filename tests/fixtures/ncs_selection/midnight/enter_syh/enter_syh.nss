//#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
#include "enter_exit1"
//#include "cutscene"
void main()
{
object oPC = GetEnteringObject();
if (NotEntered(oPC))
    {
    MakeBanditParty(GetHitDice(oPC),"SyH_AMBUSH1_",TRUE);
    SetStandardFactionReputation(STANDARD_FACTION_HOSTILE,50,GetObjectByTag("Accountant"));
    }
RearmParty(oPC);
ApplyAura(oPC);
}
