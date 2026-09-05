//69_hench_whatxp
// NPC lets PC know how much XP s/he has
// Created by: 69MEH69 Oct2004

void main()
{
  object oPC = GetPCSpeaker();
  int nHenchXP = GetLocalInt(OBJECT_SELF, "HENCH_XP");
  string sHenchXP = IntToString(nHenchXP);
  SendMessageToPC(oPC, "I have " + sHenchXP + " experience points.");
}
