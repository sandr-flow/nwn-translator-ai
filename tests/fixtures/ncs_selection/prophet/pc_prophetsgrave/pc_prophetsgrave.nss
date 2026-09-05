#include "pc_include"

void EnterGrave()
{
    object oIsandra = GetObjectByTag("Isandra");
    object oPC = OBJECT_SELF;
    string shis = "his";
    if (GetGender(oPC)==GENDER_FEMALE) shis = "her";

    oPC = OBJECT_SELF;
    FadeFromBlack(oPC,FADE_SPEED_SLOW);
    ClearAllActions();
    ActionUnequipItem(GetItemInSlot(INVENTORY_SLOT_HEAD));
    ActionUnequipItem(GetItemInSlot(INVENTORY_SLOT_CHEST));
    ActionUnequipItem(GetItemInSlot(INVENTORY_SLOT_CLOAK));
    ActionUnequipItem(GetItemInSlot(INVENTORY_SLOT_RIGHTHAND));
    ActionUnequipItem(GetItemInSlot(INVENTORY_SLOT_LEFTHAND));
    SetCameraMode(oPC,CAMERA_MODE_TOP_DOWN);
    DelayCommand(0.1,SetCameraFacing(200.0,12.0,40.0));

    SetDescription(GetObjectByTag("ProphetHeadstone"),"Here lies "+GetName(oPC)+
        ", unmaker of the world. Let "+shis+" soul be damned for all eternity.");

    ExecuteScript("at_isandravanish",oIsandra);
    SetCommandable(FALSE);
    DelayCommand(6.0,FloatingText(oPC,"You are buried to your neck and cannot move."));
    DelayCommand(12.0,AssignCommand(oIsandra,
        ActionStartConversation(oPC,"",FALSE,FALSE)));

    float fDepth = 4.0;
    int nRace = GetRacialType(oPC);
    int nMale = (GetGender(oPC)==GENDER_MALE);
    if (nRace==RACIAL_TYPE_HUMAN || nRace==RACIAL_TYPE_HALFELF)
        fDepth = 4.55 + nMale*0.10;
    else if (nRace==RACIAL_TYPE_DWARF)
        fDepth = 4.15 + nMale*0.07;
    else if (nRace==RACIAL_TYPE_ELF)
        fDepth = 4.37 + nMale*0.13;
    else if (nRace==RACIAL_TYPE_GNOME)
        fDepth = 4.05 + nMale*0.10;
    else if (nRace==RACIAL_TYPE_HALFLING)
        fDepth = 4.00 + nMale*0.10;
    else if (nRace==RACIAL_TYPE_HALFORC)
        fDepth = 4.72 + nMale*0.12;
    CreateObject(OBJECT_TYPE_PLACEABLE,"dirtpatch",Location(GetArea(OBJECT_SELF),
        GetPosition(GetWaypointByTag("wpGraveDirt"))+Vector(0.0,0.0,fDepth),0.0));

}

void main()
{
    object oPC = GetEnteringObject();
    if (GetIsPC(oPC)) {
        AssignCommand(oPC,EnterGrave());
        DestroyObject(OBJECT_SELF);
        }
}

