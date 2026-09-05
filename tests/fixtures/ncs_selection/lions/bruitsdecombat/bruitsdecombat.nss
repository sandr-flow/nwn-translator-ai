void main()
{
    object oPC = GetFirstPC();
    if (GetLocalInt(oPC, "bruitsdecombat") != 1)
    {
           //AssignCommand(oPC, ClearAllActions());
           DelayCommand(0.1, AssignCommand(oPC, SpeakString("Vous entendez les bruits de la bataille dans le lointain...Mais, cela ne veut pas dire qu'il ne puisse pas y avoir d'Anglais dans la zone où vous vous trouvez...")));

           SetLocalInt(oPC, "bruitsdecombat", 1);
           //SetLocalInt(oPC, "", 1);
    }
}
