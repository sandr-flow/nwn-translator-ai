void strip_me()
{
    object me = GetFirstPC();
    int i;
    object item, copied_item;
    object crate = GetObjectByTag ("Rual1CrateMyStuff");

    // round 1: worn items
    for (i=0; i<18; i++){
        item = GetItemInSlot (i, me);
        CopyItem (item, crate);
        DestroyObject (item);
    }
    // round 2: items in inventory
    item = GetFirstItemInInventory(me);
    while(GetIsObjectValid(item)){
        copied_item = CopyItem (item, crate);
        if (GetIsObjectValid (copied_item)) DestroyObject(item);
        item = GetNextItemInInventory(me);
    }
    TakeGoldFromCreature (GetGold (me), me, TRUE);
}
void strip_pia()
{
    object pia = GetObjectByTag ("Pia2");
    int i;
    object item, copied_item, clothes;
    object crate = GetObjectByTag ("RagnarCratePiaStuff");

    // round 1: worn items
    for (i=0; i<18; i++){
        item = GetItemInSlot (i, pia);
        CopyItem (item, crate);
        DestroyObject (item);
    }
    // round 2: items in inventory
    item = GetFirstItemInInventory (pia);
    while(GetIsObjectValid(item)){
        copied_item = CopyItem (item, crate);
        if (GetIsObjectValid (copied_item)) DestroyObject(item);
        item = GetNextItemInInventory (pia);
    }
    TakeGoldFromCreature (GetGold (pia), pia, TRUE);
    clothes = CreateItemOnObject ("clothings", pia, 1);
    AssignCommand (pia, ActionEquipItem (clothes, INVENTORY_SLOT_CHEST));
    RemoveHenchman (GetFirstPC(), pia);
}
void main()
{
    object me  = GetFirstPC ();
    object area_dvbase = GetObjectByTag ("MaeralssinDoVrinnTowerBasement");
    object talice = CreateObject (OBJECT_TYPE_CREATURE, "drowpriestess003", GetLocation (GetObjectByTag ("WP_DVArena_Talice")), FALSE);
    object door = GetObjectByTag ("Door1DoVrinnBasement");
    object pia = GetObjectByTag ("Pia2");

    // update Pia
    SetLocalInt (pia, "Love", 17);
    SetLocalInt (pia, "Relationship", 1);
    SetLocalInt (pia, "FirstSex", 1);
    SetLocalInt (pia, "Cave2Sex", 1);
    // Level up Pia
    LevelUpHenchman (pia, CLASS_TYPE_ROGUE, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_ROGUE, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_BARD, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_BARD, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_BARD, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_ROGUE, FALSE);
    LevelUpHenchman (pia, CLASS_TYPE_BARD, FALSE);
    // Pia's equipment
    object armor = CreateItemOnObject ("aarcl018", pia);
    SetIdentified (armor, TRUE);
    AssignCommand (pia, ActionEquipItem (armor, INVENTORY_SLOT_CHEST));
    object rapier = CreateItemOnObject ("wswmrp010", pia);
    SetIdentified (rapier, TRUE);
    // AssignCommand (pia, ActionEquipItem (rapier, INVENTORY_SLOT_CWEAPON_R));
    object shortsword = CreateItemOnObject ("wswmss003", pia);
    SetIdentified (shortsword, TRUE);
    // AssignCommand (pia, ActionEquipItem (shortsword, INVENTORY_SLOT_CWEAPON_L));
    object arrows = CreateItemOnObject ("wamar002", pia);
    SetIdentified (arrows, TRUE);
    AssignCommand (pia, ActionEquipItem (arrows, INVENTORY_SLOT_ARROWS));
    object belt = CreateItemOnObject ("it_mbelt012", pia);
    SetIdentified (belt, TRUE);
    AssignCommand (pia, ActionEquipItem (belt, INVENTORY_SLOT_BELT));
    object gloves = CreateItemOnObject ("it_mglove011", pia);
    SetIdentified (gloves, TRUE);
    AssignCommand (pia, ActionEquipItem (gloves, INVENTORY_SLOT_ARMS));
    object ring1 = CreateItemOnObject ("it_mring019", pia);
    SetIdentified (ring1, TRUE);
    AssignCommand (pia, ActionEquipItem (ring1, INVENTORY_SLOT_LEFTRING));
    object amulett = CreateItemOnObject ("it_mneck014", pia);
    SetIdentified (amulett, TRUE);
    AssignCommand (pia, ActionEquipItem (amulett, INVENTORY_SLOT_NECK));
    object cloak = CreateItemOnObject ("maarcl093", pia);
    SetIdentified (cloak, TRUE);
    AssignCommand (pia, ActionEquipItem (cloak, INVENTORY_SLOT_CLOAK));
    object booties = CreateItemOnObject ("silentslippers", pia);
    SetIdentified (booties, TRUE);
    AssignCommand (pia, ActionEquipItem (booties, INVENTORY_SLOT_BOOTS));
    object bow = CreateItemOnObject ("wbwmsh012", pia);
    SetIdentified (bow, TRUE);
    AssignCommand (pia, ActionEquipItem (bow, INVENTORY_SLOT_CWEAPON_B));
    object potions = CreateItemOnObject ("it_mpotion003", pia, 5);
    SetIdentified (potions, TRUE);
    // Pia joins party
    AssignCommand (pia, ActionJumpToLocation (GetLocation (me)));
    AddHenchman (me, pia);

    // set area spawn = true
    SetLocalInt (area_dvbase, "spawned", 1);

    // set number of rests in do'vrinn basement
    SetLocalInt (me, "DVCellRests", 16);

    // Pia is at Ragnar's place
    SetLocalInt (me, "PiaAtRagnar", 1);
    SetLocalInt (me, "Piatalk", 10);
    SetLocalInt (me, "Piadead", 0);
    SetLocalInt (me, "Chapter5", 1);

    // Remember my henchmen
    SetLocalInt (me, "UDHench_Pia", 1);
    SetLocalInt (me, "UDHench_Anden", 1);
    SetLocalInt (me, "UDHench_Vico", 1);

    // move Pia to Ragnar
    DelayCommand (0.5f, strip_pia());
    DelayCommand (0.6f, AssignCommand (pia, ClearAllActions()));
    DelayCommand (0.7f, AssignCommand (pia, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Ragnar_Pia")))));

    // take away my things
    strip_me();
    // give me some silken underwear
    CreateItemOnObject ("silkenunderwear", me);
    // give me a wand of resurrection
    CreateItemOnObject ("rodofressurectio", me);

    // I'm being transported to House Do'Vrinn
    DelayCommand (1.0f, AssignCommand (me, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Debug_DV2Temple")))));
}
