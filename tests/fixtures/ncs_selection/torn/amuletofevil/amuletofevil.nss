#include "x2_inc_switches"

void main()
{
    int nEvent = GetUserDefinedItemEventNumber();
    //object oGenRen = GetObjectByTag("RenaqFinal");
    object oPC;
    object oItem;



    if( nEvent == X2_ITEM_EVENT_UNEQUIP )
    {
        oPC = GetPCItemLastUnequippedBy();
        oItem = GetPCItemLastUnequipped();

        if( oItem == GetObjectByTag("AmuletofEvil") )
        {
            SetLocalInt(oPC, "EquipAmulet", FALSE);
            SendMessageToPC(oPC, "UnEquip Fire " + GetTag(oItem) + " " + IntToString(nEvent) );
        }
    }

    if( nEvent == X2_ITEM_EVENT_EQUIP )
    {
        oPC = GetPCItemLastEquippedBy();
        oItem = GetPCItemLastEquipped();
        if( oItem == GetObjectByTag("AmuletofEvil") )
        {
            SetLocalInt(oPC, "EquipAmulet", TRUE);
            SendMessageToPC(oPC, "Equip Fire " + GetTag(oItem) + " " + IntToString(nEvent) );
        }
    }

    SendMessageToPC(GetFirstPC(), "End");
}
