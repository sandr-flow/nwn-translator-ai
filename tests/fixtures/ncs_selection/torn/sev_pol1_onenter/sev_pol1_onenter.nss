#include "in_g_cutscene"

void main()
{
    int doneonce = GetLocalInt(GetObjectByTag("sev_pol1"), "doneonce");
    object oPC = GetEnteringObject();
    if( !GetIsPC(oPC) || doneonce)
        return;

    object oPollex = GetObjectByTag("pol_pollex");
    object oWraith = GetObjectByTag("pol_wraith");
    object oPCMoveTo = GetNearestObjectByTag("wp_pol_pc_moveto", oPC);
    object oCameraSetup = GetNearestObjectByTag("wp_pol_camera", oPC);

    effect ePollex = EffectVisualEffect(VFX_IMP_FLAME_M);
    effect eWraith = EffectVisualEffect(VFX_FNF_SUMMON_MONSTER_1);
    // Cutscene Start
    GestaltStartCutscene(oPC, "Pollex1");
    GestaltActionMove(0.0, oPC, oPCMoveTo, FALSE, 0.0, 3.0);
    GestaltFace(3.0, oPC, GetFacing(oPCMoveTo));

    GestaltSpeak(1.0, oPollex, "...why should he have so much and you so little? Just take it!", ANIMATION_LOOPING_TALK_FORCEFUL, 5.0);
    GestaltSpeak(6.0, oWraith, "Perhaps... but too many ears are listening. Let's depart for now.");

    DelayCommand(9.0, ApplyEffectAtLocation(DURATION_TYPE_INSTANT, ePollex, GetLocation(oPollex)));
    DelayCommand(9.0, ApplyEffectAtLocation(DURATION_TYPE_INSTANT, eWraith, GetLocation(oWraith)));

    GestaltDestroy(9.1, oPollex);
    GestaltDestroy(9.1, oWraith);

    GestaltStopCutscene(10.7, oPC);
    // Camera Movements
    GestaltCameraFacing(0.0,
                        GestaltGetDirection(oPollex, oCameraSetup), 15.0, 55.0,
                        oPC, CAMERA_TRANSITION_TYPE_FAST);

    // End Cutscene
    object oWP = GetWaypointByTag("wp_pol2_guard1");
    object oGuard = CreateObject(OBJECT_TYPE_CREATURE, "sevofficer", GetLocation(oWP));

    SetLocalInt(oPC, "pol_Viewed", 1);
    AddJournalQuestEntry("polleximp", 1, oPC);
    CreateObject(OBJECT_TYPE_CREATURE, "ghosthouseguy", GetLocation(GetWaypointByTag("wp_Ghosthouse")));
    SetLocalInt(GetObjectByTag("sev_pol1"), "doneonce", 1);

}
