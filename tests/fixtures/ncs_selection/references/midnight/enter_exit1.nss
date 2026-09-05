//#include "knightmayor1"
//#include "piroanimations"
#include "monstermaker"
//#include "enter_exit1"

void RemoveAura(object oPC)
    {
    if (GetLocalInt(oPC,"auraon"))
        {
        SetLocalInt(oPC,"auraon",0);
        effect eFect = GetFirstEffect(oPC);
        while (GetIsEffectValid(eFect))
            {
            if (GetEffectSubType(eFect)==SUBTYPE_EXTRAORDINARY && GetEffectCreator(eFect)==oPC)
                RemoveEffect(oPC,eFect);
            eFect = GetNextEffect(oPC);
            }
        }
    }

void Aura ()
    {
     effect eAura = EffectAreaOfEffect(AOE_MOB_CIRCGOOD,"enteraura","inaura","exitaura");
     ApplyEffectToObject(DURATION_TYPE_PERMANENT,ExtraordinaryEffect(eAura),OBJECT_SELF);
    }

void ApplyAura(object oPC)
    {
    if (GetLocalInt(oPC,"notfallen"))
        {
        if (GetLocalInt(oPC,"auraon"))
            RemoveAura(oPC);
        DelayCommand(0.5,AssignCommand(oPC,Aura()));
        SetLocalInt(oPC,"auraon",1);
        }
    }

void Rearm()//object NPC)
    {
    object NPC = OBJECT_SELF;
    object right = GetLocalObject(NPC,"right");
    object left = GetLocalObject(NPC,"left");
    object head =GetLocalObject(NPC,"head");
    object Temp = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,NPC);
    //if (Piro()==NPC)
     //   PiroStandGround(TRUE);
    ClearAllActions();
    if (GetIsObjectValid(Temp))
        SetLocalObject(NPC,"right",Temp);
    else
        {
        if (GetIsObjectValid(right)&&(right != Temp))
            {
            ActionEquipItem(right,INVENTORY_SLOT_RIGHTHAND);
            if (GetIsTwoHanded(right))
                SetLocalObject(NPC,"left",OBJECT_INVALID);
            }
        }
    Temp = GetItemInSlot(INVENTORY_SLOT_LEFTHAND,NPC);
    if (GetIsObjectValid(Temp))
        SetLocalObject(NPC,"left",Temp);
    else
        {
        if (GetIsObjectValid(left))
            ActionEquipItem(left,INVENTORY_SLOT_LEFTHAND);
        }
    Temp = GetItemInSlot(INVENTORY_SLOT_HEAD,NPC);
    if (GetIsObjectValid(Temp))
        SetLocalObject(NPC,"head",Temp);
    else
        {
        if (GetIsObjectValid(head))
            ActionEquipItem(head,INVENTORY_SLOT_HEAD);
        }
   /* if (Piro()==NPC)
        {
        object Gloves = GetItemPossessedBy(NPC,"MinstrelsFlair");
        if (GetIsObjectValid(Gloves))
            ExecuteScript("charm",Gloves);
        ActionDoCommand(PiroStandGround(FALSE));
        }*/
   /* object oPiro =GetHenchman(NPC);
    if (GetIsObjectValid(oPiro))
        {
        right = GetLocalObject(oPiro,"right");
        Temp = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,oPiro);
        if (GetIsObjectValid(Temp))
            {
            //right = Temp;
            SetLocalObject(oPiro,"right",Temp);
            }
        else if (GetIsObjectValid(right)&& right!=Temp)
            {
            object Gloves = GetItemPossessedBy(oPiro,"MinstrelsFlair");
            if (GetIsObjectValid(Gloves))
                ExecuteScript("charm",Gloves);
            AssignCommand(oPiro,SetAssociateState(NW_ASC_MODE_STAND_GROUND,TRUE));
            AssignCommand(oPiro, ClearAllActions());
            //AssignCommand(oPiro,ActionWait(0.5));
            AssignCommand(oPiro,ActionEquipItem(right,INVENTORY_SLOT_RIGHTHAND));
            AssignCommand(oPiro,ActionDoCommand(SetAssociateState(NW_ASC_MODE_STAND_GROUND,FALSE)));
            }
        } */
    }
void PiroRearm()
    {
    object oPC = GetFirstPC();
    object right = GetLocalObject(oPC,"Piroright");
    object Temp = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,OBJECT_SELF);
    if (GetIsObjectValid(Temp))
        SetLocalObject(oPC,"Piroright",Temp);
    else
        {
        if (GetIsObjectValid(right)&&(right != Temp))
            {
            PiroStandGround(TRUE);
            ClearAllActions();
            ActionEquipItem(right,INVENTORY_SLOT_RIGHTHAND);
            object Gloves = GetItemPossessedBy(OBJECT_SELF,"MinstrelsFlair");
            if (GetIsObjectValid(Gloves))
                ActionDoCommand(ExecuteScript("charm",Gloves));
            ActionDoCommand(PiroStandGround(FALSE));
            }
        }

    }

void RearmParty(object oPC)
    {
    if (GetIsPC(oPC))
        {
        AssignCommand(oPC,Rearm());
        DelayCommand(0.7,AssignCommand(Piro(),PiroRearm()));
        }
    }

void Unarm()//object NPC)
    {
    object NPC = OBJECT_SELF;
    object right = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,NPC);
    object left = GetItemInSlot(INVENTORY_SLOT_LEFTHAND,NPC);
    object head = GetItemInSlot(INVENTORY_SLOT_HEAD,NPC);
   // if (Piro()==NPC)
     //   PiroStandGround(TRUE);
    ClearAllActions();
    if (GetIsObjectValid(right))
        {
        SetLocalObject(NPC,"right",right);
        ActionUnequipItem(right);
        if (GetIsTwoHanded(right))
            SetLocalObject(NPC,"left",OBJECT_INVALID);
        }
    if (GetIsObjectValid(left))
        {
        SetLocalObject(NPC,"left",left);
        ActionUnequipItem(left);
        }
    if (GetIsObjectValid(head))
        {
        SetLocalObject(NPC,"head",head);
        ActionUnequipItem(head);
        }
    /*if (Piro()==NPC)
        {
        object Gloves = GetItemPossessedBy(NPC,"MinstrelsFlair");
        if (GetIsObjectValid(Gloves))
            RemoveEffectByCreator(NPC,Gloves);
        ActionDoCommand(PiroStandGround(FALSE));
        }   */
   /* object oPiro = GetHenchman(NPC);
    if (GetIsObjectValid(oPiro))
        {
        right = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,oPiro);
        if (GetIsObjectValid(right))
            {
            object Gloves = GetItemPossessedBy(oPiro,"MinstrelsFlair");
            if (GetIsObjectValid(Gloves))
                RemoveEffectByCreator(oPiro,Gloves);
            SetLocalObject(oPiro,"right",right);
            AssignCommand(oPiro,SetAssociateState(NW_ASC_MODE_STAND_GROUND,TRUE));
            AssignCommand(oPiro, ClearAllActions());
            AssignCommand(oPiro,ActionUnequipItem(right));
            AssignCommand(oPiro,ActionDoCommand(SetAssociateState(NW_ASC_MODE_STAND_GROUND,FALSE)));
            }
        } */
    }

void PiroUnarm()
    {
    object oPC = GetFirstPC();
    object right = GetItemInSlot(INVENTORY_SLOT_RIGHTHAND,OBJECT_SELF);
    if (GetIsObjectValid(right))
        {
        PiroStandGround(TRUE);
        ClearAllActions();
        SetLocalObject(oPC,"Piroright",right);
        ActionUnequipItem(right);
        object Gloves = GetItemPossessedBy(OBJECT_SELF,"MinstrelsFlair");
        if (GetIsObjectValid(Gloves))
            ActionDoCommand(RemoveEffectByCreator(OBJECT_SELF,Gloves));
        ActionDoCommand(PiroStandGround(FALSE));
        }

    }

void UnarmParty(object oPC)
    {
    if (GetIsPC(oPC))
        {
        AssignCommand(oPC,Unarm());
        DelayCommand(0.7,AssignCommand(Piro(),PiroUnarm()));
        }
    }

void SetArea(object oPC,string Area)
    {
    if (GetIsPC(oPC))
        SetLocalString(oPC,"area",Area);
    }

void StartWalkers(object oPC)
    {
    if (GetIsPC(oPC)&&!GetLocalInt(oPC,"Walkers"))
        {
        int x=1;
        object Commoner = GetFirstObjectInArea(OBJECT_SELF);
        while(GetIsObjectValid(Commoner))
            {
            if (GetTag(Commoner) == "Commoner")
                {
                ExecuteScript("c_commoner",Commoner);
                }
            Commoner = GetNextObjectInArea(OBJECT_SELF);
            }
        }
    }

void StopWalkers(object oPC)
    {
    if (GetIsPC(oPC))
        {
        int x=1;
        object Commoner = GetFirstObjectInArea(OBJECT_SELF);
        while(GetIsObjectValid(Commoner))
            {
            if (GetTag(Commoner) == "Commoner")
                AssignCommand(Commoner,ClearAllActions());
            Commoner = GetNextObjectInArea(OBJECT_SELF);
            }
        }
    }

int NotEntered(object oPC)
    {
    if (GetIsPC(oPC))
        {
        int E = GetLocalInt(OBJECT_SELF,"entered");
        SetLocalInt(OBJECT_SELF,"entered",1);
        return !E;
        }
    else
        return 0;
    }

void EnterRestArea()
    {
    object FX = FXMaker();
    SetLocalInt(GetFirstPC(),"InRestArea",1);
    DelayCommand(1.0, FloatingTextStringOnCreature("** Designated Rest Area **",GetFirstPC()));
    DelayCommand(1.0, AssignCommand(FX, PlaySound("gui_learnspell")));
    DestroyObject(FX,2.0);
    }

void ExitRestArea()
    {
    SetLocalInt(GetFirstPC(),"InRestArea",0);
    }

int PortalFX(object oPC,float Delay = 0.0)
    {
    if (GetLocalInt(oPC,"PortalFX"))
        {
        SetLocalInt(oPC,"PortalFX",0);
        DelayCommand(Delay,ApplyEffectAtLocation(DURATION_TYPE_INSTANT,EffectVisualEffect(VFX_IMP_UNSUMMON),GetLocation(oPC)));
        return TRUE;
        }
    else
        return FALSE;
    }

void SetPortalFX(object oPC)
    {
    if (GetIsPC(oPC))
        SetLocalInt(oPC,"PortalFX",1);
    }
//void main(){}
