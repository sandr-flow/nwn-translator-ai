//::///////////////////////////////////////////////////
//:: 69_HENCH_CANLVL
//:: TRUE if the caller can level up (is at least two
//:: levels below the speaker, and not currently
//:: busy).
//:: Copyright (c) 2002 Floodgate Entertainment
//:: Created By: 69MEH69
//:: Created On: JULY 2004
//::///////////////////////////////////////////////////

#include "69_hench_lib"

int StartingConditional()
{
    return GetCanLevelUp69(GetPCSpeaker());
}
