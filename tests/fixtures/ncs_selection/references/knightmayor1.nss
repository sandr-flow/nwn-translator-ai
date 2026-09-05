#include "nw_i0_generic"
//#include "nw_i0_tool"
#include "x0_inc_henai"
void Debug(string Message)
    {
    SendMessageToPC(GetFirstPC(),Message);
    }
object Piro(int TF=TRUE)
    {
    if (TF)
        return GetHenchman(GetFirstPC());
    else
        return GetObjectByTag("Pirotase");
    }

string HenchName()
    {
    return GetName(Piro(FALSE));
    }

int SkillAdjust(int Skill)
    {
    if (AutoDC(DC_HARD, Skill, GetPCSpeaker()))
        return 2;
    else if(AutoDC(DC_MEDIUM, Skill, GetPCSpeaker()))
        return 1;
    else
        return 0;
    }

void HenchmanAlign(string sAlign,int nAlign)
    {
    SetLocalInt(GetFirstPC(),sAlign,GetLocalInt(GetFirstPC(),sAlign)+nAlign);
    if (GetLocalInt(GetFirstPC(),sAlign) <0)
        SetLocalInt(GetFirstPC(),sAlign,0);
    else if(GetLocalInt(GetFirstPC(),sAlign) >100)
        SetLocalInt(GetFirstPC(),sAlign,100);
    if (sAlign == "GoodEvil")
        if (nAlign > 0)
            sAlign = "Good";
        else
            {
            sAlign = "Evil";
            nAlign = abs(nAlign);
            }
    else
        if (nAlign > 0)
            sAlign = "Law";
        else
            {
            sAlign = "Chaos";
            nAlign = abs(nAlign);
            }
    if (GetIsObjectValid(GetHenchman(GetFirstPC())))
        FloatingTextStringOnCreature(HenchName()+"'s Alignment shifted "+IntToString(nAlign)+" towards "+sAlign,GetHenchman(GetFirstPC()));
    else
        FloatingTextStringOnCreature(HenchName()+"s Alignment shifted "+IntToString(nAlign)+" towards "+sAlign,GetFirstPC());
    }

void Face (object NPC)
    {
    SetFacingPoint(GetPosition(NPC));
    }

void ACIOO2(string ResRef, object oPC, int count)
    {
    CreateItemOnObject(ResRef,oPC,count);
    }

void ActionCreateItemOnObject(string ResRef,object oPC,int count=1)
    {
    ActionDoCommand(ACIOO2(ResRef,oPC,count));
    }

int GetIsBusy(object oNPC)
    {
    return (GetIsInCombat(oNPC)|| IsInConversation(oNPC)
            || GetCurrentAction(oNPC) == ACTION_DISABLETRAP
            || GetCurrentAction(oNPC) == ACTION_OPENLOCK
            || GetCurrentAction(oNPC) == ACTION_REST);
    }
void GiveItemAnim(object giver, object taker)
    {
    AssignCommand(giver, ActionMoveToObject(taker));
    AssignCommand(giver, ActionDoCommand(AssignCommand(taker, Face(giver))));
    AssignCommand(giver, ActionDoCommand(Face(taker)));
    AssignCommand(giver, ActionDoCommand(AssignCommand(taker, ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0))));
    AssignCommand(giver, ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0));
    }

void GiveItemAnim2(object taker)
    {
    object giver = OBJECT_SELF;
    ActionMoveToObject(taker);
    ActionDoCommand(AssignCommand(taker, Face(giver)));
    ActionDoCommand(Face(taker));
    ActionDoCommand(AssignCommand(taker, ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0)));
    ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0);
    }

int DoorInWay(object oTrap)
    {
    location lCenter = GetLocation(OBJECT_SELF);
    float fDist = GetDistanceToObject(oTrap);
    vector vTrap = GetPositionFromLocation(GetLocation(oTrap));
    vector vSelf = GetPositionFromLocation(lCenter);
    vTrap.x -= vSelf.x;
    vTrap.y -= vSelf.y;
    vector vDoor;
    object oDoor = GetFirstObjectInShape(SHAPE_SPHERE,fDist,lCenter,TRUE,OBJECT_TYPE_DOOR);
    while(GetIsObjectValid(oDoor))
        {
        if (!GetIsOpen(oDoor)&& oDoor != oTrap)
            {
            vDoor = GetPositionFromLocation(GetLocation(oDoor));
            vDoor.x -= vSelf.x;
            vDoor.y -= vSelf.y;
            float temp = (vDoor.x*vTrap.x+vDoor.y*vTrap.y)/(VectorMagnitude(vTrap)*VectorMagnitude(vDoor));
            if (0.5 < temp)
                return TRUE;
            }
        oDoor = GetNextObjectInShape(SHAPE_SPHERE,fDist,lCenter,TRUE,OBJECT_TYPE_DOOR);
        }
    return FALSE;
    }

int GetIsTwoHanded(object oWeap)
    {
    int Type = GetBaseItemType(oWeap);
    if (Type == BASE_ITEM_DIREMACE || Type == BASE_ITEM_DOUBLEAXE || Type == BASE_ITEM_GREATAXE || Type == BASE_ITEM_GREATSWORD || Type == BASE_ITEM_HALBERD
             || Type == BASE_ITEM_HEAVYCROSSBOW || Type == BASE_ITEM_HEAVYFLAIL || Type == BASE_ITEM_LIGHTCROSSBOW || Type == BASE_ITEM_LONGBOW
             || Type == BASE_ITEM_MAGICSTAFF || Type == BASE_ITEM_QUARTERSTAFF || Type == BASE_ITEM_SCYTHE || Type == BASE_ITEM_SHORTBOW || Type == BASE_ITEM_SHORTSPEAR
             || Type == BASE_ITEM_SLING || Type == BASE_ITEM_TWOBLADEDSWORD)
        return TRUE;
    if (GetCreatureSize(GetItemPossessor(oWeap))==CREATURE_SIZE_SMALL && Type == BASE_ITEM_BASTARDSWORD || Type == BASE_ITEM_BATTLEAXE
             || Type == BASE_ITEM_KATANA || Type == BASE_ITEM_LIGHTFLAIL || Type == BASE_ITEM_LONGSWORD || Type == BASE_ITEM_MORNINGSTAR || Type == BASE_ITEM_RAPIER
             || Type == BASE_ITEM_SCIMITAR || Type == BASE_ITEM_TOWERSHIELD || Type == BASE_ITEM_WARHAMMER)
        return TRUE;
    return FALSE;
    }

void RemoveAllEffects(object Target)
    {
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        RemoveEffect(Target,eFect);
        eFect = GetNextEffect(Target);
        }
    }

void RemoveMagicalEffects(object Target)
    {
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        if (GetEffectSubType(eFect) == SUBTYPE_MAGICAL)
            RemoveEffect(Target,eFect);
        eFect = GetNextEffect(Target);
        }
    }

void RemoveEffectByCreator(object Target,object Creator)
    {
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        if (GetEffectCreator(eFect) == Creator)
            {
            //Debug("Removed "+GetName(Target)+" "+GetName(GetEffectCreator(eFect)));
            RemoveEffect(Target,eFect);
            }
        eFect = GetNextEffect(Target);
        }
    }

void RemoveEffectByType(object Target,int Type)
    {
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        if (GetEffectType(eFect) == Type)
            RemoveEffect(Target,eFect);
        eFect = GetNextEffect(Target);
        }
    }

void RemoveEffectByCreatorAndSubtype(object Target,object Creator,int ST)
    {
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        if (GetEffectCreator(eFect) == Creator && GetEffectSubType(eFect) == ST)
            RemoveEffect(Target,eFect);
        eFect = GetNextEffect(Target);
        }
    }

int RemoveEffectByTypeAndCreator(object Target,int Type,object Creator)
    {
    int Return = 0;
    effect eFect = GetFirstEffect(Target);
    while(GetIsEffectValid(eFect))
        {
        if (GetEffectType(eFect) == Type && GetEffectCreator(eFect) == Creator)
            {
            RemoveEffect(Target,eFect);
            Return = 1;
            }
        eFect = GetNextEffect(Target);
        }
    return Return;
    }

int GetTimeSeconds()
    {
    return (((((((GetCalendarMonth()*28) + GetCalendarDay())*24) + GetTimeHour())*2) + GetTimeMinute())*60) + GetTimeSecond();
    }

int GetTimeHours()
    {
    return ((((GetCalendarMonth()*28) + GetCalendarDay())*24) + GetTimeHour());
    }

int GetTimeBetween(int time)
    {
    return GetTimeSeconds() - time;
    }

object FXMaker()
    {
    return CreateObject(OBJECT_TYPE_PLACEABLE,"fxmaker",GetLocation(GetFirstPC()));
    }

void EnableMapPin(string tag, int TF = FALSE)
    {
    object FX = FXMaker();
    object WP = GetObjectByTag(tag);
    DelayCommand(1.0,AssignCommand(FX,PlaySound("gui_spell_mem")));
    DelayCommand(1.0,SetMapPinEnabled(WP,TRUE));
    if (TF)
        DelayCommand(1.0,FloatingTextStringOnCreature("Map updated in "+GetName(GetArea(WP)),GetFirstPC()));
    else
        DelayCommand(1.0,SendMessageToPC(GetFirstPC(),"Map updated in "+GetName(GetArea(WP))));
    DestroyObject(FX,2.0);
    }

void PiroStandGround(int TF)
    {
    AssignCommand(GetHenchman(GetFirstPC()),SetAssociateState(NW_ASC_MODE_STAND_GROUND,TF));
    }

location Loc(string tag)
    {
    return GetLocation(GetObjectByTag(tag));
    }

void SCPC(int TF)
    {
    SetCommandable(TF,GetFirstPC());
    }

void PiroMove (object NPC,int Run=FALSE,float Range=1.0)
    {
    object oPiro = Piro();
    PiroStandGround(TRUE);
    AssignCommand(oPiro,ClearAllActions(TRUE));
    AssignCommand(oPiro,ActionMoveToObject(NPC,Run,Range));
    if (GetObjectType(NPC)!=OBJECT_TYPE_WAYPOINT)
        AssignCommand(oPiro,ActionDoCommand(Face(NPC)));
    AssignCommand(oPiro,ActionDoCommand(PiroStandGround(FALSE)));
    }

void PiroMoveLoc (string Tag,int Run=FALSE)
    {
    object oPiro = Piro();
    PiroStandGround(TRUE);
    AssignCommand(oPiro,ClearAllActions(TRUE));
    AssignCommand(oPiro,ActionMoveToLocation(Loc(Tag),Run));
    AssignCommand(oPiro,ActionDoCommand(PiroStandGround(FALSE)));
    }

void PiroTalk (string Conv = "pirotase")
    {
    object oPiro = Piro();
    PiroStandGround(TRUE);
    AssignCommand(oPiro,ClearAllActions(TRUE));
    AssignCommand(oPiro,ActionStartConversation(GetFirstPC(),Conv));
    AssignCommand(oPiro,ActionDoCommand(PiroStandGround(FALSE)));
    }

void VoiceIn(object Target = OBJECT_INVALID)
    {
    location TarLoc = GetLocation(GetFirstPC());
    if (GetIsObjectValid(Target))
        TarLoc = GetLocation(Target);
    CreateObject(OBJECT_TYPE_PLACEABLE,"picturespeake002",TarLoc);
    CreateObject(OBJECT_TYPE_PLACEABLE,"picturespeake001",TarLoc);
    }

void VoiceOut()
{
 DestroyObject(GetObjectByTag("voice"),0.1);
 DestroyObject(GetObjectByTag("yourself"),0.1);
}

void MakePortal (string ResRef,location Dest)
    {
    object Portal;
    if (TRUE)//ResRef == "ylaemportal1" || ResRef == "ylaemportal3")
        {
        Portal = CreateObject(OBJECT_TYPE_PLACEABLE,ResRef,Dest);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,EffectVisualEffect(VFX_DUR_SPELLTURNING),Portal);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,EffectVisualEffect(VFX_DUR_PARALYZE_HOLD),Portal);
        }
    else
        SendMessageToPC(GetFirstPC(),"MAKE PORTAL ERROR");
    }



void Wander(string Tag,int Count)
    {
    ClearAllActions();
    if (GetArea(OBJECT_SELF)==GetArea(GetFirstPC()))
        {
        ActionMoveToObject(GetNearestObjectByTag(Tag,OBJECT_SELF,Random(Count-1)+1));
        ActionDoCommand(Wander(Tag,Count));
        }
    }

void NewSkin(object oPiro, string ResRef)
    {
    DestroyObject(GetItemInSlot(INVENTORY_SLOT_CARMOUR,oPiro));
    AssignCommand(oPiro,ClearAllActions());
    AssignCommand(oPiro,ActionEquipItem(CreateItemOnObject(ResRef,oPiro),INVENTORY_SLOT_CARMOUR));
    }

void LevelupPiro(object oPiro,int Level)
    {
    if (GetHitDice(oPiro)<Level)
        {
        FloatingTextStringOnCreature("** "+HenchName()+" Level Up **",oPiro);
        while(LevelUpHenchman(oPiro) < Level){}
        switch (Level)
            {
            case 1:
            case 2:
            case 3:
            case 4:
                {
                NewSkin(oPiro,"it_creitem056");
                break;
                }
            case 5:
            case 6:
                {
                NewSkin(oPiro,"nw_it_creitem056");
                break;
                }
            case 7:
            case 8:
                {
                NewSkin(oPiro,"it_creitem057");
                break;
                }
            case 9:
            case 10:
                {
                NewSkin(oPiro,"it_creitem058");
                break;
                }
            case 11:
            case 12:
                {
                NewSkin(oPiro,"it_creitem059");
                break;
                }
            case 13:
            case 14:
                {
                NewSkin(oPiro,"it_creitem060");
                break;
                }
            case 15:
            case 16:
                {
                NewSkin(oPiro,"it_creitem061");
                break;
                }
            }
        }
    }

void DecreaseStack(object Stack)
    {
    int Count = GetNumStackedItems(Stack)-1;
    if (Count)
        SetItemStackSize(Stack,Count);
    else
        DestroyObject(Stack);
    }

void PiroHandoff(object Item,object Taker,int Inv = -1)
    {
    object oPiro = Piro();
    object Giver = GetFirstPC();
    if (GetIsPC(Taker))
        Giver=oPiro;
    PiroStandGround(TRUE);
    AssignCommand(oPiro,ClearAllActions());
    AssignCommand(oPiro,ActionPauseConversation());
    AssignCommand(GetFirstPC(),ClearAllActions());
    AssignCommand(GetFirstPC(),Face(oPiro));
    AssignCommand(oPiro,ActionMoveToObject(GetFirstPC()));
    AssignCommand(oPiro,ActionDoCommand(Face(GetFirstPC())));
    AssignCommand(oPiro,ActionDoCommand(AssignCommand(GetFirstPC(),ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0))));
    AssignCommand(oPiro,ActionPlayAnimation(ANIMATION_LOOPING_GET_MID,1.0,1.0));
    if (GetIsPC(Taker))
        {
        AssignCommand(oPiro,ActionDoCommand(AssignCommand(Taker,ActionTakeItem(Item,Giver))));
        if (Inv != -1)
            AssignCommand(oPiro,ActionDoCommand(AssignCommand(Taker,ActionEquipItem(Item,Inv))));
        }
    else
        {
        AssignCommand(oPiro,ActionTakeItem(Item,Giver));
        if (Inv != -1)
            AssignCommand(oPiro,ActionEquipItem(Item,Inv));
        }
    AssignCommand(oPiro,ActionResumeConversation());
    AssignCommand(oPiro,ActionDoCommand(PiroStandGround(FALSE)));
    }

int GetRingFinger()
    {
    int Return = INVENTORY_SLOT_LEFTRING;
    if (GetIsObjectValid(GetItemInSlot(INVENTORY_SLOT_LEFTRING,GetObjectByTag("Pirotase"))))
        {
        Return = INVENTORY_SLOT_RIGHTRING;
        SetLocalInt(GetFirstPC(),"returnring",1);
        }
    return Return;
    }

int GetJournalEntry(string Tag)
    {
    return GetLocalInt(GetFirstPC(),"NW_JOURNAL_ENTRY"+Tag);
    }

int GetIsAlive(object NPC)
    {
    return GetCurrentHitPoints (NPC) > 0;
    }

void OpenDoor(object Door)
    {
    SetLocked(Door,FALSE);
    AssignCommand(Door,ActionOpenDoor(OBJECT_SELF));
    }

void SealDoor(object Door)
    {
    SetLocked(Door,TRUE);
    AssignCommand(Door,ActionCloseDoor(OBJECT_SELF));
    }

void FakeRestore(object oTarget)
{
    effect eVisual = EffectVisualEffect(VFX_IMP_RESTORATION_GREATER);

    effect eBad = GetFirstEffect(oTarget);
    //Search for negative effects
    while(GetIsEffectValid(eBad))
    {
        if (GetEffectType(eBad) == EFFECT_TYPE_ABILITY_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_AC_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_ATTACK_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_DAMAGE_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_DAMAGE_IMMUNITY_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_SAVING_THROW_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_SPELL_RESISTANCE_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_SKILL_DECREASE ||
            GetEffectType(eBad) == EFFECT_TYPE_BLINDNESS ||
            GetEffectType(eBad) == EFFECT_TYPE_DEAF ||
            GetEffectType(eBad) == EFFECT_TYPE_CURSE ||
            GetEffectType(eBad) == EFFECT_TYPE_DISEASE ||
            GetEffectType(eBad) == EFFECT_TYPE_POISON ||
            GetEffectType(eBad) == EFFECT_TYPE_PARALYZE ||
            GetEffectType(eBad) == EFFECT_TYPE_NEGATIVELEVEL)
            //&& GetEffectCreator(eBad) !=GetObjectByTag("Knightmayor"))
            //&& GetEffectSubType(eBad) == SUBTYPE_MAGICAL)
        {
            //Remove effect if it is negative.
            RemoveEffect(oTarget, eBad);
        }
        eBad = GetNextEffect(oTarget);
    }
    if(GetRacialType(oTarget) != RACIAL_TYPE_UNDEAD)
    {
        //Apply the VFX impact and effects
        int nHeal = GetMaxHitPoints(oTarget) - GetCurrentHitPoints(oTarget);
        effect eHeal = EffectHeal(nHeal);
        if (nHeal > 0)
            ApplyEffectToObject(DURATION_TYPE_INSTANT, eHeal, oTarget);
    }
    ApplyEffectToObject(DURATION_TYPE_INSTANT, eVisual, oTarget);
}

void AltarHeal(object oPC,object Altar)
{
    object oHenchman = GetAssociate(ASSOCIATE_TYPE_HENCHMAN,oPC);
    object oAnimal = GetAssociate(ASSOCIATE_TYPE_ANIMALCOMPANION,oPC);
    object oFamiliar = GetAssociate(ASSOCIATE_TYPE_FAMILIAR,oPC);
    object oDominated = GetAssociate(ASSOCIATE_TYPE_DOMINATED,oPC);
    object oSummoned = GetAssociate(ASSOCIATE_TYPE_SUMMONED,oPC);
    AssignCommand(Altar,ActionCastFakeSpellAtObject(SPELL_GREATER_RESTORATION, oPC));
    ActionDoCommand(FakeRestore(oPC));
    if (!GetLocalInt(oPC,"notfallen"))
        ExecuteScript("removedivinegrac",oPC);
    if(GetIsObjectValid(oHenchman))
    {
        ActionDoCommand(FakeRestore(oHenchman));
    }
    if(GetIsObjectValid(oAnimal))
    {
        ActionDoCommand(FakeRestore(oAnimal));
    }
    if(GetIsObjectValid(oFamiliar))
    {
        ActionDoCommand(FakeRestore(oFamiliar));
    }
    if(GetIsObjectValid(oDominated))
    {
        ActionDoCommand(FakeRestore(oDominated));
    }
    if(GetIsObjectValid(oSummoned))
    {
        ActionDoCommand(FakeRestore(oSummoned));
    }
}

int GetHostile(object Enemy)
    {
    //Debug("Hostility "+GetTag(Enemy)+" "+IntToString(GetReputation(GetFirstPC(),Enemy)));
   // Debug("Reverse Hostility "+GetName(Enemy)+" "+IntToString(GetReputation(Enemy,GetFirstPC())));
    return GetReputation(GetFirstPC(),Enemy)<=10;
    }

void FaceEachOther()
    {
    AssignCommand(Piro(),Face(GetFirstPC()));
    AssignCommand(GetFirstPC(),Face(Piro()));
    }

int GetIsMobile(object ME)
    {
    effect eFect = GetFirstEffect(ME);
    while (GetIsEffectValid(eFect))
        {
        if (GetEffectType(eFect)==EFFECT_TYPE_STUNNED)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_SLEEP)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_PETRIFY)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_PARALYZE)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_FRIGHTENED)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_DOMINATED)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_DAZED)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_CONFUSED)
            return FALSE;
        if (GetEffectType(eFect)==EFFECT_TYPE_CHARMED)
            return FALSE;
        eFect = GetNextEffect(ME);
        }
    if (GetHasFeatEffect(FEAT_KNOCKDOWN,ME))
        return FALSE;
    return TRUE;
    }
void NextStepInCircuit(string Tag,object WP, int Run)
    {
    ActionMoveToLocation(GetLocation(WP),Run);
    int Temp = StringToInt(GetStringRight(GetTag(WP),GetStringLength(GetTag(WP))-GetStringLength(Tag)))+1;
    object Next = GetObjectByTag(Tag+IntToString(Temp));
    if (!GetIsObjectValid(Next))
        Next = GetObjectByTag(Tag+"1");
    ActionDoCommand(NextStepInCircuit(Tag,Next,Run));
    }

void RunCircuit(string Tag, int Run = FALSE)
    {
    ClearAllActions();
    int x=1;
    int Length = GetStringLength(Tag);
    object WP = GetNearestObject(OBJECT_TYPE_WAYPOINT,OBJECT_SELF,x);
    while (GetIsObjectValid(WP) && GetStringLeft(GetTag(WP),Length)!=Tag)
        WP = GetNearestObject(OBJECT_TYPE_WAYPOINT,OBJECT_SELF,++x);
    if (GetIsObjectValid(WP))
        NextStepInCircuit(Tag,WP,Run);
    }

void AreaTrans(string Tag,int Position = 6)
    {
    object Door = GetObjectByTag(Tag);
    object Hench = GetHenchman(OBJECT_SELF);
    if (GetObjectType(Door)==OBJECT_TYPE_DOOR)
        JumpToLocation(GenerateNewLocation(Door,2.0,GetFacing(Door)+165.0,GetFacing(Door)+180.0));
    else
        JumpToLocation(Loc(Tag));
    if (GetArea(Door)==GetArea(OBJECT_SELF))
        AssignCommand(Hench,JumpToLocation(Loc(Tag)));
    }
///  Midnight only functions

void MonkJournal (string Name, int update=1,int Adjustment=0)
    {
    GiveXPToCreature(GetFirstPC(),100);
    object oPC = GetFirstPC();
    int Journal = GetLocalInt(oPC,"NW_JOURNAL_ENTRYSnowWhite")+Adjustment;
    string Token = GetLocalString(oPC,"MonkToken");
    switch (Journal)
        {
        case 10:
            {
            Token = Name;
            break;
            }
        case 20:
            {
            Token = Name+" and "+ Token;
            break;
            }
        default:
            {
            Token = Name+", "+Token;
            break;
            }
        }
    SetLocalString(oPC,"MonkToken",Token);
    SetCustomToken(50,Token);
    if (update)
        AddJournalQuestEntry("SnowWhite",Journal+10,oPC);
    }

void UsePenalty()
    {
    string Tag = GetTag(GetArea(OBJECT_SELF));
    if (GetIsPC(OBJECT_SELF)&&(Tag=="Mokura" || Tag == "Playground_M" || Tag == "MokuraApothecary" || Tag == "TempleofDarkness"))
        {
        SetLocalInt(GetFirstPC(),"TimeOut",3);
        AssignCommand(GetFirstPC(),JumpToLocation(Loc("NPC_new")));
        }
    }
//void main(){}
