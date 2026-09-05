void main()
{
    object oPC = GetFirstPC();

if (GetLocalInt(oPC, "werecat_dead") == 1)
    {

    if (GetLocalInt(oPC, "tp_") == 0)
    {
    AssignCommand(GetFirstPC(), JumpToObject(GetObjectByTag("tp_a")));
    SetLocalInt(GetFirstPC(), "tp_", 1);
             if (GetLocalInt(oPC, "tp_phrase") == 0)
             {
             AssignCommand(GetFirstPC(), SpeakString("Tiens-tiens!...Un passage secret magique...Voyons où il mène..."));
             SetLocalInt(GetFirstPC(), "tp_phrase", 1);
             }
    }
    else if (GetLocalInt(oPC, "tp_") == 1)
    {
    AssignCommand(GetFirstPC(), JumpToObject(GetObjectByTag("tp_b")));
    SetLocalInt(GetFirstPC(), "tp_", 0);
    }

    }

else

     if (GetLocalInt(oPC, "tp_phrase2") == 0)
             {
             AssignCommand(GetFirstPC(), SpeakString("Une étrange vibration émane de cette bibliothèque...Il faudrait avoir davantage de temps pour l'examiner en détail..."));
             SetLocalInt(GetFirstPC(), "tp_phrase2", 1);
             }







}
