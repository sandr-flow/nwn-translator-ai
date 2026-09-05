#include "in_g_cutscene"

void main()
{
     object oPC = GetLocalObject(GetModule(),"cutscenebejala");

     float fFace = GetFacing(oPC);

     GestaltStartCutscene(oPC,"_bejalascene",TRUE,TRUE,TRUE,TRUE,FALSE,0);
     GestaltInvisibility (0.0, oPC, 35.0);
     GestaltCameraMove       (0.0,
                            fFace + 365.0,7.0,85.0,
                            fFace + 365.0,7.0,85.0,
                            20.0,30.0,oPC);
     GestaltStopCutscene(35.0,oPC);

     }



