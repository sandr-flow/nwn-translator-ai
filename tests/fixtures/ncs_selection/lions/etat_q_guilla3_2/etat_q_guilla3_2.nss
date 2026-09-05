void main()
{
object oItem = GetObjectByTag("EPEE_BATARDE");
object oItem2 = GetObjectByTag("DOCUMENTS_COMPROMETTANTS");
object oItem3 = GetObjectByTag("FLEUR_SPECTRALE");
object oItem4 = GetObjectByTag("ecaille_grotte");
object oItem5 = GetObjectByTag("parcheminpartiel");
object oItem6 = GetObjectByTag("spiritussanctus");


object oPC = GetItemPossessor(oItem);
object oPC2 = GetItemPossessor(oItem2);
object oPC3 = GetItemPossessor(oItem3);
object oPC4 = GetItemPossessor(oItem4);
object oPC5 = GetItemPossessor(oItem5);
object oPC6 = GetItemPossessor(oItem6);



if (GetModuleItemAcquired() == oItem)
  {
  if (GetLocalInt(GetFirstPC(), "epee_recup") != 1)
    {
  AddJournalQuestEntry("Q_guillaumette", 4, oPC, FALSE, FALSE, TRUE);
  SetLocalInt(GetFirstPC(),"Etat_Q_Guillaumette_Gweth", 1);
  SetLocalInt(GetFirstPC(),"epee_recup", 1);
    }
  }

if (GetModuleItemAcquired() == oItem2)
  {
  AddJournalQuestEntry("Q1", 5, oPC2, FALSE, FALSE, FALSE);
  SetLocalInt(GetFirstPC(),"Etat_Q_Ogier", 5);
  SetLocalInt(GetFirstPC(),"doc_compro", 1);
  }

if (GetModuleItemAcquired() == oItem3)
  {
  AddJournalQuestEntry("Q_fleur", 2, oPC3, FALSE, FALSE, FALSE);
  SetLocalInt(GetFirstPC(),"Etat_Q_Fleur", 2);
  }

if (GetModuleItemAcquired() == oItem4)
  {
    if (GetLocalInt(oPC4, "ecaille_recup") != 1)
    {
     AssignCommand(oPC, SpeakString("Une écaille de Dragon Rouge?!!...Mmmh!...Cette grotte ne vous dit rien qui vaille!..."));
     //AddJournalQuestEntry("Q_fleur", 2, oPC3, FALSE, FALSE, FALSE);
     SetLocalInt(GetFirstPC(),"ecaille_recup", 1);
    }
  }

if (GetModuleItemAcquired() == oItem5)
  {
     if (GetLocalInt(GetFirstPC(), "rec_parch") != 1)
    {
  //AssignCommand(oPC, SpeakString(""));
  SetLocalInt(GetFirstPC(),"rec_parch", 1);
  AssignCommand (GetFirstPC() ,PlaySound("it_ring"));
  GiveXPToCreature(GetFirstPC(), 100);


         if (GetLocalInt(oPC, "pentagramme") != 1)
          {
            if (GetLocalInt(oPC, "livre_exor") != 1)
            {
                AddJournalQuestEntry("verite", 10, oPC, FALSE, FALSE, TRUE);
            }
            else if (GetLocalInt(oPC, "livre_exor") == 1)
            {
          AddJournalQuestEntry("verite", 30, oPC, FALSE, FALSE, TRUE);
            }

          }

        else if (GetLocalInt(oPC, "pentagramme") == 1)
          {
             if (GetLocalInt(oPC, "livre_exor") != 1)
            {
                AddJournalQuestEntry("verite", 20, oPC, FALSE, FALSE, TRUE);
            }
             else if (GetLocalInt(oPC, "livre_exor") == 1)
            {
          AddJournalQuestEntry("verite", 40, oPC, FALSE, FALSE, TRUE);
            }
          }



    }
  }

if (GetModuleItemAcquired() == oItem6)
  {
     if (GetLocalInt(GetFirstPC(), "livre_exor") != 1)
    {
  SetLocalInt(GetFirstPC(),"livre_exor", 1);
  AssignCommand (GetFirstPC() ,PlaySound("it_ring"));
  GiveXPToCreature(GetFirstPC(), 20);


           if (GetLocalInt(oPC, "pentagramme") != 1)
          {
            if (GetLocalInt(oPC, "rec_parch") != 1)
            {
                AddJournalQuestEntry("verite", 15, oPC, FALSE, FALSE, TRUE);
            }
            else if (GetLocalInt(oPC, "rec_parch") == 1)
            {
          AddJournalQuestEntry("verite", 30, oPC, FALSE, FALSE, TRUE);
            }

          }

        else if (GetLocalInt(oPC, "pentagramme") == 1)
          {
             if (GetLocalInt(oPC, "rec_parch") != 1)
            {
                AddJournalQuestEntry("verite", 25, oPC, FALSE, FALSE, TRUE);
            }
             else if (GetLocalInt(oPC, "rec_parch") == 1)
            {
          AddJournalQuestEntry("verite", 40, oPC, FALSE, FALSE, TRUE);
            }
          }











    }
  }






}
