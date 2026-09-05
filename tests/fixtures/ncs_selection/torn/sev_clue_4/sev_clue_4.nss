void main()
{
    //Adds the clue for the Wizard Quest.
    //Updates the Journal as necessary.

    object oPC = GetFirstPC();
    SetLocalInt(oPC, "WizardQuestClue", 1);         //journal clue 4

    int iC = GetLocalInt(oPC, "CollectorClue");     //journal clue 1
    int iK = GetLocalInt(oPC, "KeepClue");          //journal clue 2
    int iF = GetLocalInt(oPC, "FighterQuestClue");  //journal clue 3
    int iR = GetLocalInt(oPC, "RogueQuestClue");    //journal clue 5

    if (iC) //1
    {
        if (iK) //2
        {
            if (iF) //3
            {
                if (iR) //5
                {
                    //all clues obtained
                    AddJournalQuestEntry("baronquest", 33, oPC);
                    GiveXPToCreature(oPC, GetJournalQuestExperience("baronquest"));
                    SetLocalInt(oPC, "fightquest", 1); //fighter quest open
                    AddJournalQuestEntry("arenafightquest", 1, oPC);
                    SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                    AddJournalQuestEntry("sorcerorpet", 1, oPC);
                    SetLocalInt(oPC, "roguequest", 1); //rogue quest open
                    AddJournalQuestEntry("realestatequest", 1, oPC);
                }
                else
                {
                    //4 1 2 3
                    AddJournalQuestEntry("baronquest", 28, oPC);
                    SetLocalInt(oPC, "fightquest", 1); //fighter quest open
                    AddJournalQuestEntry("arenafightquest", 1, oPC);
                    SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                    AddJournalQuestEntry("sorcerorpet", 1, oPC);


                }
            }
            else if (iR)
            {
                //4 1 2 5
                AddJournalQuestEntry("baronquest", 30, oPC);
                SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                AddJournalQuestEntry("sorcerorpet", 1, oPC);
                SetLocalInt(oPC, "roguequest", 1); //rogue quest open
                AddJournalQuestEntry("realestatequest", 1, oPC);

            }
            else
            {
                //4 1 2
                AddJournalQuestEntry("baronquest", 19, oPC);
                SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                AddJournalQuestEntry("sorcerorpet", 1, oPC);
            }
        }
        else if (iF) //3
        {
            if (iR) //5
            {
                //4 1 3 5
                AddJournalQuestEntry("baronquest", 31, oPC);
                SetLocalInt(oPC, "fightquest", 1); //fighter quest open
                AddJournalQuestEntry("arenafightquest", 1, oPC);

            }
            else
            {
                //4 1 3
                AddJournalQuestEntry("baronquest", 21, oPC);
                SetLocalInt(oPC, "fightquest", 1); //fighter quest open
                AddJournalQuestEntry("arenafightquest", 1, oPC);
            }
        }
        else
        {
            if (iR) //5
            {
                //4 1 5
                AddJournalQuestEntry("baronquest", 23, oPC);
            }
            else
            {
                //4 1
                AddJournalQuestEntry("baronquest", 10, oPC);
            }
        }
    }
    //end looking for leading 1

    else if (iK) //2
    {
        if (iF) //3
        {
            if (iR) //5
            {
                //4 2 3 5
                AddJournalQuestEntry("baronquest", 32, oPC);
                SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                AddJournalQuestEntry("sorcerorpet", 1, oPC);
                SetLocalInt(oPC, "roguequest", 1); //rogue quest open
                AddJournalQuestEntry("realestatequest", 1, oPC);
            }
            else
            {
                //4 2 3
                AddJournalQuestEntry("baronquest", 24, oPC);
                SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
                AddJournalQuestEntry("sorcerorpet", 1, oPC);
            }
        }
        else if (iR)
        {
            //4 2 5
            AddJournalQuestEntry("baronquest", 26, oPC);
            SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
            AddJournalQuestEntry("sorcerorpet", 1, oPC);
            SetLocalInt(oPC, "roguequest", 1); //rogue quest open
            AddJournalQuestEntry("realestatequest", 1, oPC);
        }
        else
        {
            //4 2
            AddJournalQuestEntry("baronquest", 13, oPC);
            SetLocalInt(oPC, "wizquest", 1);   //wizard quest open
            AddJournalQuestEntry("sorcerorpet", 1, oPC);
        }
    }
    //end looking for leading 2


    else if (iF) //3
    {
        if (iR) //5
        {
            //4 3 5
            AddJournalQuestEntry("baronquest", 27, oPC);
        }
        else
        {
            //4 3
            AddJournalQuestEntry("baronquest", 15, oPC);
        }
    }
    //end looking for leading 3


    else if (iR) //5
    {
        //4 5
        AddJournalQuestEntry("baronquest", 17, oPC);
    }

    //end looking for leading 5
    else
    {
        //4
        AddJournalQuestEntry("baronquest", 5, oPC);
    }
}
