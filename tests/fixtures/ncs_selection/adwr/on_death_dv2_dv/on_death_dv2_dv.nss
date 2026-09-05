void main()
{
    object killer = GetLastKiller();
    object new;
    string start_loc;
    string destination;
    string new_template;
    int n, x1, x2;

    // dead object -> Do'Vrinn Faction
    n = d4();
    if (n == 1) new_template = "dovrinnpriest001";
    else if (n == 2) new_template = "dovrinnwizard001";
    else new_template = "dovrinnwarrio001";

    // determine starting loc and destination
    x1 = d4();
    x2 = d2();
    start_loc = "WP_DV2_DoVrinnIn" + IntToString (x1);
    if (x1 == 1){
        if (x2 == 1) destination = "WP_DV2_Fight6";
        else destination = "WP_DV2_Fight14";
    }
    else if (x1 == 2){
        if (x2 == 1) destination = "WP_DV2_Fight7";
        else destination = "WP_DV2_Fight1";
    }
    else if (x1 == 3){
        if (x2 == 1) destination = "WP_DV2_Fight6";
        else destination = "WP_DV2_NoquttarIn4";
    }
    else{
        if (x2 == 1) destination = "WP_DV2_Fight15";
        else destination = "WP_DV2_Fight15";
    }

    new = CreateObject (OBJECT_TYPE_CREATURE, new_template, GetLocation (GetObjectByTag (start_loc)), FALSE);

    // killer: Try to move to final destination
    AssignCommand (killer, ActionMoveToLocation (GetLocalLocation (killer, "DVDestination"), TRUE));

    // new: set final destination
    SetLocalLocation (new, "DVDestination", GetLocation (GetObjectByTag (destination)));
    AssignCommand (new, ActionMoveToLocation (GetLocalLocation (new, "DVDestination"), TRUE));
}
