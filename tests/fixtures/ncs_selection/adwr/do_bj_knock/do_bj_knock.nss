void Strip()
{
    object me = GetFirstPC();
    object my_clothes;
    object clothes = GetItemInSlot (INVENTORY_SLOT_CHEST, me);

    // remember my clothes
    SetLocalObject (me, "EquippedClothes", clothes);

    // unequip my visible equipment
    AssignCommand (me, ActionUnequipItem (clothes));
    AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_CLOAK, me)));
    AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_RIGHTHAND, me)));
    AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_LEFTHAND, me)));
    AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_HEAD, me)));
}
void Face (object who, object what)
{
    AssignCommand (who, ActionDoCommand (SetFacingPoint (GetPosition (what))));
}
void Speak (object who, string text, int anim, float duration)
{
    AssignCommand (who, ActionSpeakString (text));
    DelayCommand (0.2f, AssignCommand (who, ActionPlayAnimation (anim, 1.0f, duration)));
}
void NewClothes()
{
    object me = GetFirstPC();
    object rags;
    if (!GetIsObjectValid (GetItemPossessedBy (me, "PrisonRags"))) rags = CreateItemOnObject ("rags004", me, 1, "PrisonRags");
    else rags = GetItemPossessedBy (me, "PrisonRags");
    AssignCommand (me, ClearAllActions (TRUE));
    AssignCommand (me, ActionEquipItem (rags, INVENTORY_SLOT_CHEST));
}
void CopyStolen()
{
    object me = GetFirstPC();
    object item;
    int i;

    // check for stolen items
    // round 1: worn items
    for (i=0; i<18; i++){
        item = GetItemInSlot (i, me);
        if (GetIsObjectValid (item)){
            if (GetStolenFlag (item)){
                if (!GetPlotFlag (item)){
                    CopyItem (item, GetObjectByTag ("BJ1_Cabinet"), TRUE);
                    DestroyObject (item, 0.3f);
                }
            }
        }
    }
    // round 2: items in inventory
    item = GetFirstItemInInventory (me);
    while (GetIsObjectValid (item)){
        if (GetStolenFlag (item)){
            if (!GetPlotFlag (item)){
                CopyItem (item, GetObjectByTag ("BJ1_Cabinet"), TRUE);
                DestroyObject (item, 0.3f);
            }
        }
        item = GetNextItemInInventory(me);
    }
}
void main()
{
    if (!GetIsPC (GetEnteringObject())) return;

    object me = GetFirstPC();

    if ((GetLocalInt (me, "BJArrested") == 1)
     || (GetLocalInt (me, "BJArrested") == 2)){

        object gaoler = GetObjectByTag ("BJ1_Gaoler");
        object whip = GetObjectByTag ("BJ1_Whip");
        object clothes = GetItemInSlot (INVENTORY_SLOT_CHEST, me);
        int gold = GetGold (me);

        if (gold > 0) CreateItemOnObject ("nw_it_gold001", GetObjectByTag ("BJ1_Chest"), gold);

        SetImmortal (me, TRUE);

        AssignCommand (me, ClearAllActions());

        if (GetLocalInt (me, "BJArrested") == 1){
            if (GetIsObjectValid (clothes)){
                CopyItem (clothes, GetObjectByTag ("BJ1_Cabinet"), TRUE);
                DelayCommand (0.2f, AssignCommand (me, ActionUnequipItem (clothes)));
                DestroyObject (clothes, 0.3f);
            }
        }
        else if (GetLocalInt (me, "BJArrested") == 2){
            CopyStolen();
            DelayCommand (0.2f, Strip());
        }

        DelayCommand (0.4f, ApplyEffectToObject (DURATION_TYPE_TEMPORARY, EffectKnockdown(), me, 21.0f));
        DelayCommand (0.5f, AssignCommand (gaoler, JumpToObject (GetObjectByTag ("WP_BJ1_Gaoler"))));
        DelayCommand (0.5f, AssignCommand (whip, JumpToObject (GetObjectByTag ("WP_BJ1_Whip"))));
        DelayCommand (0.7f, Face (gaoler, whip));
        DelayCommand (0.7f, Face (whip, gaoler));

        DelayCommand (1.5f, Speak (gaoler, "What's she in for?", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        if (GetLocalInt (me, "BJArrested") == 1){
            DelayCommand (4.0f, Speak (whip, "Indecent behavior.", ANIMATION_FIREFORGET_READ, 2.0f));
            if (gold == 0) DelayCommand (7.0f, Speak (gaoler, "Ok. Ten lashes.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
            else DelayCommand (7.0f, Speak (gaoler, "Ok. Ten lashes plus " + IntToString (gold) + " gold fine.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        }
        else if (GetLocalInt (me, "BJArrested") == 2){
            DelayCommand (4.0f, Speak (whip, "Possession of stolen goods.", ANIMATION_FIREFORGET_READ, 2.0f));
            if (gold == 0) DelayCommand (7.0f, Speak (gaoler, "Ok. Twenty five lashes.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
            else DelayCommand (7.0f, Speak (gaoler, "Ok. Twenty five lashes plus " + IntToString (gold) + " gold fine.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        }
        DelayCommand (9.5f, Speak (whip, "Aye, Sir!", ANIMATION_FIREFORGET_SALUTE, 1.0f));

        if (gold > 0) DelayCommand (11.0f, AssignCommand (whip, TakeGoldFromCreature (gold, me, TRUE)));
        DelayCommand (11.5f, SetIsTemporaryEnemy (me, whip, FALSE));
        DelayCommand (12.0f, AssignCommand (whip, ActionAttack (me)));
        DelayCommand (13.0f, AssignCommand (me, PlayVoiceChat (VOICE_CHAT_PAIN1, OBJECT_SELF)));
        DelayCommand (15.0f, AssignCommand (me, PlayVoiceChat (VOICE_CHAT_PAIN2, OBJECT_SELF)));
        DelayCommand (17.0f, AssignCommand (me, PlayVoiceChat (VOICE_CHAT_PAIN3, OBJECT_SELF)));
        DelayCommand (19.0f, AssignCommand (me, PlayVoiceChat (VOICE_CHAT_DEATH, OBJECT_SELF)));
        DelayCommand (20.0f, FadeToBlack (me));
        DelayCommand (21.0f, ClearPersonalReputation (me, whip));
        DelayCommand (21.2f, AssignCommand (whip, ClearAllActions (TRUE)));
        DelayCommand (22.0f, AssignCommand (whip, JumpToObject (GetObjectByTag ("WP_BJ1_Park"))));
        DelayCommand (22.0f, AssignCommand (gaoler, JumpToObject (GetObjectByTag ("WP_BJ1_Park"))));
        DelayCommand (22.0f, SetImmortal (me, FALSE));
        if (GetLocalInt (me, "BJArrested") == 1) DelayCommand (22.2f, NewClothes());
        DelayCommand (22.4f, SetTime (GetTimeHour() + 4, 0, 0, 0));
        DelayCommand (22.6f, AssignCommand (me, JumpToObject (GetObjectByTag ("WP_BetaW_ExitBJ"))));

        //DelayCommand (22.5f, FadeFromBlack (me));
        //SetLocked (GetObjectByTag ("BJ1_Cabinet"), FALSE);
        //SetTrapActive (GetObjectByTag ("BJ1_Cabinet"), FALSE);
        //DelayCommand (22.5f, AssignCommand (me, JumpToObject (GetObjectByTag ("WP_BJ_Debug"))));
    }
}
