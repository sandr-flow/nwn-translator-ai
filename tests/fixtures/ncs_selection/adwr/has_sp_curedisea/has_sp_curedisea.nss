int StartingConditional()
{
    object jacob = GetObjectByTag ("FatherJacob");

    if (GetHasSpell (SPELL_REMOVE_DISEASE, jacob) > 0) return TRUE;
    else return FALSE;
}
