void main()
{
    if (!GetIsPC (GetEnteringObject ())) return;

    object me = GetFirstPC();
    object creature, chair;
    int i;

    if (GetLocalInt (me, "Raventext2") == 0){
        SetLocalInt (me, "Raventext2", 1);
        AssignCommand (me, ActionStartConversation (me, "self_conv_raven2", TRUE, FALSE));

        // prepare castle
        for (i=0; i<43; i++){
            creature = GetObjectByTag ("RavenMan", i);
            chair = GetNearestObjectByTag ("Chair", creature);
            DelayCommand (0.5, AssignCommand (creature, ActionSit(chair)));
        }
        creature = GetObjectByTag ("BaronRavenstower");
        DelayCommand (0.5f, AssignCommand (creature, ActionSit (GetObjectByTag ("RavenstowerThrone"))));
    }
    else if ((GetLocalInt (me, "Piatalk") > 1)&&(GetLocalInt (me, "Piatalk") < 7)){
        creature = GetObjectByTag ("RaventowerOuterGuard", 0);
        AssignCommand (creature, ClearAllActions());
        DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_1")))));
        creature = GetObjectByTag ("RaventowerOuterGuard", 1);
        AssignCommand (creature, ClearAllActions());
        DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_2")))));
        creature = GetObjectByTag ("RaventowerOuterGuard", 2);
        AssignCommand (creature, ClearAllActions());
        DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_3")))));
        if (GetIsDay()){
            creature = GetObjectByTag ("RaventowerOuterGuard", 3);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_6")))));
            creature = GetObjectByTag ("RaventowerOuterGuard", 4);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_5")))));
            DelayCommand (1.5f, AssignCommand (creature, ActionRandomWalk()));
            creature = GetObjectByTag ("RaventowerOuterGuard", 5);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("WP_Raventower_Outer_4")))));
            DelayCommand (1.5f, AssignCommand (creature, ActionRandomWalk()));
        }
        else {
            creature = GetObjectByTag ("RaventowerOuterGuard", 3);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("Parkplatz")))));
            creature = GetObjectByTag ("RaventowerOuterGuard", 4);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("Parkplatz")))));
            creature = GetObjectByTag ("RaventowerOuterGuard", 5);
            AssignCommand (creature, ClearAllActions());
            DelayCommand (1.0f, AssignCommand (creature, ActionJumpToLocation (GetLocation (GetObjectByTag ("Parkplatz")))));
        }
    }
    else if (GetLocalInt (me, "Piatalk") >= 7){
        for (i=0; i<6; i++){
            creature = GetObjectByTag ("RaventowerOuterGuard", i);
            DestroyObject (creature);
        }
    }
}
