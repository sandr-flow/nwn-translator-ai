int StartingConditional()
{
    if((GetLocalInt(GetPCSpeaker(), "PirateWon") == 1) || (GetLocalInt(GetPCSpeaker(), "PirateWon") == 2))
    {
        return TRUE;
    }

    return FALSE;
}
