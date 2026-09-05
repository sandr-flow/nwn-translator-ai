void Face (object who, object what)
{
    AssignCommand (who, ActionDoCommand (SetFacingPoint (GetPosition (what))));
}
void Speak (object who, string text, int anim, float duration)
{
    AssignCommand (who, ActionSpeakString (text));
    DelayCommand (0.2f, AssignCommand (who, ActionPlayAnimation (anim, 1.0f, duration)));
}
void main()
{
    if (!GetIsPC (GetEnteringObject())) return;

    if (GetLocalInt (OBJECT_SELF, "triggered") == 0){
        SetLocalInt (OBJECT_SELF, "triggered", 1);

        object tony = GetObjectByTag ("OBT9_Tony");
        object cata = GetObjectByTag ("OBT9_Cata");
        object soza1 = GetObjectByTag ("OBT9_Soza1");   // "Anden"
        object soza2 = GetObjectByTag ("OBT9_Soza2");
        object soza3 = GetObjectByTag ("OBT9_Soza3");   // "Guard"
        object soza4 = GetObjectByTag ("OBT9_Soza4");   // "Guard 2"
        object crate = GetObjectByTag ("OBT9_Crate");
        object chest = GetObjectByTag ("OBT9_Chest");
        object smuggler;
        object me = GetFirstPC();
        int i;

        SetCutsceneMode (me, TRUE, FALSE);
        AssignCommand (me, ClearAllActions());
        if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_LEFTHAND, me))) AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_LEFTHAND, me)));
        if (GetIsObjectValid (GetItemInSlot (INVENTORY_SLOT_RIGHTHAND, me))) AssignCommand (me, ActionUnequipItem (GetItemInSlot (INVENTORY_SLOT_RIGHTHAND, me)));
        SetCreatureAppearanceType (me, APPEARANCE_TYPE_INVISIBLE_HUMAN_MALE);
        DelayCommand (0.5f, AssignCommand (me, SetCameraMode (me, CAMERA_MODE_TOP_DOWN)));
        DelayCommand (0.5f, AssignCommand (me, SetCameraFacing (45.0f, -1.0f, -1.0f, CAMERA_TRANSITION_TYPE_SNAP)));
        DelayCommand (0.5f, AssignCommand (me, SetCameraFacing (-1.0f, -1.0f, 50.0f, CAMERA_TRANSITION_TYPE_SNAP)));
        DelayCommand (0.5f, AssignCommand (me, SetCameraFacing (-1.0f, 20.0f, -1.0f, CAMERA_TRANSITION_TYPE_SNAP)));

        ApplyEffectToObject (DURATION_TYPE_TEMPORARY, EffectCutsceneGhost(), tony, 360000.0f);
        ApplyEffectToObject (DURATION_TYPE_TEMPORARY, EffectCutsceneGhost(), cata, 360000.0f);
        ApplyEffectToObject (DURATION_TYPE_TEMPORARY, EffectCutsceneGhost(), soza1, 360000.0f);
        ApplyEffectToObject (DURATION_TYPE_TEMPORARY, EffectCutsceneGhost(), soza2, 360000.0f);

        for (i=0; i<3; i++){
            smuggler = GetObjectByTag ("OBT9_Smuggler", i);
            DelayCommand (0.5f, Face (smuggler, soza1));
        }

        DelayCommand (0.5f, Face (tony, soza1));
        DelayCommand (0.5f, Face (cata, soza2));
        DelayCommand (0.5f, Face (soza1, tony));
        DelayCommand (0.5f, Face (soza2, tony));
        DelayCommand (0.5f, Face (soza3, cata));
        DelayCommand (0.5f, Face (soza4, tony));

        DelayCommand (1.0f, AssignCommand (soza1, ActionMoveToObject (GetObjectByTag ("WP_OBT9_Meet1"), FALSE)));
        DelayCommand (5.0f, AssignCommand (tony, ActionMoveToObject (GetObjectByTag ("WP_OBT9_Meet2"), FALSE)));
        DelayCommand (8.0f, Speak (soza1, "Tony. Nice to see you're on time.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (12.0f, Speak (tony, "Matter of courtesy, as my father used to say.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (16.0f, Speak (soza1, "Yeah. Your father was a good man.", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (19.0f, Speak (tony, "Yeah.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (22.0f, Speak (soza1, "So. I hear you've got something for us.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (25.0f, Speak (tony, "Feel free to take a look.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (27.0f, Face (tony, crate));
        DelayCommand (27.5f, Speak (tony, "(Tony points at the crates)", ANIMATION_FIREFORGET_HEAD_TURN_RIGHT, 1.0f));
        DelayCommand (28.0f, Face (soza1, crate));
        DelayCommand (30.5f, AssignCommand (soza1, ActionMoveToObject (crate, FALSE)));
        DelayCommand (35.0f, Speak (soza1, "Very good...", ANIMATION_FIREFORGET_HEAD_TURN_LEFT, 1.0f));
        DelayCommand (37.0f, Face (soza1, tony));
        DelayCommand (38.5f, Speak (tony, "Ten crates full of the finest seeds money can buy this side of the Mountains.", ANIMATION_LOOPING_TALK_NORMAL, 3.5f));
        DelayCommand (42.0f, Face (soza1, crate));

        DelayCommand (42.0f, Speak (soza1, "These are going to make a lot of people very, very happy.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (44.0f, Face (soza1, crate));
        DelayCommand (44.0f, Speak (tony, "No doubt.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (46.0f, Speak (soza1, "Excellent work, Tony.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (49.0f, Speak (tony, "You've got the gold?", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (52.0f, Speak (soza1, "Yep.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (53.0f, Face (soza1, soza2));
        DelayCommand (54.0f, Speak (soza1, "Show them.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (56.0f, Speak (cata, "With your permission, I'd like to take a look myself.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (57.0f, Face (soza1, cata));
        DelayCommand (59.0f, Speak (soza1, "Of course.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (61.0f, AssignCommand (cata, ActionMoveToObject (chest, FALSE)));
        DelayCommand (64.0f, Face (soza1, cata));
        DelayCommand (64.0f, Face (soza2, cata));
        DelayCommand (66.0f, Face (cata, soza1));
        DelayCommand (67.0f, Speak (cata, "Yes. This looks good.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (68.0f, Face (soza1, tony));
        DelayCommand (69.0f, Speak (soza1, "We don't fuck our suppliers, Tony.", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (70.0f, Face (tony, soza1));
        DelayCommand (70.5f, Face (cata, soza1));
        DelayCommand (72.0f, Speak (soza1, "Trust. Another matter of courtesy.", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (75.0f, Speak (tony, "You don't stay alive in this business if you're too trusting.", ANIMATION_LOOPING_TALK_NORMAL, 3.5f));
        DelayCommand (79.0f, Speak (soza1, "Yeah. I guess you're right.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (82.0f, Speak (tony, "Need any help getting this on board your ship?", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (85.0f, Speak (soza1, "Sure. Your men's help would be most welcome.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (87.0f, Face (tony, smuggler));
        DelayCommand (88.0f, Speak (tony, "Take care of it, okay?", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (91.0f, Speak (smuggler, "Sure thing, Tony.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (94.0f, Speak (soza1, "So I hear you've got a new lady?", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (95.0f, Face (tony, soza1));
        DelayCommand (97.0f, Speak (soza1, "One so strikingly beautiful she makes a man weak in his knees when she just passes him?", ANIMATION_LOOPING_TALK_NORMAL, 4.5f));
        DelayCommand (102.0f, Speak (tony, "Where did you hear that?", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (105.0f, Speak (soza1, "People talk.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (108.0f, Speak (tony, "I do have a new girl, yes.", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (111.0f, Speak (soza1, "Think we could meet her?", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (114.0f, Speak (tony, "She's not here.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (117.0f, Speak (soza1, "Of course not.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (120.0f, Speak (soza1, "But maybe you would invite us to your house?", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (123.0f, Speak (tony, "You're not sailing back to Sargoza right away?", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (126.0f, Speak (soza1, "My men will, together with our precious cargo.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (129.0f, Speak (soza1, "Erny and I could just sail to Betancuria together with you.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (132.0f, Speak (soza1, "If you don't mind, that is.", ANIMATION_LOOPING_TALK_NORMAL, 1.5f));
        DelayCommand (135.0f, Speak (tony, "I'm afraid our boat's a little too small.", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (138.0f, Speak (tony, "But I'll get a ship to pick you up soon as possible.", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (141.0f, Speak (soza1, "You're going to keep us company in the meantime, I hope?", ANIMATION_LOOPING_TALK_NORMAL, 2.5f));
        DelayCommand (144.0f, Speak (soza1, "This place seems a bit... dull...", ANIMATION_LOOPING_TALK_NORMAL, 2.0f));
        DelayCommand (147.0f, Speak (tony, "Yeah, sure. Cat and I will stay with you while we wait for the ship.", ANIMATION_LOOPING_TALK_NORMAL, 3.0f));
        DelayCommand (151.0f, Speak (soza1, "Excellent.", ANIMATION_LOOPING_TALK_NORMAL, 1.0f));
        DelayCommand (152.0f, Face (tony, cata));
        DelayCommand (153.0f, Speak (tony, "Tell Vico to get the gold and everything else to Betancuria, then to return with a larger ship.", ANIMATION_LOOPING_TALK_NORMAL, 4.5f));
        DelayCommand (154.0f, Face (cata, tony));
        DelayCommand (158.0f, Speak (cata, "You got it.", ANIMATION_FIREFORGET_SALUTE, 1.0f));
        DelayCommand (161.0f, AssignCommand (cata, ActionMoveToObject (GetObjectByTag ("WP_OBT9_CataOut"))));
        DelayCommand (163.0f, FadeToBlack (me));
        DelayCommand (164.0f, SetCreatureAppearanceType (me, APPEARANCE_TYPE_HUMAN));
        DelayCommand (164.0f, SetCutsceneMode (me, FALSE));
        DelayCommand (166.0f, AssignCommand (me, JumpToObject (GetObjectByTag ("WP_TH5_In"))));
    }
}
