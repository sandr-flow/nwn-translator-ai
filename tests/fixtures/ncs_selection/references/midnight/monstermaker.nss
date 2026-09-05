#include "knightmayor1"
//#include "piroanimations"
//#include "monstermaker"
//#include "enter_exit1"

int Adjusted (int Level)
    {
    return Level-!Random(6)+!Random(4);
    }

object MakeRogueMerc(int Level,location loc)
    {
    if (Level < 4)
        Level = 4;
    else if (Level > 6)
        Level = 6;
    string ResRef;
    switch (Level)
        {
        case 4:
            {
            if (Random(2))
                ResRef = "roguemercenary";
            else
                ResRef = "roguemercenar003";
            break;
            }
        case 5:
            {
            if (Random(2))
                ResRef = "roguemercenar001";
            else
                ResRef = "bardmercenary";
            break;
            }
        case 6:
            {
            ResRef = "roguemercenar002";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,loc);
    }

object MakeFighterMerc(int Level,location loc)
    {
    if (Level < 4)
        Level = 4;
    else if (Level > 6)
        Level = 6;
    string ResRef;
    switch (Level)
        {
        case 4:
            {
            switch(Random(3)+1)
                {
                case 1:
                    ResRef = "fightermercen002";
                    break;
                case 2:
                    ResRef = "fightermercen003";
                    break;
                case 3:
                    ResRef = "fightermercen005";
                    break;
                 }
            break;
            }
        case 5:
            {
            switch (d4())
                {
                case 1:
                    {
                    ResRef = "fightermercen004";
                    break;
                    }
                case 2:
                    {
                    ResRef = "fightermercenary";
                    break;
                    }
                case 3:
                    {
                    ResRef = "monkmercenary";
                    break;
                    }
                case 4:
                    {
                    ResRef = "rangermercenary";
                    break;
                    }
                }
            break;
            }
        case 6:
            {
           if (Random(2))
                ResRef = "barbarianmercena";
            else
                ResRef = "fightermercen001";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,loc);
    }

object MakeCasterMerc(int Level,location loc)
     {
    if (Level < 5)
        Level = 5;
    else if (Level > 7)
        Level = 7;
    string ResRef;
    switch (Level)
        {
        case 5:
            {
            if (Random(2))
                ResRef = "druidmercenary";
            else
                ResRef = "wizardmercena001";
            break;
            }
        case 6:
            {
            ResRef = "sorceressmercenary";
            break;
            }
        case 7:
            {
            if (Random(2))
                ResRef = "clericmercenary";
            else
                ResRef = "wizardmercenary";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,loc);
    }

object MakeMerc(int Level, location loc)
    {
    switch (d4())
        {
        case 1:
            return MakeRogueMerc(Level,loc);
        case 2:
            return MakeCasterMerc(Level+1,loc);
        default:
            return MakeFighterMerc(Level,loc);
        }
    return OBJECT_INVALID;
    }

object MakeNonCasterMerc(int Level, location loc)
    {
    switch (d6())
        {
        case 1:
        case 2:
            return MakeRogueMerc(Level,loc);
        default:
            return MakeFighterMerc(Level,loc);
        }
    return OBJECT_INVALID;
    }

void MakeMercParty(int Level, string Tag)
    {
    switch (d10())
        {
        case 1:
        case 2:
            {
            Level -=4;
            MakeRogueMerc(Adjusted(Level),Loc(Tag+"1"));
            MakeFighterMerc(Adjusted(Level)+1,Loc(Tag+"3"));
            MakeCasterMerc(Adjusted(Level)+1,Loc(Tag+"4"));
            MakeNonCasterMerc(Adjusted(Level)-1,Loc(Tag+"6"));
            break;
            }
        case 3:
            {
            Level -=6;
            MakeRogueMerc(Adjusted(Level),Loc(Tag+"1"));
            MakeFighterMerc(Adjusted(Level),Loc(Tag+"2"));
            MakeFighterMerc(Adjusted(Level)+1,Loc(Tag+"3"));
            MakeCasterMerc(Adjusted(Level)+1,Loc(Tag+"4"));
            MakeMerc(Adjusted(Level),Loc(Tag+"5"));
            MakeNonCasterMerc(Adjusted(Level)-1,Loc(Tag+"6"));
            break;
            }
        default:
            {
            Level -=5;
            MakeRogueMerc(Adjusted(Level),Loc(Tag+"1"));
            MakeFighterMerc(Adjusted(Level),Loc(Tag+"2"));
            MakeFighterMerc(Adjusted(Level)+1,Loc(Tag+"3"));
            MakeCasterMerc(Adjusted(Level)+1,Loc(Tag+"4"));
            MakeNonCasterMerc(Adjusted(Level)-1,Loc(Tag+"6"));
            break;
            }
        }
    }

object MakeRangedDragonClan(int Level, string Tag)
    {
    string ResRef;
    if (Level < 6)
        ResRef = "earthdragoncl001";
    else
        ResRef = "earthdragoncl007";
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeMeleeDragonClan(int Level, string Tag)
    {
    string ResRef;
    if (Level < 5)
        Level = 5;
    else if (Level > 7)
        Level = 7;
    switch (Level)
        {
         case 5:
            {
            if (Random(2))
                ResRef="earthdragoncl004";
            else
                ResRef="earthdragoncl005";
            break;
            }
         case 6:
            {
            if (Random(2))
                ResRef="earthdragoncl002";
            else
                ResRef="earthdragonclanm";
            break;
            }
         case 7:
            {
            if (Random(2))
                ResRef="earthdragoncl003";
            else
                ResRef="earthdragoncl006";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeDragonClan(int Level,string Tag,int Ranged=0)
    {
    int AL = Adjusted(Level)-4;
    switch (Ranged)
        {
        case 0:
            {
            return MakeMeleeDragonClan(AL,Tag);
            break;
            }
        case 1:
            {
            return MakeRangedDragonClan(AL,Tag);
            break;
            }
        case 2:
            {
            switch(AL)
                {
                case 3:
                case 4:
                case 5:
                    {
                    if (!Random(3))
                        return MakeRangedDragonClan(AL,Tag);
                    else
                        return MakeMeleeDragonClan(AL,Tag);
                    break;
                    }
                case 6:
                    {
                    if (!Random(3))
                        return MakeRangedDragonClan(AL,Tag);
                    else
                        return MakeMeleeDragonClan(AL,Tag);
                    break;
                    }
                default:
                    {
                    return MakeMeleeDragonClan(AL,Tag);
                    break;
                    }
                }
            }
        }
    return OBJECT_INVALID;
    }

object MakeGiant(int Level, string Tag)
    {
    Level -= 3;
    string ResRef;
    if (Level < 6)
        Level = 6;
    else if (Level > 8)
        Level = 8;
    switch (Level)
        {
         case 6:
            {
            if (Random(2))
                ResRef="giantogre";
            else
                ResRef="ettin001";
            break;
            }
         case 7:
            {
            if (Random(2))
                ResRef="cripplergiantogr";
            else
                ResRef="gnthill002";
            break;
            }
         case 8:
            {
            if (Random(2))
                ResRef="ettin002";
            else
                ResRef="gnthill001";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeTroll (string Tag)
    {
    switch (d4())
        {
        case 1:
            {
            return CreateObject(OBJECT_TYPE_CREATURE,"troll001",Loc(Tag));
            break;
            }
        case 2:
            {
            return CreateObject(OBJECT_TYPE_CREATURE,"troll002",Loc(Tag));
            break;
            }
        case 3:
            {
            return CreateObject(OBJECT_TYPE_CREATURE,"troll003",Loc(Tag));
            break;
            }
        case 4:
            {
            return CreateObject(OBJECT_TYPE_CREATURE,"troll004",Loc(Tag));
            break;
            }
        }
    return OBJECT_INVALID;
    }

object MakeLowerAncient(int Level, string Tag)
    {
    Level = Adjusted(Level)-4;
    string ResRef;
    if (Level < 6)
        Level = 6;
    else if (Level > 9)
        Level = 9;
    switch (Level)
        {
         case 6:
            {
            switch (Random(4))
                {
                case 0:
                    ResRef="skeleton";
                    break;
                case 1:
                    ResRef="skeleton012";
                    break;
                case 2:
                    ResRef="zombie";
                    break;
                case 3:
                    ResRef="zombie010";
                    break;
                }
            break;
            }
         case 7:
            {
            switch (Random(5))
                {
                case 0:
                    ResRef="skeleton015";
                    break;
                case 1:
                    ResRef="zombie011";
                    break;
                case 2:
                    ResRef="skeleton011";
                    break;
                case 3:
                    ResRef="zombie008";
                    break;
                case 4:
                    ResRef="zombie001";
                    break;
                }
            break;
            }
         case 8:
            {
            switch (Random(5))
                {
                case 0:
                    ResRef="skeleton016";
                    break;
                case 1:
                    ResRef="skeleton013";
                    break;
                case 2:
                    ResRef="skeleton008";
                    break;
                case 3:
                    ResRef="zombie012";
                    break;
                case 4:
                    ResRef="zombie013";
                    break;
                }
            break;
            }
         case 9:
            {
            if (Random(2))
                ResRef="skeleton014";
            else
                ResRef="zombie009";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeAncientMummy(int Level, location LOC)
    {
    Level = Adjusted(Level)-2;
    string ResRef;
    if (Level < 8)
        Level = 8;
    else if (Level > 11)
        Level = 11;
    switch (Level)
        {
         case 8:
            {
            if (Random(2))
                ResRef="ancientmummycler";
            else
                ResRef="fencerancient";
            break;
            }
         case 9:
            {
            if (Random(2))
                ResRef="monkancient";
            else
                ResRef="ancientmummycrip";
            break;
            }
         case 10:
            {
            if (Random(2))
                ResRef="ancientmummymage";
            else
                ResRef="ancientmummybers";
            break;
            }
        case 11:
            {
            if (Random(2))
                ResRef="knightancient";
            else
                ResRef="burningancientmu";
            break;
            }
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,LOC);
    }

object MakeHalfFiendMelee(int Level, string Tag)
    {
    Level = Adjusted(Level-2);
    string ResRef;
    if (Level < 7)
        Level = 7;
    else if (Level > 12)
        Level = 12;
    switch(Level)
        {
        case 7:
            ResRef = "creature";
            break;
        case 8:
            if(Random(2))
                ResRef = "creature001";
            else
                ResRef = "creature006";
            break;
        case 9:
            ResRef = "creature008";
            break;
        case 10:
            ResRef = "creature002";
            break;
        case 11:
            if(Random(2))
                ResRef = "creature007";
            else
                ResRef = "creature003";
            break;
        case 12:
            ResRef = "creature009";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeHalfFiendArcher(int Level, string Tag)
    {
    string ResRef;
    if (Level<=11)
        ResRef = "creature004";
    else
        ResRef = "creature005";
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeHalfFiendMage(int Level, string Tag)
    {
    string ResRef;
    if (Level<=11)
        ResRef = "creature010";
    else
        ResRef = "creature011";
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeHalfFiend(int Level, string Tag)
    {
    Level = Adjusted(Level-2);
    string ResRef;
    if (Level < 7)
        Level = 7;
    else if (Level > 12)
        Level = 12;
    switch(Level)
        {
        case 7:
            if(Random(2))
                ResRef = "creature";
            else
                ResRef = "creature004";
            break;
        case 8:
            if(Random(2))
                ResRef = "creature001";
            else
                ResRef = "creature006";
            break;
        case 9:
            if(Random(2))
                ResRef = "creature008";
            else
                ResRef = "creature010";
            break;
        case 10:
            if(Random(2))
                ResRef = "creature005";
            else
                ResRef = "creature002";
            break;
        case 11:
            if(Random(2))
                ResRef = "creature007";
            else
                ResRef = "creature003";
            break;
        case 12:
            if(Random(2))
                ResRef = "creature009";
            else
                ResRef = "creature011";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeMeleeBandit(int Level, string Tag,int TF = FALSE)
    {
    Level = Adjusted(Level)-2;
    string ResRef;
    if (Level < 7)
        Level = 7;
    else if (Level > 12+TF)
        Level = 12+TF;
    switch(Level)
        {
        case 7:
            if(Random(2))
                ResRef = "bandit";
            else
                ResRef = "thug";
            break;
        case 8:
            if(Random(2))
                ResRef = "tracker";
            else
                ResRef = "brute";
            break;
        case 9:
            if(Random(2))
                ResRef = "burglar";
            else
                ResRef = "swashbuckler";
            break;
        case 10:
            if(Random(2))
                ResRef = "thug001";
            else
                ResRef = "bandit002";
            break;
        case 11:
            if(Random(2))
                ResRef = "tracker001";
            else
                ResRef = "brute001";
            break;
        case 12:
            if(Random(2))
                ResRef = "burglar001";
            else
                ResRef = "swashbuckler001";
            break;
        case 13:
            switch (Random(4)+1)
                {
                case 1:
                    ResRef = "iskariwarrior";
                    break;
                case 2:
                    ResRef = "pygmywarrior";
                    break;
                case 3:
                    ResRef = "shinobi";
                    break;
                case 4:
                    ResRef = "creature016";
                    break;
                }
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeArcherBandit(int Level, string Tag, int TF = FALSE)
    {
    Level = Adjusted(Level)-2;
    string ResRef;
    if (Level < 7)
        Level = 7;
    else if (Level > 12+TF)
        Level = 12+TF;
    switch(Level)
        {
        case 7:
            ResRef = "sharpshooter";
            break;
        case 8:
            if(Random(2))
                ResRef = "sharpshooter";
            else
                ResRef = "bandit001";
            break;
        case 9:
            ResRef = "bandit001";
            break;
        case 10:
            ResRef = "sharpshooter001";
            break;
        case 11:
            if(Random(2))
                ResRef = "sharpshooter001";
            else
                ResRef = "bandit003";
            break;
        case 12:
            ResRef = "bandit003";
            break;
        case 13:
            ResRef = "bramblethrower";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeCasterBandit(int Level, string Tag,int TF = FALSE)
    {
    Level = Adjusted(Level)-2;
    string ResRef;
    if (Level < 7)
        Level = 7;
    else if (Level > 12+TF)
        Level = 12+TF;
    switch(Level)
        {
        case 7:
            ResRef = "naturist";
            break;
        case 8:
            ResRef = "witch";
            break;
        case 9:
            ResRef = "shaman";
            break;
        case 10:
            ResRef = "naturist001";
            break;
        case 11:
            ResRef = "witch001";
            break;
        case 12:
            ResRef = "shaman001";
            break;
        case 13:
            if(Random(2))
                ResRef = "heretic";
            else
                ResRef = "warlock";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeBandit(int Level, string Tag,int TF = FALSE)
    {
    switch (Random(11)+1)
        {
        case 1:
        case 2:
            return MakeArcherBandit(Level,Tag,TF);
        case 3:
        case 4:
        case 5:
            return MakeCasterBandit(Level,Tag,TF);
        default:
            return MakeMeleeBandit(Level,Tag,TF);
        }
    return OBJECT_INVALID;
    }

object MakeNonCasterBandit(int Level, string Tag,int TF = FALSE)
    {
    switch (d10())
        {
        case 1:
        case 2:
        case 3:
        case 4:
            return MakeArcherBandit(Level,Tag,TF);
        default:
            return MakeMeleeBandit(Level,Tag,TF);
        }
    return OBJECT_INVALID;
    }

void MakeBanditParty(int Level, string Tag,int TF = FALSE)
    {
    switch (d10())
        {
        case 1:
        case 2:
            {
            MakeArcherBandit(Level+1,Tag+"1",TF);
            MakeMeleeBandit(Level+2,Tag+"3",TF);
            MakeCasterBandit(Level+1,Tag+"4",TF);
            MakeNonCasterBandit(Level,Tag+"6",TF);
            break;
            }
        case 3:
            {
            MakeArcherBandit(Level-1,Tag+"1",TF);
            MakeMeleeBandit(Level-1,Tag+"2",TF);
            MakeMeleeBandit(Level,Tag+"3",TF);
            MakeCasterBandit(Level-1,Tag+"4",TF);
            MakeBandit(Level,Tag+"5",TF);
            MakeNonCasterBandit(Level-2,Tag+"6",TF);
            break;
            }
        default:
            {
            MakeArcherBandit(Level,Tag+"1",TF);
            MakeMeleeBandit(Level,Tag+"2",TF);
            MakeMeleeBandit(Level+1,Tag+"3",TF);
            MakeCasterBandit(Level,Tag+"4",TF);
            MakeNonCasterBandit(Level-1,Tag+"6",TF);
            break;
            }
        }
    }

void MakeVampWolfPack(int Level, string Tag)
    {
    Level = Adjusted(Level);
    if (Level < 11)
        Level = 11;
    else if (Level > 14)
        Level = 14;
    switch (Level)
        {
        case 11:
            CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
            if (Random(2))
                CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"1"));
            else
                CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
            break;
        case 12:
            switch(d10())
                {
                case 1:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    break;
                case 2:
                case 3:
                case 4:
                case 5:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"2"));
                    break;
                case 6:
                case 7:
                case 8:
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"3"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"3"));
                    break;
                }
            break;
        case 13:
            switch (d10())
                case 1:
                case 2:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"3"));
                    break;
                case 3:
                case 4:
                case 5:
                case 6:
                case 7:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"3"));
                    break;
                case 8:
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"3"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"3"));
                    CreateObject(OBJECT_TYPE_CREATURE,"wolf001",Loc(Tag+"4"));
                    break;

            break;
        case 14:
            CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"1"));
            CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
            CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"4"));
            if (Random(2))
                CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
            else
                CreateObject(OBJECT_TYPE_CREATURE,"wolfwint001",Loc(Tag+"2"));
            if (!Random(4))
                CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"5"));
            break;
        }
    }

void MakeVampCatPack(int Level, string Tag)
    {
    Level = Adjusted(Level);
    if (Level < 11)
        Level = 11;
    else if (Level > 14)
        Level = 14;
    switch (Level)
        {
        case 11:
            CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
            if (Random(2))
                CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"1"));
            else
                CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
            break;
        case 12:
            switch(d10())
                {
                case 1:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    break;
                case 2:
                case 3:
                case 4:
                case 5:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"2"));
                    break;
                case 6:
                case 7:
                case 8:
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
                    break;
                }
            break;
        case 13:
            switch (d10())
                case 1:
                case 2:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
                    break;
                case 3:
                case 4:
                case 5:
                case 6:
                case 7:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
                    break;
                case 8:
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"3"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
                    CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"4"));
                    break;

            break;
        case 14:
            CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"1"));
            CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"3"));
            CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"4"));
            if (Random(2))
                CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"2"));
            else
                CreateObject(OBJECT_TYPE_CREATURE,"cat001",Loc(Tag+"2"));
            if (!Random(4))
                CreateObject(OBJECT_TYPE_CREATURE,"cat002",Loc(Tag+"5"));
            break;
        }
    }

void MakeVampBearPack(int Level, string Tag)
    {
    Level = Adjusted(Level);
    if (Level < 11)
        Level = 11;
    else if (Level > 14)
        Level = 14;
    switch (Level)
        {
        case 11:
            CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"1"));
            if (!Random(3))
                CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
            break;
        case 12:
            switch(d10())
                {
                case 1:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"1"));
                    break;
                case 2:
                case 3:
                case 4:
                case 5:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
                    break;
                case 6:
                case 7:
                case 8:
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
                    break;
                }
            break;
        case 13:
         switch(d10())
                {
                case 1:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
                    break;
                case 2:
                case 3:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
                    break;

                case 4:
                case 5:
                case 6:
                case 7:
                case 8:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
                    break;
                case 9:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"2"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"3"));
                    break;
                case 10:
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
                    CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"2"));
                    break;
                }
            break;
        case 14:
            CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"1"));
            CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"3"));
            if (Random(2))
                CreateObject(OBJECT_TYPE_CREATURE,"bearpolar001",Loc(Tag+"3"));
            else
                CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"3"));
            if (!Random(6))
                CreateObject(OBJECT_TYPE_CREATURE,"bearpolar002",Loc(Tag+"4"));
            break;
        }
    }

void MakeVampAnimalPack(int Level, string Tag)
    {
    switch (d3())
        {
        case 1:
            MakeVampWolfPack(Level,Tag);
            break;
        case 2:
            MakeVampCatPack(Level,Tag);
            break;
        case 3:
            MakeVampBearPack(Level,Tag);
            break;
        }
    }

object MakeMeleeVamp(int Level, string Tag)
    {
    string ResRef;
    Level = Adjusted(Level);
    if (Level < 11)
        Level = 11;
    else if (Level > 14)
        Level = 14;
    switch (Level)
        {
        case 11:
            switch (d3())
                {
                case 1:
                    ResRef = "wrathvampire";
                    break;
                case 2:
                    ResRef = "vampirewarrior";
                    break;
                case 3:
                    ResRef = "vampirerogue";
                    break;
                }
            break;
        case 12:
            switch (d3())
                {
                case 1:
                    ResRef = "vampirebarbarian";
                    break;
                case 2:
                    ResRef = "fencervampire";
                    break;
                case 3:
                    ResRef = "vampiremonk";
                    break;
                }
            break;
        case 13:
            switch (d3())
                {
                case 1:
                    ResRef = "brawlervampire";
                    break;
                case 2:
                    ResRef = "cripplervampire";
                    break;
                case 3:
                    ResRef = "frozenvampire";
                    break;
                }
            break;
        case 14:
            switch (d3())
                {
                case 1:
                    ResRef = "vampireassassin";
                    break;
                case 2:
                    ResRef = "vampireknight";
                    break;
                case 3:
                    ResRef = "vampirechampion";
                    break;
                }
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeCasterVamp(int Level, string Tag)
    {
    string ResRef;
    Level = Adjusted(Level);
    if (Level < 11)
        Level = 11;
    else if (Level > 14)
        Level = 14;
    switch (Level)
        {
        case 11:
            ResRef = "vampiremage";
            break;
        case 12:
            ResRef = "vampirecleric";
            break;
        case 13:
            ResRef = "vampirewitch";
            break;
        case 14:
            ResRef = "creature012";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object MakeVampire(int Level, string Tag)
    {
    if (Random(4))
        return MakeMeleeVamp(Level,Tag);
    else
        return MakeCasterVamp(Level,Tag);
    }

object MakeRangedDrider (int Level, string Tag,int Appear)
    {
    string ResRef;
    Level = Adjusted(Level);
    if (Level < 10)
        Level = 10;
    else if (Level > 13)
        Level = 13;
    switch(Level)
        {
        case 10:
            ResRef = "driderscout";
            break;
        case 11:
            ResRef = "dridersharpshoot";
            break;
        case 12:
            ResRef = "dridersniper";
            break;
        case 13:
            ResRef = "dridermarksman";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag),Appear);
    }

object MakeCasterDrider (int Level, string Tag,int Appear)
    {
    string ResRef;
    Level = Adjusted(Level);
    if (Level < 10)
        Level = 10;
    else if (Level > 14)
        Level = 14;
    switch(Level)
        {
        case 10:
            ResRef = "drideracolyte";
            break;
        case 11:
            if (Random(2))
                ResRef = "dridersummoner";
            else
                ResRef = "dridernecromance";
            break;
        case 12:
            if (Random(2))
                ResRef = "driderpriest";
            else
                ResRef = "driderelementalist";
            break;
        case 13:
            if (Random(2))
                ResRef = "dridermagus";
            else
                ResRef = "driderhealer";
            break;
        case 14:
            ResRef = "driderhighpriest";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag),Appear);
    }

object MakeMeleeDrider (int Level, string Tag,int Appear)
    {
    string ResRef;
    Level = Adjusted(Level);
    if (Level < 10)
        Level = 10;
    else if (Level > 14)
        Level = 14;
    switch(Level)
        {
        case 10:
            if (Random(2))
                ResRef = "driderskirmisher";
            else
                ResRef = "dridersoldier";
            break;
        case 11:
            if (Random(2))
                ResRef = "driderwarrior";
            else
                ResRef = "dridershadowwalk";
            break;
        case 12:
            if (Random(2))
                ResRef = "driderberserker";
            else
                ResRef = "driderphalanx";
            break;
        case 13:
            if (Random(2))
                ResRef = "driderchampion";
            else
                ResRef = "driderassassin";
            break;
        case 14:
            ResRef = "driderblackknigh";
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag),Appear);
    }

object MakeDrider (int Level, string Tag,int Appear=FALSE)
    {
    switch(d6())
        {
        case 1:
            return MakeRangedDrider (Level,Tag,Appear);
            break;
        case 2:
        case 3:
            return MakeCasterDrider (Level,Tag,Appear);
            break;
        case 4:
        case 5:
        case 6:
            return MakeMeleeDrider (Level,Tag,Appear);
            break;
        }
    return OBJECT_INVALID;
    }

void MakeDriderParty (int Level, string Tag, int Appear = FALSE)
    {
    switch (d10())
        {
        case 1:
        case 2:
            {
            MakeRangedDrider(Level+1,Tag+"1",Appear);
            MakeMeleeDrider(Level+1,Tag+"3",Appear);
            MakeCasterDrider(Level+1,Tag+"4",Appear);
            break;
            }
        case 3:
            {
            MakeRangedDrider(Level-1,Tag+"1",Appear);
            MakeMeleeDrider(Level,Tag+"2",Appear);
            MakeMeleeDrider(Level-1,Tag+"3",Appear);
            MakeCasterDrider(Level,Tag+"4",Appear);
            MakeDrider(Level-1,Tag+"5",Appear);
            break;
            }
        default:
            {
            MakeRangedDrider(Level,Tag+"1",Appear);
            MakeMeleeDrider(Level,Tag+"2",Appear);
            MakeMeleeDrider(Level+1,Tag+"3",Appear);
            MakeCasterDrider(Level,Tag+"4",Appear);
            break;
            }
        }
    }

void MakeUnderdarkEncounter(int Level, string Tag)
    {
    if (!Random(5))
        MakeDriderParty(Level-1,Tag);
    else
        {
        Level = Adjusted(Level);
        if (Level < 10)
            Level = 10;
        else if (Level > 14)
            Level = 14;
        switch(Level)
            {
            case 10:
                switch(d10())
                    {
                    case 1:
                    case 2:
                    case 3:
                    case 4:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        break;
                    case 5:
                    case 6:
                    case 7:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        break;
                    case 8:
                    case 9:
                    case 10:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        break;
                    }
                break;
            case 11:
                switch(d10())
                    {
                    case 1:
                    case 2:
                    case 3:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        break;
                    case 4:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"4"));
                        break;
                    case 5:
                    case 6:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        break;
                    case 7:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        break;
                    case 8:
                    case 9:
                    case 10:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        break;
                    }
                break;
            case 12:
                switch(d10())
                    {
                    case 1:
                    case 2:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"4"));
                        break;
                    case 3:
                    case 4:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        break;
                    case 5:
                    case 6:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"3"));
                        break;
                    case 7:
                    case 8:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        break;
                    case 9:
                    case 10:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        break;
                    }
                break;
            case 13:
                switch(d10())
                    {
                    case 1:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"4"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"5"));
                        break;
                    case 2:
                    case 3:
                    case 4:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"3"));
                        break;
                    case 5:
                    case 6:
                    case 7:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        break;
                    case 8:
                    case 9:
                    case 10:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"3"));
                        break;
                    }
                break;
            case 14:
                switch(d10())
                    {
                    case 1:
                    case 2:
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"3"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"4"));
                        CreateObject(OBJECT_TYPE_CREATURE,"horror002",Loc(Tag+"5"));
                        break;
                    case 3:
                    case 4:
                    case 5:
                    case 6:
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"trollchief001",Loc(Tag+"3"));
                        break;
                    case 7:
                    case 8:
                    case 9:
                    case 10:
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"1"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"2"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"3"));
                        CreateObject(OBJECT_TYPE_CREATURE,"umberhulk002",Loc(Tag+"4"));
                        break;
                    }
                break;
            }
        }
    }

string GetPFResRef(int x)
    {
    if (x>9)
        return "pitfighter0"+IntToString(x);
    else if(x)
        return "pitfighter00"+IntToString(x);
    else
        return "pitfighter";
    }

void SpawnPitFight(int Start, string Tag = "WP_M_PF")
    {
    int x;
    for(x=1;x<=9;x++)
        CreateObject(OBJECT_TYPE_CREATURE,GetPFResRef(x+Start-1),Loc(Tag+IntToString(x)));
    }

object SpawnConstruct(int Level, string Tag)
    {
    Level = Adjusted(Level);
    if (Level < 10)
        Level = 10;
    else if (Level > 14)
        Level = 14;
    string ResRef;
    switch(Level)
        {
        case 10:
            switch(d3())
                {
                case 1:
                    ResRef = "livingarmor";
                    break;
                case 2:
                    ResRef = "minogon001";
                    break;
                case 3:
                    ResRef = "helmhorr001";
                    break;
                }
            break;
        case 11:
            switch(d3())
                {
                case 1:
                    ResRef = "livingarmor001";
                    break;
                case 2:
                    ResRef = "bathorror001";
                    break;
                case 3:
                    ResRef = "livingarmor003";
                    break;
                }
            break;
        case 12:
            switch(d3())
                {
                case 1:
                    ResRef = "livingarmor002";
                    break;
                case 2:
                    ResRef = "bathorror002";
                    break;
                case 3:
                    ResRef = "livingarmor004";
                    break;
                }
            break;
        case 13:
            switch(d3())
                {
                case 1:
                    ResRef = "livingarmor006";
                    break;
                case 2:
                    ResRef = "helmhorr002";
                    break;
                case 3:
                    ResRef = "livingarmor005";
                    break;
                }
            break;
        case 14:
            switch(d2())
                {
                case 1:
                    ResRef = "livingarmor007";
                    break;
                case 2:
                    ResRef = "livingarmor008";
                    break;
                }
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

object SpawnDemon(int Level, string Tag)
    {
    Level = Adjusted(Level)-1;
    if (Level < 10)
        Level = 10;
    else if (Level > 14)
        Level = 14;
    string ResRef;
    switch(Level)
        {
        case 10:
            switch(d3())
                {
                case 1:
                    ResRef = "beastxvim001";
                    break;
                case 2:
                    ResRef = "creature014";
                    break;
                case 3:
                    ResRef = "panhighpriestess";
                    break;
                }
            break;
        case 11:
            switch(d3())
                {
                case 1:
                    ResRef = "creature015";
                    break;
                case 2:
                    ResRef = "giantpandemon";
                    break;
                case 3:
                    ResRef = "panpaladin";
                    break;
                }
            break;
        case 12:
            switch(d3())
                {
                case 1:
                    ResRef = "dmquasit001";
                    break;
                case 2:
                    ResRef = "dmvrock001";
                    break;
                case 3:
                    ResRef = "slaadred001";
                    break;
                }
            break;
        case 13:
            switch(d3())
                {
                case 1:
                    ResRef = "hellcat";
                    break;
                case 2:
                    ResRef = "demon001";
                    break;
                case 3:
                    ResRef = "slaadbl001";
                    break;
                }
            break;
        case 14:
            switch(d3())
                {
                case 1:
                    ResRef = "demonknight";
                    break;
                case 2:
                    ResRef = "devil002";
                    break;
                case 3:
                    ResRef = "slaadgrn001";
                    break;
                }
            break;
        }
    return CreateObject(OBJECT_TYPE_CREATURE,ResRef,Loc(Tag));
    }

//void main(){}
