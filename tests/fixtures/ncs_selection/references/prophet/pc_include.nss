// PC_INCLUDE:
//
// Miscellaneous useful functions and constants

// Constants:

const int EVENT_USER_DEFINED_PRESPAWN = 1510;
const int EVENT_USER_DEFINED_POSTSPAWN = 1511;

// Events

// Trigger events

// Shouts
int PC_SHOUT_HELP                       = 1001;
int PC_SHOUT_IMDEAD                     = 1002;
int PC_SHOUT_GETHIM                     = 1003;
int PC_SHOUT_IMOPENED                   = 1004;     // A chest calls out when opened
int PC_SHOUT_IMPLUNDERED                = 1005;     // A chest calls out when something is taken from its inventory
int PC_SHOUT_COMEHERE                   = 1006;     // A general "come here" shout
int PC_SHOUT_GIVEPORTAL                 = 1007;     // A command to give the PC a portable portal
int PC_SHOUT_FORBIDOPENED               = 1008;     // A forbidden door was opened
int PC_SHOUT_PING                       = 1009;     // An object was struck - this can distract guards
int PC_SHOUT_OUTOFMYWAY                 = 1010;     // Someone was blocked

// Conversation launchers for commoners
int PC_SPEAK_MERCHANT                   = 1101;     // Merchants speak this to draw commoners to converse
int PC_SPEAK_IDLECHAT                   = 1102;     // Commoners chat with each other

// Custom event codes
int PC_EVENT_SLEEPTRIG                  = 7001;     // Person stepped onto a sleep trigger
int PC_EVENT_BLOCKING                   = 7002;     // Person is blocking someone
int PC_EVENT_PC_ENTERED                 = 7003;     // A PC entered the area
int PC_EVENT_PCS_COMING                 = 7004;     // PCs are approaching the caller (used in Kravikhel)

// DECLARATIONS

void FloatingText(object oTarget, string sText, int bBroadcast=FALSE);
int IsBusy(object o = OBJECT_SELF);
void ActionTurnTo(object oTarget);
void Decompose(object oCorpse);
void DieAndLeaveCorpse(string sTemplate = "corpse");

// FUNCTIONS

void ClearFX(object oActor)
{
    effect eFect = GetFirstEffect(oActor);
    int iType = GetEffectType(eFect);
    while (GetIsEffectValid(eFect))
        {
        if (iType == EFFECT_TYPE_IMPROVEDINVISIBILITY
         || iType == EFFECT_TYPE_CUTSCENEGHOST
         || iType == EFFECT_TYPE_CUTSCENE_PARALYZE
         || iType == EFFECT_TYPE_VISUALEFFECT
         || iType == EFFECT_TYPE_INVISIBILITY
         || iType == EFFECT_TYPE_SANCTUARY
         || iType == EFFECT_TYPE_POLYMORPH
         || iType == EFFECT_TYPE_BLINDNESS
         || iType == EFFECT_TYPE_ETHEREAL
         || iType == EFFECT_TYPE_DARKNESS)
            { RemoveEffect(oActor,eFect); }
        eFect = GetNextEffect(oActor);
        iType = GetEffectType(eFect);
        }
}

void VCreateObject(int nType, string sTemplate, location lLoc, int bAnim=FALSE, string sTag="")
{
    CreateObject(nType,sTemplate,lLoc,bAnim,sTag);
}

int CalcXPValue(object oPC, int nCR)
{
    int nPartyCR = 0;   // Total CR of all players in the party
    int nNumPCs = 0;    // Total number of players in the party
    int nNum = 0;       // Total number of associates in the party
    int n=1;
    int nXP;

    /*
    object o = GetFirstFactionMember(oPC);
    while (GetIsObjectValid(o)) {
        nPartyCR += GetHitDice(o);
        nNumPCs++;
        while (GetIsObjectValid(GetHenchman(o,n))) {
            nNum++;
            n++;
            }
        if (GetIsObjectValid(GetAssociate(ASSOCIATE_TYPE_ANIMALCOMPANION,o)))
            nNum++;
        if (GetIsObjectValid(GetAssociate(ASSOCIATE_TYPE_DOMINATED,o)))
            nNum++;
        if (GetIsObjectValid(GetAssociate(ASSOCIATE_TYPE_FAMILIAR,o)))
            nNum++;
        if (GetIsObjectValid(GetAssociate(ASSOCIATE_TYPE_SUMMONED,o)))
            nNum++;
        o = GetNextFactionMember(oPC);
        }
    nPartyCR /= nNumPCs;
    */
    nPartyCR = GetHitDice(oPC);

    if (nPartyCR>20)
        nPartyCR = 20;
    if (nCR>20)
        nCR = 20;

    string s = Get2DAString("xptable","C"+IntToString(nCR),nPartyCR-1);
    nXP = StringToInt(s);
    nXP = nXP / (1+nNum/4);     // Adjust value for associates
    return nXP;
}

void AwardXPForKill(object oKiller, object oVictim)
{
    while (GetIsObjectValid(GetMaster(oKiller)))
        oKiller = GetMaster(oKiller);
    if (GetIsPC(oKiller)) {
        object oAlly = GetFirstFactionMember(oKiller);
        while (GetIsObjectValid(oAlly)) {
            float fDist = GetDistanceBetween(oAlly,oVictim);
            if (fDist>=0.0 && fDist<20.0) {
                GiveXPToCreature(oAlly,CalcXPValue(oAlly,FloatToInt(GetChallengeRating(oVictim))));
                //SendMessageToPC(oAlly,"Estimated XP: "
                //    +IntToString(CalcXPValue(oAlly,FloatToInt(GetChallengeRating(oVictim)))));
                }
            oAlly = GetNextFactionMember(oKiller);
            }
        }
}

void TakeAwayXP(object oKiller)
{
    //SendMessageToPC(GetFirstPC(),"Taking XP from "+GetName(oKiller)+"'s faction."); // DEBUGOFF
    while (GetIsObjectValid(GetMaster(oKiller)))
        oKiller = GetMaster(oKiller);
    if (GetIsPC(oKiller)) {
        object oAlly = GetFirstFactionMember(oKiller,TRUE);
        while (GetIsObjectValid(oAlly)) {
            int nXP = GetLocalInt(oAlly,"nXP");
            //SendMessageToPC(GetFirstPC(),"Taking "+IntToString(GetXP(oAlly)-nXP)+" XP from "+GetName(oAlly)); // DEBUGOFF
            if (nXP>0)
                SetXP(oAlly,nXP);
            oAlly = GetNextFactionMember(oKiller,TRUE);
            }
        }
}

void SetListeningPatternsPC()
{
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_HELP",PC_SHOUT_HELP);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_IMDEAD",PC_SHOUT_IMDEAD);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_GETHIM",PC_SHOUT_GETHIM);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_IMOPENED",PC_SHOUT_IMOPENED);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_IMPLUNDERED",PC_SHOUT_IMPLUNDERED);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_COMEHERE",PC_SHOUT_COMEHERE);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_FORBIDOPENED",PC_SHOUT_FORBIDOPENED);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_PING",PC_SHOUT_PING);
    SetListenPattern(OBJECT_SELF,"PC_SHOUT_OUTOFMYWAY",PC_SHOUT_OUTOFMYWAY);
    SetListenPattern(OBJECT_SELF,"A portal please",PC_SHOUT_GIVEPORTAL);

    SetListenPattern(OBJECT_SELF,"PC_SPEAK_MERCHANT",PC_SPEAK_MERCHANT);
    SetListenPattern(OBJECT_SELF,"PC_SPEAK_IDLECHAT",PC_SPEAK_IDLECHAT);
}

// Returns TRUE if the object is alive
int IsAlive(object o)
{
    return GetIsObjectValid(o) && !GetIsDead(o);
}

// Returns TRUE if the object is busy
int IsBusy(object o = OBJECT_SELF)
{
    if (GetLocalInt(o,"bOverride")
        || GetIsInCombat(o)
        || IsInConversation(o)
        || GetIsObjectValid(GetNearestCreature(CREATURE_TYPE_REPUTATION,REPUTATION_TYPE_ENEMY,
            o,1,CREATURE_TYPE_PERCEPTION,PERCEPTION_SEEN)))
        return TRUE;
    return FALSE;
}

// Returns true if object o is a merchant who is in their store
int IsMerchant(object o=OBJECT_SELF)
{
    int bMerchant = FALSE;
    object oStore = GetNearestObjectByTag("Store"+GetTag(o));
    if (GetIsObjectValid(oStore)
        && GetArea(o)==GetArea(oStore)
        && GetDistanceBetween(o,oStore)<=5.0)
        bMerchant = TRUE;   // I am a merchant

    return bMerchant;
}

void TurnTo(object oTarget)
{
    if (GetIsObjectValid(oTarget)) {
        int bComm = GetCommandable();
        SetCommandable(TRUE);
        SetFacing(VectorToAngle(GetPosition(oTarget) - GetPosition(OBJECT_SELF)));
        SetCommandable(bComm);
        }
}

void ActionTurnTo(object oTarget)
{
    ActionDoCommand(TurnTo(oTarget));
}

void NecklaceKills()
{
    object o = GetObjectByTag("NecklaceofLoyalty");
    object oVict = GetItemPossessor(o);
    if (GetIsObjectValid(oVict) && GetItemInSlot(INVENTORY_SLOT_NECK,oVict)==o &&
        !GetIsDead(GetObjectByTag("Atelkhan"))) {
        // Necklace of loyalty kills its possessor
        SetLocalInt(oVict,"bKilledByNecklace",TRUE);
        ApplyEffectToObject(DURATION_TYPE_INSTANT,EffectVisualEffect(VFX_COM_CHUNK_RED_LARGE),oVict);
        ApplyEffectToObject(DURATION_TYPE_INSTANT,EffectVisualEffect(VFX_COM_CHUNK_BONE_MEDIUM),oVict);
        ApplyEffectToObject(DURATION_TYPE_INSTANT,EffectDeath(TRUE),oVict);
        }
}

// Call friends for help
// - oEnemy: The person who attacked
// - bKilled: TRUE if the caller was killed
void CallForHelp(object oEnemy, int bKilled = FALSE)
{
    if (GetIsObjectValid(oEnemy)) {

        // Call for help

        SetLocalObject(OBJECT_SELF,"oShoutersEnemy",oEnemy);
        SpeakString("PC_SHOUT_HELP",TALKVOLUME_SILENT_SHOUT);
        if (bKilled)
            SpeakString("PC_SHOUT_IMDEAD",TALKVOLUME_SILENT_SHOUT);
        }
}

void TransferItem(object oItem, object oDest, int bDestroy=FALSE)
{
    object oNew;

    if (GetIsObjectValid(oItem)) {
        if (GetDroppableFlag(oItem)
            && !GetLocalInt(oItem,"bNotDroppable")
            && GetTag(oItem)!="P1_MarkoftheProphet"
            && GetStringLeft(GetTag(oItem),10)!="DestinyRod"
            && GetTag(oItem)!="SashofthePasharthai"
            && GetTag(oItem)!="x3_it_pchide"
            && GetTag(oItem)!="Yourlefthand") {

            oNew = CopyItem(oItem,oDest,TRUE);
            if (!GetIsObjectValid(oNew) && GetBaseItemType(oItem)==BASE_ITEM_LARGEBOX)
                // Non empty container
                SetIdentified(CreateItemOnObject(GetResRef(oItem),oDest),GetIdentified(oItem));
            if (bDestroy)
                DestroyObject(oItem,0.1);
            else
                SetLocalInt(oItem,"bNotDestroyedOnDeath",TRUE);
            }
        }
}

void DestroyCompletely(object o)
{
    SetCommandable(TRUE,o);
    AssignCommand(o,SetIsDestroyable(TRUE));
    AssignCommand(o,TakeGoldFromCreature(GetGold(o),o,TRUE));
    object oItem = GetFirstItemInInventory(o);
    while (GetIsObjectValid(oItem)) {
        DestroyObject(oItem,0.1);
        oItem = GetNextItemInInventory(o);
        }
    int n;
    for (n=0; n<NUM_INVENTORY_SLOTS; n++) {
        oItem = GetItemInSlot(n,o);
        if (GetIsObjectValid(oItem))
            DestroyObject(oItem,0.1);
        }
    DestroyObject(o,0.5);
}

void Decompose(object oCorpse)
{
    object oNew;

    // Body decomposes
    SetIsDestroyable(TRUE);
    object oItem = GetFirstItemInInventory();
    while (GetIsObjectValid(oItem)) {
        DestroyObject(oItem,0.1);
        oItem = GetNextItemInInventory();
        }
    int n;
    for (n=0; n<NUM_INVENTORY_SLOTS; n++) {
        oItem = GetItemInSlot(n);
        if (GetIsObjectValid(oItem))
            DestroyObject(oItem,0.1);
        }
    TakeGoldFromCreature(GetGold(),OBJECT_SELF,TRUE);
    DestroyObject(OBJECT_SELF,0.1);

    // Corpse is destroyed and remains remain
    DestroyObject(oCorpse);

    /*
    // Corpse is destroyed and items fall on the ground
    oItem = GetFirstItemInInventory(oCorpse);
    while (GetIsObjectValid(oItem)) {
        oNew = CopyObject(oItem,GetLocation(oCorpse));
        DestroyObject(oItem,0.1);
        oItem = GetNextItemInInventory(oCorpse);
        }
    DestroyObject(oCorpse,0.2);
    */
}

void DieAndLeaveCorpse(string sTemplate = "corpse")
{
    if (!GetLocalInt(OBJECT_SELF,"bNoBlood")
        && !GetLocalInt(GetArea(OBJECT_SELF),"bAir")) {
        switch (GetRacialType(OBJECT_SELF))
            {
        case RACIAL_TYPE_ANIMAL:
        case RACIAL_TYPE_BEAST:
        case RACIAL_TYPE_DWARF:
        case RACIAL_TYPE_DRAGON:
        case RACIAL_TYPE_ELF:
        case RACIAL_TYPE_GIANT:
        case RACIAL_TYPE_GNOME:
        case RACIAL_TYPE_HALFELF:
        case RACIAL_TYPE_HALFLING:
        case RACIAL_TYPE_HALFORC:
        case RACIAL_TYPE_HUMAN:
        case RACIAL_TYPE_HUMANOID_GOBLINOID:
        case RACIAL_TYPE_HUMANOID_MONSTROUS:
        case RACIAL_TYPE_HUMANOID_ORC:
            DestroyObject(CreateObject(OBJECT_TYPE_PLACEABLE,
                "plc_bloodstain",GetLocation(OBJECT_SELF)),600.0);
        default:
            }
        }

    if (GetLocalInt(OBJECT_SELF,"bDieImmediately") || GetLootable(OBJECT_SELF))
        return;

    // Leave a lootable corpse
    SetIsDestroyable(FALSE,TRUE,GetLocalInt(OBJECT_SELF,"bSelectableWhenDead"));

    // Create useable corpse object and transfer inventory of droppable items
    vector vPos = GetPosition(OBJECT_SELF) - Vector(0.0,0.0,0.11);
    float fOrient = GetFacing(OBJECT_SELF);
    location lLoc = Location(GetArea(OBJECT_SELF),vPos,fOrient);
    object oCorpse = CreateObject(OBJECT_TYPE_PLACEABLE,sTemplate,lLoc,
        FALSE/*,"CorpseOf"+GetTag(OBJECT_SELF)*/);
    SetLocalObject(oCorpse,"oOwner",OBJECT_SELF);
    SetLocalInt(oCorpse,"bNoRaise",GetLocalInt(OBJECT_SELF,"bNoRaise"));
    SetLocalObject(OBJECT_SELF,"oCorpse",oCorpse);

    // Transfer objects in backpack - these items are destroyed from the NPC body.
    object oItem = GetFirstItemInInventory();
    while (GetIsObjectValid(oItem)) {
        TransferItem(oItem,oCorpse,TRUE);
        oItem = GetNextItemInInventory();
        }

    // Transfer equipped items - except for creature items
    // Note: Except for weapons, all equipped items remain on the NPC body as duplicates
    TransferItem(GetItemInSlot(INVENTORY_SLOT_ARMS),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_ARROWS),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_BELT),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_BOLTS),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_BOOTS),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_BULLETS),oCorpse,TRUE);
    //TransferItem(GetItemInSlot(INVENTORY_SLOT_CARMOUR),oCorpse);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_CHEST),oCorpse,FALSE);    // Armor stays on the victim
    SetDroppableFlag(GetItemInSlot(INVENTORY_SLOT_CHEST),FALSE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_CLOAK),oCorpse,TRUE);
    //TransferItem(GetItemInSlot(INVENTORY_SLOT_CWEAPON_B),oCorpse);
    //TransferItem(GetItemInSlot(INVENTORY_SLOT_CWEAPON_L),oCorpse);
    //TransferItem(GetItemInSlot(INVENTORY_SLOT_CWEAPON_R),oCorpse);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_HEAD),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_LEFTHAND),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_LEFTRING),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_NECK),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_RIGHTHAND),oCorpse,TRUE);
    TransferItem(GetItemInSlot(INVENTORY_SLOT_RIGHTRING),oCorpse,TRUE);

    int nGold = GetGold();
    TakeGoldFromCreature(nGold, OBJECT_SELF, TRUE);
    CreateItemOnObject("nw_it_gold001", oCorpse, nGold);

    if (!GetLocalInt(OBJECT_SELF,"bDoNotDecompose") && !GetLocalInt(OBJECT_SELF,"bNeverDecay")) {
        if (!GetIsObjectValid(GetFirstItemInInventory(oCorpse))) {
            // Owned nothing, so do not create a corpse
            DestroyObject(oCorpse);
            oCorpse = OBJECT_INVALID;
            }
        DelayCommand(180.0,Decompose(oCorpse));
        }

}

// This function checks whether a given NPC is currently performing a ForceMove as part of his/her
// WalkWayPoints() command. If so, then actions are cancelled and the walk restarted.
// This is done whenever a PC enters an area in which NPCs are using the pc_movejump script.
void CheckMoveJump(object oArea)
{
    object o = GetFirstObjectInArea(oArea);
    while (GetIsObjectValid(o)) {
        if (GetObjectType(o)==OBJECT_TYPE_CREATURE
            && GetLocalInt(o,"bCheckJump")
            && GetLocalInt(o,"bForceMove")
            && !GetLocalInt(o,"bOverride")
            //&& !GetLocalInt(o,"nNoTalk")
            && GetCurrentAction(o)==ACTION_MOVETOPOINT) {
            ClearAllActions();
            SetLocalInt(o,"bForceMove",FALSE);
            ExecuteScript("nw_walk_wp",o);
            }
        o = GetNextObjectInArea(oArea);
        }
}

// Handle all generic things which occur when an area is entered
void HandleAreaEnter()
{
    object o = GetEnteringObject();

    SetLocalLocation(o,"lEntered",GetLocation(o));
    if (GetIsPC(o)) {
        CheckMoveJump(OBJECT_SELF);
        SetLocalObject(GetModule(),"oPCArea",GetArea(o));
        }
}

void FollowCheck(object oFollow, float fDist=1.0)
{
    object oArea = GetArea(oFollow);

    if (GetIsObjectValid(oFollow) && GetArea(OBJECT_SELF)!=oArea) {
        if (GetLocalInt(oArea,"bSpecialEnter") && GetTag(OBJECT_SELF)!="Kruor") {
            DeleteLocalObject(oFollow,"oFollowedBy");
            DeleteLocalObject(OBJECT_SELF,"oFollow");
            SetAILevel(OBJECT_SELF,AI_LEVEL_DEFAULT);
            SetLocalInt(OBJECT_SELF,"bNoAnims",FALSE);
            }
        else {
            // Appear wherever the leader entered
            location lLoc = GetLocalLocation(oFollow,"lEntered");
            if (GetAreaFromLocation(lLoc)==GetArea(oFollow)) {
                ClearAllActions(TRUE);
                JumpToLocation(lLoc);
                }
            }
        }
}

void Follow(object oFollow, float fDist=1.0, int bForce=FALSE)
{
    if (!GetIsObjectValid(oFollow))
        return;

    if (GetCurrentAction()!=ACTION_MOVETOPOINT && !GetLocalInt(OBJECT_SELF,"bOverride"))
        ClearAllActions();

    if (GetArea(OBJECT_SELF)==GetArea(oFollow)) {
        // Follow, keeping at a distance of fDist
        if (GetDistanceToObject(oFollow)<fDist)
            ActionMoveAwayFromObject(oFollow,FALSE,fDist);
        else if (bForce)
            ActionForceMoveToObject(oFollow,TRUE,fDist,10.0);
        else
            ActionMoveToObject(oFollow,TRUE,fDist);
        }
    else
        DelayCommand(9.0,FollowCheck(oFollow,fDist));
}

//////////////////////////////////////////////////////////////////
// PC Dreaming functions
//////////////////////////////////////////////////////////////////

// This function makes a copy of the PC which is ghosted and invisible
object MakePCCopy(object oPC = OBJECT_SELF, object oLocation = OBJECT_INVALID)
{
    //object oCopy = CopyObject(OBJECT_SELF,GetLocation(GetObjectByTag("wpPrepareCreature")));
    if (!GetIsObjectValid(oLocation))
        oLocation = oPC;
    object oCopy = CopyObject(oPC,GetLocation(oLocation));
    ApplyEffectToObject(DURATION_TYPE_PERMANENT,EffectCutsceneGhost(),oCopy);
    ApplyEffectToObject(DURATION_TYPE_PERMANENT,EffectVisualEffect(VFX_DUR_CUTSCENE_INVISIBILITY),oCopy);
    SetPlotFlag(oCopy,TRUE);
    ChangeToStandardFaction(oCopy,STANDARD_FACTION_COMMONER);
    SetIsTemporaryFriend(oPC,oCopy);
    SetLocalObject(oPC,"oMyCopy",oCopy);
    SetLocalObject(oCopy,"oOwner",oPC);

    effect e = GetFirstEffect(oPC);
    while (GetIsEffectValid(e)) {
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,e,oCopy);
        e = GetNextEffect(oPC);
        }

    return oCopy;
}
void CopyRests()
{
    SetLocalInt(OBJECT_SELF,"bSleepingCopy",TRUE);
    ActionPlayAnimation(ANIMATION_LOOPING_SIT_CROSS,1.0,999.0);
    SetCommandable(FALSE);
}

// This makes an object invisible and ghosted
void MakeGhosted(object o=OBJECT_SELF, float fDelay=0.1)
{
    ApplyEffectToObject(DURATION_TYPE_PERMANENT,EffectCutsceneGhost(),o);
    DelayCommand(fDelay,ApplyEffectToObject(DURATION_TYPE_PERMANENT,
        EffectVisualEffect(VFX_DUR_CUTSCENE_INVISIBILITY),o));
    SetLocalInt(o,"bGhosted",TRUE);
    if (GetIsPC(o)) {
        object oPC = GetFirstPC();
        // This is here to make sure players don't follow each other into dreams
        while (GetIsObjectValid(oPC)) {
            if (GetCurrentAction(oPC)==ACTION_FOLLOW)
                AssignCommand(oPC,ClearAllActions());
            oPC = GetNextPC();
            }
        }
}

// This makes an object visible and unghosted
void MakeNotGhosted(object o=OBJECT_SELF)
{
    DeleteLocalInt(o,"bGhosted");

    effect e = GetFirstEffect(o);
    int nType = GetEffectType(e);
    while (GetIsEffectValid(e))
        {
        if (nType == EFFECT_TYPE_CUTSCENEGHOST && !GetLocalInt(o,"hls_invis"))
            RemoveEffect(o,e);
        if (nType == EFFECT_TYPE_VISUALEFFECT && !GetLocalInt(o,"hls_invis"))
            RemoveEffect(o,e);
        e = GetNextEffect(o);
        nType = GetEffectType(e);
        }
}

// This function makes a player lost his/her associates for the duration of a cutscene
void LoseAssociates(object oPC = OBJECT_SELF, int bRest=FALSE)
{
    // Henchmen get unjoined and wait for the master
    int n=1;
    object o = GetHenchman(oPC,n);
    while (GetIsObjectValid(o)) {
        //SendMessageToPC(oPC,GetName(o)+" leaves temporarily");  // DEBUGOFF
        SetLocalObject(oPC,"oHench"+IntToString(n),o);
        AssignCommand(o,ClearAllActions(TRUE));
        AssignCommand(o,RemoveHenchman(oPC,o));
        SetLocalInt(o,"bAssocLost",TRUE);
        //RemoveHenchman(oPC,o);
        if (bRest) {
            SetLocalInt(o,"bWaitingForMaster",TRUE);
            //AssignCommand(o,ActionPlayAnimation(ANIMATION_LOOPING_SIT_CROSS,1.0,15.0));
            //AssignCommand(o,ActionRest());
            AssignCommand(o,ActionDoCommand(SetCommandable(TRUE,o)));
            AssignCommand(o,ActionDoCommand(ForceRest(o)));
            //SetCommandable(FALSE,o);
            }
        n=n+1;
        o = GetHenchman(oPC,n);
        }
    // Other associates simply get removed
    o = GetAssociate(ASSOCIATE_TYPE_ANIMALCOMPANION,oPC);
    if (GetIsObjectValid(o)) {
        AssignCommand(o,SetIsDestroyable(TRUE));
        DestroyObject(o);
        }
    o = GetAssociate(ASSOCIATE_TYPE_DOMINATED,oPC);
    if (GetIsObjectValid(o)) {
        effect e = GetFirstEffect(o);
        while (GetIsEffectValid(e)) {
            if (GetEffectType(e)==EFFECT_TYPE_DOMINATED)
                RemoveEffect(o,e);
            e = GetNextEffect(o);
            }
        }
    o = GetAssociate(ASSOCIATE_TYPE_FAMILIAR,oPC);
    if (GetIsObjectValid(o)) {
        AssignCommand(o,SetIsDestroyable(TRUE));
        DestroyObject(o);
        }
    o = GetAssociate(ASSOCIATE_TYPE_SUMMONED,oPC);
    if (GetIsObjectValid(o)) {
        RemoveSummonedAssociate(oPC,o);
        AssignCommand(o,SetIsDestroyable(TRUE));
        DestroyObject(o,1.0);
        }
}

// Regain associates after a cutscene ends
void RegainAssociates(object oPC = OBJECT_SELF, int bRegain = TRUE)
{
    int n;
    object o;
    for (n=1; n<=GetMaxHenchmen(); n++) {
        o = GetLocalObject(oPC,"oHench"+IntToString(n));
        if (GetIsObjectValid(o)) {
            if (bRegain)
                AddHenchman(oPC,o);
            SetLocalInt(o,"bAssocLost",FALSE);
            DeleteLocalInt(o,"bWaitingForMaster");
            AssignCommand(o,ClearAllActions());
            }
        DeleteLocalObject(oPC,"oHench"+IntToString(n));
        }
}

void DoWakeFromLucidDream()
{
    object oPC = OBJECT_SELF;
    ClearAllActions();
    MakeNotGhosted(oPC);
    SetLocalInt(oPC,"nDreamState",2);
    SetLocalLocation(oPC,"lLucidLoc",GetLocation(oPC));
    ForceRest(oPC);
    int n=1;
    object o = GetHenchman(oPC,1);
    while (GetIsObjectValid(o)) {
        if (GetLocalInt(o,"bHassirDream")) {
            AssignCommand(o,ClearAllActions(TRUE));
            SetLocalObject(o,"NW_L_FORMERMASTER", oPC);
            AssignCommand(o,RemoveHenchman(oPC,o));
            ForceRest(o);
            }
        else {
            SetPlotFlag(o,FALSE);
            AssignCommand(o,SetIsDestroyable(TRUE,FALSE,FALSE));
            DestroyObject(o,3.0);
            }
        n++;
        o = GetHenchman(oPC,n);
        }
    JumpToLocation(GetLocalLocation(oPC,"lRestLoc"));
    DelayCommand(4.0,RegainAssociates(oPC,TRUE));
}

void WakeFromLucidDream()
{
    DoWakeFromLucidDream();
    if (GetLocalInt(OBJECT_SELF,"bProphet")) {
        SetLocalInt(GetModule(),"bProphetDreaming",FALSE);
        // All dreaming players must wake from the dream
        object oPC = GetFirstPC();
        if (GetLocalInt(oPC,"nDreamState")==1) {
            ApplyEffectToObject(DURATION_TYPE_INSTANT,EffectResurrection(),oPC);
            AssignCommand(oPC,DoWakeFromLucidDream());
            }
        oPC = GetNextPC();
        }
}

float DoFloatingText(object oTarget, string sText, int bBroadcast=FALSE)
{
    int nCharPerLine = 50;
    int nLen = GetStringLength(sText);
    float fDelay = 0.0;
    int i=-1;
    string sLine;
    SetLocalInt(oTarget,"bFloatingTextActive",TRUE);
    i = FindSubString(sText,"#");
    while (nLen > nCharPerLine || i>=0) {
        // First look for any hard-wired line breaks "#"
        i = FindSubString(sText,"#");
        if (i==-1) {
            // If breaks were not found, separate the string at different words.
            i = nCharPerLine;
            while (i>=0 & GetSubString(sText,i,1)!=" ")
                i--;
            }
        if (i>=0) {
            sLine = GetSubString(sText,0,i);
            sText = GetSubString(sText,i+1,nLen-i-1);
            nLen = GetStringLength(sText);
            DelayCommand(fDelay,FloatingTextStringOnCreature(sLine,oTarget,bBroadcast));
            fDelay = fDelay + 1.5;
            }
        else
            nLen = 0;
        i = FindSubString(sText,"#");
        }
    DelayCommand(fDelay,FloatingTextStringOnCreature(sText,oTarget,bBroadcast));
    DelayCommand(fDelay+1.0,SetLocalInt(oTarget,"bFloatingTextActive",FALSE));
    return fDelay;
}

void FloatingText(object oTarget, string sText, int bBroadcast=FALSE)
{
    DoFloatingText(oTarget,sText,bBroadcast);
}

void LevelUp(object oHench, int bLearn = TRUE)
{
    int nClass = GetClassByPosition(1,oHench);
    int nPackage = PACKAGE_INVALID;
    if (GetTag(oHench)=="Llarien") {
        if ((GetHitDice(oHench)/2)*2 == GetHitDice(oHench)) { // if total level is even
            nClass = CLASS_TYPE_RANGER;
            nPackage = PACKAGE_RANGER;
            }
        else {
            nClass = CLASS_TYPE_CLERIC;
            nPackage = PACKAGE_CLERIC;
            }
        }
    if (GetTag(oHench)=="Merudoc") {
        if ((GetHitDice(oHench)/2)*2 == GetHitDice(oHench)) { // if total level is even
            nClass = CLASS_TYPE_ROGUE;
            nPackage = PACKAGE_ROGUE;
            }
        else {
            nClass = CLASS_TYPE_FIGHTER;
            nPackage = PACKAGE_FIGHTER_FINESSE;
            }
        }
    if (GetTag(oHench)=="Hassir") {
        nClass = CLASS_TYPE_BARBARIAN;
        nPackage = PACKAGE_BARBARIAN;
        }
    if (GetTag(oHench)=="Isandra") {
        nClass = CLASS_TYPE_MONK;
        nPackage = PACKAGE_MONK_ASSASSIN;
        }
    LevelUpHenchman(oHench,nClass,bLearn,nPackage);
}

