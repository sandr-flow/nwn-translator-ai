/*69_client_enter
 OnClientEnter Module Event
 Checks for Leadership, if TRUE sets maximum henchmen
 on PC

 Created by: 69MEH69
 Created on: Sep2004
*/

#include "69_hench_lib"

void main()
{
  object oPC = GetEnteringObject();
  int nLeadership = GetLocalInt(GetModule(), "nLeadership");
  if(nLeadership == 1)
  {
    //SendMessageToPC(oPC, "Leadership = 1"); //Test
    SetMaxHenchmen69(oPC);
  }

}
