void main()
{
       object oPC = GetFirstPC();
       object oNPC = GetObjectByTag("paysan__4");
       //string szPlotID =  "LA TOMBE."

       if(GetLocalInt(oPC, "trouve_lieu_tombe") != 1)
        {

         AssignCommand (oPC ,PlaySound("it_ring"));
         GiveXPToCreature(GetPCSpeaker(), 250);
         //DestroyObject(oTarget);
         AssignCommand(oNPC, SpeakString("Voilà, messire, nous y sommes!...La tombe de Jehanne, votre mère!...Ca...Ca fait au moins dix ans que je n'étais pas revenu ici...C'est...c'est bizarre comme le chêne sous lequel elle a été enterrée s'est flétri et a séché..."));

         //AssignCommand(oNPC, SpeakString("Nous y sommes presque!...Suivez-moi!..."));
         //AssignCommand(oNPC, ActionForceMoveToObject(GetObjectByTag("tombe_mere")));

         DelayCommand(2.0, AssignCommand(oPC, SpeakString("Bizarre en effet!...")));
         //DelayCommand(0.4, AssignCommand(oNPC, ActionForceMoveToObject(GetObjectByTag("tombe_mere"), TRUE)));
         AddJournalQuestEntry("LA TOMBE DE VOTRE MERE.", 5, oPC);

         SetLocalInt(oPC, "trouve_lieu_tombe", 1);

         ChangeFaction(oNPC, GetObjectByTag("neutre"));


        }
}
