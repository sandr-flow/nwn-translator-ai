int StartingConditional()
{
    object me = GetFirstPC();

    // check items in inventory
    object item = GetFirstItemInInventory (me);
    int i;
    while (GetIsObjectValid (item)){
        if ((GetTag (item) != "DancersDress")
            && (GetTag (item) != "MySpecialDiary1")
            && (GetTag (item) != "MySpecialDiary2")
            && (GetTag (item) != "MySpecialDiary3")
            && (GetTag (item) != "MySpecialDiary4")
            && (GetTag (item) != "MySpecialDiary5")
            && (GetTag (item) != "MySpecialDiary6")
            && (GetTag (item) != "MySpecialDiary7")
            && (GetTag (item) != "PiasLoveRing")
            && (GetTag (item) != "PiasRing")
            && (GetTag (item) != "HyathsRing")) return FALSE;
        item = GetNextItemInInventory (me);
    }

    // check equipped items
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_ARMS, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_ARROWS, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_BELT, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_BOLTS, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_BOOTS, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_BULLETS, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_CHEST, me))){
        item = GetItemInSlot (INVENTORY_SLOT_CHEST, me);
        if (GetTag (item) != "DancersDress") return FALSE;
    }
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_CLOAK, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_HEAD, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_LEFTHAND, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_LEFTRING, me))){
        item = GetItemInSlot (INVENTORY_SLOT_LEFTRING, me);
        if ((GetTag (item) != "PiasLoveRing")
         && (GetTag (item) != "PiasRing")
         && (GetTag (item) != "HyathsRing")) return FALSE;
    }
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_NECK, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_RIGHTHAND, me))) return FALSE;
    if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_RIGHTRING, me))){
        item = GetItemInSlot (INVENTORY_SLOT_RIGHTRING, me);
        if ((GetTag (item) != "PiasLoveRing")
         && (GetTag (item) != "PiasRing")
         && (GetTag (item) != "HyathsRing")) return FALSE;
    }

    // good girl
    return TRUE;
}



