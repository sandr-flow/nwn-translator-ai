// In Heartbeat
void main()
{

   ActionPlayAnimation(ANIMATION_LOOPING_SIT_CROSS, 1.0, 999.9);

   if (IsInConversation(OBJECT_SELF)) return;

   if (GetLocalInt(GetModule(), "metcbamerc") == 1) return;

   object oPC = GetNearestCreature(CREATURE_TYPE_PLAYER_CHAR, PLAYER_CHAR_IS_PC);
   object oPlayer = GetFirstPC();
   if (GetIsObjectValid(oPC) == FALSE) return;

   if (GetDistanceBetween(OBJECT_SELF, oPC) > 6.0f) return;
   switch(d4())
   {
   case 1:

   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="Rags") {
     SpeakString("Hey street-side, looking for some blades?");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="ROBE_ALMRAIVEN2") {
     SpeakString("Hey! That's right, overe here Loomer.");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="RUBY_WARD_OUTFIT") {
     SpeakString("Hey stinker, looking for some protection?");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="EVELYN__APP_ROBE") {
     SpeakString("Sure you're on the right street weaver?");
     break;
   }
   SpeakString("You looking for some blades?");
       break;

   case 2:

   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="Rags") {
     SpeakString("Street-side! I want to speak with you!");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="ROBE_ALMRAIVEN2") {
     SpeakString("Hey there Loomer. Why don't you come over here.");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="RUBY_WARD_OUTFIT") {
     SpeakString("You need a new blade there stinker?");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="EVELYN__APP_ROBE") {
     SpeakString("Need a blade in your stomach weaver?");
     break;
   }
   SpeakString("Come here! Yeah I mean you!");

   break;

      case 3:

         if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="Rags") {
     SpeakString("Over here.");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="ROBE_ALMRAIVEN2") {
     SpeakString("Psst... Loomer. Over here!");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="RUBY_WARD_OUTFIT") {
     SpeakString("This way.");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="EVELYN__APP_ROBE") {
     SpeakString("Move on weaver.");
     break;
   }
   SpeakString("I got your goods.");

      break;

   case 4:

   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="Rags") {
     SpeakString("You need protection street-side?");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="ROBE_ALMRAIVEN2") {
     SpeakString("Loomer looking for some fun?");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="RUBY_WARD_OUTFIT") {
     SpeakString("I got some goods for you stinker.");
     break;
   }
   if (GetTag(GetItemInSlot(INVENTORY_SLOT_CHEST, oPlayer))=="EVELYN__APP_ROBE") {
     SpeakString("What are you looking at weaver?");
     break;
   }
   SpeakString("You just going to stand there?");

      break;
   }
}

