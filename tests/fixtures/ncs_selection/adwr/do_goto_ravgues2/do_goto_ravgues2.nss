void main()
{
    object me = GetFirstPC();
    object castias = GetObjectByTag ("Castias");

    SetLocalInt (me, "RavTailIn", 0);
    SetCreatureTailType (CREATURE_TAIL_TYPE_NONE, me);

    SetTime (18, 0, 0, 0);

    SetLocalInt (castias, "Talk", 3);
    DelayCommand (0.5f, AssignCommand (castias, JumpToObject (GetObjectByTag ("WP_NRC2_Castias"))));
    DelayCommand (0.5f, AssignCommand (GetObjectByTag ("Terek"), JumpToObject (GetObjectByTag ("WP_NRC2_Terek"))));
    DelayCommand (0.5f, AssignCommand (GetObjectByTag ("Ashald"), JumpToObject (GetObjectByTag ("WP_NRC2_Ashald"))));
    DelayCommand (0.5f, AssignCommand (GetObjectByTag ("Dana"), JumpToObject (GetObjectByTag ("WP_NRC2_Dana"))));
    DelayCommand (0.5f, AssignCommand (GetObjectByTag ("Timin"), JumpToObject (GetObjectByTag ("WP_NRC2_Timin"))));
    DelayCommand (0.5f, AssignCommand (GetObjectByTag ("Claile"), JumpToObject (GetObjectByTag ("WP_NRC2_Claile"))));
    DelayCommand (1.0f, AssignCommand (me, JumpToObject (GetObjectByTag ("WP_RavCastle1_Me"))));
}
