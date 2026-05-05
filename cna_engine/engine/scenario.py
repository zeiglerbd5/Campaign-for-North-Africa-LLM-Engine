"""
CNA Engine — Scenario & OOB Loader
Creates initial game state with historical orders of battle,
unit positions, strengths, supply levels, and reinforcement schedules.

Supports GT1 (September 1940) Operation Compass setup as the default
starting scenario.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

from cna_engine.models.game_state import (
    GameState, Unit, Formation, HexState, TurnState,
    TOEStrength, UnitSupply, SupplyDump, Aircraft, SGSU,
    FleetState,
)
from cna_engine.models.enums import (
    Side, Nationality, UnitClass, UnitSize, MotorizationType,
    UnitStatus, TerrainType, RoadType, GamePhase, Weather, Season,
    AircraftStatus, AircraftMission,
)
from cna_engine.engine.air import get_aircraft_stats


# ════════════════════════════════════════
# DEPLOYMENT HEX MAPPING
# ════════════════════════════════════════

_DEPLOYMENT_HEXES = {
    "alexandria": "E1326",  # Alexandria port (Map E)
    "tripoli": "A3511",     # Tripoli port (Map A)
}


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class ScenarioLoadResult:
    """Result of loading a scenario."""
    success: bool
    scenario_name: str
    game_turn: int
    allied_units: int = 0
    axis_units: int = 0
    allied_formations: int = 0
    axis_formations: int = 0
    hexes_populated: int = 0
    aircraft_deployed: int = 0
    reinforcements_scheduled: int = 0
    description: str = ""


@dataclass
class Reinforcement:
    """A scheduled reinforcement arrival."""
    unit_id: str
    arrival_gt: int
    arrival_opstage: int = 1
    arrival_hex: Optional[str] = None
    arrival_location: Optional[str] = None  # Off-map location


@dataclass
class AirReinforcement:
    """A scheduled air reinforcement arrival."""
    aircraft_id: str
    arrival_gt: int
    sgsu_id: str  # SGSU to assign on arrival


# ════════════════════════════════════════
# UNIT FACTORY HELPERS
# ════════════════════════════════════════

def _make_infantry_bn(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 6, strength: int = 10,
    motorization: str = MotorizationType.NON_MOTORIZED,
    parent_formation: Optional[str] = None,
    fuel: float = 5.0, water: float = 10.0,
    ammo: float = 10.0, stores: float = 5.0,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=motorization, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=2,
        toe_strength=TOEStrength(infantry=strength),
        current_strength=TOEStrength(infantry=strength),
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=fuel, water=water, ammo=ammo, stores=stores,
            fuel_capacity=fuel*2, water_capacity=water*2,
            ammo_capacity=ammo*2, stores_capacity=stores*2,
        ),
    )


def _make_armor_bn(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 10, strength: int = 12,
    bar: int = 3, parent_formation: Optional[str] = None,
    fuel: float = 15.0, water: float = 8.0,
    ammo: float = 12.0, stores: float = 5.0,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.ARMOR, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.MECHANIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=3,
        toe_strength=TOEStrength(armor=strength),
        current_strength=TOEStrength(armor=strength),
        bar=bar, armor_protection=bar * 4,
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=fuel, water=water, ammo=ammo, stores=stores,
            fuel_capacity=fuel*2, water_capacity=water*2,
            ammo_capacity=ammo*2, stores_capacity=stores*2,
        ),
    )


def _make_artillery_bn(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 6, strength: int = 6,
    parent_formation: Optional[str] = None,
    fuel: float = 8.0, water: float = 8.0,
    ammo: float = 16.0, stores: float = 4.0,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.GUN, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=2,
        toe_strength=TOEStrength(gun=strength),
        current_strength=TOEStrength(gun=strength),
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=fuel, water=water, ammo=ammo, stores=stores,
            fuel_capacity=fuel*2, water_capacity=water*2,
            ammo_capacity=ammo*2, stores_capacity=stores*2,
        ),
    )


def _make_recon_co(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 10, strength: int = 4,
    parent_formation: Optional[str] = None,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.RECON, unit_size=UnitSize.COMPANY,
        motorization=MotorizationType.MECHANIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=1,
        toe_strength=TOEStrength(recon=strength),
        current_strength=TOEStrength(recon=strength),
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=8.0, water=5.0, ammo=6.0, stores=3.0,
            fuel_capacity=16.0, water_capacity=10.0,
            ammo_capacity=12.0, stores_capacity=6.0,
        ),
    )


def _make_engineer_co(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 6, strength: int = 4,
    parent_formation: Optional[str] = None,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.ENGINEER, unit_size=UnitSize.COMPANY,
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=1,
        toe_strength=TOEStrength(infantry=strength),
        current_strength=TOEStrength(infantry=strength),
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=5.0, water=5.0, ammo=4.0, stores=3.0,
            fuel_capacity=10.0, water_capacity=10.0,
            ammo_capacity=8.0, stores_capacity=6.0,
        ),
    )


def _make_truck_unit(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, base_cpa: int = 8, parent_formation: Optional[str] = None,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.TRUCK, unit_size=UnitSize.COMPANY,
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=1,
        toe_strength=TOEStrength(infantry=2),
        current_strength=TOEStrength(infantry=2),
        parent_formation_id=parent_formation,
        attached_truck_points=4,
        supply=UnitSupply(
            fuel=15.0, water=5.0, ammo=2.0, stores=3.0,
            fuel_capacity=30.0, water_capacity=10.0,
            ammo_capacity=4.0, stores_capacity=6.0,
        ),
    )


def _make_hq(
    uid: str, name: str, side: str, nationality: str,
    hex_id: str, parent_formation: Optional[str] = None,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side, nationality=nationality,
        unit_class=UnitClass.HQ, unit_size=UnitSize.BRIGADE,
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=4, stacking_points=1,
        toe_strength=TOEStrength(infantry=2),
        current_strength=TOEStrength(infantry=2),
        parent_formation_id=parent_formation,
        supply=UnitSupply(
            fuel=5.0, water=5.0, ammo=2.0, stores=3.0,
            fuel_capacity=10.0, water_capacity=10.0,
            ammo_capacity=4.0, stores_capacity=6.0,
        ),
    )


def _make_aircraft(ac_id, aircraft_type_id, side, sgsu_id,
                   status=AircraftStatus.READY):
    """Create an aircraft with stats loaded from reference data."""
    _, tacair, bombload, _ = get_aircraft_stats(aircraft_type_id)
    return Aircraft(
        id=ac_id, aircraft_type_id=aircraft_type_id, side=side,
        sgsu_id=sgsu_id, status=status, mission=AircraftMission.NONE,
        fuel_remaining=0.0, bombload_remaining=bombload,
        tacair_remaining=tacair,
    )


def _make_sgsu(sgsu_id, side, hex_id, capacity=12,
               fuel=200.0, ammo=100.0, stores=100.0):
    """Create an SGSU (airbase)."""
    return SGSU(
        id=sgsu_id, side=side, hex_id=hex_id, capacity=capacity,
        is_operational=True, fuel=fuel, ammo=ammo, stores=stores,
    )


# ════════════════════════════════════════
# MAP SETUP
# ════════════════════════════════════════

def _create_base_map() -> dict[str, HexState]:
    """
    Load the full 5-section North African theater map from hex_database.json.

    The CNA board has 5 map sheets (A-E) covering ~2,400 km from Tunis
    to Alexandria.  ~6,500 land hexes at 8 km/hex.

    Geography (west-to-east):
      Map A: Tunisia → western Tripolitania → Gulf of Sidra
      Map B: Cyrenaica (Benghazi, Derna, Jebel Akhdar)
      Map C: Tobruk → Bardia → Sollum/Halfaya border
      Map D: Sidi Barrani → Mersa Matruh → El Alamein
      Map E: Alexandria → Nile Delta → Cairo
    """
    from cna_engine.data.hex_map import load_hex_database, load_named_locations, load_road_network

    hex_db = load_hex_database()
    named_locs = load_named_locations()
    road_net = load_road_network()
    hexes = {}

    # Map hex_database terrain strings to TerrainType enum
    _TERRAIN_MAP = {
        "CLEAR": TerrainType.CLEAR,
        "SAND_SEA": TerrainType.SAND_SEA,
        "DESERT": TerrainType.DESERT,
        "SALT_MARSH": TerrainType.SALT_MARSH,
        "ROUGH": TerrainType.ROUGH,
        "SWAMP": TerrainType.SWAMP,
        "MOUNTAIN": TerrainType.MOUNTAIN,
    }

    # Create HexState for every land hex in the database
    for hex_id, terrain_str in hex_db.items():
        terrain = _TERRAIN_MAP.get(terrain_str, TerrainType.CLEAR)
        hexes[hex_id] = HexState(hex_id=hex_id, terrain=terrain)

    # Apply named location overlays: ports, airfields
    for name, info in named_locs.items():
        hid = info["hex_id"]
        if hid not in hexes:
            continue
        hs = hexes[hid]
        if info["is_port"]:
            hs.is_port = True
            hs.port_name = name
        if info["is_airfield"]:
            hs.has_airfield = True

    # Apply road network (Via Balbia, tracks, railroad)
    _ROAD_MAP = {
        "road": RoadType.ROAD,
        "track": RoadType.TRACK,
        "railroad": RoadType.RAILROAD,
        "road_railroad": RoadType.ROAD,  # Road takes precedence for movement
    }
    road_count = 0
    for hid, road_type_str in road_net.items():
        if hid not in hexes:
            continue
        hexes[hid].road = _ROAD_MAP.get(road_type_str, RoadType.NONE)
        if road_type_str in ("railroad", "road_railroad"):
            hexes[hid].railroad_operational = True
        road_count += 1

    logger.info("Loaded %d hexes from hex database, %d with road/track/railroad",
                len(hexes), road_count)
    return hexes


# ════════════════════════════════════════
# GT1 OPERATION COMPASS SCENARIO
# ════════════════════════════════════════

def load_operation_compass() -> tuple[GameState, list[Reinforcement]]:
    """
    Load the GT1 Operation Compass scenario.
    September 1940: Italian 10th Army in Libya, British Western Desert Force in Egypt.

    Returns (game_state, reinforcement_schedule).
    """
    state = GameState()

    # Turn state
    state.turn = TurnState(
        game_turn=1, op_stage=1,
        phase=GamePhase.STORES_EXPENDITURE,
        current_weather=Weather.CLEAR,
        current_season=Season.AUTUMN,
        allied_initiative_rating=3,
        axis_initiative_rating=5,
    )

    # Map
    state.hexes = _create_base_map()

    # ── Infrastructure ──
    # Named-location airfields/ports are already set by _create_base_map().
    # Here we add infrastructure not derivable from named_locations.json.

    # Egypt
    state.hexes["E1326"].railroad_operational = True   # Alexandria
    state.hexes["E1326"].has_water_pipeline = True
    state.hexes["D1822"].has_water_pipeline = True     # Mersa Matruh
    state.hexes["D0821"].has_air_landing_strip = True  # Sidi Barrani strip

    # Egypt-Libya border
    state.hexes["C1714"].fort_level = 1   # Sollum
    state.hexes["C1715"].fort_level = 1   # Halfaya Pass

    # Cyrenaica
    state.hexes["C1215"].fort_level = 2   # Bardia
    state.hexes["C0512"].fort_level = 3   # Tobruk
    state.hexes["B3405"].has_air_landing_strip = True  # Gazala

    # Tripolitania
    state.hexes["A1437"].fort_level = 1   # El Agheila bottleneck
    state.hexes["A2438"].has_air_landing_strip = True  # Sirte
    state.hexes["A3117"].has_air_landing_strip = True  # Misurata
    state.hexes["A3511"].fort_level = 2                # Tripoli defenses

    # ── ALLIED OOB ──

    # Western Desert Force HQ
    state.formations["cw_wdf"] = Formation(
        id="cw_wdf", name="Western Desert Force",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.CORPS,
        unit_ids=[], sub_formation_ids=["cw_7arm_div", "cw_4ind_div"],
    )

    # 7th Armoured Division
    state.formations["cw_7arm_div"] = Formation(
        id="cw_7arm_div", name="7th Armoured Division",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="cw_wdf",
        unit_ids=["cw_7arm_hq", "cw_2rtr", "cw_7hus", "cw_3hus",
                  "cw_1rha", "cw_11hus"],
        sub_formation_ids=["cw_7arm_bde", "cw_4arm_bde"],
        hq_unit_id="cw_7arm_hq",
    )
    state.formations["cw_7arm_bde"] = Formation(
        id="cw_7arm_bde", name="7th Armoured Brigade",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.BRIGADE,
        parent_formation_id="cw_7arm_div",
        unit_ids=["cw_2rtr", "cw_7hus"],
    )
    state.formations["cw_4arm_bde"] = Formation(
        id="cw_4arm_bde", name="4th Armoured Brigade",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.BRIGADE,
        parent_formation_id="cw_7arm_div",
        unit_ids=["cw_3hus"],
    )

    # 7th Armoured Division units — deployed between Matruh and Sidi Barrani
    state.units["cw_7arm_hq"] = _make_hq(
        "cw_7arm_hq", "7th Armoured Div HQ",
        Side.ALLIED, Nationality.BRITISH, "D1322",
        parent_formation="cw_7arm_div",
    )
    state.units["cw_2rtr"] = _make_armor_bn(
        "cw_2rtr", "2nd Royal Tank Regiment",
        Side.ALLIED, Nationality.BRITISH, "D1222",
        strength=12, bar=3, parent_formation="cw_7arm_bde",
    )
    state.units["cw_7hus"] = _make_armor_bn(
        "cw_7hus", "7th Queen's Own Hussars",
        Side.ALLIED, Nationality.BRITISH, "D1222",
        strength=10, bar=2, parent_formation="cw_7arm_bde",
    )
    state.units["cw_3hus"] = _make_armor_bn(
        "cw_3hus", "3rd King's Own Hussars",
        Side.ALLIED, Nationality.BRITISH, "D1223",
        strength=8, bar=2, parent_formation="cw_4arm_bde",
    )
    state.units["cw_1rha"] = _make_artillery_bn(
        "cw_1rha", "1st RHA Battery",
        Side.ALLIED, Nationality.BRITISH, "D1322",
        strength=6, parent_formation="cw_7arm_div",
    )
    state.units["cw_11hus"] = _make_recon_co(
        "cw_11hus", "11th Hussars (Recce)",
        Side.ALLIED, Nationality.BRITISH, "D0922",
        parent_formation="cw_7arm_div",
    )

    # 4th Indian Division
    state.formations["cw_4ind_div"] = Formation(
        id="cw_4ind_div", name="4th Indian Division",
        side=Side.ALLIED, nationality=Nationality.INDIAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="cw_wdf",
        unit_ids=["cw_4ind_hq", "cw_1_6raj", "cw_3_1pun",
                  "cw_1rhy_art"],
        hq_unit_id="cw_4ind_hq",
    )

    state.units["cw_4ind_hq"] = _make_hq(
        "cw_4ind_hq", "4th Indian Div HQ",
        Side.ALLIED, Nationality.INDIAN, "D1822",  # Mersa Matruh
        parent_formation="cw_4ind_div",
    )
    state.units["cw_1_6raj"] = _make_infantry_bn(
        "cw_1_6raj", "1/6th Rajputana Rifles",
        Side.ALLIED, Nationality.INDIAN, "D1422",
        motorization=MotorizationType.MOTORIZED,
        parent_formation="cw_4ind_div",
    )
    state.units["cw_3_1pun"] = _make_infantry_bn(
        "cw_3_1pun", "3/1st Punjab Regiment",
        Side.ALLIED, Nationality.INDIAN, "D1422",
        motorization=MotorizationType.MOTORIZED,
        parent_formation="cw_4ind_div",
    )
    state.units["cw_1rhy_art"] = _make_artillery_bn(
        "cw_1rhy_art", "1st Field Regt RA",
        Side.ALLIED, Nationality.BRITISH, "D1822",  # Mersa Matruh
        parent_formation="cw_4ind_div",
    )

    # ── AXIS OOB ──

    # Italian 10th Army
    state.formations["it_10army"] = Formation(
        id="it_10army", name="Italian 10th Army",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        formation_size=UnitSize.CORPS,
        unit_ids=[],
        sub_formation_ids=["it_1lib_div", "it_2lib_div", "it_maletti_grp"],
    )

    # 1st Libyan Division
    state.formations["it_1lib_div"] = Formation(
        id="it_1lib_div", name="1st Libyan Division",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="it_10army",
        unit_ids=["it_1lib_hq", "it_1lib_1bn", "it_1lib_2bn", "it_1lib_art"],
        hq_unit_id="it_1lib_hq",
    )

    state.units["it_1lib_hq"] = _make_hq(
        "it_1lib_hq", "1st Libyan Div HQ",
        Side.AXIS, Nationality.ITALIAN, "D0821",  # Sidi Barrani
        parent_formation="it_1lib_div",
    )
    state.units["it_1lib_1bn"] = _make_infantry_bn(
        "it_1lib_1bn", "1st Libyan Infantry Bn",
        Side.AXIS, Nationality.ITALIAN, "D0821",  # Sidi Barrani
        strength=10, parent_formation="it_1lib_div",
    )
    state.units["it_1lib_2bn"] = _make_infantry_bn(
        "it_1lib_2bn", "2nd Libyan Infantry Bn",
        Side.AXIS, Nationality.ITALIAN, "D0721",
        strength=10, parent_formation="it_1lib_div",
    )
    state.units["it_1lib_art"] = _make_artillery_bn(
        "it_1lib_art", "1st Libyan Artillery Group",
        Side.AXIS, Nationality.ITALIAN, "D0821",  # Sidi Barrani
        strength=4, parent_formation="it_1lib_div",
    )

    # 2nd Libyan Division
    state.formations["it_2lib_div"] = Formation(
        id="it_2lib_div", name="2nd Libyan Division",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="it_10army",
        unit_ids=["it_2lib_hq", "it_2lib_1bn", "it_2lib_2bn"],
        hq_unit_id="it_2lib_hq",
    )

    state.units["it_2lib_hq"] = _make_hq(
        "it_2lib_hq", "2nd Libyan Div HQ",
        Side.AXIS, Nationality.ITALIAN, "D0621",
        parent_formation="it_2lib_div",
    )
    state.units["it_2lib_1bn"] = _make_infantry_bn(
        "it_2lib_1bn", "3rd Libyan Infantry Bn",
        Side.AXIS, Nationality.ITALIAN, "D0621",
        strength=10, parent_formation="it_2lib_div",
    )
    state.units["it_2lib_2bn"] = _make_infantry_bn(
        "it_2lib_2bn", "4th Libyan Infantry Bn",
        Side.AXIS, Nationality.ITALIAN, "D0521",
        strength=10, parent_formation="it_2lib_div",
    )

    # Maletti Group (mechanized)
    state.formations["it_maletti_grp"] = Formation(
        id="it_maletti_grp", name="Maletti Group",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        formation_size=UnitSize.BRIGADE,
        parent_formation_id="it_10army",
        unit_ids=["it_maletti_hq", "it_m11_plt", "it_maletti_inf"],
        hq_unit_id="it_maletti_hq",
    )

    state.units["it_maletti_hq"] = _make_hq(
        "it_maletti_hq", "Maletti Group HQ",
        Side.AXIS, Nationality.ITALIAN, "D0423",  # Interior fortified camp
        parent_formation="it_maletti_grp",
    )
    state.units["it_m11_plt"] = _make_armor_bn(
        "it_m11_plt", "M11/39 Tank Battalion",
        Side.AXIS, Nationality.ITALIAN, "D0423",
        strength=6, bar=1, base_cpa=8,
        parent_formation="it_maletti_grp",
    )
    state.units["it_maletti_inf"] = _make_infantry_bn(
        "it_maletti_inf", "Maletti Motorized Infantry",
        Side.AXIS, Nationality.ITALIAN, "D0523",
        motorization=MotorizationType.MOTORIZED,
        parent_formation="it_maletti_grp",
    )

    # ── Engineer units ──
    state.units["cw_8fd_engr"] = _make_engineer_co(
        "cw_8fd_engr", "8th Field Company RE",
        Side.ALLIED, Nationality.BRITISH, "D1322",
        parent_formation="cw_wdf",
    )
    state.formations["cw_wdf"].unit_ids.append("cw_8fd_engr")

    state.units["it_eng_co"] = _make_engineer_co(
        "it_eng_co", "Italian Engineer Company",
        Side.AXIS, Nationality.ITALIAN, "D0821",  # Sidi Barrani
        parent_formation="it_1lib_div",
    )
    state.formations["it_1lib_div"].unit_ids.append("it_eng_co")

    # ── Truck units ──
    state.units["cw_rasc_trucks"] = _make_truck_unit(
        "cw_rasc_trucks", "RASC Truck Column",
        Side.ALLIED, Nationality.BRITISH, "D1822",  # Mersa Matruh
        parent_formation="cw_wdf",
    )
    state.formations["cw_wdf"].unit_ids.append("cw_rasc_trucks")

    state.units["it_autogrp"] = _make_truck_unit(
        "it_autogrp", "Italian Autogruppo Column",
        Side.AXIS, Nationality.ITALIAN, "D0621",
        parent_formation="it_10army",
    )
    state.formations["it_10army"].unit_ids.append("it_autogrp")

    # ── Place units on map ──
    for uid, unit in state.units.items():
        if unit.hex_id and unit.hex_id in state.hexes:
            hex_state = state.hexes[unit.hex_id]
            if unit.side == Side.ALLIED:
                if uid not in hex_state.allied_unit_ids:
                    hex_state.allied_unit_ids.append(uid)
            else:
                if uid not in hex_state.axis_unit_ids:
                    hex_state.axis_unit_ids.append(uid)

    # ── Fill units to capacity (pre-war stockpiling) ──
    for unit in state.units.values():
        if unit.hex_id is not None:
            unit.supply.fuel = unit.supply.fuel_capacity
            unit.supply.water = unit.supply.water_capacity
            unit.supply.ammo = unit.supply.ammo_capacity
            unit.supply.stores = unit.supply.stores_capacity

    # ── Supply dumps ──
    # Allied supply at Mersa Matruh
    state.hexes["D1822"].supply_dumps.append(SupplyDump(
        id="allied_matruh_depot", side=Side.ALLIED, is_real=True,
        fuel=500.0, water=400.0, ammo=300.0, stores=200.0,
    ))
    # Axis forward supply at Sidi Barrani
    state.hexes["D0821"].supply_dumps.append(SupplyDump(
        id="axis_barrani_depot", side=Side.AXIS, is_real=True,
        fuel=200.0, water=150.0, ammo=200.0, stores=100.0,
    ))
    # Axis main depot at Tripoli
    state.hexes["A3511"].supply_dumps.append(SupplyDump(
        id="axis_tripoli_depot", side=Side.AXIS, is_real=True,
        fuel=800.0, water=600.0, ammo=500.0, stores=400.0,
    ))
    # Axis depot at Benghazi
    state.hexes["B1403"].supply_dumps.append(SupplyDump(
        id="axis_benghazi_depot", side=Side.AXIS, is_real=True,
        fuel=300.0, water=200.0, ammo=200.0, stores=150.0,
    ))

    # ── Fleet ──
    state.cw_fleet = FleetState(
        is_available=True, sorties_remaining=2,
    )

    # ── Supply pools ──
    state.allied_supply_in_egypt = {
        "fuel": 5000.0, "water": 5000.0, "ammo": 3000.0, "stores": 2000.0,
    }
    state.axis_supply_in_tripoli_boxes = {
        "fuel": 2000.0, "water": 1000.0, "ammo": 1500.0, "stores": 800.0,
    }

    # ── AIR OOB ──

    # RAF SGSUs
    state.sgsus["sgsu_raf_alex"] = _make_sgsu(
        "sgsu_raf_alex", Side.ALLIED, "E1326", capacity=12)   # Alexandria
    state.sgsus["sgsu_raf_matruh"] = _make_sgsu(
        "sgsu_raf_matruh", Side.ALLIED, "D1822", capacity=12) # Mersa Matruh

    # Italian SGSUs
    state.sgsus["sgsu_ra_tobruk"] = _make_sgsu(
        "sgsu_ra_tobruk", Side.AXIS, "C0512", capacity=12)    # Tobruk
    state.sgsus["sgsu_ra_benghazi"] = _make_sgsu(
        "sgsu_ra_benghazi", Side.AXIS, "B1403", capacity=12)  # Benghazi

    # RAF Aircraft (8 squadrons)
    raf_aircraft = [
        ("raf_33sqn_glad", "gladiator", "sgsu_raf_matruh"),
        ("raf_80sqn_glad", "gladiator", "sgsu_raf_matruh"),
        ("raf_112sqn_glad", "gladiator", "sgsu_raf_matruh"),
        ("raf_274sqn_hurr", "hurricane_i", "sgsu_raf_alex"),
        ("raf_45sqn_blen", "blenheim_iv", "sgsu_raf_alex"),
        ("raf_55sqn_blen", "blenheim_iv", "sgsu_raf_alex"),
        ("raf_113sqn_blen", "blenheim_iv", "sgsu_raf_matruh"),
        ("raf_211sqn_blen", "blenheim_iv", "sgsu_raf_matruh"),
    ]
    for ac_id, ac_type, sgsu_id in raf_aircraft:
        state.aircraft[ac_id] = _make_aircraft(ac_id, ac_type, Side.ALLIED, sgsu_id)
        state.sgsus[sgsu_id].aircraft_ids.append(ac_id)

    # Regia Aeronautica Aircraft (12 squadrons)
    ra_aircraft = [
        ("ra_cr42_1sqn", "cr42", "sgsu_ra_tobruk"),
        ("ra_cr42_2sqn", "cr42", "sgsu_ra_tobruk"),
        ("ra_cr32_1sqn", "cr32", "sgsu_ra_benghazi"),
        ("ra_cr32_2sqn", "cr32", "sgsu_ra_benghazi"),
        ("ra_sm79_1sqn", "sm79", "sgsu_ra_tobruk"),
        ("ra_sm79_2sqn", "sm79", "sgsu_ra_tobruk"),
        ("ra_sm79_3sqn", "sm79", "sgsu_ra_tobruk"),
        ("ra_sm79_4sqn", "sm79", "sgsu_ra_tobruk"),
        ("ra_sm79_5sqn", "sm79", "sgsu_ra_benghazi"),
        ("ra_sm79_6sqn", "sm79", "sgsu_ra_benghazi"),
        ("ra_sm79_7sqn", "sm79", "sgsu_ra_benghazi"),
        ("ra_sm79_8sqn", "sm79", "sgsu_ra_benghazi"),
    ]
    for ac_id, ac_type, sgsu_id in ra_aircraft:
        state.aircraft[ac_id] = _make_aircraft(ac_id, ac_type, Side.AXIS, sgsu_id)
        state.sgsus[sgsu_id].aircraft_ids.append(ac_id)

    # ── Air Reinforcements (Luftwaffe, NOT_YET_ARRIVED) ──

    # Tripoli SGSU — Axis rear base
    state.sgsus["sgsu_ra_tripoli"] = _make_sgsu(
        "sgsu_ra_tripoli", Side.AXIS, "A3511", capacity=12)   # Tripoli

    # Derna SGSU — created now but empty until reinforcements arrive
    state.sgsus["sgsu_lw_derna"] = _make_sgsu(
        "sgsu_lw_derna", Side.AXIS, "B2703", capacity=12)     # Derna

    lw_reinforcement_aircraft = [
        ("lw_bf109e_1sqn", "bf109e", 25, "sgsu_lw_derna"),
        ("lw_bf109e_2sqn", "bf109e", 25, "sgsu_lw_derna"),
        ("lw_ju87b_1sqn", "ju87b", 25, "sgsu_lw_derna"),
        ("lw_ju87b_2sqn", "ju87b", 25, "sgsu_lw_derna"),
        ("lw_bf110_1sqn", "bf110", 27, "sgsu_lw_derna"),
        ("lw_ju88a_1sqn", "ju88a", 27, "sgsu_lw_derna"),
        ("lw_ju88a_2sqn", "ju88a", 27, "sgsu_lw_derna"),
    ]
    for ac_id, ac_type, arrival_gt, sgsu_id in lw_reinforcement_aircraft:
        ac = _make_aircraft(ac_id, ac_type, Side.AXIS, sgsu_id,
                           status=AircraftStatus.NOT_YET_ARRIVED)
        state.aircraft[ac_id] = ac

    # ── Reinforcement formations ──

    # Australian 6th Division (arrives GT8)
    state.formations["cw_6aus_div"] = Formation(
        id="cw_6aus_div", name="6th Australian Division",
        side=Side.ALLIED, nationality=Nationality.AUSTRALIAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="cw_wdf",
        unit_ids=["cw_6aus_1bn", "cw_6aus_2bn"],
    )
    state.formations["cw_wdf"].sub_formation_ids.append("cw_6aus_div")

    # DAK (German Afrika Korps)
    state.formations["dak"] = Formation(
        id="dak", name="Deutsches Afrikakorps",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        formation_size=UnitSize.CORPS,
        unit_ids=[],
        sub_formation_ids=["dak_recon", "dak_5lt_div", "dak_15pz_div"],
    )
    state.formations["it_10army"].sub_formation_ids.append("dak")

    state.formations["dak_recon"] = Formation(
        id="dak_recon", name="DAK Reconnaissance",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        formation_size=UnitSize.BATTALION,
        parent_formation_id="dak",
        unit_ids=["dak_recon_bn"],
    )

    # 5th Light Division (arrives GT27)
    state.formations["dak_5lt_div"] = Formation(
        id="dak_5lt_div", name="5th Light Division",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="dak",
        unit_ids=["dak_5lt_1bn", "dak_5lt_2bn"],
    )

    # 15th Panzer Division (arrives GT33)
    state.formations["dak_15pz_div"] = Formation(
        id="dak_15pz_div", name="15th Panzer Division",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        formation_size=UnitSize.DIVISION,
        parent_formation_id="dak",
        unit_ids=["dak_15pz_1bn", "dak_15pz_2bn"],
    )

    # ── Reinforcement units (NOT_YET_ARRIVED) ──

    # Australian 6th Div — 2 infantry battalions
    state.units["cw_6aus_1bn"] = _make_infantry_bn(
        "cw_6aus_1bn", "2/1st Australian Infantry Bn",
        Side.ALLIED, Nationality.AUSTRALIAN, hex_id=None,
        strength=12, motorization=MotorizationType.MOTORIZED,
        parent_formation="cw_6aus_div",
    )
    state.units["cw_6aus_1bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["cw_6aus_1bn"].arrival_gt = 8

    state.units["cw_6aus_2bn"] = _make_infantry_bn(
        "cw_6aus_2bn", "2/2nd Australian Infantry Bn",
        Side.ALLIED, Nationality.AUSTRALIAN, hex_id=None,
        strength=12, motorization=MotorizationType.MOTORIZED,
        parent_formation="cw_6aus_div",
    )
    state.units["cw_6aus_2bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["cw_6aus_2bn"].arrival_gt = 8

    # DAK Recon Bn (arrives GT25)
    state.units["dak_recon_bn"] = _make_recon_co(
        "dak_recon_bn", "DAK Reconnaissance Battalion",
        Side.AXIS, Nationality.GERMAN, hex_id=None,
        base_cpa=10, strength=6,
        parent_formation="dak_recon",
    )
    state.units["dak_recon_bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["dak_recon_bn"].arrival_gt = 25

    # 5th Light Div — 2 mixed battalions (arrives GT27)
    state.units["dak_5lt_1bn"] = _make_infantry_bn(
        "dak_5lt_1bn", "5th Light MG Battalion",
        Side.AXIS, Nationality.GERMAN, hex_id=None,
        strength=10, motorization=MotorizationType.MOTORIZED,
        parent_formation="dak_5lt_div",
    )
    state.units["dak_5lt_1bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["dak_5lt_1bn"].arrival_gt = 27

    state.units["dak_5lt_2bn"] = _make_armor_bn(
        "dak_5lt_2bn", "5th Panzer Regiment (I Abt)",
        Side.AXIS, Nationality.GERMAN, hex_id=None,
        strength=10, bar=4, base_cpa=10,
        parent_formation="dak_5lt_div",
    )
    state.units["dak_5lt_2bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["dak_5lt_2bn"].arrival_gt = 27

    # 15th Panzer Div — 2 battalions (arrives GT33)
    state.units["dak_15pz_1bn"] = _make_infantry_bn(
        "dak_15pz_1bn", "15th Panzer Grenadier Battalion",
        Side.AXIS, Nationality.GERMAN, hex_id=None,
        strength=10, motorization=MotorizationType.MOTORIZED,
        parent_formation="dak_15pz_div",
    )
    state.units["dak_15pz_1bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["dak_15pz_1bn"].arrival_gt = 33

    state.units["dak_15pz_2bn"] = _make_armor_bn(
        "dak_15pz_2bn", "15th Panzer Regiment (I Abt)",
        Side.AXIS, Nationality.GERMAN, hex_id=None,
        strength=10, bar=4, base_cpa=10,
        parent_formation="dak_15pz_div",
    )
    state.units["dak_15pz_2bn"].status = UnitStatus.NOT_YET_ARRIVED
    state.units["dak_15pz_2bn"].arrival_gt = 33

    # ── Reinforcement schedule ──
    reinforcements = [
        # Australian 6th Division arrives GT8 (Nov 1940)
        Reinforcement("cw_6aus_1bn", arrival_gt=8, arrival_location="alexandria"),
        Reinforcement("cw_6aus_2bn", arrival_gt=8, arrival_location="alexandria"),
        # German DAK advance party arrives GT25 (Feb 1941)
        Reinforcement("dak_recon_bn", arrival_gt=25, arrival_location="tripoli"),
        # 5th Light Division arrives GT27 (Mar 1941)
        Reinforcement("dak_5lt_1bn", arrival_gt=27, arrival_location="tripoli"),
        Reinforcement("dak_5lt_2bn", arrival_gt=27, arrival_location="tripoli"),
        # 15th Panzer Division arrives GT33 (Apr 1941)
        Reinforcement("dak_15pz_1bn", arrival_gt=33, arrival_location="tripoli"),
        Reinforcement("dak_15pz_2bn", arrival_gt=33, arrival_location="tripoli"),
    ]

    return state, reinforcements


# ════════════════════════════════════════
# REINFORCEMENT PROCESSING
# ════════════════════════════════════════

def process_reinforcements(
    state: GameState,
    reinforcements: list[Reinforcement],
) -> list[str]:
    """
    Check for and process reinforcement arrivals for the current GT.
    Returns list of unit IDs that arrived this turn.
    """
    gt = state.turn.game_turn
    arrived = []

    for reinf in reinforcements:
        if reinf.arrival_gt != gt:
            continue

        unit = state.units.get(reinf.unit_id)
        if not unit:
            continue

        if unit.status != UnitStatus.NOT_YET_ARRIVED:
            continue

        unit.status = UnitStatus.ACTIVE
        if reinf.arrival_hex:
            unit.hex_id = reinf.arrival_hex
            # Add unit to hex state unit lists
            if reinf.arrival_hex in state.hexes:
                hex_state = state.hexes[reinf.arrival_hex]
                side_list = (hex_state.allied_unit_ids
                             if unit.side == Side.ALLIED
                             else hex_state.axis_unit_ids)
                if reinf.unit_id not in side_list:
                    side_list.append(reinf.unit_id)
        elif reinf.arrival_location:
            deploy_hex = _DEPLOYMENT_HEXES.get(reinf.arrival_location)
            if deploy_hex:
                if deploy_hex in state.hexes:
                    unit.hex_id = deploy_hex
                    unit.off_map_location = None
                    hex_state = state.hexes[deploy_hex]
                    side_list = (hex_state.allied_unit_ids
                                 if unit.side == Side.ALLIED
                                 else hex_state.axis_unit_ids)
                    if reinf.unit_id not in side_list:
                        side_list.append(reinf.unit_id)
                else:
                    # deploy_hex not in state.hexes — keep unit off-map
                    logger.warning(
                        "Deploy hex %s for %s not in state.hexes, keeping off-map at %s",
                        deploy_hex, unit.name, reinf.arrival_location,
                    )
                    unit.off_map_location = reinf.arrival_location
            else:
                unit.off_map_location = reinf.arrival_location

        # Fill arriving unit's supply to capacity
        unit.supply.fuel = unit.supply.fuel_capacity
        unit.supply.water = unit.supply.water_capacity
        unit.supply.ammo = unit.supply.ammo_capacity
        unit.supply.stores = unit.supply.stores_capacity

        arrived.append(reinf.unit_id)
        state.log_event("reinforcement", f"{unit.name} arrives at GT{gt}",
                        unit_id=reinf.unit_id)

    return arrived


def process_air_reinforcements(
    state: GameState,
    air_reinforcements: list[AirReinforcement],
) -> list[str]:
    """
    Check for and process air reinforcement arrivals for the current GT.
    Returns list of aircraft IDs that arrived this turn.
    """
    gt = state.turn.game_turn
    arrived = []

    for reinf in air_reinforcements:
        if reinf.arrival_gt != gt:
            continue

        ac = state.aircraft.get(reinf.aircraft_id)
        if not ac:
            continue

        if ac.status != AircraftStatus.NOT_YET_ARRIVED:
            continue

        ac.status = AircraftStatus.READY
        ac.sgsu_id = reinf.sgsu_id

        # Register with SGSU
        sgsu = state.sgsus.get(reinf.sgsu_id)
        if sgsu and reinf.aircraft_id not in sgsu.aircraft_ids:
            sgsu.aircraft_ids.append(reinf.aircraft_id)

        arrived.append(reinf.aircraft_id)
        state.log_event("air_reinforcement",
                        f"Aircraft {reinf.aircraft_id} arrives at GT{gt}, base {reinf.sgsu_id}",
                        aircraft_id=reinf.aircraft_id)

    return arrived


def get_air_reinforcement_schedule() -> list[AirReinforcement]:
    """Return the air reinforcement schedule for Operation Compass."""
    return [
        AirReinforcement("lw_bf109e_1sqn", arrival_gt=25, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_bf109e_2sqn", arrival_gt=25, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_ju87b_1sqn", arrival_gt=25, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_ju87b_2sqn", arrival_gt=25, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_bf110_1sqn", arrival_gt=27, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_ju88a_1sqn", arrival_gt=27, sgsu_id="sgsu_lw_derna"),
        AirReinforcement("lw_ju88a_2sqn", arrival_gt=27, sgsu_id="sgsu_lw_derna"),
    ]


# ════════════════════════════════════════
# SCENARIO LOADER ENTRY POINT
# ════════════════════════════════════════

def load_scenario(scenario_name: str = "operation_compass") -> tuple[GameState, list[Reinforcement]]:
    """
    Load a named scenario. Currently supports:
    - "operation_compass": GT1 September 1940
    """
    if scenario_name == "operation_compass":
        state, reinforcements = load_operation_compass()
        result = ScenarioLoadResult(
            success=True, scenario_name=scenario_name,
            game_turn=state.turn.game_turn,
            allied_units=sum(1 for u in state.units.values() if u.side == Side.ALLIED),
            axis_units=sum(1 for u in state.units.values() if u.side == Side.AXIS),
            allied_formations=sum(1 for f in state.formations.values() if f.side == Side.ALLIED),
            axis_formations=sum(1 for f in state.formations.values() if f.side == Side.AXIS),
            hexes_populated=len(state.hexes),
            reinforcements_scheduled=len(reinforcements),
            description=f"Loaded '{scenario_name}': GT1 September 1940, Western Desert",
        )
        state.log_event("scenario_load", result.description)
        return state, reinforcements
    else:
        raise ValueError(f"Unknown scenario: {scenario_name}")
