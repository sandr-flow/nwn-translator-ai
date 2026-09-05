void main()
{
    object pc = GetFirstPC();
    object waitress = GetObjectByTag("Waitress");
    object cook = GetObjectByTag("cook2");
    object soup = GetObjectByTag("SpicySoup");
    object special = GetObjectByTag("SpecialDish");
    int none = GetLocalInt(waitress, "allgone");
    int order = GetLocalInt(waitress, "getorder");



    if (cook==OBJECT_SELF)
    {
        CreateItemOnObject("spicysoup", pc, 1);
        SetLocalInt(cook, "twice", 1);
        return;
    }
    if (GetEnteringObject()==waitress)
    {
        if (!order)
            return;

        //AssignCommand(pc, JumpToObject(waitress));
        if (none)
            AssignCommand(waitress, SpeakString("Sorry, hun.  We're all out."));
        else if (order==1) //soup
        {
            AssignCommand(waitress, SpeakString("Here's your soup!"));
            DestroyObject(soup);


            //CreateObject(OBJECT_TYPE_ITEM, "spicysoup", GetLocation(GetObjectByTag("Food")));
            CreateItemOnObject("spicysoup", pc, 1);
        }
        else if (order==2) //special
        {
            AssignCommand(waitress, SpeakString("Here's your special!"));
            DestroyObject(special);
            CreateItemOnObject("item011", pc, 1);
        }
        SetLocalInt(waitress, "getorder", 0);
        SetLocalInt(waitress, "allgone", 1);
    }
}
