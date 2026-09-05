//::///////////////////////////////////////////////
//:: FileName sc_havepentanar
//:://////////////////////////////////////////////
//:://////////////////////////////////////////////
//:: Created By: Script Wizard
//:: Created On: 1/11/2009 8:19:12 PM
//:://////////////////////////////////////////////
#include "nw_i0_tool"

int StartingConditional()
{

	// Make sure the PC speaker has these items in their inventory
	if(!HasItem(GetPCSpeaker(), "Pentanar"))
		return FALSE;

	return TRUE;
}
