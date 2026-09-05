/* 69_hench_lib
   Created: 69MEH69 July 2004

   Library functions for henchman control
*/
#include "x0_i0_henchman"
#include "69_leadership"

//void main(){}

/**********************************************************************
 * CONSTANTS
 **********************************************************************/



/**********************************************************************
 * FUNCTION PROTOTYPES
 **********************************************************************/

//Returns TRUE if the object to check is oPC's henchman
int HenchmanCheck69(object oCheck, object oPC);

//Checks to see whether or not the PC can hire the NPC
int GetCanWork69(object oPC, object oHench);

// Can be used for both initial hiring and rejoining.
void HireHenchman69(object oPC, object oHench=OBJECT_SELF, int bAdd=TRUE);

// Can be used to fire henchman or quit a henchman
void QuitHenchman69(object oPC, object oHench=OBJECT_SELF);

//Checks to see whether or not the NPC henchman can level
int GetCanLevelUp69(object oPC, object oHench = OBJECT_SELF);

//Moves items in oHench inventory to oContainer inventory
void MoveHenchmanItems69(object oContainer, object oHench);

//Sets items in oHench inventory as droppable: nDrop = TRUE or FALSE
void HenchmanNoDropItems69(int nDrop, object oHench = OBJECT_SELF);

//Gets the total number of henchmen currently working for the PC
int GetHenchmanTotal69(object oPC);

//Checks whether the henchman has enough XP to level
int HenchXPCheck69(object oHench = OBJECT_SELF);

//Initializes XP for henchman
void HenchXPInitialize69(object oHench = OBJECT_SELF, int nHenchLvl = 0);

//Levels up henchman
//nHired = 0: Standard level up for henchman either XP or non-XP (Default)
//nHired = 1: Initial level up at hire will level up henchman any number of levels
//            to satisfy HenchLag and/or MaxHenchLevel
//nHired = 2: Special level up for specific henchman, new code must be created under this
//            option by the scripter for the particular henchman (see example code in function)
void LevelUpHenchman69(object oHench, object oPC, int nHired = 0);

//Does a check on prestige class levels
void CheckHenchClass69(object oHench);

//Henchman summons Familiar/Animal Companion
//set variable string SummonCreature = TAG of Creature
//to be summoned on oHench
void HenchSummonCreature69(object oHench = OBJECT_SELF);

//Henchman unsummons Familiar/Animal Companion
//set variable string SummonCreature = TAG of Creature
//to be unsummoned by oHench
void HenchUnSummonCreature69(object oHench = OBJECT_SELF);

//DEATH SCRIPTS

location GetRespawnLocation69(object oHench=OBJECT_SELF);
void RespawnHenchman69(object oHench=OBJECT_SELF);
void PostRespawnCleanup69(object oHench=OBJECT_SELF);
void HenchRessurect69(object oPC, object oHealer = OBJECT_SELF);

//COMBAT SCRIPTS

void HenchRearm69(object oIntruder, object oHench = OBJECT_SELF);

//Retuns TRUE if caller has a melee weapon in their inventory
int HasMeleeWeapon69(object oSelf);

//Retuns TRUE if caller has a ranged weapon in their inventory
int HasRangedWeapon69(object oSelf);

//Henchman scouts out enemy, then returns to master
void HenchScout69(object oTarget, object oHench=OBJECT_SELF);

//Reports scouted creatures to oPC
void HenchReport69(object oPC, object oHench=OBJECT_SELF);

/***** MODULE TRANSFER FUNCTIONS *****/

// Call this function when the PC is about to leave a module
// to enable restoration of the henchman on re-entry into the
// sequel module. Both modules must use the same campaign db
// for this to work.
void StoreCampaignHenchman69(object oPC);

// Call this function when a PC enters a sequel module to restore
// the henchman (complete with inventory). The function
// StoreCampaignHenchman must have been called first, and both
// modules must use the same campaign db. (See notes in x0_i0_campaign.)
//
// The restored henchman will automatically be re-hired and will be
// created next to the PC.
void RetrieveCampaignHenchman69(object oPC);

//::FUNCTION DEFINITIONS:://

//Returns TRUE if the object to check is oPC's henchman
int HenchmanCheck69(object oCheck, object oPC)
{
    int nHenchCount = 1;
    object oHench = GetAssociate(ASSOCIATE_TYPE_HENCHMAN, oPC, nHenchCount);
    while(GetIsObjectValid(oHench))
    {
     if(oCheck == oHench)
     {
        return TRUE;
     }
     oHench = GetAssociate(ASSOCIATE_TYPE_HENCHMAN, oPC, ++nHenchCount);
    }
    return FALSE;
}

int GetCanWork69(object oPC, object oHench)
{
 int nLeadership = GetLocalInt(GetModule(), "nLeadership");
 int nLoyalty =GetLocalInt(GetModule(), "nLoyalty");
 int MAX_HENCHMEN = GetMaxHenchmen();
 int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
 //Check for Leadership
 if(nLeadership == 1)
 {
   //Test
   if(TEST_MODE == 1)
   {
     SendMessageToPC(oPC, "Module Leadership = 1");
   }
   MAX_HENCHMEN = GetMaxHenchmen69(oPC);
   if(GetHasLeadership(oPC) == FALSE)
     return FALSE;
 }
 if(nLoyalty == 1 && GetHenchLoyalty(oHench, oPC) <= 3)
 {
   return FALSE;
 }
 int HENCH_LAG = GetLocalInt(OBJECT_SELF, "HenchLag");
 int nHench = X2_GetNumberOfHenchmen(oPC); //Number of henchmen PC currently has
 //Test
 if(TEST_MODE == 1)
 {
  string sHench = IntToString(nHench);
  SendMessageToPC(oPC, "You have " +sHench+ " henchmen");
 }
 int nPClvl = GetHitDice(oPC);
 int nHenchlvl = GetHitDice(oHench);
 int nLevel = nPClvl - nHenchlvl;
 if((nLevel < HENCH_LAG) || (nHench >= MAX_HENCHMEN) || MAX_HENCHMEN <= 0)
 {
   return FALSE;
 }
 return TRUE;
}

void HireHenchman69(object oPC, object oHench=OBJECT_SELF, int bAdd=TRUE)
{
    if ( !GetIsObjectValid(oPC) || !GetIsObjectValid(oHench) )
    {
        return;
    }
//    SpawnScriptDebugger();

    // Fire the PC's former henchman if necessary
    int nCountHenchmen = X2_GetNumberOfHenchmen(oPC);
    int nNumberOfFollowers = X2_GetNumberOfHenchmen(oPC, TRUE);
    // * The true number of henchmen are the number of hired
    nCountHenchmen = nCountHenchmen ;

    int nMaxHenchmen = GetMaxHenchmen(); //69MEH69

    //Check for Leadership, TRUE resets nCountHenchman
    if(GetLocalInt(GetModule(), "nLeadership") == 1)
    {
      nMaxHenchmen = GetMaxHenchmen69(oPC);
    }


    // Adding this henchman would exceed the module imposed
    // henchman limit.
    // Fire the first henchman
    // The third slot is reserved for the follower
    if ((nCountHenchmen  >= nMaxHenchmen) && bAdd == TRUE)
    {
        X2_FireFirstHenchman(oPC);
    }

    // Mark the henchman as working for the given player
    if (!GetPlayerHasHired(oPC, oHench))
    {
        // This keeps track if the player has EVER hired this henchman
        // Floodgate only (XP1). Should never store info to a database as game runs, only between modules or in Persistent setting
        if (GetLocalInt(GetModule(), "X2_L_XP2") !=  1)
        {
            SetPlayerHasHiredInCampaign(oPC, oHench);
        }
        SetPlayerHasHired(oPC, oHench);
    }
    SetLastMaster(oPC, oHench);

    // Clear the 'quit' setting in case we just persuaded
    // the henchman to rejoin us.
    SetDidQuit(oPC, oHench, FALSE);

    // If we're hooking back up with the henchman after s/he
    //  died, clear that.
    SetDidDie(FALSE, oHench);
    SetKilled(oPC, oHench, FALSE);
    SetResurrected(oPC, oHench, FALSE);

    // Turn on standard henchman listening patterns
    SetAssociateListenPatterns(oHench);

    // By default, companions come in with Attack Nearest and Follow
    // modes enabled.
    SetLocalInt(oHench,
                "NW_COM_MODE_COMBAT",ASSOCIATE_COMMAND_ATTACKNEAREST);
    SetLocalInt(oHench,
                "NW_COM_MODE_MOVEMENT",ASSOCIATE_COMMAND_FOLLOWMASTER);

    // Add the henchman
    if (bAdd == TRUE)
    {
        AddHenchman(oPC, oHench);
    }
    //Run Hench XP Initialization
    int HENCH_XP = GetLocalInt(GetModule(), "HENCHXP");
    if(HENCH_XP == 1 && GetLocalInt(oHench, "HENCH_XP") == 0)
    {
      int nMasterLevel = GetHitDice(oPC);
      int HENCH_LAG = GetLocalInt(oHench, "HenchLag");
      int nHenchLevel = nMasterLevel - HENCH_LAG;
      HenchXPInitialize69(oHench, nHenchLevel);
    }
}

void QuitHenchman69(object oPC, object oHench=OBJECT_SELF)
{
  object oHome = GetObjectByTag("WP_Home_" + GetTag(oHench));
  int nHenchQuit = GetLocalInt(GetModule(), "HENCHQUIT");
  string sHench = GetResRef(oHench);
  SetLastMaster(oPC, oHench);

  // If we're hooking back up with the henchman after s/he
  //  died, clear that.
  SetDidDie(FALSE, oHench);
  SetKilled(oPC, oHench, FALSE);
  SetResurrected(oPC, oHench, FALSE);

  if(GetMaster() == oPC)
  {
    ClearAllActions();
    // Turn off stealth mode
    SetActionMode(oHench, ACTION_MODE_STEALTH, FALSE);
    // Move away from PC
    ActionMoveAwayFromObject(oPC, FALSE, 10.0f);
    // Remove the henchman from service
    RemoveHenchman(oPC, oHench);
    // Clear dialogue events
    ClearAllDialogue(oPC, oHench);
    // Check for henchman companion and get rid of it
    if(GetLocalInt(oHench, "HasCompanion"))
    {
     HenchUnSummonCreature69();
    }
  }
  if(nHenchQuit == 1 && GetIsObjectValid(oHome))
  {
    DelayCommand(3.0, DestroyObject(oHench));
    CreateObject(OBJECT_TYPE_CREATURE, sHench, GetLocation(oHome));
  }
  else if(nHenchQuit == 2)
  {
    DelayCommand(3.0, DestroyObject(oHench));
  }
  else if(GetIsObjectValid(oHome))
  {
    DelayCommand(10.0, ActionJumpToObject(oHome));
  }
}

//Leveling Functions

int GetCanLevelUp69(object oPC, object oHench = OBJECT_SELF)
{
    int HENCH_MAXLEVEL = GetLocalInt(oHench, "HENCH_MAXLEVEL");
    int nMasterLevel = GetHitDice(oPC);
    int nMyLevel = GetHitDice(oHench);
    int HENCH_XP = GetLocalInt(GetModule(), "HENCHXP");
    int HENCH_LAG = GetLocalInt(oHench, "HenchLag");
    int TEST_MODE = GetLocalInt(oHench, "TEST_MODE");
    //Test
    if(TEST_MODE == 1)
    {
     string sMasterLevel = IntToString(nMasterLevel);
     string sMyLevel = IntToString(nMyLevel);
     SendMessageToPC(oPC, "My level is " +sMasterLevel+ ", Hench level is " +sMyLevel);
    }
    if((nMasterLevel > (nMyLevel + HENCH_LAG)) && (nMyLevel < HENCH_MAXLEVEL))
    {
      if(HENCH_XP == 1)
      {
       //Test
       if(TEST_MODE == 1)
       {
        SendMessageToPC(oPC, "Hench XP Check");
       }
        return HenchXPCheck69(oHench);
      }
      else
      {
       //Test
       if(TEST_MODE == 1)
       {
        SendMessageToPC(oPC, "True");
       }
       return TRUE;
      }
    }
    else
    {
     //Test
     if(TEST_MODE == 1)
     {
      SendMessageToPC(oPC, "False");
     }
     return FALSE;
    }
}

void LevelUpHenchman69(object oHench, object oPC, int nHired = 0)
{
  string sHenchName = GetName(oHench);
  int HENCH_MAXLEVEL = GetLocalInt(oHench, "HENCH_MAXLEVEL");
  int HENCH_XP = GetLocalInt(GetModule(), "HENCHXP");
  int nHenchXP2 = GetLocalInt(oHench, "HENCH_XP");
  int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
  int nAutoLevelup = GetLocalInt(GetModule(), "nAutoLevelup");
  int nHenchLag = GetLocalInt(OBJECT_SELF, "HenchLag");
  int nNewClass = GetLocalInt(oHench, "NewClass");
  int nClassPackage = GetLocalInt(oHench, "ClassPackage");
  int nPC = GetHitDice(oPC);
  int nLevel = nPC - nHenchLag;
  if(HENCH_MAXLEVEL < nLevel)
  {
   nLevel = HENCH_MAXLEVEL;
  }
  int nHenchXP;
  string sHenchXP, sHenchXP2;
  CheckHenchClass69(oHench);
  if(nHired == 0) //Not Hired
  {
   AssignCommand(oHench, SpeakString("Leveling Up"));
   LevelUpHenchman(oHench, nNewClass, TRUE, nClassPackage);
   if(HENCH_XP == 1)
   {
    //Test
    if(TEST_MODE == 1)
    {
     string sHD = IntToString(GetHitDice(oHench));
     sHenchXP2 = IntToString(nHenchXP2);
     SendMessageToPC(oPC, sHenchName + " has " + sHD + " hit dice.");
     SendMessageToPC(oPC, sHenchName + " has " + sHenchXP2 + " experience points.");
    }
    //End Test
    HenchXPInitialize69(oHench);
    nHenchXP = GetLocalInt(oHench, "HENCH_XP");
    if(nHenchXP < nHenchXP2)
    {
     SetLocalInt(oHench, "HENCH_XP", nHenchXP2);
     nHenchXP = nHenchXP2;
    }
    sHenchXP = IntToString(nHenchXP);
    SendMessageToPC(oPC, sHenchName + " has " + sHenchXP + " experience points.");
   }
  }
  else if(nHired == 1)
  {
   LevelHenchmanUpTo(OBJECT_SELF, nLevel);
   if(HENCH_XP == 1)
   {
    HenchXPInitialize69(oHench, nLevel);
    nHenchXP = GetLocalInt(oHench, "HENCH_XP");
    if(nHenchXP < nHenchXP2)
    {
     SetLocalInt(oHench, "HENCH_XP", nHenchXP2);
     nHenchXP = nHenchXP2;
    }
    sHenchXP = IntToString(nHenchXP);
    SendMessageToPC(oPC, sHenchName + " has " + sHenchXP + " experience points.");
   }
   else if(nHired == 2)  //Modify example code to satisfy needs for particular henchman
   {                     //See x0_i0_henchman line 1292 for examples of code
    //Begin Example Code
    if(GetTag(OBJECT_SELF) == "HENCHTAG")
    {
     LevelHenchmanUpTo(OBJECT_SELF, nLevel, CLASS_TYPE_INVALID, nLevel); //See notes for this particular function
    }
    //End Example Code
   }
  }
}

void CheckHenchClass69(object oHench)
{
 int nNewClass = GetLocalInt(oHench, "NewClass");

 if(nNewClass == -1)
 {
    SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
    SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
 }
 if(nNewClass == 29) //CLASS_TYPE_ARCANE_ARCHER
 {
   if (GetLevelByClass(CLASS_TYPE_ARCANE_ARCHER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 30) //CLASS_TYPE_ASSASSIN
 {
   if (GetLevelByClass(CLASS_TYPE_ASSASSIN, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 31) //CLASS_TYPE_BLACKGUARD
 {
   if (GetLevelByClass(CLASS_TYPE_BLACKGUARD, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 28) //CLASS_TYPE_HARPER
 {
   if (GetLevelByClass(CLASS_TYPE_HARPER, oHench) == 5)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 27) //CLASS_TYPE_SHADOWDANCER
 {
   if (GetLevelByClass(CLASS_TYPE_SHADOWDANCER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 32) //CLASS_TYPE_DIVINECHAMPION
 {
   if (GetLevelByClass(CLASS_TYPE_DIVINECHAMPION, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 37) //CLASS_TYPE_DRAGONDISCIPLE
 {
   if (GetLevelByClass(CLASS_TYPE_DRAGONDISCIPLE, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 36) //CLASS_TYPE_DWARVENDEFENDER
 {
   if (GetLevelByClass(CLASS_TYPE_DWARVENDEFENDER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 34) //CLASS_TYPE_PALEMASTER
 {
   if (GetLevelByClass(CLASS_TYPE_PALEMASTER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 35) //CLASS_TYPE_SHIFTER
 {
   if (GetLevelByClass(CLASS_TYPE_SHIFTER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
 if(nNewClass == 33) //CLASS_TYPE_WEAPON_MASTER
 {
   if (GetLevelByClass(CLASS_TYPE_WEAPON_MASTER, oHench) == 40)
   {
      SetLocalInt(oHench, "NewClass", GetClassByPosition(1, oHench));
      SetLocalInt(oHench, "ClassPackage", GetCreatureStartingPackage(oHench));
   }
 }
}

int HenchXPCheck69(object oHench = OBJECT_SELF)
{
 int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
 string sHenchName = GetName(oHench);
 object oPC = GetMaster(oHench);
 int nHenchXP = GetLocalInt(oHench, "HENCH_XP");
 string sHenchXP = IntToString(nHenchXP);
 //Test
 if(TEST_MODE == 1)
 {
  SendMessageToPC(oPC, sHenchName + " has " + sHenchXP + " experience points.");
 }
 int nHenchLvl = GetHitDice(oHench);
 int nResult;
 switch(nHenchLvl)
 {
  case 1:
   if(nHenchXP >= 1000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 2:
   if(nHenchXP >= 3000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 3:
   if(nHenchXP >= 6000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 4:
   if(nHenchXP >= 10000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 5:
   if(nHenchXP >= 15000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 6:
   if(nHenchXP >= 21000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 7:
   if(nHenchXP >= 28000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 8:
   if(nHenchXP >= 36000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 9:
   if(nHenchXP >= 45000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 10:
   if(nHenchXP >= 55000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 11:
   if(nHenchXP >= 66000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 12:
   if(nHenchXP >= 78000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 13:
   if(nHenchXP >= 91000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 14:
   if(nHenchXP >= 105000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 15:
   if(nHenchXP >= 120000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 16:
   if(nHenchXP >= 136000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 17:
   if(nHenchXP >= 153000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 18:
   if(nHenchXP >= 171000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 19:
   if(nHenchXP >= 190000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 20:
   if(nHenchXP >= 210000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 21:
   if(nHenchXP >= 231000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 22:
   if(nHenchXP >= 253000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 23:
   if(nHenchXP >= 276000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 24:
   if(nHenchXP >= 300000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 25:
   if(nHenchXP >= 325000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 26:
   if(nHenchXP >= 351000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 27:
   if(nHenchXP >= 378000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 28:
   if(nHenchXP >= 406000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 29:
   if(nHenchXP >= 435000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 30:
   if(nHenchXP >= 465000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 31:
   if(nHenchXP >= 496000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 32:
   if(nHenchXP >= 528000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 33:
   if(nHenchXP >= 561000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 34:
   if(nHenchXP >= 595000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 35:
   if(nHenchXP >= 630000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 36:
   if(nHenchXP >= 666000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 37:
   if(nHenchXP >= 703000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 38:
   if(nHenchXP >= 741000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  case 39:
   if(nHenchXP >= 780000)
   {
     nResult = TRUE;
   }
   else
   {
     nResult = FALSE;
   }
   break;

  default:
   nResult = FALSE;
   break;
 }
 return nResult;
}

void HenchXPInitialize69(object oHench = OBJECT_SELF, int nHenchLvl = 0)
{
 int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
 if(nHenchLvl == 0)
 {
    nHenchLvl = GetHitDice(oHench);
 }
 //Test
 if(TEST_MODE == 1)
 {
  string sHenchLvl = IntToString(nHenchLvl);
  SendMessageToPC(GetMaster(oHench), "Henchman HD is " + sHenchLvl);
 }
 switch(nHenchLvl)
 {
  case 1:
   SetLocalInt(oHench, "HENCH_XP", 0);
   break;

  case 2:
   SetLocalInt(oHench, "HENCH_XP", 1000);
   break;

  case 3:
   SetLocalInt(oHench, "HENCH_XP", 3000);
   break;

  case 4:
   SetLocalInt(oHench, "HENCH_XP", 6000);
   break;

  case 5:
   SetLocalInt(oHench, "HENCH_XP", 10000);
   break;

  case 6:
   SetLocalInt(oHench, "HENCH_XP", 15000);
   break;

  case 7:
   SetLocalInt(oHench, "HENCH_XP", 21000);
   break;

  case 8:
   SetLocalInt(oHench, "HENCH_XP", 28000);
   break;

  case 9:
   SetLocalInt(oHench, "HENCH_XP", 36000);
   break;

  case 10:
   SetLocalInt(oHench, "HENCH_XP", 45000);
   break;

  case 11:
   SetLocalInt(oHench, "HENCH_XP", 55000);
   break;

  case 12:
   SetLocalInt(oHench, "HENCH_XP", 66000);
   break;

  case 13:
   SetLocalInt(oHench, "HENCH_XP", 78000);
   break;

  case 14:
   SetLocalInt(oHench, "HENCH_XP", 91000);
   break;

  case 15:
   SetLocalInt(oHench, "HENCH_XP", 105000);
   break;

  case 16:
   SetLocalInt(oHench, "HENCH_XP", 120000);
   break;

  case 17:
   SetLocalInt(oHench, "HENCH_XP", 136000);
   break;

  case 18:
   SetLocalInt(oHench, "HENCH_XP", 153000);
   break;

  case 19:
   SetLocalInt(oHench, "HENCH_XP", 171000);
   break;

  case 20:
   SetLocalInt(oHench, "HENCH_XP", 190000);
   break;

  case 21:
   SetLocalInt(oHench, "HENCH_XP", 210000);
   break;

  case 22:
   SetLocalInt(oHench, "HENCH_XP", 231000);
   break;

  case 23:
   SetLocalInt(oHench, "HENCH_XP", 253000);
   break;

  case 24:
   SetLocalInt(oHench, "HENCH_XP", 276000);
   break;

  case 25:
   SetLocalInt(oHench, "HENCH_XP", 300000);
   break;

  case 26:
   SetLocalInt(oHench, "HENCH_XP", 325000);
   break;

  case 27:
   SetLocalInt(oHench, "HENCH_XP", 351000);
   break;

  case 28:
   SetLocalInt(oHench, "HENCH_XP", 378000);
   break;

  case 29:
   SetLocalInt(oHench, "HENCH_XP", 406000);
   break;

  case 30:
   SetLocalInt(oHench, "HENCH_XP", 435000);
   break;

  case 31:
   SetLocalInt(oHench, "HENCH_XP", 465000);
   break;

  case 32:
   SetLocalInt(oHench, "HENCH_XP", 496000);
   break;

  case 33:
   SetLocalInt(oHench, "HENCH_XP", 528000);
   break;

  case 34:
   SetLocalInt(oHench, "HENCH_XP", 561000);
   break;

  case 35:
   SetLocalInt(oHench, "HENCH_XP", 595000);
   break;

  case 36:
   SetLocalInt(oHench, "HENCH_XP", 630000);
   break;

  case 37:
   SetLocalInt(oHench, "HENCH_XP", 666000);
   break;

  case 38:
   SetLocalInt(oHench, "HENCH_XP", 703000);
   break;

  case 39:
   SetLocalInt(oHench, "HENCH_XP", 741000);
   break;

  case 40:
   SetLocalInt(oHench, "HENCH_XP", 780000);
   break;

  default:
   SetLocalInt(oHench, "HENCH_XP", 820000);
   break;
 }
}

void MoveHenchmanItems69(object oContainer, object oHench)
{
 object oItem, oNewItem;
 int i;

 oItem = GetFirstItemInInventory(oHench);
 while(GetIsObjectValid(oItem))
 {
  if(!GetItemCursedFlag(oItem))
  {
   oNewItem = CopyItem(oItem, oContainer);
   DestroyObject(oItem, 0.2);
  }
  //AssignCommand(oHench, ActionGiveItem(oItem, oContainer));
  oItem = GetNextItemInInventory(oHench);
 }

 for (i=0; i < NUM_INVENTORY_SLOTS; i++)
 {
   oItem = GetItemInSlot(i, oHench);
   if(GetIsObjectValid(oItem) && !GetItemCursedFlag(oItem))
   {
    if(oItem != GetItemInSlot(INVENTORY_SLOT_CHEST, oHench))
    {
     ActionGiveItem(oItem, oContainer);
    }
   }
 }
}

void HenchmanNoDropItems69(int nDrop, object oHench=OBJECT_SELF)
{
 object oItem, oNewItem;
 int i;
 string sObject;
 int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");

 oItem = GetFirstItemInInventory(oHench);
 while(GetIsObjectValid(oItem))
 {
  SetItemCursedFlag(oItem, nDrop);
  //Test
  if(TEST_MODE == 1)
  {
   sObject = ObjectToString(oItem);
   SendMessageToPC(GetNearestPC(),sObject+ " is now not droppable");
  }
  oItem = GetNextItemInInventory(oHench);
 }

 for(i=0; i < NUM_INVENTORY_SLOTS; i++)
 {
   oItem = GetItemInSlot(i, oHench);
   if(GetIsObjectValid(oItem))
   {
    SetItemCursedFlag(oItem, nDrop);
    //Test
    if(TEST_MODE == 1)
    {
     sObject = ObjectToString(oItem);
     SendMessageToPC(GetNearestPC(),sObject+ " is now not droppable");
    }
   }
 }
}

void BattleCry()    //Changed to determine battlecry by class of henchman, called in 69_hen_percep
{
      return; // Baldecaran doesn't like the battle cries

      int iSpeakProb = Random(35) +1; // Probability of Battle Cry. MUST be a number from 1 to at least 8
      string sDeity = GetDeity(OBJECT_SELF);
      if(sDeity == "")
      {
       sDeity = "God";
      }
      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_FIGHTER || GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_RANGER  || GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_BARBARIAN)
        switch (iSpeakProb) {
           case 1: SpeakString("Take this, fool!"); break;
           case 2: SpeakString("Say hello to my little friend!"); break;
           case 3: SpeakString("To hell with you, hideous fiend!"); break;
           case 4: SpeakString("Come here. Come here I say!"); break;
           case 5: SpeakString("Meet cold steel!"); break;
           case 6: SpeakString("To Arms!"); break;
           case 7: SpeakString("Outstanding!"); break;
           case 8: SpeakString("You CAN do better than this, can you not?"); break;
           case 9: SpeakString("Embrace Death, and long for it!"); break;
           case 10: SpeakString("Press forward and give no quarter!"); break;
           case 11: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;

           default: break;
        }

      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_ROGUE)
        switch (iSpeakProb) {
           case 1: SpeakString("I got a little present for you here!"); break;
           case 2: SpeakString("Gotcha"); break;
           case 3: SpeakString("Think twice before messing with me!"); break;
           case 4: SpeakString("Silent and Deadly!"); break;
           case 5: SpeakString("Your momma raised ya to become THIS?"); break;
           case 6: SpeakString("Hey! Where's your manners!"); break;
           case 7: SpeakString("How about a little knife in the back victim"); break;
           case 8: SpeakString("You're an ugly little beastie, ain't ya?"); break;
           case 9: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;

           default: break;
        }

        if(GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_BARD)
        switch (iSpeakProb) {
           case 1: SpeakString("I got a little song for you here!"); break;
           case 2: SpeakString("Press forward and give no quarter!"); break;
           case 3: SpeakString("Think twice before messing with me!"); break;
           case 4: SpeakString("Silent and Deadly!"); break;
           case 5: SpeakString("Gather around and hear a song of valor."); break;
           case 6: SpeakString("Hey! Where's your manners!"); break;
           case 7: SpeakString("Your chances of success are dwindling."); break;
           case 8: SpeakString("These fools deserve no less."); break;
           case 9: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;

           default: break;
        }


      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_PALADIN)
        switch (iSpeakProb) {
           case 1: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;
           case 2: SpeakString(sDeity+ ", look kindly upon your servant!"); break;
           case 3: SpeakString(sDeity+ " comes to take you!"); break;
           case 4: SpeakString("Fight on! For our victory is at hand!"); break;
           case 5: SpeakString("Prepare yourself! Your time is near!"); break;
           case 6: SpeakString("The light of justice shall overcome you!"); break;
           case 7: SpeakString("Evil shall perish from the land!"); break;
           case 8: SpeakString("Make peace with your god!"); break;
           case 9: SpeakString("By the name of all that is holy, you shall perish!"); break;
           case 10: SpeakString("The smell of evil permeates your presence!"); break;
           case 11: SpeakString("Press forward and give no quarter!"); break;

           default: break;

        }

      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_CLERIC)
        switch (iSpeakProb) {
           case 1: SpeakString("This is not the time for healing!"); break;
           case 2: SpeakString("You have chosen pain!"); break;
           case 3: SpeakString("You attack us, only to die!"); break;
           case 4: SpeakString("Must you chase destruction? Very well!"); break;
           case 5: SpeakString("It does not please me to crush you like this."); break;
           case 6: SpeakString("Do not provoke me!"); break;
           case 7: SpeakString("I am at my wit's end with you all!"); break;
           case 8: SpeakString("Do you even know what you face?"); break;
           case 9: SpeakString("Prepare yourself! Your time is near!"); break;
           case 10: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;
           case 11: SpeakString(sDeity+ ", look kindly upon your servant!"); break;
           case 12: SpeakString(sDeity+ " comes to take you!"); break;

           default: break;
        }

      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_DRUID)
        switch (iSpeakProb) {
           case 1: SpeakString("Nature will have vengeance upon you now!"); break;
           case 2: SpeakString("What is your grievance? Begone!"); break;
           case 3: SpeakString("I won't allow you to harm anyone else!"); break;
           case 4: SpeakString("Retreat or feel my wrath!"); break;
           case 5: SpeakString("By " +sDeity+ ", you will not pass unchecked."); break;
           case 6: SpeakString("I am nature's weapon."); break;
           case 7: SpeakString(sDeity+ " willing, you'll soon be undone!"); break;
           case 8: SpeakString("Destroyer of all that is green, you shall die!"); break;
           case 9: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;

           default: break;
        }

      if (GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_WIZARD || GetClassByPosition(1, OBJECT_SELF) == CLASS_TYPE_SORCERER)
        switch (iSpeakProb) {
           case 1: SpeakString("You face a mage of considerable power!"); break;
           case 2: SpeakString("I find your resistance illogical."); break;
           case 3: SpeakString("I bind the powers of the very Planes!"); break;
           case 4: SpeakString("Fighting for now, and research for later."); break;
           case 5: SpeakString("Sad to destroy a fine specimen such as yourself."); break;
           case 6: SpeakString("Your chances of success are dwindling."); break;
           case 7: SpeakString("These fools deserve no less."); break;
           case 8: SpeakString("Now you are making me lose my patience."); break;
           case 9: SpeakString("Do or Do Not, there is no try."); break;
           case 10: SpeakString("By the name of " +sDeity+ ", you shall perish!"); break;
           case 11: SpeakString("Strike me down now and I shall become more powerful than you could ever imagine!"); break;

           default: break;
        }
}

location GetRespawnLocation69(object oHench = OBJECT_SELF)
{
  int HENCH_BLEED = GetLocalInt(GetModule(), "HENCH_BLEED");
  string sHenchTAG = GetTag(oHench);
  object oRespawn = GetWaypointByTag("WP_Respawn_" + sHenchTAG);
  object oMaster;
  location lRespawn;
  if(GetDidDie(oHench))
  {
   if(HENCH_BLEED == 3)
   {
    oMaster = GetLastMaster(oHench);
    lRespawn = GetBehindLocation(oMaster);
    HireHenchman69(oMaster, oHench);

    return lRespawn;
   }
   if(GetIsObjectValid(oRespawn) && HENCH_BLEED == 0)
   {
    lRespawn = GetLocation(oRespawn);
   }
   else if(HENCH_BLEED == 0)
   {
    lRespawn = GetLocation(GetWaypointByTag("NW_DEATH_TEMPLE"));
   }
  }
  else if(!GetDidDie(oHench))
  {
    lRespawn = GetLocation(oHench);
  }
   return lRespawn;

}

void RespawnHenchman69(object oHench=OBJECT_SELF)
{
    location lRespawn = GetRespawnLocation69(oHench);

    if(GetAreaFromLocation(lRespawn) == OBJECT_INVALID)
    {
      AssignCommand(oHench, SetIsDestroyable(TRUE, FALSE, FALSE));
      DestroyObject(oHench, 3.0);
    }
    else
    {
     // * Ensure the action queue is open to modification again
     SetCommandable(TRUE,oHench);

     // * Heal henchman
     ApplyEffectToObject(DURATION_TYPE_TEMPORARY, EffectVisualEffect(VFX_DUR_SMOKE), oHench, 4.0);
     RemoveEffects(oHench);
     ApplyEffectToObject(DURATION_TYPE_PERMANENT, EffectResurrection(), oHench);
     ApplyEffectToObject(DURATION_TYPE_PERMANENT, EffectHeal(GetMaxHitPoints(oHench)), oHench);

     // * Jump to Target
     JumpToLocation(lRespawn);

     // * Unset busy state
     ActionDoCommand(SetAssociateState(NW_ASC_IS_BUSY, FALSE));

     // * Make self vulnerable
     SetPlotFlag(oHench, FALSE);

     // * Set destroyable flag to leave corpse
     SetIsDestroyable(TRUE, TRUE, TRUE);
    }

}

void PostRespawnCleanup69(object oHench=OBJECT_SELF)
{
    string sTag = GetTag(oHench);
    object oBackpack = GetObjectByTag(sTag+ "BAG");

    DelayCommand(0.5, RemoveEffects(oHench));

    DelayCommand(1.0,
                 SetHenchmanDying(oHench, FALSE));

    // Clear henchman being busy
    DelayCommand(1.1,
                 SetAssociateState(NW_ASC_IS_BUSY, FALSE, oHench));

    // Clear the plot flag
    DelayCommand(1.2, SetPlotFlag(oHench, FALSE));

    //DelayCommand(1.3, SetLocalInt(oHench, MH_MODE_ATTACK, TRUE));
    DelayCommand(1.4, SetAssociateState(NW_ASC_USE_RANGED_WEAPON, FALSE));
    DelayCommand(1.5, SetIsDestroyable(TRUE, TRUE, TRUE));
    if(GetIsObjectValid(oBackpack))
    {
     DelayCommand(1.6, MoveHenchmanItems69(oHench, oBackpack));
     DelayCommand(2.0, AssignCommand(oHench, ActionPickUpItem(oBackpack)));
     DelayCommand(2.5, DestroyObject(oBackpack));
    }
}

void HenchRearm69(object oIntruder, object oHench = OBJECT_SELF)
{
    int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
    float fHenchRange = GetLocalFloat(oHench, "HenchRange");
    float fIntruderRange = GetDistanceToObject(oIntruder);
    string sHenchRange = FloatToString(GetDistanceToObject(oIntruder), 4, 2);
    object oPC = GetMaster(oHench);
    string sHench = GetName(oHench);
    //Test
    if(TEST_MODE == 1)
    {
     SendMessageToPC(GetFirstPC(), "Enemy is " + sHenchRange + " meters from " + sHench);
    }
    if(fIntruderRange > fHenchRange)
    {
     if(HasRangedWeapon69(OBJECT_SELF) && !GetAssociateState(NW_ASC_USE_RANGED_WEAPON) && GetLocalInt(OBJECT_SELF, "COMBAT_FLAG_SWITCH"))
     {
      ClearAllActions(TRUE);
      SetAssociateState(NW_ASC_USE_RANGED_WEAPON, TRUE);
      //Test
      if(TEST_MODE == 1)
      {
       SendMessageToPC(oPC, sHench + " is arming a ranged weapon!");
      }
      ActionEquipMostDamagingRanged(oIntruder);
     }
    }
    else if(fIntruderRange <= fHenchRange && fIntruderRange > 0.0)
    {
     if(HasMeleeWeapon69(OBJECT_SELF) && GetAssociateState(NW_ASC_USE_RANGED_WEAPON) && GetLocalInt(OBJECT_SELF, "COMBAT_FLAG_SWITCH"))
     {
      ClearAllActions(TRUE);
      SetAssociateState(NW_ASC_USE_RANGED_WEAPON, FALSE);
      //Test
      if(TEST_MODE == 1)
      {
       SendMessageToPC(oPC, sHench + " is arming a melee weapon!");
      }
      ActionEquipMostDamagingMelee(oIntruder,GetLocalInt(OBJECT_SELF,"bTwoHanded"));
     }
    }

}

int HasMeleeWeapon69(object oSelf)
{
  int nSlot = 4;
  object oItem = GetFirstItemInInventory(oSelf);
  while(GetIsObjectValid(oItem))
  {
    if(MatchMeleeWeapon(oItem))
    {
     return TRUE;
    }
    oItem = GetNextItemInInventory(oSelf);
  }
  for(nSlot; nSlot < 6; nSlot++)
  {
    oItem = GetItemInSlot(nSlot, oSelf);
    if(MatchMeleeWeapon(oItem))
    {
     return TRUE;
    }
  }
  return FALSE;
}

int HasRangedWeapon69(object oSelf)
{
  int nSlot = 4;
  object oItem = GetFirstItemInInventory(oSelf);
  while(GetIsObjectValid(oItem))
  {
    if(GetWeaponRanged(oItem))
    {
     return TRUE;
    }
    oItem = GetNextItemInInventory(oSelf);
  }
  for(nSlot; nSlot < 6; nSlot++)
  {
    oItem = GetItemInSlot(nSlot, oSelf);
    if(GetWeaponRanged(oItem))
    {
     return TRUE;
    }
  }
  return FALSE;
}

void HenchScout69(object oTarget, object oHench=OBJECT_SELF)
{
  object oPC = GetMaster(oHench);
  location lTarget = GetLocation(oTarget);
  int nTarget = 1;
  int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
  string sTarget;
  oTarget = GetNearestSeenEnemy(oHench, nTarget);
  while(GetIsObjectValid(oTarget))
  {
   sTarget = IntToString(nTarget);
   SetLocalObject(oHench, "ScoutingTarget" + sTarget, oTarget);
   oTarget = GetNearestSeenEnemy(oHench, ++nTarget);
  }
  //Test
  if(TEST_MODE == 1)
  {
   SendMessageToPC(oPC, "Moving back to you");
  }
  ClearAllActions();
  ActionForceFollowObject(oPC, 2.0);
  SetLocalInt(oHench,"ScoutingReport",TRUE);
}

void HenchReport69(object oPC, object oHench=OBJECT_SELF)
{
  int nTarget = 1;
  string sTarget = IntToString(nTarget);
  string sTargetName;
  object oTarget = GetLocalObject(oHench, "ScoutingTarget" +sTarget);
  DeleteLocalInt(oHench, "X2_HENCH_STEALTH_MODE");
  SetActionMode(oHench, ACTION_MODE_STEALTH, FALSE);
  while(GetIsObjectValid(oTarget))
  {
    sTargetName = GetName(oTarget);
    SpeakString("I scouted a " + sTargetName);
    DeleteLocalObject(oHench, "ScoutingTarget" +sTarget);
    ++nTarget;
    sTarget = IntToString(nTarget);
    oTarget = GetLocalObject(oHench, "ScoutingTarget" +sTarget);
  }
  SetLocalInt(oHench,"Scouting",FALSE);
  SetLocalInt(oHench,"ScoutingReport",FALSE);
  SetAssociateState(NW_ASC_MODE_DEFEND_MASTER, FALSE);
}

void HenchRessurect69(object oPC, object oHealer = OBJECT_SELF)
{
     location lLoc;
     int nRoll;

    // BALDECARAN: No free raise by henchman... sorry.
    return;

      string sPC = GetName(oPC);
      string sName = GetName(oHealer);
      if(GetClassByPosition(1, oHealer) == CLASS_TYPE_CLERIC || GetClassByPosition(2, oHealer) == CLASS_TYPE_CLERIC || GetClassByPosition(3, oHealer) == CLASS_TYPE_CLERIC || GetClassByPosition(1, oHealer) == CLASS_TYPE_DRUID  || GetClassByPosition(2, oHealer) == CLASS_TYPE_DRUID || GetClassByPosition(3, oHealer) == CLASS_TYPE_DRUID)
      {
        nRoll = d2(1);
        ClearAllActions();
        if(nRoll == 1)
        {
         SendMessageToPC(oPC, sName+ " is casting Raise Dead onto " +sPC);
         AssignCommand(oHealer, ActionCastSpellAtObject(SPELL_RAISE_DEAD, oPC, METAMAGIC_ANY, TRUE, 0, PROJECTILE_PATH_TYPE_DEFAULT, FALSE));
        }
        else if(nRoll == 2)
        {
         SendMessageToPC(oPC, sName+ " is casting Resurrection onto " +sPC);
         AssignCommand(oHealer, ActionCastSpellAtObject(SPELL_RESURRECTION, oPC, METAMAGIC_ANY, TRUE, 0, PROJECTILE_PATH_TYPE_DEFAULT, FALSE));
        }
     }

}

/***** MODULE TRANSFER FUNCTIONS *****/

// Call this function when the PC is about to leave a module
// to enable restoration of the henchman on re-entry into the
// sequel module. Both modules must use the same campaign db
// for this to work.
void StoreCampaignHenchman69(object oPC)
{
    object oHench;
    int ret;
    int iSlot, nXP, nHenchDeath;
    int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
    string sHench;
    string sDB = GetLocalString(GetModule(), "X0_CAMPAIGN_DB");
    int nCountHenchmen = GetHenchmanTotal69(oPC); //GetMaxHenchmen();
    string sCount = IntToString(nCountHenchmen);
    //Test
    if(TEST_MODE == 1)
    {
     SendMessageToPC(oPC, "Number of henchmen is " + sCount);
    }
    int HENCH_XP = GetLocalInt(GetModule(), "HENCHXP");
    int HENCH_LEADERSHIP = GetLocalInt(GetModule(), "nLeadership");
    if(HENCH_LEADERSHIP == 1)
    {
      nCountHenchmen = GetMaxHenchmen69(oPC);
      nHenchDeath = GetLocalInt(oPC, "Hench_Death");
      SetCampaignDBInt(oPC, "Hench_Death", nHenchDeath);
    }

    for (iSlot = 1; iSlot <= nCountHenchmen; iSlot++)
    {
        oHench = GetHenchman(oPC, iSlot);
        if (!GetIsObjectValid(oHench))
        {
         //Test
         if(TEST_MODE == 1)
         {
            DBG_msg("No valid henchman to store");
            SendMessageToPC(oPC, "No valid henchman to store");
         }
        }
        else
        {
            //Test
            if(TEST_MODE == 1)
            {
             DBG_msg("Storing henchman: " + GetTag(oHench));
             SendMessageToPC(oPC, "Storing henchman: " + GetTag(oHench));
            }
            sHench = "Henchman" + IntToString(iSlot);
            //ret = StoreCampaignDBObject(oPC, sHench, oHench);
            ret = StoreCampaignObject(sDB, sHench, oHench, oPC);

            if(!ret)
            {
              //Test
             if(TEST_MODE == 1)
             {
                DBG_msg("Error attempting to store henchman " + GetName(oHench));
                SendMessageToPC(oPC, "Error attempting to store henchman " + GetName(oHench));
             }
           }
            else
            {
                if(HENCH_XP == 1)  //Store Henchman XP
                {
                  nXP = GetLocalInt(oHench, "HENCH_XP");
                  SetCampaignDBInt(oPC, "XP_" + sHench, nXP);
                }
                //Test
                if(TEST_MODE == 1)
                {
                 DBG_msg("Henchman " + GetName(oHench) + " stored successfully");
                 SendMessageToPC(oPC, "Henchman " + GetName(oHench) + " stored successfully");
                }
            }
        }
    }
}
// Call this function when a PC enters a sequel module to restore
// the henchman (complete with inventory). The function
// StoreCampaignHenchman must have been called first, and both
// modules must use the same campaign db. (See notes in x0_i0_campaign.)
//
// The restored henchman will automatically be re-hired and will be
// created next to the PC.
//
// Any object in the module with the same tag as the henchman will be
// destroyed (to remove duplicates).
void RetrieveCampaignHenchman69(object oPC)
{
    location lLoc = GetLocation(oPC);
    object oHench;
    object oDupe;
    int iSlot, nXP, nHenchDeath;
    string sDB = GetLocalString(GetModule(), "X0_CAMPAIGN_DB");
    int nCountHenchmen = GetMaxHenchmen();
    int HENCH_XP = GetLocalInt(GetModule(), "HENCHXP");
    int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
    //Check for Leadership, TRUE resets nCountHenchman
    int HENCH_LEADERSHIP = GetLocalInt(GetModule(), "nLeadership");
    if(HENCH_LEADERSHIP == 1)
    {
      nCountHenchmen = GetMaxHenchmen69(oPC);
      nHenchDeath = GetCampaignDBInt(oPC, "Hench_Death");
      SetLocalInt(oPC, "Hench_Death", nHenchDeath);
      DelayCommand(1.0, DeleteCampaignVariable(sDB, "Hench_Death", oPC));

    }
    string sCount = IntToString(nCountHenchmen);
    //Test
    if(TEST_MODE == 1)
    {
     SendMessageToPC(oPC, "Number of henchmen is " + sCount);
    }
    string sHench;
    for(iSlot = 1; iSlot <= nCountHenchmen; iSlot++)
    {
        sHench = "Henchman" + IntToString(iSlot);
        //oHench = RetrieveCampaignDBObject(oPC, sHench, lLoc);
        oHench = RetrieveCampaignObject(sDB, sHench, lLoc, OBJECT_INVALID, oPC);
        //DelayCommand(1.0, DeleteCampaignDBVariable(oPC, sHench));
        DelayCommand(1.0, DeleteCampaignVariable(sDB, sHench, oPC));
        if(GetIsObjectValid(oHench))
        {
           //Test
           if(TEST_MODE == 1)
           {
            SendMessageToPC(oPC, sHench + " is available");
           }
            DelayCommand(1.5, HireHenchman69(oPC, oHench));
            oDupe = GetNearestObjectByTag(GetTag(oHench), oHench);
            if((oDupe != OBJECT_INVALID) && (oDupe != oHench))
            {
                AssignCommand(oDupe,SetIsDestroyable(TRUE));
                SetPlotFlag(oDupe,FALSE);
                SetImmortal(oDupe,FALSE);
                DestroyObject(oDupe);
            }
            if(HENCH_XP == 1)  //Retrieve and Set Henchman XP
            {
               nXP = GetCampaignDBInt(oPC, "XP_" + sHench);
               SetLocalInt(oHench, "HENCH_XP", nXP);
               DelayCommand(1.0, DeleteCampaignVariable(sDB, "XP_" + sHench, oPC));
            }
        }
        else
        {
           //Test
           if(TEST_MODE == 1)
           {
            DBG_msg("No valid henchman retrieved");
            SendMessageToPC(oPC, "No valid henchman retrieved");
           }
        }
    }
}

int GetHenchmanTotal69(object oPC)
{
  int nHenchCount = 0;
  int nHench = 1;
  object oHench = GetHenchman(oPC, nHench);
  while(GetIsObjectValid(oHench))
  {
    ++nHench;
    ++nHenchCount;
    oHench =GetHenchman(oPC, nHench);
  }
  return nHenchCount;
}

void HenchSummonCreature69(object oHench = OBJECT_SELF)
{
 int nLevel = GetHitDice(oHench) - 1;
 string sCreatureTAG = GetLocalString(oHench, "SummonCreature");
 string sCreatureResref = GetStringLowerCase(sCreatureTAG);
 location lLoc = GetAheadLocation(oHench);
 SetLocalInt(oHench, "HasCompanion", TRUE);
 SetLocalInt(oHench, "HadCompanion", TRUE);
 ActionCastFakeSpellAtLocation(SPELL_SUMMON_CREATURE_I, lLoc);
 ApplyEffectAtLocation(DURATION_TYPE_INSTANT, EffectSummonCreature(sCreatureResref, VFX_FNF_SUMMON_MONSTER_1), lLoc);
 object oCreature = GetObjectByTag(sCreatureTAG);
 DelayCommand(1.0, LevelHenchmanUpTo(oCreature, nLevel, GetLevelByPosition(2, oCreature), nLevel));
 DelayCommand(1.5, AssignCommand(oCreature, ActionMoveToObject(oHench)));
}

void HenchUnSummonCreature69(object oHench = OBJECT_SELF)
{
 string sCreatureTAG = GetLocalString(oHench, "SummonCreature");
 object oCreature = GetObjectByTag(sCreatureTAG);
 SetLocalInt(oHench, "HasCompanion", FALSE);
 ActionCastFakeSpellAtObject(SPELL_SUMMON_CREATURE_I, oCreature);
 DelayCommand(2.0, ApplyEffectToObject(DURATION_TYPE_TEMPORARY,EffectVisualEffect(VFX_IMP_UNSUMMON), oCreature, 4.0));
 DelayCommand(2.3, RemoveSummonedAssociate(oHench, oCreature));
 DestroyObject(oCreature, 2.4);
}
