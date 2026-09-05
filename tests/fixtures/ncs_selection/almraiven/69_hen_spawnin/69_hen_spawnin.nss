//::///////////////////////////////////////////////
//:: Associate: On Spawn In
//:: 69_hen_spawnin
//:: Copyright (c) 2001 Bioware Corp.
//:://////////////////////////////////////////////
//:://////////////////////////////////////////////
//:: Created By: Preston Watamaniuk
//:: Created On: Nov 19, 2001
//:://////////////////////////////////////////////

#include "69_INC_HENAI"

void main()
{
    // FALIS CHECK
    object oMod = GetModule();
    object oTarget1 = GetObjectByTag("RE_RND_M6");
    object oChair1 = GetObjectByTag("GoodmaneChair");
    int iAnimation;
    iAnimation = ANIMATION_LOOPING_DEAD_BACK;

    if ( (GetLocalInt(oMod, "wundraquest") >= 10) && (GetLocalInt(oMod, "wundraquest") <= 29) )
    {
    AssignCommand(oTarget1, ActionSit(oChair1));
    }

    if (GetLocalInt(oMod, "wundraquest") == 30)
    {
    AssignCommand(oTarget1, ActionSit(oChair1));
    }

    if (GetLocalInt(oMod, "wundraquest") == 50)
    {
    ExecuteScript("_random_walk", OBJECT_SELF);
    }
    // END FALIS CHECK

    //RESPAWN WAYPOINT INSTRUCTIONS
    //Create a specific respawn location for henchman with a waypoint with TAG "WP_Respawn_'TAG'"
    //where 'TAG' is the TAG of the NPC
    //Create a default general respawn location with waypoint with TAG "NW_DEATH_TEMPLE"
    //Create a Home waypoint with TAG "WP_Home_'TAG'" where 'TAG' is the TAG of the NPC
    //     This is where the Hench will go when they quit the PC

    //Sets default level up package based on settings in henchman's blueprint
    //This may be changed through henchman dialog, do not edit
    SetLocalInt(OBJECT_SELF, "ClassPackage", GetCreatureStartingPackage(OBJECT_SELF));
    //Set variable for level up, do not edit
    SetLocalInt(OBJECT_SELF, "NewClass", -1);

    //Sets up the HENCH_LAG for this henchman,
    //Replace the 0 with any number the henchman lags(+)
    //or leads(-) in level. Save this script as something
    //else per this henchman. This allows multileveled
    //henchman
    //Minimum of -1,-2,-3... Maximum of 1,2,3... Same Level = 0
    SetLocalInt(OBJECT_SELF, "HenchLag", 0);

    //Sets the Maximum Level the Henchman may level
    //Default: 40
    SetLocalInt(OBJECT_SELF, "HENCH_MAXLEVEL", 5);

    //Sets whether or not PC is allowed into henchman inventory
    //TRUE: Inventory is accessible
    //FALSE: Inventory is not accessible
    //Default: TRUE
    SetLocalInt(OBJECT_SELF, "HenchInv", FALSE);

    //Sets whether or not initial henchman inventory is no drop
    //TRUE: Inventory is droppable
    //FALSE: Inventory is not droppable
    //Default: TRUE
    SetLocalInt(OBJECT_SELF, "HenchInvDrop", TRUE);

    //Sets the distance from the enemy that the henchman will switch to melee weapons
    SetLocalFloat(OBJECT_SELF, "HenchRange", 5.0);

    //Sets up the special henchmen listening patterns
    SetAssociateListenPatterns();
    // Set additional henchman listening patterns
    bkSetListeningPatterns();

    //Equips melee weapon by default
    //Equips ranged weapons by default if TRUE.
    SetAssociateState(NW_ASC_USE_RANGED_WEAPON, FALSE);

    //Sets the default distance that the henchman will follow
    //the PC, only uncomment one of the following three
    SetAssociateState(NW_ASC_DISTANCE_2_METERS);
    //SetAssociateState(NW_ASC_DISTANCE_4_METERS);
    //SetAssociateState(NW_ASC_DISTANCE_6_METERS);
    //End default distances

    SetAssociateState(NW_ASC_POWER_CASTING);
    SetAssociateState(NW_ASC_HEAL_AT_50);
    SetAssociateState(NW_ASC_RETRY_OPEN_LOCKS);
    SetAssociateState(NW_ASC_DISARM_TRAPS);
    SetAssociateState(NW_ASC_MODE_DEFEND_MASTER, FALSE);

    // April 2002: Summoned monsters, associates and familiars need to stay
    // further back due to their size.
    if (GetAssociate(ASSOCIATE_TYPE_HENCHMAN, GetMaster()) == OBJECT_SELF  ||
        GetAssociate(ASSOCIATE_TYPE_ANIMALCOMPANION, GetMaster()) == OBJECT_SELF  ||
        GetAssociate(ASSOCIATE_TYPE_DOMINATED, GetMaster()) == OBJECT_SELF  ||
        GetAssociate(ASSOCIATE_TYPE_FAMILIAR, GetMaster()) == OBJECT_SELF  ||
        GetAssociate(ASSOCIATE_TYPE_SUMMONED, GetMaster()) == OBJECT_SELF)
    {
        SetAssociateState(NW_ASC_DISTANCE_4_METERS);
    }

    // SPECIAL CONVERSATION SETTTINGS
    //SetSpawnInCondition(NW_FLAG_SPECIAL_CONVERSATION);
    //SetSpawnInCondition(NW_FLAG_SPECIAL_COMBAT_CONVERSATION);
            // This causes the creature to say a special greeting in their conversation file
            // upon Perceiving the player. Attach the [NW_D2_GenCheck.nss] script to the desired
            // greeting in order to designate it. As the creature is actually saying this to
            // himself, don't attach any player responses to the greeting.

    //Set starting location
    SetAssociateStartLocation();

    // For some general behavior while we don't have a master,
    // let's do some immobile animations
    // SetSpawnInCondition(NW_FLAG_IMMOBILE_AMBIENT_ANIMATIONS);

    // For some general behavior while we don't have a master,
    // let's do some mobile animations
    // SetSpawnInCondition(NW_FLAG_AMBIENT_ANIMATIONS);


    // **** Special Combat Tactics *****//
    // * These are special flags that can be set on creatures to
    // * make them follow certain specialized combat tactics.
    // * NOTE: ONLY ONE OF THESE SHOULD BE SET ON A SINGLE CREATURE.

    // * Ranged attacker
    // * Will attempt to stay at ranged distance from their
    // * target.
    // SetCombatCondition(X0_COMBAT_FLAG_RANGED);

    // * Defensive attacker
    // * Will use defensive combat feats and parry
    // SetCombatCondition(X0_COMBAT_FLAG_DEFENSIVE);

    // * Ambusher
    // * Will go stealthy/invisible and attack, then
    // * run away and try to go stealthy again before
    // * attacking anew.
    // SetCombatCondition(X0_COMBAT_FLAG_AMBUSHER);

    // * Cowardly
    // * Cowardly creatures will attempt to flee
    // * attackers.
    // SetCombatCondition(X0_COMBAT_FLAG_COWARDLY);

// CUSTOM USER DEFINED EVENTS
/*
    The following settings will allow the user to fire one of the blank user defined events in the NW_D2_DefaultD.  Like the
    On Spawn In script this script is meant to be customized by the end user to allow for unique behaviors.  The user defined
    events user 1000 - 1010
*/
    //SetSpawnInCondition(NW_FLAG_PERCIEVE_EVENT);         //OPTIONAL BEHAVIOR - Fire User Defined Event 1002
    //SetSpawnInCondition(NW_FLAG_ATTACK_EVENT);           //OPTIONAL BEHAVIOR - Fire User Defined Event 1005
    //SetSpawnInCondition(NW_FLAG_DAMAGED_EVENT);          //OPTIONAL BEHAVIOR - Fire User Defined Event 1006
    //SetSpawnInCondition(NW_FLAG_DISTURBED_EVENT);        //OPTIONAL BEHAVIOR - Fire User Defined Event 1008
    //SetSpawnInCondition(NW_FLAG_END_COMBAT_ROUND_EVENT); //OPTIONAL BEHAVIOR - Fire User Defined Event 1003
    //SetSpawnInCondition(NW_FLAG_ON_DIALOGUE_EVENT);      //OPTIONAL BEHAVIOR - Fire User Defined Event 1004
    //SetSpawnInCondition(NW_FLAG_DEATH_EVENT);            //OPTIONAL BEHAVIOR - Fire User Defined Event 1007

    if(GetLocalInt(OBJECT_SELF, "HenchInvDrop") == FALSE)
    {
      HenchmanNoDropItems69(TRUE, OBJECT_SELF);
    }
}
