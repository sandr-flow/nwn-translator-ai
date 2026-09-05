#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
//#include "enter_exit1"
//#include "cutscene"

struct Disk
    {
    int Height;
    object Ring;
    int Pole;
    };

object GetStructRing(struct Disk Struct)
    {
    object Return = Struct.Ring;
    return Return;
    }

int GetStructPole(struct Disk Struct)
    {
    int Return = Struct.Pole;
    return Return;
    }

struct Disk GetTopRing(int iPole)
    {
    struct Disk Return;
    object oPole =GetObjectByTag("Pole"+IntToString(iPole));
    int iHeight =GetLocalInt(oPole,"top");
    Return.Pole = iPole;
    Return.Height = iHeight;
    Return.Ring = GetLocalObject(oPole,IntToString(iHeight));
    return Return;
    }

int GetSize (object Ring)
    {
    if (GetIsObjectValid(Ring))
        return StringToInt(GetStringRight(GetTag(Ring),1));
    else
        return 7;
    }

location GetNewLoc(object Pole, int Height)
    {
    vector Vec = GetPosition(Pole);
    Vec.z = (Height-1) * 0.6;
    location Return = Location(GetArea(OBJECT_SELF),Vec,0.0);
    return Return;
    }
string GetPartTag(int x)
    {
    return GetStringLeft(GetTag(GetLocalObject(GetObjectByTag("Pole2"),IntToString(x))),4);
    }

void Complete()
    {
    if (GetLocalInt(GetArea(OBJECT_SELF),"giveup"))
        {
        SetCustomToken(30,HenchName());
        SetLocalInt(OBJECT_SELF,"ionahXP",1);
        }
    else if (GetLocalInt(OBJECT_SELF,"moves")<=75)
        {
        SetCustomToken(30,GetName(GetFirstPC()));
        SetLocalInt(OBJECT_SELF,"ionahXP",3);
        }
    else
        {
        SetCustomToken(30,GetName(GetFirstPC()));
        SetLocalInt(OBJECT_SELF,"ionahXP",2);
        }
    ActionStartConversation(GetFirstPC());
    }

void CheckSolution()
    {
    if (GetLocalInt(GetObjectByTag("Pole2"),"top")+GetLocalInt(GetObjectByTag("Pole3"),"top")==10)
        {
        string Tag = GetPartTag(5);
        if (Tag == GetPartTag(4) && Tag == GetPartTag(3) && Tag == GetPartTag(2) &&
            Tag == GetPartTag(1))
            {
            SetLocalInt(GetArea(OBJECT_SELF),"done",1);
            DelayCommand(2.0,Complete());
            }
        }
    }

void MoveRing(struct Disk Source,struct Disk Dest)
    {
    SetLocalInt(OBJECT_SELF,"moves",GetLocalInt(OBJECT_SELF,"moves")+1);
    object SourcePole = GetObjectByTag("Pole"+IntToString(GetStructPole(Source)));
    object DestPole = GetObjectByTag("Pole"+IntToString(GetStructPole(Dest)));
    int NewHeight = GetLocalInt(DestPole,"top")+1;
    int OldHeight = GetLocalInt(SourcePole,"top");
    SetLocalInt(DestPole,"top",NewHeight);
    SetLocalObject(DestPole,IntToString(NewHeight),CreateObject(OBJECT_TYPE_PLACEABLE,GetResRef(GetStructRing(Source)),GetNewLoc(DestPole,NewHeight)));
    DestroyObject (GetLocalObject(SourcePole,IntToString(OldHeight)));
    SetLocalInt(SourcePole,"top",OldHeight-1);
    effect eFect = EffectVisualEffect(VFX_FNF_SUMMON_MONSTER_3);
    ApplyEffectAtLocation(DURATION_TYPE_INSTANT,eFect,GetLocation(SourcePole));
    ApplyEffectAtLocation(DURATION_TYPE_INSTANT,eFect,GetLocation(DestPole));
    CheckSolution();
    }

void Reset()
    {
    int x;
    int y;
    object Pole;
    for (x=1;x<=4;x++)
        {
        Pole = GetObjectByTag("Pole"+IntToString(x));
        for (y=1;y<=GetLocalInt(Pole,"top");y++)
            DestroyObject(GetLocalObject(Pole,IntToString(y)));
        SetLocalInt(Pole,"top",0);
        }

    Pole = GetObjectByTag("Pole1");
    SetLocalInt(Pole,"top",5);
    SetLocalObject(Pole,"1",CreateObject(OBJECT_TYPE_PLACEABLE,"bluedisk5",GetNewLoc(Pole,1)));
    SetLocalObject(Pole,"3",CreateObject(OBJECT_TYPE_PLACEABLE,"bluedisk3",GetNewLoc(Pole,3)));
    SetLocalObject(Pole,"5",CreateObject(OBJECT_TYPE_PLACEABLE,"bluedisk1",GetNewLoc(Pole,5)));
    Pole = GetObjectByTag("Pole4");
    SetLocalInt(Pole,"top",5);
    SetLocalObject(Pole,"2",CreateObject(OBJECT_TYPE_PLACEABLE,"bluedisk4",GetNewLoc(Pole,2)));
    SetLocalObject(Pole,"4",CreateObject(OBJECT_TYPE_PLACEABLE,"bluedisk2",GetNewLoc(Pole,4)));
    SetLocalInt(OBJECT_SELF,"moves",0);
    Pole = GetObjectByTag("Pole1");
    if (GetLocalInt(GetArea(OBJECT_SELF),"Colorblind"))
        {
        SetLocalObject(Pole,"2",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk4b",GetNewLoc(Pole,2)));
        SetLocalObject(Pole,"4",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk2b",GetNewLoc(Pole,4)));
        Pole = GetObjectByTag("Pole4");
        SetLocalObject(Pole,"1",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk5b",GetNewLoc(Pole,1)));
        SetLocalObject(Pole,"5",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk1b",GetNewLoc(Pole,5)));
        SetLocalObject(Pole,"3",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk3b",GetNewLoc(Pole,3)));
        }
    else
        {
        SetLocalObject(Pole,"2",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk4",GetNewLoc(Pole,2)));
        SetLocalObject(Pole,"4",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk2",GetNewLoc(Pole,4)));
        Pole = GetObjectByTag("Pole4");
        SetLocalObject(Pole,"1",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk5",GetNewLoc(Pole,1)));
        SetLocalObject(Pole,"3",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk3",GetNewLoc(Pole,3)));
        SetLocalObject(Pole,"5",CreateObject(OBJECT_TYPE_PLACEABLE,"reddisk1",GetNewLoc(Pole,5)));
        }
    }

string Insult()
    {
    switch(Random(5)+1)
        {
        case 1:
            return "Did your mother drop you on your head when you were a baby?";
        case 2:
            return "You have the mental capacity of a cauliflower.";
        case 3:
            return "This is the most impressive display of stupidity I have ever witnessed.";
        case 4:
            return "You are a patheic little simpleton.";
        case 5:
            return "I've seen deranged goblins with better trains of thought.";
        }
    return "";
    }
void main()
{
switch(GetUserDefinedEventNumber())
    {
    case 1004:
        {
        switch(GetListenPatternNumber())
            {
            case 1:
                {
                string Move = GetMatchedSubstring(1);
                struct Disk Source = GetTopRing(StringToInt(GetStringLeft(Move,1)));
                struct Disk Dest = GetTopRing(StringToInt(GetStringRight(Move,1)));
                if (GetStructPole(Source) && GetStructPole(Dest))
                    {
                    if (GetIsObjectValid(GetStructRing(Source)))
                        {
                        if (GetSize(GetStructRing(Source)) < GetSize(GetStructRing(Dest)))
                            {
                            MoveRing(Source,Dest);
                            }
                        else
                            SpeakString (Insult()+"  You can't move a disk to a pole with a smaller disk on top.");
                        }
                    else
                        SpeakString(Insult()+"  There's no disk on that pole.");
                    }
                else
                    SpeakString(Insult()+" Invalid request.");
                break;
                }
            case 2:
                {
                Reset();
                break;
                }
            case 3:
                {
                if (!GetLocalInt(GetArea(OBJECT_SELF),"Colorblind"))
                    {
                    SetLocalInt(GetArea(OBJECT_SELF),"Colorblind",1);
                    FloatingTextStringOnCreature("Colorblind adjustments enabled",GetFirstPC());
                    Reset();
                    }
                break;
                }
            }
        break;
        }
    }
}
