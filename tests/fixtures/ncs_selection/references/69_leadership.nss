/*69_leadership
    Leadership Library Functions
    Created by: 69MEH69
    Created on: Sep2004
*/
//void main(){}

//Level of PC when Leadership begins
const int LEADERSHIP_LEVEL = 6;

//Returns Loyalty modifier
int GetHenchLoyalty(object oHench, object oPC);
//Returns TRUE if PC has Leadership (must be level 6 or higher)
int GetHasLeadership(object oPC);
//Returns Leadership Score
int GetLeadershipScore(object oPC);
//Sets maximum number of henchmen based on Leadership
void SetMaxHenchmen69(object oPC);
//Returns maximum number of henchmen based on Leadership
int GetMaxHenchmen69(object oPC);

int GetHenchLoyalty(object oHench, object oPC)
{
 int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
 int nCharisma = GetAbilityModifier(ABILITY_CHARISMA, oPC);
 //Initial roll + charisma
 int nLoyalty = d6(3) + nCharisma;
 string sLoyalty, sHenchdeath;
 //Test
 if(TEST_MODE == 1)
 {
  sLoyalty = IntToString(nLoyalty);
  SendMessageToPC(oPC, "Initial nLoyalty = " + sLoyalty);
 }
 int nHenchAlign = GetAlignmentGoodEvil(oHench);
 int nPCAlign = GetAlignmentGoodEvil(oPC);
 int nHenchDeath = GetLocalInt(oPC, "Hench_Death");
 //Adjustment for alignments
 if(nHenchAlign == nPCAlign)
 {
   ++nLoyalty;
 }
 else
 {
   --nLoyalty;
 }
 //Test
 if(TEST_MODE == 1)
 {
  sLoyalty = IntToString(nLoyalty);
  SendMessageToPC(oPC, "nLoyalty - Alignment = " + sLoyalty);
 }
 //Adjustment for number of dead henchmen
 nLoyalty = nLoyalty - nHenchDeath;
 //Test
 if(TEST_MODE == 1)
 {
  sHenchdeath =IntToString(nHenchDeath);
  sLoyalty = IntToString(nLoyalty);
  SendMessageToPC(oPC, "Henchmen deaths = " + sHenchdeath);
  SendMessageToPC(oPC, "nLoyalty - nHenchDeath = " + sLoyalty);
  SendMessageToPC(oPC, "Loyalty score = " + sLoyalty);
 }
 return nLoyalty;
}

int GetHasLeadership(object oPC)
{
  int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
  int nLeadership = GetHitDice(oPC);
  if(nLeadership >= LEADERSHIP_LEVEL)
  {
    //Test
    if(TEST_MODE == 1)
    {
     SendMessageToPC(oPC, "Leadership is True");
    }
    return TRUE;
  }
  else
  {
    //Test
    if(TEST_MODE == 1)
    {
     SendMessageToPC(oPC, "Leadership is False");
    }
    return FALSE;
  }
}

int GetLeadershipScore(object oPC)
{
  int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
  int nPCLevel = GetHitDice(oPC);
  int nCharisma = GetAbilityModifier(ABILITY_CHARISMA, oPC);
  int nPersuade = GetSkillRank(SKILL_PERSUADE, oPC);
  int nLeadershipScore = nPCLevel + nCharisma;
  //Test
  if(TEST_MODE == 1)
  {
   string sCharisma = IntToString(nCharisma);
   SendMessageToPC(oPC, "Charisma score = " + sCharisma);
   string sPersuade = IntToString(nPersuade);
   SendMessageToPC(oPC, "Persuade score = " + sPersuade);
   string sLeadershipScore = IntToString(nLeadershipScore);
   SendMessageToPC(oPC, "Leadership score = " + sLeadershipScore);
  }
  return nLeadershipScore;
}

void SetMaxHenchmen69(object oPC)
{
 int nLeadershipScore = GetLeadershipScore(oPC);

 //Primary Code
 if(GetHasLeadership(oPC) == FALSE)
 {
  SetLocalInt(oPC, "MaxHenchmen", 0);
 }
 else if(nLeadershipScore >= 25)
 {
  SetLocalInt(oPC, "MaxHenchmen", 10);
 }
 else if(nLeadershipScore >= 20)
 {
  SetLocalInt(oPC, "MaxHenchmen", 5);
 }
 else if(nLeadershipScore >= 15)
 {
  SetLocalInt(oPC, "MaxHenchmen", 4);
 }
 else if(nLeadershipScore >= 10)
 {
  SetLocalInt(oPC, "MaxHenchmen", 3);
 }
 else if(nLeadershipScore >= 8)
 {
  SetLocalInt(oPC, "MaxHenchmen", 2);
 }
 else if(nLeadershipScore >= 2)
 {
  SetLocalInt(oPC, "MaxHenchmen", 1);
 }
 else
 {
  SetLocalInt(oPC, "MaxHenchmen", 0);
 }
 //End Primary Code

 //Secondary Code
 //Uncomment following code to use this form
 //Comment out the Primary Code
 /*
 if(GetHasLeadership(oPC) == FALSE)
 {
  SetLocalInt(oPC, "MaxHenchmen", 0);
  return;
 }
 switch(nLeadershipScore)
 {
  case 0: case -1: case -2: case -3:
    SetLocalInt(oPC, "MaxHenchmen", 0);
    break;

  case 1:
    SetLocalInt(oPC, "MaxHenchmen", 1);
    break;

  case 2:
    SetLocalInt(oPC, "MaxHenchmen", 1);
    break;

  case 3:
    SetLocalInt(oPC, "MaxHenchmen", 1);
    break;

  case 4:
    SetLocalInt(oPC, "MaxHenchmen", 2);
    break;

  case 5:
    SetLocalInt(oPC, "MaxHenchmen", 2);
    break;

  case 6:
    SetLocalInt(oPC, "MaxHenchmen", 2);
    break;

  case 7:
    SetLocalInt(oPC, "MaxHenchmen", 3);
    break;

  case 8:
    SetLocalInt(oPC, "MaxHenchmen", 3);
    break;

  case 9:
    SetLocalInt(oPC, "MaxHenchmen", 3);
    break;

  case 10:
    SetLocalInt(oPC, "MaxHenchmen", 4);
    break;

  case 11:
    SetLocalInt(oPC, "MaxHenchmen", 4);
    break;

  case 12:
    SetLocalInt(oPC, "MaxHenchmen", 4);
    break;

  case 13: case 14: case 15:
    SetLocalInt(oPC, "MaxHenchmen", 5);
    break;

  case 16: case 17: case 18:
    SetLocalInt(oPC, "MaxHenchmen", 6);
    break;

  default:
    SetLocalInt(oPC, "MaxHenchmen", 7);
    break;
 }*/
 //End Secondary Code

}

int GetMaxHenchmen69(object oPC)
{
  int TEST_MODE = GetLocalInt(GetModule(), "TEST_MODE");
  int nMaxHenchmen = GetLocalInt(oPC, "MaxHenchmen");
  //Test
  if(TEST_MODE == 1)
  {
   string sMaxHenchmen = IntToString(nMaxHenchmen);
   SendMessageToPC(oPC, "Maximum allowable henchmen = " + sMaxHenchmen);
  }
  return nMaxHenchmen;
}
