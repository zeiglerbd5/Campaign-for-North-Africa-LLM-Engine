"""
CNA Engine — Game State Data Models
Pydantic-style dataclasses for all entities tracked in the game state JSON.
Every field is serializable to/from JSON for LLM agent consumption.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from .enums import (
    Side, Nationality, UnitClass, UnitSize, MotorizationType, UnitStatus,
    TerrainType, RoadType, FortLevel, Weather, Season, AircraftType,
    AircraftMission, AircraftStatus, SupplyType, GamePhase, OpStagePhase,
)


# ════════════════════════════════════════
# UNIT MODELS
# ════════════════════════════════════════

@dataclass
class TOEStrength:
    """Table of Equipment strength breakdown for a single unit."""
    infantry: int = 0       # TOE infantry strength points
    armor: int = 0          # TOE armor/tank points
    gun: int = 0            # TOE artillery/AT gun points
    mg: int = 0             # TOE MG/heavy weapons points
    recon: int = 0          # TOE recce points

    @property
    def total(self) -> int:
        return self.infantry + self.armor + self.gun + self.mg + self.recon


@dataclass
class UnitSupply:
    """Current supply state for one unit."""
    fuel: float = 0.0       # Fuel points
    water: float = 0.0      # Water points
    ammo: float = 0.0       # Ammo points
    stores: float = 0.0     # General stores points

    fuel_capacity: float = 0.0   # Max internal fuel
    water_capacity: float = 0.0
    ammo_capacity: float = 0.0
    stores_capacity: float = 0.0


@dataclass
class Unit:
    """
    A single game counter — battalion, regiment, company, battery, etc.
    This is the fundamental tracked entity.
    """
    id: str                         # Unique identifier, e.g. "cw_2rtr"
    name: str                       # Display name, e.g. "2nd Royal Tank Regiment"
    side: str                       # Side enum value
    nationality: str                # Nationality enum value
    unit_class: str                 # UnitClass enum value
    unit_size: str                  # UnitSize enum value
    motorization: str               # MotorizationType enum value

    # ── Location ──
    hex_id: Optional[str] = None    # Current hex, e.g. "B2415" or None if off-map
    off_map_location: Optional[str] = None  # "tripoli_box", "alexandria", etc.

    # ── Parent / Organization ──
    parent_formation_id: Optional[str] = None  # Which formation this belongs to
    attached_to_id: Optional[str] = None       # Temporary attachment override

    # ── Combat Characteristics ──
    base_cpa: int = 0               # Base Capability Point Allowance
    current_cpa_spent: int = 0      # CPs spent this OpStage
    cohesion: int = 0               # Running cohesion modifier (can be negative)
    disorganization: int = 0        # Disorganization level for morale
    stacking_points: int = 1        # SPs this unit occupies

    toe_strength: TOEStrength = field(default_factory=TOEStrength)
    current_strength: TOEStrength = field(default_factory=TOEStrength)  # After losses

    # ── Armor-specific ──
    bar: Optional[int] = None       # Basic Armor Rating (None for non-armor)
    armor_protection: Optional[int] = None

    # ── Status ──
    status: str = UnitStatus.ACTIVE  # UnitStatus enum value
    is_pinned: bool = False
    is_in_contact: bool = False      # In EZOC
    has_acted_this_stage: bool = False
    terrains_traversed_this_stage: list[str] = field(default_factory=list)

    # ── Supply ──
    supply: UnitSupply = field(default_factory=UnitSupply)

    # ── Trucks ──
    attached_truck_points: int = 0   # Light/Medium/Heavy truck SPs attached
    truck_cargo_fuel: float = 0.0
    truck_cargo_water: float = 0.0
    truck_cargo_ammo: float = 0.0
    truck_cargo_stores: float = 0.0

    # ── Arrival / Withdrawal ──
    arrival_gt: Optional[int] = None
    arrival_opstage: Optional[int] = None
    withdrawal_gt: Optional[int] = None

    # ── History ──
    losses_taken: int = 0           # Cumulative strength points lost
    combats_fought: int = 0

    # ── Positional ──
    turns_in_position: int = 0
    last_hex_id: Optional[str] = None

    @property
    def effective_cpa(self) -> int:
        """CPA available this stage = base + cohesion - disorg - spent, adjusted for fuel."""
        raw = max(0, self.base_cpa + self.cohesion - self.disorganization - self.current_cpa_spent)
        if self.is_motorized and self.supply.fuel_capacity > 0:
            fuel_pct = (self.supply.fuel / self.supply.fuel_capacity) * 100
            if fuel_pct < 25:
                raw = int(raw * 0.75)
            elif fuel_pct < 50:
                raw = int(raw * 0.9)
        return raw

    @property
    def is_motorized(self) -> bool:
        return self.motorization in (
            MotorizationType.MOTORIZED,
            MotorizationType.HISTORICALLY_MOTORIZED,
            MotorizationType.MECHANIZED,
        )

    @property
    def entrenchment_bonus(self) -> int:
        """Defensive bonus from time in position. 0-1 turns: 0, 2 turns: +1, 3+: +2."""
        if self.turns_in_position <= 1:
            return 0
        return min(2, self.turns_in_position - 1)

    @property
    def close_assault_points(self) -> int:
        """Close assault strength = total current SP. Used for ZOC checks."""
        return self.current_strength.total

    @property
    def max_cpa_this_stage(self) -> int:
        """Maximum CPA a unit can spend this OpStage (150% cap for non-motorized)."""
        if self.is_motorized:
            return self.base_cpa + self.cohesion
        else:
            return int(self.base_cpa * 1.5)


# ════════════════════════════════════════
# FORMATION / ORGANIZATION
# ════════════════════════════════════════

@dataclass
class Formation:
    """
    An organizational grouping — brigade, division, corps.
    Formations contain unit IDs and track collective state.
    """
    id: str                         # e.g. "cw_7armoured_div"
    name: str                       # "7th Armoured Division"
    side: str
    nationality: str
    formation_size: str             # UnitSize enum value
    parent_formation_id: Optional[str] = None  # Division belongs to Corps, etc.

    # Unit membership (IDs only — units reference back)
    unit_ids: list[str] = field(default_factory=list)
    sub_formation_ids: list[str] = field(default_factory=list)

    # HQ unit
    hq_unit_id: Optional[str] = None

    # Formation-level state
    is_active: bool = True
    activation_gt: Optional[int] = None
    disbandment_gt: Optional[int] = None


# ════════════════════════════════════════
# MAP / HEX
# ════════════════════════════════════════

@dataclass
class HexState:
    """
    Dynamic state for a single hex. Static terrain data is in reference tables.
    This tracks what changes during play: units present, supply dumps, construction.
    """
    hex_id: str                     # e.g. "B2415"
    terrain: str                    # TerrainType — from static map data
    road: str = RoadType.NONE       # Best road in hex

    # Units present (IDs)
    allied_unit_ids: list[str] = field(default_factory=list)
    axis_unit_ids: list[str] = field(default_factory=list)

    # Supply dumps
    supply_dumps: list[SupplyDump] = field(default_factory=list)

    # Construction
    fort_level: int = 0             # FortLevel
    has_airfield: bool = False
    has_air_landing_strip: bool = False
    has_repair_facility: bool = False
    has_water_pipeline: bool = False
    is_port: bool = False
    port_name: Optional[str] = None
    port_capacity: int = 0
    railroad_operational: bool = False

    # Sighting
    allied_sighted: bool = False    # Allies have sighted this hex (expires end of OpStage)
    axis_sighted: bool = False

    # Minefields
    real_minefield: bool = False
    fake_minefield: bool = False
    minefield_owner: Optional[str] = None  # Side that placed the minefield

    @property
    def total_stacking_points(self) -> int:
        """Would need to sum from unit data — placeholder."""
        return len(self.allied_unit_ids) + len(self.axis_unit_ids)


@dataclass
class SupplyDump:
    """A supply dump in a hex. Can be real or fake."""
    id: str
    side: str
    is_real: bool = True
    fuel: float = 0.0
    water: float = 0.0
    ammo: float = 0.0
    stores: float = 0.0


# ════════════════════════════════════════
# AIR GAME
# ════════════════════════════════════════

@dataclass
class Aircraft:
    """A single aircraft counter (represents ~3 planes)."""
    id: str                         # e.g. "cw_hurricane_i_001"
    aircraft_type_id: str           # References AircraftCharacteristics
    side: str
    sgsu_id: Optional[str] = None   # Current base

    status: str = AircraftStatus.READY
    mission: str = AircraftMission.NONE
    pilot_id: Optional[str] = None

    fuel_remaining: float = 0.0
    bombload_remaining: int = 0
    tacair_remaining: int = 0

    sorties_flown: int = 0
    kills: int = 0


@dataclass
class Pilot:
    """Individual pilot tracked for the air game."""
    id: str
    name: str
    side: str
    nationality: str
    experience: int = 0             # 0=green, 1=regular, 2=veteran, 3=ace
    kills: int = 0
    missions_flown: int = 0
    is_alive: bool = True
    aircraft_id: Optional[str] = None
    arrival_gt: Optional[int] = None


@dataclass
class SGSU:
    """Squadron Ground Support Unit — an airbase."""
    id: str
    side: str
    hex_id: Optional[str] = None
    capacity: int = 0               # Max aircraft
    aircraft_ids: list[str] = field(default_factory=list)
    pilot_ids: list[str] = field(default_factory=list)
    fuel: float = 0.0
    ammo: float = 0.0
    stores: float = 0.0
    is_operational: bool = True


# ════════════════════════════════════════
# NAVAL / FLEET
# ════════════════════════════════════════

@dataclass
class FleetState:
    """Commonwealth Mediterranean Fleet state."""
    is_available: bool = False
    current_hex: Optional[str] = None
    sorties_remaining: int = 0
    repair_turns_remaining: int = 0
    ships_committed: int = 0


@dataclass
class ConvoyState:
    """Axis naval convoy tracking for one game turn."""
    planned_tonnage: dict[str, float] = field(default_factory=dict)  # port -> tons
    actual_tonnage_delivered: dict[str, float] = field(default_factory=dict)
    lanes_reconned: list[str] = field(default_factory=list)
    cap_assigned: list[str] = field(default_factory=list)  # aircraft IDs
    losses_this_turn: float = 0.0


# ════════════════════════════════════════
# TURN / PHASE TRACKING
# ════════════════════════════════════════

@dataclass
class TurnState:
    """
    Tracks exactly where we are in the Sequence of Play.
    This is the state machine cursor.
    """
    game_turn: int = 1              # GT1 = September 1940 Week 1
    op_stage: int = 1               # 1, 2, or 3
    phase: str = GamePhase.STORES_EXPENDITURE
    sub_phase: Optional[str] = None
    active_side: Optional[str] = None  # Who is currently acting

    # Initiative
    initiative_side: Optional[str] = None  # Side with initiative this OpStage
    allied_initiative_rating: int = 3
    axis_initiative_rating: int = 5

    # Weather
    current_weather: str = Weather.CLEAR
    current_season: str = Season.AUTUMN  # Campaign starts Sept 1940

    # Movement & Combat tracking within OpStage
    movement_combat_iteration: int = 0  # Which pass through H.1-4

    @property
    def date_string(self) -> str:
        """Approximate real-world date from game turn."""
        # GT1 = Sept 1940 Wk1, 4 GTs per month approximately
        month_offset = (self.game_turn - 1) // 4
        week_in_month = ((self.game_turn - 1) % 4) + 1
        base_month = 9  # September
        base_year = 1940
        total_months = base_month + month_offset - 1
        year = base_year + total_months // 12
        month = (total_months % 12) + 1
        month_names = [
            "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        return f"{month_names[month]} {year} Wk{week_in_month}"


# ════════════════════════════════════════
# TOP-LEVEL GAME STATE
# ════════════════════════════════════════

@dataclass
class GameState:
    """
    The complete game state. Serialized to JSON between turns.
    LLM agents read from and write to this structure.
    """
    # ── Turn tracking ──
    turn: TurnState = field(default_factory=TurnState)

    # ── Units (keyed by unit ID) ──
    units: dict[str, Unit] = field(default_factory=dict)

    # ── Formations (keyed by formation ID) ──
    formations: dict[str, Formation] = field(default_factory=dict)

    # ── Map state (keyed by hex ID) ──
    hexes: dict[str, HexState] = field(default_factory=dict)

    # ── Air game ──
    aircraft: dict[str, Aircraft] = field(default_factory=dict)
    pilots: dict[str, Pilot] = field(default_factory=dict)
    sgsus: dict[str, SGSU] = field(default_factory=dict)

    # ── Naval ──
    cw_fleet: FleetState = field(default_factory=FleetState)
    axis_convoy: ConvoyState = field(default_factory=ConvoyState)

    # ── Global supply tracking ──
    allied_supply_in_egypt: dict[str, float] = field(default_factory=lambda: {
        "fuel": 99999, "water": 99999, "ammo": 99999, "stores": 99999
    })
    axis_supply_in_tripoli_boxes: dict[str, float] = field(default_factory=lambda: {
        "fuel": 0, "water": 0, "ammo": 0, "stores": 0
    })

    # ── Production tracking ──
    allied_replacement_pool: dict[str, int] = field(default_factory=dict)
    axis_replacement_pool: dict[str, int] = field(default_factory=dict)
    allied_truck_production_queue: list = field(default_factory=list)
    axis_truck_production_queue: list = field(default_factory=list)

    # ── Event log ──
    event_log: list[dict] = field(default_factory=list)

    # ── Options / Toggles ──
    options: dict[str, bool] = field(default_factory=lambda: {
        "njh_initiative_per_opstage": True,
        "njh_trucks_move_in_opstage": True,
        "njh_engaged_persists": False,
        "njh_fuel_in_tanks_no_evap": True,
    })

    def get_units_in_hex(self, hex_id: str, side: Optional[str] = None) -> list[Unit]:
        """Get all units in a hex, optionally filtered by side."""
        results = []
        for uid, unit in self.units.items():
            if unit.hex_id == hex_id:
                if side is None or unit.side == side:
                    results.append(unit)
        return results

    def get_units_in_formation(self, formation_id: str) -> list[Unit]:
        """Get all units belonging to a formation."""
        return [u for u in self.units.values() if u.parent_formation_id == formation_id]

    def log_event(self, event_type: str, description: str, **kwargs):
        """Append to the event log."""
        entry = {
            "gt": self.turn.game_turn,
            "op_stage": self.turn.op_stage,
            "phase": self.turn.phase,
            "type": event_type,
            "description": description,
            **kwargs,
        }
        self.event_log.append(entry)
