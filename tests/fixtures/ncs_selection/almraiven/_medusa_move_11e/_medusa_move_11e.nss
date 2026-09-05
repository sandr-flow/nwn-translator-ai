void main()
{
object oPC = GetPCSpeaker();
object oMove = GetObjectByTag("MEDUSA_010");
object oMoveMedusa1 = GetObjectByTag("MEDUSA_001");
object oMoveMedusa2 = GetObjectByTag("MEDUSA_002");
object oMoveMedusa3 = GetObjectByTag("MEDUSA_003");
object oMoveMedusa4 = GetObjectByTag("MEDUSA_004");
object oMoveMedusa5 = GetObjectByTag("MEDUSA_005");
object oMoveMedusa6 = GetObjectByTag("MEDUSA_006");
object oMoveMedusa7 = GetObjectByTag("MEDUSA_007");
object oMoveMedusa8 = GetObjectByTag("MEDUSA_008");
object oMoveMedusa9 = GetObjectByTag("MEDUSA_009");
object oMoveMedusa10 = GetObjectByTag("MEDUSA_010");
object oMoveMedusa11 = GetObjectByTag("MEDUSA_011");
object oMoveMedusa12 = GetObjectByTag("MEDUSA_012");
object oMoveMedusa13 = GetObjectByTag("MEDUSA_013");
object oMedusa = GetObjectByTag("Medusa");
object oMod = GetModule();
object oPlayer = GetFirstPC();

AssignCommand(oPC, SetCameraFacing(0.0f));

DelayCommand(1.0, AssignCommand (oPC, ActionMoveToObject(oMove, TRUE)));

if ((GetLocalInt(oMod, "medusamove") == 15)) {

// Spawned in at Medusa Spawn 1 // 10
// Only exit is to move to 006
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa6, TRUE)));
SetLocalInt(oMod, "medusamove", 6);
return;
}

if ((GetLocalInt(oMod, "medusamove") == 20)) {

// Spawned in at Medusa Spawn 2 // 20
// Only exit is to move to 004
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa4, TRUE)));
SetLocalInt(oMod, "medusamove", 4);
return;
}

if ((GetLocalInt(oMod, "medusamove") == 30)) {

// Spawned in at Medusa Spawn 3 // 30
// Only exit is to move to 007
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa7, TRUE)));
SetLocalInt(oMod, "medusamove", 7);
return;
}

if ((GetLocalInt(oMod, "medusamove") == 40)) {

// Spawned in at Medusa Spawn 4 // 40
// Only exit is to move to 008
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa8, TRUE)));
SetLocalInt(oMod, "medusamove", 8);
return;
}

if ((GetLocalInt(oMod, "medusamove") == 50)) {

// Spawned in at Medusa Spawn 5 // 50
// Only exit is to move to 002
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa2, TRUE)));
SetLocalInt(oMod, "medusamove", 2);
return;
}

if ((GetLocalInt(oMod, "medusamove") == 1)) {

// Currently at MEDUSA_006
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa6, TRUE)));
  SetLocalInt(oMod, "medusamove", 6);
return;
}
if ((nValue >= 51))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa2, TRUE)));
  SetLocalInt(oMod, "medusamove", 2);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 2)) {

// Currently at MEDUSA_002
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 19))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa1, TRUE)));
  SetLocalInt(oMod, "medusamove", 1);
return;
}
if ((nValue >= 20) && (nValue <= 49))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa3, TRUE)));
  SetLocalInt(oMod, "medusamove", 3);
return;
}
if ((nValue >= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa5, TRUE)));
  SetLocalInt(oMod, "medusamove", 5);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 3)) {

// Currently at MEDUSA_006
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa2, TRUE)));
  SetLocalInt(oMod, "medusamove", 2);
return;
}
if ((nValue >= 51))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa4, TRUE)));
  SetLocalInt(oMod, "medusamove", 4);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 4)) {

// Currently at MEDUSA_004
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 19))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa3, TRUE)));
  SetLocalInt(oMod, "medusamove", 3);
return;
}
if ((nValue >= 20) && (nValue <= 79))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa5, TRUE)));
  SetLocalInt(oMod, "medusamove", 5);
return;
}
if ((nValue >= 80))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa7, TRUE)));
  SetLocalInt(oMod, "medusamove", 7);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 5)) {

// Currently at MEDUSA_005
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 19))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa4, TRUE)));
  SetLocalInt(oMod, "medusamove", 4);
return;
}
if ((nValue >= 20) && (nValue <= 29))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa2, TRUE)));
  SetLocalInt(oMod, "medusamove", 2);
return;
}
if ((nValue >= 30) && (nValue <= 49))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa6, TRUE)));
  SetLocalInt(oMod, "medusamove", 6);
return;
}
if ((nValue >= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa8, TRUE)));
  SetLocalInt(oMod, "medusamove", 8);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 6)) {

// Currently at MEDUSA_006
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa1, TRUE)));
  SetLocalInt(oMod, "medusamove", 1);
return;
}
if ((nValue >= 51))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa5, TRUE)));
  SetLocalInt(oMod, "medusamove", 5);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 7)) {

// Currently at MEDUSA_007
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 50))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa8, TRUE)));
  SetLocalInt(oMod, "medusamove", 8);
return;
}
if ((nValue >= 51) && (nValue <= 79))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa4, TRUE)));
  SetLocalInt(oMod, "medusamove", 4);
return;
}
if ((nValue >= 80))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa10, TRUE)));
  SetLocalInt(oMod, "medusamove", 10);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 8)) {

// Currently at MEDUSA_008
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 15))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa9, TRUE)));
  SetLocalInt(oMod, "medusamove", 9);
return;
}
if ((nValue >= 16) && (nValue <= 69))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa5, TRUE)));
  SetLocalInt(oMod, "medusamove", 5);
return;
}
if ((nValue >= 70))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa11, TRUE)));
  SetLocalInt(oMod, "medusamove", 11);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 9)) {

// Currently at MEDUSA_009
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 70))  {
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa8, TRUE)));
SetLocalInt(oMod, "medusamove", 8);
return;
}
if ((nValue >= 71)) {
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa12, TRUE)));
SetLocalInt(oMod, "medusamove", 12);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 10)) {

// Currently at MEDUSA_010
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 50))  {
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa7, TRUE)));
SetLocalInt(oMod, "medusamove", 7);
return;
}
if ((nValue >= 51)) {
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa11, TRUE)));
SetLocalInt(oMod, "medusamove", 11);
return;
}

}

if ((GetLocalInt(oMod, "medusamove") == 11)) {

// Currently at MEDUSA_011
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 70))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa8, TRUE)));
  SetLocalInt(oMod, "medusamove", 8);
return;
}
if ((nValue >= 71))
{ DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa10, TRUE)));
  SetLocalInt(oMod, "medusamove", 10);
return;
}
}

if ((GetLocalInt(oMod, "medusamove") == 12)) {

// Currently at MEDUSA_012
// Random choice

int nValue = Random(100) + 1;
if ((nValue <= 100))  {
DelayCommand(1.0, AssignCommand (oMedusa, ActionMoveToObject(oMoveMedusa9, TRUE)));
SetLocalInt(oMod, "medusamove", 9);
return;
}

}


}

