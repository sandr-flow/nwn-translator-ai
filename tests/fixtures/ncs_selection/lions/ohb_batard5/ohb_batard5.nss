void main()
{

   object oPC = GetFirstPC();
   int nDay = GetCalendarDay();



if (GetLocalInt(oPC, "debut_cluse") == 0)
 {
   if (GetLocalInt(oPC, "timer_on") == 1)
    {



   if(nDay == 19)
   {



   }




    else if(nDay == 20)
   {
         if (GetLocalInt(oPC, "ss_day21") != 1)
            {
           AssignCommand(oPC, SpeakString("Dans 5 jours vous devrez être prêt à rejoindre la cluse de Saint Blaise où vous devrez stopper les renforts français en marche vers l'Auxerrois..."));
           SetLocalInt(oPC, "ss_day21", 1);
            }
   }


  else if(nDay == 21)
   {
          if (GetLocalInt(oPC, "ss_day21") != 1)
            {
           AssignCommand(oPC, SpeakString("Vous avez 4 jours pour vous préparer à la bataille finale..."));
           SetLocalInt(oPC, "ss_day21", 1);
            }
   }



   else if(nDay == 22)
   {
          if (GetLocalInt(oPC, "ss_day22") != 1)
            {
           AssignCommand(oPC, SpeakString("Il vous reste trois jours pour que vos troupes soient prêtes pour la bataille finale..."));
           SetLocalInt(oPC, "ss_day22", 1);
            }
   }



   else if(nDay == 23)
   {
          if (GetLocalInt(oPC, "ss_day23") != 1)
            {
           AssignCommand(oPC, SpeakString("Dans 48 heures maximum vos troupes devront faire mouvement en direction de la cluse de Saint Blaise..."));
           SetLocalInt(oPC, "ss_day23", 1);
            }

   }


   else if(nDay == 24)
   {
          if (GetLocalInt(oPC, "ss_day24") != 1)
            {
           AssignCommand(oPC, SpeakString("Il vous reste moins de 24 heures pour faire mouvement en direction de la cluse de Saint Blaise...Cette fois, ça commence sérieusement à urger!..."));
           SetLocalInt(oPC, "ss_day24", 1);
            }


   }


   else if(nDay == 25)
   {
           if (GetLocalInt(oPC, "ss_day25") != 1)
            {
           AssignCommand(oPC, SpeakString("Il est trop tard...Le temps que vous arriviez à la cluse de Saint Blaise, les troupes françaises seront déjà passées...VOUS AVEZ IRREMEDIABLEMENT PERDU KOSIGAN, et le duché de Bourgogne repassera aux mains du royaume de France...C'est donc avec une certaine amertume que vous quittez la région et que vous partez en direction de la ville de Rome...Souhaitons que là-bas la réussite vous sourie davantage qu'elle ne l'a fait ici..."));
           DelayCommand(8.0, AssignCommand(oPC, SpeakString("Il est trop tard...Le temps que vous arriviez à la cluse de Saint Blaise, les troupes françaises seront déjà passées...VOUS AVEZ IRREMEDIABLEMENT PERDU KOSIGAN, et le duché de Bourgogne repassera aux mains du royaume de France...C'est donc avec une certaine amertume que vous quittez la région et que vous partez en direction de la ville de Rome...Souhaitons que là-bas la réussite vous sourie davantage qu'elle ne l'a fait ici...")));
           DelayCommand(38.0, AssignCommand(oPC, JumpToObject(GetObjectByTag("fin_echec"))));

           SetLocalInt(oPC, "ss_day25", 1);
            }

      }
   }
 }


}
