"""
CNA Engine — Enumerations and Constants
All game-level enums, side identifiers, unit types, terrain, status markers, etc.
"""
from enum import Enum, IntEnum


class Side(str, Enum):
    ALLIED = "allied"
    AXIS = "axis"

    @property
    def enemy(self):
        return Side.AXIS if self == Side.ALLIED else Side.ALLIED


class Nationality(str, Enum):
    BRITISH = "british"
    AUSTRALIAN = "australian"
    NEW_ZEALAND = "new_zealand"
    SOUTH_AFRICAN = "south_african"
    INDIAN = "indian"
    FREE_FRENCH = "free_french"
    POLISH = "polish"
    CZECH = "czech"
    GREEK = "greek"
    GERMAN = "german"
    ITALIAN = "italian"


SIDE_NATIONALITIES = {
    Side.ALLIED: [
        Nationality.BRITISH, Nationality.AUSTRALIAN, Nationality.NEW_ZEALAND,
        Nationality.SOUTH_AFRICAN, Nationality.INDIAN, Nationality.FREE_FRENCH,
        Nationality.POLISH, Nationality.CZECH, Nationality.GREEK,
    ],
    Side.AXIS: [Nationality.GERMAN, Nationality.ITALIAN],
}


class UnitClass(str, Enum):
    """High-level unit classification for CRT lookups."""
    INFANTRY = "infantry"
    ARMOR = "armor"
    GUN = "gun"
    RECON = "recon"
    ENGINEER = "engineer"
    HQ = "hq"
    TRUCK = "truck"
    SGSU = "sgsu"           # Squadron Ground Support Unit (air)
    RAIDER = "raider"       # LRDG / SAS / Commandos
    DUMMY = "dummy"
    SUPPLY_DUMP = "supply_dump"
    REPLACEMENT = "replacement"


class UnitSize(str, Enum):
    """Organizational echelon."""
    COMPANY = "company"
    BATTALION = "battalion"
    REGIMENT = "regiment"
    BRIGADE = "brigade"
    DIVISION = "division"
    CORPS = "corps"


class MotorizationType(str, Enum):
    NON_MOTORIZED = "non_motorized"
    MOTORIZED = "motorized"         # Has organic transport
    HISTORICALLY_MOTORIZED = "historically_motorized"  # + symbol, uses truck CPA
    MECHANIZED = "mechanized"       # Tracked vehicles (tanks, armored cars)


class UnitStatus(str, Enum):
    ACTIVE = "active"
    ENGAGED = "engaged"
    IN_RESERVE = "in_reserve"
    PINNED = "pinned"
    BROKEN_DOWN = "broken_down"
    DESTROYED = "destroyed"
    SURRENDERED = "surrendered"
    IN_TRANSIT = "in_transit"       # Rail / off-map movement
    NOT_YET_ARRIVED = "not_yet_arrived"
    WITHDRAWN = "withdrawn"


class TerrainType(str, Enum):
    CLEAR = "clear"
    ROUGH = "rough"
    MOUNTAIN = "mountain"
    SAND_SEA = "sand_sea"
    SWAMP = "swamp"
    OASIS = "oasis"
    VILLAGE = "village"
    MAJOR_CITY = "major_city"
    BIR = "bir"
    SALT_MARSH = "salt_marsh"
    DELTA = "delta"
    ESCARPMENT = "escarpment"
    WADI = "wadi"
    COASTAL = "coastal"
    DESERT = "desert"
    SEA = "sea"


class RoadType(str, Enum):
    NONE = "none"
    TRACK = "track"         # Halves terrain cost (does NOT replace it)
    ROAD = "road"           # Flat movement cost
    RAILROAD = "railroad"


class FortLevel(IntEnum):
    NONE = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3  # Tobruk-class


class Weather(str, Enum):
    CLEAR = "clear"
    HOT = "hot"
    MUD = "mud"
    SANDSTORM = "sandstorm"


class Season(str, Enum):
    SPRING = "spring"   # Wk4 March – Wk3 June
    SUMMER = "summer"   # Wk4 June – Wk3 September
    AUTUMN = "autumn"   # Wk4 September – Wk3 December
    WINTER = "winter"   # Wk4 December – Wk3 March


class AircraftType(str, Enum):
    """NJH classification system."""
    FIGHTER = "F"
    ATTACK_BOMBER = "AB"
    LEVEL_BOMBER = "LB"
    RECON = "R"
    TRANSPORT = "T"
    FIGHTER_AB = "F/AB"     # Dual-role
    LB_TRANSPORT = "LB/T"  # Dual-role
    AB_RECON = "AB/R"       # Dual-role


class AircraftMission(str, Enum):
    NONE = "none"
    STRATEGIC = "strategic"
    LAND_SUPPORT = "land_support"
    OCAP = "ocap"
    DCAP = "dcap"
    RECON = "recon"
    BOMBING = "bombing"
    STRAFING = "strafing"
    TRANSPORT = "transport"
    MALTA = "malta"
    CONVOY_CAP = "convoy_cap"
    CONVOY_STRIKE = "convoy_strike"


class AircraftStatus(str, Enum):
    READY = "ready"
    FLEW_THIS_STAGE = "flew_this_stage"
    MAINTENANCE = "maintenance"
    DAMAGED = "damaged"
    DESTROYED = "destroyed"
    NOT_YET_ARRIVED = "not_yet_arrived"


class SupplyType(str, Enum):
    FUEL = "fuel"
    WATER = "water"
    AMMO = "ammo"
    STORES = "stores"       # General supply (food, parts, etc.)


# ── Sequence of Play Phases ──

class GamePhase(str, Enum):
    """
    Top-level phases within a Game Turn.
    Maps to NJH restructured SoP.
    """
    STORES_EXPENDITURE = "stores_expenditure"
    STRATEGIC_STAGE = "strategic_stage"
    OP_STAGE = "op_stage"
    END_OF_TURN = "end_of_turn"


class OpStagePhase(str, Enum):
    """Phases within an Operation Stage."""
    WEATHER = "weather"
    ORGANIZATION = "organization"
    CONVOY_ARRIVAL = "convoy_arrival"
    FLEET = "fleet"
    AIR = "air"
    INITIATIVE = "initiative"
    # Per-side phases
    RESERVE = "reserve"
    MOVEMENT_COMBAT = "movement_combat"
    VEHICLE_REPAIR = "vehicle_repair"
    CONVOY_MOVEMENT = "convoy_movement"
    PATROL = "patrol"


class CombatSegment(str, Enum):
    """Sub-phases within Movement & Combat."""
    RECON = "recon"
    MOVEMENT = "movement"
    BREAKDOWN = "breakdown"
    BARRAGE = "barrage"
    RETREAT_BEFORE_ASSAULT = "retreat_before_assault"
    ANTI_ARMOR = "anti_armor"
    CLOSE_ASSAULT = "close_assault"
    RESERVE_RELEASE = "reserve_release"


# ── Constants ──

GAME_TURNS_CAMPAIGN = 111   # 111 turns in Operation Compass scenario (GT1–GT111)
OP_STAGES_PER_TURN = 3
HEX_SCALE_KM = 8
TURN_SCALE_WEEKS = 1
