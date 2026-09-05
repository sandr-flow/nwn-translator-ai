#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
//#include "enter_exit1"
//#include "cutscene"

void TokenMaker()
    {
    int Light = GetLocalInt(GetFirstPC(),"Light");
    string Token;
    if (Light)
        {
        Token = "have "+IntToString(Light)+" Light scroll";
        if (Light > 1)
            Token +="s";
        Token +=".";
        }
    else
        Token = "don't have any Light scrolls.";
    SetCustomToken(20,Token);
    }

void StartConv()
    {
    AssignCommand(GetFirstPC(),ClearAllActions());
    ActionStartConversation(GetFirstPC(),"areatrans");
    }

void CheckDist()
    {
    if (GetDistanceBetween(Piro(),GetFirstPC()) < 5.0)
        StartConv();
    else
        DelayCommand(0.2,CheckDist());
    }

void main()
{
  object oClicker = GetClickingObject();
  object oTarget = GetTransitionTarget(OBJECT_SELF);
  location lLoc = GetLocation(oTarget);
if (GetLocalInt(OBJECT_SELF,"done") && GetIsObjectValid(Piro()))
    {
    if (GetJournalEntry("BloodMoon")==60)
        {
        VoiceIn();
        DelayCommand(0.5,StartConv());
        }
    else
        AssignCommand(oClicker,AreaTrans(GetTag(oTarget)));
    }
else
    {
    VoiceIn();
    TokenMaker();
    if (GetIsObjectValid(Piro()))
        {
        //AssignCommand(Piro(),PlayVoiceChat(VOICE_CHAT_HOLD));
        PiroMove(GetFirstPC(),TRUE);
        DelayCommand(0.5,CheckDist());
        }
    else
        DelayCommand(0.5,StartConv());
    }

}
