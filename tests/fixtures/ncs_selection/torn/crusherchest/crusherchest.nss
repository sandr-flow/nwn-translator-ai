#include "x2_inc_switches"
#include "ta_inc_henchman"

void main()
{
    int iEvent = GetUserDefinedItemEventNumber();
    object oPC = GetFirstPC();
    object oItem = GetObjectByTag("crusherchest");

    if( iEvent == X2_ITEM_EVENT_ACQUIRE )
    {
        SetLocalInt(oItem, "iCost", 38);
        ExecuteScript("check_moondust", oItem);
        CreateItemOnObject("skullcrusher", oPC, 1);
        DelayCommand(0.5, DestroyObject(oItem));
    }
}
