//Text appears when (ability check)
int StartingConditional()
{
    object me = GetFirstPC();
    int n = GetSkillRank (SKILL_BLUFF, me);

    SendMessageToPC (me, "Bluff: " + IntToString (n) + " versus 10");
    if (n >= 10){
        GiveXPToCreature (me, 100);
        return TRUE;
    }
    else return FALSE;
}
