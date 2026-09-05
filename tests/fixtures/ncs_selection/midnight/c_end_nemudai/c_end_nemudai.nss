#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
//#include "enter_exit1"
void main()
{
MonkJournal("Nemudai");
ClearAllActions();
ActionForceMoveToObject(GetObjectByTag("WP_GC_Exit2"),TRUE,1.0,45.0);
ActionDoCommand(DestroyObject(OBJECT_SELF));
DelayCommand(0.1,SetCommandable(FALSE,OBJECT_SELF));
}
