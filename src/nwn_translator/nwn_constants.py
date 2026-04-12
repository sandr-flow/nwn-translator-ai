"""Shared NWN constant mappings for race, gender, and base item types.

NWN 2DA tables (racialtypes.2da, gender.2da, baseitems.2da) are extensible —
module authors can add custom entries with IDs beyond the standard range.
The lookup helpers return ``""`` for unknown IDs so callers can build clean
context strings without special-casing.
"""

# ---------------------------------------------------------------------------
# racialtypes.2da — standard rows
# ---------------------------------------------------------------------------
_RACE_MAP = {
    0: "Dwarf",
    1: "Elf",
    2: "Gnome",
    3: "Halfling",
    4: "Half-Elf",
    5: "Half-Orc",
    6: "Human",
    7: "Aberration",
    8: "Animal",
    9: "Beast",
    10: "Construct",
    11: "Dragon",
    12: "Humanoid Goblinoid",
    13: "Humanoid Monstrous",
    14: "Humanoid Orc",
    15: "Humanoid Reptilian",
    16: "Elemental",
    17: "Fey",
    18: "Giant",
    19: "Magical Beast",
    20: "Outsider",
    21: "Shapechanger",
    22: "Undead",
    23: "Vermin",
    24: "Ooze",
}

# ---------------------------------------------------------------------------
# gender.2da — standard rows
# ---------------------------------------------------------------------------
_GENDER_MAP = {
    0: "Male",
    1: "Female",
    2: "Both",
    3: "Other",
    4: "None",
}

# ---------------------------------------------------------------------------
# baseitems.2da — standard rows (NWN:EE 1.69+)
# ---------------------------------------------------------------------------
_BASE_ITEM_MAP = {
    0: "Shortsword",
    1: "Longsword",
    2: "Battleaxe",
    3: "Bastard Sword",
    4: "Light Flail",
    5: "Warhammer",
    6: "Heavy Crossbow",
    7: "Light Crossbow",
    8: "Longbow",
    9: "Mace",
    10: "Halberd",
    11: "Shortbow",
    12: "Two-Bladed Sword",
    13: "Greatsword",
    14: "Small Shield",
    15: "Torch",
    16: "Armor",
    17: "Helmet",
    18: "Greataxe",
    19: "Amulet",
    20: "Arrow",
    21: "Belt",
    22: "Dagger",
    24: "Misc Small",
    25: "Bolt",
    26: "Boots",
    27: "Bullet",
    28: "Club",
    29: "Misc Medium",
    31: "Dart",
    32: "Dire Mace",
    33: "Double Axe",
    34: "Heavy Flail",
    35: "Gloves",
    36: "Light Hammer",
    37: "Handaxe",
    38: "Heal Kit",
    39: "Kama",
    40: "Katana",
    41: "Kukri",
    42: "Misc Large",
    43: "Morningstar",
    44: "Quarterstaff",
    45: "Rapier",
    46: "Ring",
    47: "Scimitar",
    48: "Scroll",
    49: "Scythe",
    50: "Large Shield",
    51: "Tower Shield",
    52: "Short Spear",
    53: "Shuriken",
    54: "Sickle",
    55: "Sling",
    56: "Thieves' Tools",
    57: "Throwing Axe",
    58: "Trap Kit",
    59: "Key",
    60: "Whip",
    61: "Trident",
    62: "Dwarf Axe",
    63: "Blank Wand",
    66: "Gem",
    67: "Bracer",
    68: "Cloak",
    73: "Potion",
    74: "Blank Potion",
    75: "Blank Scroll",
    76: "Blank Magic Wand",
    77: "Crafting Base Material",
    78: "Crafting Component",
    80: "Misc Thin",
}


def race_label(race_id: int) -> str:
    """Human-readable race label. Returns ``''`` for unknown/custom IDs."""
    return _RACE_MAP.get(race_id, "")


def gender_label(gender_id: int) -> str:
    """Human-readable gender label. Returns ``''`` for unknown/custom IDs."""
    return _GENDER_MAP.get(gender_id, "")


def base_item_label(base_item_id: int) -> str:
    """Human-readable base item type. Returns ``''`` for unknown/custom IDs."""
    return _BASE_ITEM_MAP.get(base_item_id, "")
