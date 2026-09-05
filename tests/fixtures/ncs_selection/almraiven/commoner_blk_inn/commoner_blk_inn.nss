//::///////////////////////////////////////////////
//:: commoner_resume
//:://////////////////////////////////////////////
/*
Sends commoner to random waypoint after speaking
its one liner.
*/
//:://////////////////////////////////////////////
//:: Created By: EntropyDecay
//:: Created On: May 2003
//:://////////////////////////////////////////////

// declarations
void MoveAwayAndDisappear(object oPerson, int nWalkType);

// implementation
void main()
{
object oArea = GetArea(OBJECT_SELF);
int nWalkType = GetLocalInt(oArea, "nWalkType");
object oUmbrella=GetItemPossessedBy(OBJECT_SELF, "umbrella");
object oTorch=GetItemPossessedBy(OBJECT_SELF, "NW_IT_TORCH001");

AssignCommand(OBJECT_SELF, DelayCommand(0.1,ClearAllActions()));
if (GetIsObjectValid(oUmbrella))
{
if (GetLocalInt(oArea, "nWeather")==WEATHER_RAIN)
{
AssignCommand(OBJECT_SELF, DelayCommand(0.1,ActionUnequipItem(oTorch)));
AssignCommand(OBJECT_SELF, DelayCommand(0.1,ActionEquipItem(oUmbrella, INVENTORY_SLOT_RIGHTHAND)));
if (GetIsNight()) {nWalkType=GetLocalInt(oArea, "nCommonerNightRun");}
else {nWalkType=GetLocalInt(oArea, "nCommonerDayRun");}
}
else
{
AssignCommand(OBJECT_SELF, DelayCommand(0.1, ActionUnequipItem(oUmbrella)));
if (GetIsNight()) {AssignCommand(OBJECT_SELF, DelayCommand(0.1, ActionEquipItem(oTorch, INVENTORY_SLOT_LEFTHAND)));}
}
}

DelayCommand(1.0, MoveAwayAndDisappear(OBJECT_SELF, nWalkType));
}


// Function: MoveAwayAndDisappear
// Parameters: object oPerson this should be a person object, ie something that can move.
// int nWalkType this says whether to run or walk
// Returns: void
// Description: here we move the Person object to the nearest waypoint. How they move depends on the walktype parameter.
// when the person gets there they disappear.
//
// suggestions for improvement are to store the destination location object as a variable on creation.
// this will prevent the person from going back to the original location or changing direction if that point is the closest and
// seemingly give them a sense of purpose. ie. you interrupted me while going THERE so now we have finished chatting I want to
// continue going THERE
//

void MoveAwayAndDisappear(object oPerson, int nWalkType)
{
int iAnimation;
switch (Random(8))
    {
    case 0: iAnimation = ANIMATION_FIREFORGET_PAUSE_SCRATCH_HEAD; break;
    case 1: iAnimation = ANIMATION_LOOPING_TALK_FORCEFUL; break;
    case 2: iAnimation = ANIMATION_LOOPING_TALK_PLEADING; break;
    case 3: iAnimation = ANIMATION_FIREFORGET_GREETING; break;
    case 4: iAnimation = ANIMATION_LOOPING_PAUSE_TIRED; break;
    case 5: iAnimation = ANIMATION_LOOPING_TALK_LAUGHING; break;
    case 6: iAnimation = ANIMATION_LOOPING_CONJURE1; break;
    case 7: iAnimation = ANIMATION_FIREFORGET_SALUTE; break;
    }


if (GetIsDay()) {

if(d20()<=5) {

int iRandSpeak;
string sSpeak;
string sMe=GetTag(OBJECT_SELF);  //or you can use racial types which is what i do
if(sMe=="NW_COMMONER")
{
iRandSpeak=d10();
if(iRandSpeak==1)
  sSpeak="Gettin' yourself a room?";

if(iRandSpeak==2)
  sSpeak="I be hearin' a room go broken into last night!";

if(iRandSpeak==3)
  sSpeak="Pardon, have you seen a gold pouch laying around?";

if(iRandSpeak==4)
  sSpeak="A fine day if I be sayin'";

if(iRandSpeak==5)
  sSpeak="What ye be doin' stuck inside?";

if(iRandSpeak==6)
  sSpeak="Be hearin' there be a fine Bard here tonight.";

if(iRandSpeak==7)
  sSpeak="Pardon me.";

if(iRandSpeak==8)
  sSpeak="My mistake, I should watch where I be walkin'";

if(iRandSpeak==9)
  sSpeak="Pardon, is it hot outside?";

if(iRandSpeak==10)
  sSpeak="Blast!  I be sleepin' in again.";

//etc.
}
ActionPlayAnimation(iAnimation);
ActionWait(2.0);
SpeakString(sSpeak);
ActionWait(5.0);
AssignCommand(oPerson,
ActionForceMoveToObject(
GetNearestObjectByTag("NW_COMMONER_WALKTO", oPerson, 1),
nWalkType, 1.0, 30.0));
AssignCommand(oPerson,
ActionDoCommand(DestroyObject(oPerson, 0.1)));

} else {
ActionPlayAnimation(iAnimation);
ActionWait(5.0);
AssignCommand(oPerson,
ActionForceMoveToObject(
GetNearestObjectByTag("NW_COMMONER_WALKTO", oPerson, 1),
nWalkType, 1.0, 30.0));
AssignCommand(oPerson,
ActionDoCommand(DestroyObject(oPerson, 0.1)));
}
} else {

if((d20()<=5) && (GetIsNight())) {

int iRandSpeak;
string sSpeak;
string sMe=GetTag(OBJECT_SELF);  //or you can use racial types which is what i do
if(sMe=="NW_COMMONER")
{
iRandSpeak=d10();
if(iRandSpeak==1)
  sSpeak="Ye blasted drunken fool, watch where ye be goin'!";

if(iRandSpeak==2)
  sSpeak="Pardon me, I ain't be seein' ye.";

if(iRandSpeak==3)
  sSpeak="I be hearin' there bein' a storm brewin' this evenin'";

if(iRandSpeak==4)
  sSpeak="Get outta me way I be sayin'!";

if(iRandSpeak==5)
  sSpeak="Is it really you?  Oh, no, it ain't.  My mistake.";

if(iRandSpeak==6)
  sSpeak="Watch your step!";

if(iRandSpeak==7)
  sSpeak="I hear tomorrow is going to be another hot one!";

if(iRandSpeak==8)
  sSpeak="*hic*";

if(iRandSpeak==9)
  sSpeak="Blast!  Ye stepped on me broken toe!";

if(iRandSpeak==10)
  sSpeak="Watch where ye be going'";

//etc.
}
ActionPlayAnimation(iAnimation);
ActionWait(2.0);
SpeakString(sSpeak);
ActionWait(5.0);
AssignCommand(oPerson,
ActionForceMoveToObject(
GetNearestObjectByTag("NW_COMMONER_WALKTO", oPerson, 1),
nWalkType, 1.0, 30.0));
AssignCommand(oPerson,
ActionDoCommand(DestroyObject(oPerson, 0.1)));

} else {
ActionPlayAnimation(iAnimation);
ActionWait(5.0);
AssignCommand(oPerson,
ActionForceMoveToObject(
GetNearestObjectByTag("NW_COMMONER_WALKTO", oPerson, 1),
nWalkType, 1.0, 30.0));
AssignCommand(oPerson,
ActionDoCommand(DestroyObject(oPerson, 0.1)));
}}}
