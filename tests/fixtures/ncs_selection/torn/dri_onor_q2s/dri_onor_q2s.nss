void main()
{
    object oPC = GetPCSpeaker();
    location lWay = GetLocation(GetObjectByTag("WP_onorspawn"));
    location lSpawn = GetLocation(GetObjectByTag("WP_bob"));


    CreateObject(OBJECT_TYPE_CREATURE, "onor_bob", lSpawn, FALSE, "bob");
    SetLocalInt(oPC, "sevnight", 1);
    AssignCommand(oPC, ActionJumpToLocation(lWay));

}
