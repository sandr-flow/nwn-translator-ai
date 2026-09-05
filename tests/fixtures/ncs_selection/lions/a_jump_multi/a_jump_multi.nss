#include "NW_I0_GENERIC"

void main()
{
   object oPC = GetFirstPC();

    if (GetLocalInt(oPC, "conseil_elfe1") != 1)
    {
    SetLocalInt(GetFirstPC(), "conseil_elfe", 1);
    SetLocalInt(GetFirstPC(), "conseil_elfe1", 1);
    AssignCommand(GetObjectByTag("bellem"), JumpToObject(GetObjectByTag("bellem_tour")));
    AssignCommand(GetObjectByTag("octael"), JumpToObject(GetObjectByTag("octael_tour")));
    AssignCommand(GetObjectByTag("prialle"), JumpToObject(GetObjectByTag("prialle_tour")));
    AssignCommand(GetObjectByTag("iandrine"), JumpToObject(GetObjectByTag("iandrine_tour")));
   // AssignCommand(GetObjectByTag("elwen"), JumpToObject(GetObjectByTag("elwen_tour")));
    //AssignCommand(GetObjectByTag("elwen"), JumpToObject(GetObjectByTag("elwen_reserve")));

    //effect eEffect = EffectVisualEffect(VFX_FNF_SUMMON_GATE);
    effect eEffect = EffectVisualEffect(VFX_DUR_MAGIC_RESISTANCE);

      ApplyEffectAtLocation(DURATION_TYPE_TEMPORARY, eEffect, GetLocation(GetObjectByTag("bellem_tour")), 2.0);
      ApplyEffectAtLocation(DURATION_TYPE_TEMPORARY, eEffect, GetLocation(GetObjectByTag("octael_tour")), 2.0);
      ApplyEffectAtLocation(DURATION_TYPE_TEMPORARY, eEffect, GetLocation(GetObjectByTag("prialle_tour")), 2.0);
      ApplyEffectAtLocation(DURATION_TYPE_TEMPORARY, eEffect, GetLocation(GetObjectByTag("iandrine_tour")), 2.0);


    }

}
