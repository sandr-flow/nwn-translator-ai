void main()
{
    object oPC = GetPCSpeaker();
    object oOldMan = OBJECT_SELF;
    ChangeToStandardFaction(OBJECT_SELF, STANDARD_FACTION_HOSTILE);
    SpeakString("Ahh! No! Someone Help!");

    AssignCommand(oPC, ActionAttack(oOldMan));
}
