
void CheckAttack()
{
    // Zombie PC fights all commoners until all are dead
    object o=OBJECT_SELF;
    int n=1;
    SetCommandable(TRUE);
    object oEnemy = GetNearestObjectByTag("Commoner",o,n);
    while (GetIsObjectValid(oEnemy)) {
        if (!GetIsDead(oEnemy)) {
            ClearAllActions();
            ActionAttack(oEnemy);
            SetCommandable(FALSE);
            DelayCommand(6.0,CheckAttack());
            return;
            }
        oEnemy = GetNearestObjectByTag("Commoner",o,++n);
        }
    ClearAllActions();
    SetImmortal(o,FALSE);
    FloatingTextStringOnCreature("They are all dead...",o,FALSE);
    DelayCommand(3.0,ExecuteScript("at_gotonextdream",o));
}

void main()
{
    object o = GetEnteringObject();

    if (GetIsPC(o) && !GetLocalInt(o,"bIsZombie")) {
        SetLocalInt(o,"bIsZombie",TRUE);
        SetImmortal(o,TRUE);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,
            SupernaturalEffect(EffectPolymorph(POLYMORPH_TYPE_ZOMBIE,TRUE)),o);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,
            SupernaturalEffect(EffectMovementSpeedDecrease(50)),o);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT,
            SupernaturalEffect(EffectSilence()),o);
        AssignCommand(o,CheckAttack());
        }
}
