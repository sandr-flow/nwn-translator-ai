void main()
{
object oMod = GetModule();

if (GetLocalInt(oMod, "insidespit") == 1) {

           string sMyTagName = GetTag(OBJECT_SELF);
           string sSittableTagName = "SC_MANSIT";
           int nChair = 1;
           object oChair;
           oChair = GetNearestObjectByTag(sSittableTagName, OBJECT_SELF, nChair);
           //ActionSit(oChair);
            AssignCommand(OBJECT_SELF, SetFacing(DIRECTION_SOUTH));
            ActionSit(oChair);
            }
if (GetLocalInt(oMod, "insideburnlog") == 1) {

           string sMyTagName = GetTag(OBJECT_SELF);
           string sSittableTagName = "EBL_MANSIT";
           int nChair = 1;
           object oChair;
           oChair = GetNearestObjectByTag(sSittableTagName, OBJECT_SELF, nChair);
           //ActionSit(oChair);
            AssignCommand(OBJECT_SELF, SetFacing(DIRECTION_SOUTH));
            ActionSit(oChair);
            }

           }
