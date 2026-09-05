#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
object DW = GetObjectByTag("Dragonweaver");
float Pan()
    {
    float pause = 0.0 ;
    float Start = GetFacing(GetFirstPC());
    vector Vec2 = GetPosition(DW);
    vector Vec1 =GetPosition(GetFirstPC());
    Vec2 = Vector(Vec2.x-Vec1.x,Vec2.y-Vec1.y,0.0);
    float End =atan(Vec2.y/Vec2.x)+180;
    while (Start > End)
        {
        pause +=0.04;
        Start -=0.4;
        DelayCommand(pause,SetCameraFacing(Start));
        }
    return pause;
    }

void DoStuff()
    {
    ClearAllActions();
    ActionPauseConversation();
    Face(GetFirstPC());
    DelayCommand(Pan(),ActionResumeConversation());
    }

void main()
{

AssignCommand(DW,ClearAllActions());
AssignCommand(DW,ActionPauseConversation());
AssignCommand(DW,Face(GetFirstPC()));
float Pause = Pan();
DelayCommand(Pause,AssignCommand(DW,ActionResumeConversation()));
DelayCommand(Pause,Face(DW));
}
