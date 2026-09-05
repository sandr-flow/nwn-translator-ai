//Script sending you to Arena from holding area and triggering next fight.
void StartFight(object oPC, int iFightNum);
void SpawnCrowd();

void main()
{


    //Sends PC to Arena
    object oPC = GetPCSpeaker();
    object oBaron = GetObjectByTag("mysterious");
    object oThrone = GetObjectByTag("SitThrone");
    object oWaypoint = GetObjectByTag("wp_gladfight");
    location lLocation = GetLocation(oWaypoint);
    int iFightNum = GetLocalInt(oPC, "iArenaFightNum");

    if( iFightNum < 1 )
    {
        iFightNum = 1;
        SetLocalInt(oPC, "iArenaFightNum", iFightNum);
    }

    if( GetLocalInt(oPC, "ArenaDied") )
        SetLocalInt(oPC, "ArenaDied", 0);
    SpawnCrowd();
    StartFight(oPC, iFightNum);

    SetAreaTransitionBMP(AREA_TRANSITION_CITY);

    AssignCommand(oPC,ClearAllActions());
    AssignCommand(oPC,ActionJumpToLocation(lLocation));
    AssignCommand(oPC,SetFacing(GetFacing(oWaypoint)));
    AssignCommand(oBaron, ActionSit(oThrone));

}
void SpawnCrowd()
{
    object oSpecStart;
    location lSpecStart;
    int iCount = 1;
    int iIteration;
    string sWaypointTag;
    object oSpec;
    for( iIteration = 1; iIteration <= 6; iIteration++ )
    {
        for(iCount = 1; iCount <= 4; iCount++)
        {
            oSpecStart = GetWaypointByTag("spectator" + IntToString(iIteration) + "_spawn" + IntToString(iCount));
            lSpecStart = GetLocation(oSpecStart);
            oSpec = CreateObject(OBJECT_TYPE_CREATURE, "spectator" + IntToString(iIteration), lSpecStart, FALSE, "Spectator"+IntToString(iIteration)+IntToString(iCount));
        }
    }
}
void StartFight(object oPC, int iFightNum)
{
    string sOpponentTag;
    object wGladiator1, wGladiator2, wGladiator3, wGladiator4, wGladiator5;
    object oGladiator1, oGladiator2, oGladiator3, oGladiator4, oGladiator5;
    object oEvil = GetObjectByTag("ta_bawizardchick");

    switch( iFightNum )
    {
    case 1:
        wGladiator1 = GetWaypointByTag("wp_opponent");
        wGladiator2 = GetWaypointByTag("wp_opponent2");
        wGladiator3 = GetWaypointByTag("wp_opponent3");
        wGladiator4 = GetWaypointByTag("wp_opponent4");
        wGladiator5 = GetWaypointByTag("wp_opponent5");

        oGladiator1 = GetObjectByTag("Fram");
        oGladiator2 = GetObjectByTag("Erurbag");
        oGladiator3 = GetObjectByTag("Belalendel");
        oGladiator4 = GetObjectByTag("Ronus");
        oGladiator5 = GetObjectByTag("Merin");

        AssignCommand(oGladiator1, ActionJumpToLocation(GetLocation(wGladiator1)));
        while( GetPlotFlag(oGladiator1) )
        {
            SetPlotFlag(oGladiator1, FALSE);
        }

        AssignCommand(oGladiator2, ActionJumpToLocation(GetLocation(wGladiator2)));
        while( GetPlotFlag(oGladiator2) )
        {
            SetPlotFlag(oGladiator2, FALSE);
        }

        AssignCommand(oGladiator3, ActionJumpToLocation(GetLocation(wGladiator3)));
        while( GetPlotFlag(oGladiator3) )
        {
            SetPlotFlag(oGladiator3, FALSE);
        }

        AssignCommand(oGladiator4, ActionJumpToLocation(GetLocation(wGladiator4)));
        while( GetPlotFlag(oGladiator4) )
        {
            SetPlotFlag(oGladiator4, FALSE);
        }

        AssignCommand(oGladiator5, ActionJumpToLocation(GetLocation(wGladiator5)));
        while( GetPlotFlag(oGladiator5) )
        {
            SetPlotFlag(oGladiator5, FALSE);
        }
        ChangeFaction(oGladiator1, oEvil);
        ChangeFaction(oGladiator2, oEvil);
        ChangeFaction(oGladiator3, oEvil);
        ChangeFaction(oGladiator4, oEvil);
        ChangeFaction(oGladiator5, oEvil);

        break;
    case 2:
        sOpponentTag = "ta_gladiator2";
        break;
    case 3:
        sOpponentTag = "ta_gladiator3";
        break;
    case 4:
        sOpponentTag = "ta_gladiator5";
        int iSteroids = GetLocalInt(oPC, "GotSteriods");
        object oCuprak = GetObjectByTag(sOpponentTag);
        MusicBattleChange(GetArea(GetWaypointByTag("wp_opponent")), 89);

        if( !iSteroids )
        {
            AssignCommand(oCuprak, SpeakOneLinerConversation("CUPRAK SMASH!!!"));
            effect eStrBuff = EffectAbilityIncrease(ABILITY_STRENGTH, 6);
            effect eDexBuff = EffectAbilityIncrease(ABILITY_DEXTERITY, 4);
            effect eConBuff = EffectAbilityIncrease(ABILITY_CONSTITUTION, 6);

            ApplyEffectToObject(DURATION_TYPE_PERMANENT, eStrBuff, oCuprak);
            ApplyEffectToObject(DURATION_TYPE_PERMANENT, eDexBuff, oCuprak);
            ApplyEffectToObject(DURATION_TYPE_PERMANENT, eConBuff, oCuprak);
        }
        break;
    }

    SetLocalString(oPC, "opponent", sOpponentTag);

    object oFighter = GetObjectByTag(sOpponentTag);
    object oWaypoint = GetWaypointByTag("wp_opponent");
    location lFighterStart = GetLocation(oWaypoint);

    ChangeFaction(oFighter, oEvil);
    SetPlotFlag(oFighter, FALSE);
    SetLocalInt(oPC, "ArenaFightState", 1);
    SetLocalObject(oPC, "ArenaFighter", oFighter);
    AssignCommand(oFighter, ActionJumpToLocation(lFighterStart));
    AssignCommand(oFighter, SetFacing(GetFacing(oWaypoint)));
    DelayCommand(5.0, AssignCommand(oFighter, ActionAttack(oPC)));
}
