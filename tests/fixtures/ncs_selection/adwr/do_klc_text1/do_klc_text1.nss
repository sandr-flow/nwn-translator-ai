void main()
{
    if (!GetIsPC (GetEnteringObject())) return;

    if (GetLocalInt (OBJECT_SELF, "triggered") == 0){
        SetLocalInt (OBJECT_SELF, "triggered", 1);

        FloatingTextStringOnCreature ("Eww. That meat stinks badly!", GetFirstPC(), FALSE);
    }
}
