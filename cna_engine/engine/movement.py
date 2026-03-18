"""
CNA Engine — Movement & CPA System
Hex geometry, terrain-based movement costs, CPA tracking,
EZOC/contact, stacking enforcement, engaged status, reaction movement.

Hex coordinate convention:
  CNA hex IDs = "{section_letter}{col:02d}{row:02d}", e.g. "B2415"
  Uses column-parity offset grid (standard wargame hex convention).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random
import re

from cna_engine.models.game_state import GameState, HexState, Unit
from cna_engine.models.enums import (
    Side, UnitStatus, UnitClass, RoadType, TerrainType, MotorizationType,
)
from cna_engine.data.reference_data import ReferenceData
from cna_engine.data.hex_map import SECTION_COL_OFFSETS


# ════════════════════════════════════════
# FUEL CONSUMPTION RATES BY MOTORIZATION
# ════════════════════════════════════════

_MOTORIZATION_FUEL_RATE = {
    MotorizationType.NON_MOTORIZED: 0,
    MotorizationType.MOTORIZED: 2,
    MotorizationType.HISTORICALLY_MOTORIZED: 2,
    MotorizationType.MECHANIZED: 3,
}


# ════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════

# Hex parity: True = odd columns shift down (row+1 for SE/SW neighbors).
# Parameterized so it can be flipped after checking the physical map.
ODD_COL_SHIFT_DOWN = True

# CPA costs for status transitions
BREAK_CONTACT_COST = 2
BREAK_ENGAGED_COST = 4

# Road movement cost (flat, regardless of terrain)
ROAD_MOVEMENT_COST = 0.5

# Default terrain cost when hex is missing from state.hexes
DEFAULT_TERRAIN = TerrainType.CLEAR
DEFAULT_ROAD = RoadType.NONE


# ════════════════════════════════════════
# CROSS-SECTION GEOMETRY
# ════════════════════════════════════════

# Local column range per section (from hex database)
_SECTION_COL_RANGES = {
    "A": (1, 39),
    "B": (2, 39),
    "C": (2, 39),
    "D": (1, 39),
    "E": (2, 39),
}


def _to_global_col(section: str, col: int) -> int:
    """Convert section-local column to global column for cross-section math."""
    return col + SECTION_COL_OFFSETS[section]


def _from_global_col(global_col: int) -> tuple[str, int] | None:
    """Convert global column back to (section, local_col). None if off-map."""
    for section in ("A", "B", "C", "D", "E"):
        offset = SECTION_COL_OFFSETS[section]
        local_col = global_col - offset
        min_col, max_col = _SECTION_COL_RANGES[section]
        if min_col <= local_col <= max_col:
            return (section, local_col)
    return None


# ════════════════════════════════════════
# HEX COORDINATE SYSTEM
# ════════════════════════════════════════

_HEX_ID_RE = re.compile(r'^([A-Z])(\d{2})(\d{2})$')


@dataclass(frozen=True)
class HexCoord:
    """Parsed hex coordinate."""
    section: str   # Map section letter, e.g. "B"
    col: int       # Column number (0-99)
    row: int       # Row number (0-99)

    @property
    def hex_id(self) -> str:
        return f"{self.section}{self.col:02d}{self.row:02d}"


def parse_hex_id(hex_id: str) -> HexCoord:
    """Parse a hex ID string like 'B2415' into a HexCoord."""
    hex_id = hex_id.strip().upper()
    m = _HEX_ID_RE.match(hex_id)
    if not m:
        raise ValueError(f"Invalid hex ID format: '{hex_id}' (expected e.g. 'B2415')")
    return HexCoord(section=m.group(1), col=int(m.group(2)), row=int(m.group(3)))


def _hex_id_from_parts(section: str, col: int, row: int) -> str:
    return f"{section}{col:02d}{row:02d}"


def get_neighbors(hex_id: str) -> list[str]:
    """
    Get the 6 adjacent hex IDs for a given hex.
    Supports cross-section adjacency at map-sheet seams (A↔B, B↔C, etc.).
    Uses global column numbers for correct parity across sections.
    """
    coord = parse_hex_id(hex_id)
    gc = _to_global_col(coord.section, coord.col)
    r = coord.row

    # Use global column parity for consistent behavior across sections
    is_odd_col = (gc % 2 == 1)

    # In offset hex grids, the 6 neighbors depend on column parity.
    # ODD_COL_SHIFT_DOWN = True means odd columns are shifted down
    # (their SE/SW neighbors have row+1 relative to even column layout).
    if ODD_COL_SHIFT_DOWN:
        if is_odd_col:
            # Odd column (shifted down)
            deltas = [
                (0, -1),   # N
                (+1, 0),   # NE
                (+1, +1),  # SE
                (0, +1),   # S
                (-1, +1),  # SW
                (-1, 0),   # NW
            ]
        else:
            # Even column
            deltas = [
                (0, -1),   # N
                (+1, -1),  # NE
                (+1, 0),   # SE
                (0, +1),   # S
                (-1, 0),   # SW
                (-1, -1),  # NW
            ]
    else:
        # Even-column-shift-down (inverse)
        if is_odd_col:
            deltas = [
                (0, -1),
                (+1, -1),
                (+1, 0),
                (0, +1),
                (-1, 0),
                (-1, -1),
            ]
        else:
            deltas = [
                (0, -1),
                (+1, 0),
                (+1, +1),
                (0, +1),
                (-1, +1),
                (-1, 0),
            ]

    neighbors = []
    for dc, dr in deltas:
        ngc, nr = gc + dc, r + dr
        if nr < 0 or nr > 99:
            continue
        result = _from_global_col(ngc)
        if result is None:
            continue
        ns, nc = result
        neighbors.append(_hex_id_from_parts(ns, nc, nr))
    return neighbors


def are_adjacent(hex1: str, hex2: str) -> bool:
    """Check if two hexes are adjacent (supports cross-section adjacency)."""
    return hex2 in get_neighbors(hex1)


def _hex_distance(a: str, b: str) -> int:
    """Hex distance between two hex IDs using global coordinates (cube-coordinate Manhattan)."""
    ca, cb = parse_hex_id(a), parse_hex_id(b)
    # Use global columns for cross-section distance
    ga = _to_global_col(ca.section, ca.col)
    gb = _to_global_col(cb.section, cb.col)

    def _to_cube(col: int, row: int):
        x = col
        if ODD_COL_SHIFT_DOWN:
            z = row - (col - (col & 1)) // 2
        else:
            z = row - (col + (col & 1)) // 2
        y = -x - z
        return x, y, z

    ax, ay, az = _to_cube(ga, ca.row)
    bx, by, bz = _to_cube(gb, cb.row)
    return (abs(ax - bx) + abs(ay - by) + abs(az - bz)) // 2


def find_path(
    state: GameState,
    ref: ReferenceData,
    unit: Unit,
    destination: str,
    max_steps: int = 20,
) -> list[str] | None:
    """
    Dijkstra search for the cheapest path from unit's current hex
    to *destination*, respecting CPA budget and EZOC rules.

    If the destination is unreachable, returns a partial path to the
    reachable hex closest to the destination (best-effort advance).
    Returns ``None`` only if the unit cannot move at all.

    Rules:
    - Movement cost per hex comes from ``compute_hex_entry_cost()``.
    - Entering an EZOC hex is allowed only as the *last* step (EZOC
      stops movement).
    - Stacking limits at the destination are checked.
    - The unit's pre-move costs (break-engaged / break-contact) are
      deducted from the available budget before searching.
    """
    import heapq

    origin = unit.hex_id
    if origin is None:
        return None
    if origin == destination:
        return None  # already there

    # Budget available for movement (after pre-move costs)
    budget = unit.max_cpa_this_stage - unit.current_cpa_spent
    pre_cost = 0.0
    if unit.status == UnitStatus.ENGAGED:
        pre_cost += BREAK_ENGAGED_COST
    if unit.is_in_contact and is_hex_in_ezoc(state, unit.hex_id, unit.side):
        pre_cost += BREAK_CONTACT_COST
    move_budget = budget - pre_cost
    if move_budget <= 0:
        return None

    # Pre-compute EZOC hexes once
    ezoc_hexes = get_ezoc_hexes(state, unit.side)

    # Dijkstra: (cumulative_cost, steps, hex_id)
    start_entry = (0.0, 0, origin)
    heap: list[tuple[float, int, str]] = [start_entry]
    best_cost: dict[str, float] = {origin: 0.0}
    came_from: dict[str, str] = {}

    # Track the reachable hex closest to the destination for partial paths
    dest_dist = _hex_distance(origin, destination)
    best_partial: tuple[int, float, str] = (dest_dist, 0.0, origin)  # (dist, cost, hex)

    while heap:
        cost_so_far, steps, current = heapq.heappop(heap)

        if current == destination:
            # Reconstruct full path
            path = [current]
            while current != origin:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        # If we entered an EZOC hex (and it isn't the origin), stop expanding.
        if current != origin and current in ezoc_hexes:
            continue

        if steps >= max_steps:
            continue

        for neighbor in get_neighbors(current):
            # Block movement into enemy-occupied hexes
            if hex_has_active_enemy(state, neighbor, unit.side):
                continue
            entry_cost = compute_hex_entry_cost(
                state, ref, unit, current, neighbor,
            )
            new_cost = cost_so_far + entry_cost.effective_cost
            if new_cost > move_budget:
                continue
            if neighbor in best_cost and best_cost[neighbor] <= new_cost:
                continue

            best_cost[neighbor] = new_cost
            came_from[neighbor] = current
            heapq.heappush(heap, (new_cost, steps + 1, neighbor))

            # Update best partial candidate (closer to dest wins; ties → cheaper)
            d = _hex_distance(neighbor, destination)
            if (d, new_cost) < (best_partial[0], best_partial[1]):
                best_partial = (d, new_cost, neighbor)

    # Destination unreachable — return partial path to closest reachable hex
    _, _, partial_hex = best_partial
    if partial_hex == origin:
        return None  # couldn't move at all

    path = [partial_hex]
    cur = partial_hex
    while cur != origin:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    return path


# ════════════════════════════════════════
# ZOC COMPUTATION
# ════════════════════════════════════════

def _unit_exerts_zoc(unit: Unit) -> bool:
    """Check if a unit exerts ZOC. Requires ACTIVE/ENGAGED, >1 SP, >9 CA pts."""
    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return False
    if unit.current_strength.total <= 1:
        return False
    if unit.close_assault_points <= 9:
        return False
    return True


def get_ezoc_hexes(state: GameState, moving_side: str) -> set[str]:
    """
    Get all hex IDs under Enemy ZOC for a given moving side.
    Enemy ZOC extends to all 6 hexes adjacent to qualifying enemy units.
    """
    enemy_side = Side.AXIS if moving_side == Side.ALLIED else Side.ALLIED
    ezoc = set()
    for unit in state.units.values():
        if unit.side != enemy_side:
            continue
        if unit.hex_id is None:
            continue
        if not _unit_exerts_zoc(unit):
            continue
        for neighbor in get_neighbors(unit.hex_id):
            ezoc.add(neighbor)
    return ezoc


def is_hex_in_ezoc(state: GameState, hex_id: str, moving_side: str) -> bool:
    """Check if a specific hex is in enemy ZOC for the moving side."""
    enemy_side = Side.AXIS if moving_side == Side.ALLIED else Side.ALLIED
    neighbors = get_neighbors(hex_id)
    for unit in state.units.values():
        if unit.side != enemy_side:
            continue
        if unit.hex_id is None:
            continue
        if not _unit_exerts_zoc(unit):
            continue
        if unit.hex_id in neighbors:
            return True
    return False


def hex_has_active_enemy(state: GameState, hex_id: str, moving_side: str) -> bool:
    """Check if hex contains active enemy units (ACTIVE or ENGAGED)."""
    hex_state = state.hexes.get(hex_id)
    if not hex_state:
        return False
    enemy_list = (hex_state.axis_unit_ids if moving_side == Side.ALLIED
                  else hex_state.allied_unit_ids)
    for uid in enemy_list:
        unit = state.units.get(uid)
        if unit and unit.status in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
            return True
    return False


# ════════════════════════════════════════
# MOVEMENT COST & STACKING
# ════════════════════════════════════════

@dataclass
class MovementCost:
    """Cost to enter a single hex."""
    hex_id: str
    terrain_cost: float       # Raw terrain CP cost
    effective_cost: float     # After road/track adjustments
    used_road: bool
    description: str = ""


def _get_hex_state(state: GameState, hex_id: str) -> HexState:
    """Get HexState, or synthesize a default for missing hexes."""
    if hex_id in state.hexes:
        return state.hexes[hex_id]
    # Missing hex → default clear terrain, no road
    return HexState(hex_id=hex_id, terrain=DEFAULT_TERRAIN, road=DEFAULT_ROAD)


def compute_hex_entry_cost(
    state: GameState,
    ref: ReferenceData,
    unit: Unit,
    from_hex: str,
    to_hex: str,
) -> MovementCost:
    """
    Compute the CP cost for a unit to enter a hex.
    1. If destination has road == ROAD → 0.5 CP
    2. Else look up terrain cost (motorized vs non-motorized)
    3. Track halving handled by ref.get_terrain_movement_cost
    """
    dest = _get_hex_state(state, to_hex)
    terrain_key = dest.terrain.lower().replace(" ", "_")

    # Check for road
    if dest.road == RoadType.ROAD:
        cost = ROAD_MOVEMENT_COST
        desc = f"{to_hex}: road → {cost} CP"
        # Minefields on roads still cost extra
        if (dest.real_minefield or dest.fake_minefield) and dest.minefield_owner != unit.side:
            cost += 2
            desc += " +minefield(2 CP)"
        return MovementCost(
            hex_id=to_hex,
            terrain_cost=cost,
            effective_cost=cost,
            used_road=True,
            description=desc,
        )

    # Check for railroad (non-motorized only — motorized use normal terrain)
    if dest.road == RoadType.RAILROAD and dest.railroad_operational and not unit.is_motorized:
        cost = 0.25
        desc = f"{to_hex}: railroad → {cost} CP"
        if (dest.real_minefield or dest.fake_minefield) and dest.minefield_owner != unit.side:
            cost += 2
            desc += " +minefield(2 CP)"
        return MovementCost(
            hex_id=to_hex,
            terrain_cost=cost,
            effective_cost=cost,
            used_road=True,
            description=desc,
        )

    # Check for track
    has_track = (dest.road == RoadType.TRACK)

    # Look up terrain cost
    terrain_cost = ref.get_terrain_movement_cost(
        terrain_key, unit.is_motorized, has_track=has_track
    )
    desc = f"{to_hex}: {terrain_key}"
    if has_track:
        desc += "+track"
    desc += f" → {terrain_cost} CP"

    # Minefield surcharge for enemy minefields
    if (dest.real_minefield or dest.fake_minefield) and dest.minefield_owner != unit.side:
        terrain_cost += 2
        desc += " +minefield(2 CP)"

    return MovementCost(
        hex_id=to_hex,
        terrain_cost=terrain_cost,
        effective_cost=terrain_cost,
        used_road=False,
        description=desc,
    )


def compute_stacking_in_hex(state: GameState, hex_id: str, side: str) -> int:
    """Compute total stacking points for a side in a hex."""
    total = 0
    for unit in state.units.values():
        if unit.hex_id == hex_id and unit.side == side:
            total += unit.stacking_points
    return total


def check_stacking(
    state: GameState,
    ref: ReferenceData,
    hex_id: str,
    side: str,
    additional_sp: int = 0,
) -> tuple[bool, int, Optional[int]]:
    """
    Check if adding additional_sp to a hex would violate stacking.
    Returns (ok, current_sp, limit). limit=None means unlimited.
    """
    hex_state = _get_hex_state(state, hex_id)
    terrain_key = hex_state.terrain.lower().replace(" ", "_")
    limit = ref.get_stacking_limit(terrain_key)
    current = compute_stacking_in_hex(state, hex_id, side)

    if limit is None:
        return (True, current, None)

    ok = (current + additional_sp) <= limit
    return (ok, current, limit)


# ════════════════════════════════════════
# CONTACT / ENGAGED MANAGEMENT
# ════════════════════════════════════════

def break_contact(state: GameState, unit_id: str) -> tuple[bool, str]:
    """
    Break contact costs 2 CP. Unit must be in contact.
    Returns (success, description).
    """
    unit = state.units.get(unit_id)
    if not unit:
        return (False, f"Unit {unit_id} not found")
    if not unit.is_in_contact:
        return (False, f"{unit.name} is not in contact")

    budget = unit.max_cpa_this_stage
    remaining = budget - unit.current_cpa_spent
    if remaining < BREAK_CONTACT_COST:
        return (False, f"{unit.name} needs {BREAK_CONTACT_COST} CP to break contact, "
                       f"only {remaining} remaining")

    unit.current_cpa_spent += BREAK_CONTACT_COST
    unit.is_in_contact = False
    state.log_event("break_contact", f"{unit.name} breaks contact (-{BREAK_CONTACT_COST} CP)",
                    unit_id=unit_id)
    return (True, f"{unit.name} breaks contact (-{BREAK_CONTACT_COST} CP)")


def break_engaged(state: GameState, unit_id: str) -> tuple[bool, str]:
    """
    Break engaged status costs 4 CP. Unit must be ENGAGED.
    Returns (success, description).
    """
    unit = state.units.get(unit_id)
    if not unit:
        return (False, f"Unit {unit_id} not found")
    if unit.status != UnitStatus.ENGAGED:
        return (False, f"{unit.name} is not engaged (status: {unit.status})")

    budget = unit.max_cpa_this_stage
    remaining = budget - unit.current_cpa_spent
    if remaining < BREAK_ENGAGED_COST:
        return (False, f"{unit.name} needs {BREAK_ENGAGED_COST} CP to break engaged, "
                       f"only {remaining} remaining")

    unit.current_cpa_spent += BREAK_ENGAGED_COST
    unit.status = UnitStatus.ACTIVE
    state.log_event("break_engaged", f"{unit.name} breaks engaged (-{BREAK_ENGAGED_COST} CP)",
                    unit_id=unit_id)
    return (True, f"{unit.name} breaks engaged status (-{BREAK_ENGAGED_COST} CP)")


def auto_clear_contact(state: GameState, unit_id: str) -> bool:
    """
    If a unit is in contact but no longer in EZOC, clear contact for free.
    Returns True if contact was cleared.
    """
    unit = state.units.get(unit_id)
    if not unit or not unit.is_in_contact or not unit.hex_id:
        return False

    if not is_hex_in_ezoc(state, unit.hex_id, unit.side):
        unit.is_in_contact = False
        return True
    return False


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class MoveValidation:
    """Result of validating a proposed move."""
    is_valid: bool
    unit_id: str
    path: list[str]
    total_cp_cost: float = 0.0
    cp_available: float = 0.0
    enters_ezoc: bool = False
    ezoc_hex: Optional[str] = None
    violates_stacking: bool = False
    blocked_reason: Optional[str] = None
    hex_costs: list[MovementCost] = field(default_factory=list)


@dataclass
class MoveResult:
    """Result of executing a move."""
    success: bool
    unit_id: str
    from_hex: Optional[str] = None
    to_hex: Optional[str] = None
    path_taken: list[str] = field(default_factory=list)
    total_cp_spent: float = 0.0
    contact_gained: bool = False
    contact_broken: bool = False
    description: str = ""


@dataclass
class ReactionMoveResult:
    """Result of a reaction movement attempt."""
    success: bool
    unit_id: str
    eligible: bool = False
    prevented: bool = False
    prevention_roll: Optional[int] = None
    move_result: Optional[MoveResult] = None
    blocked_reason: Optional[str] = None
    description: str = ""


# ════════════════════════════════════════
# VALIDATION
# ════════════════════════════════════════

def validate_move(
    state: GameState,
    ref: ReferenceData,
    unit_id: str,
    path: list[str],
) -> MoveValidation:
    """
    Validate a proposed movement path.

    Checks:
    1. Unit exists, is on-map, ACTIVE or ENGAGED
    2. Path starts at unit's current hex, each step adjacent
    3. Pre-move costs: 4 CP if ENGAGED, 2 CP if in contact + still in EZOC
    4. Cumulative CP cost doesn't exceed budget
    5. EZOC entry = must be last hex in path
    6. Stacking limit at destination
    """
    unit = state.units.get(unit_id)

    # 1. Unit checks
    if not unit:
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason=f"Unit {unit_id} not found")
    if unit.hex_id is None:
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason=f"{unit.name} is not on the map")
    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason=f"{unit.name} status is {unit.status}, "
                                             f"must be ACTIVE or ENGAGED to move")

    # Cohesion block: cannot move at -26 or worse
    if unit.cohesion <= -26:
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason=f"{unit.name} cannot move: cohesion {unit.cohesion} (blocked at -26)")

    # SGSU airfield restriction
    if unit.unit_class == UnitClass.SGSU:
        dest_hex = _get_hex_state(state, path[-1])
        if not dest_hex.has_airfield and not dest_hex.has_air_landing_strip:
            return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                                  blocked_reason=f"SGSUs can only move to airfield/landing strip hexes")

    # 2. Path adjacency
    if len(path) < 2:
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason="Path must have at least 2 hexes "
                                             "(origin + destination)")
    if path[0] != unit.hex_id:
        return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                              blocked_reason=f"Path starts at {path[0]} but unit is at "
                                             f"{unit.hex_id}")
    for i in range(len(path) - 1):
        if not are_adjacent(path[i], path[i + 1]):
            return MoveValidation(is_valid=False, unit_id=unit_id, path=path,
                                  blocked_reason=f"Hexes {path[i]} and {path[i+1]} "
                                                 f"are not adjacent (step {i+1})")

    # 2b. No enemy units at destination
    dest_hex = path[-1]
    if hex_has_active_enemy(state, dest_hex, unit.side):
        return MoveValidation(
            is_valid=False, unit_id=unit_id, path=path,
            blocked_reason=f"Destination {dest_hex} occupied by enemy units — "
                           f"use close_assault to attack")

    # 2c. No enemy units on intermediate hexes in path
    for i in range(1, len(path) - 1):
        if hex_has_active_enemy(state, path[i], unit.side):
            return MoveValidation(
                is_valid=False, unit_id=unit_id, path=path,
                blocked_reason=f"Path passes through enemy-occupied hex {path[i]}")

    # 3. Pre-move costs
    budget = unit.max_cpa_this_stage
    pre_cost = 0.0
    if unit.status == UnitStatus.ENGAGED:
        pre_cost += BREAK_ENGAGED_COST
    if unit.is_in_contact and is_hex_in_ezoc(state, unit.hex_id, unit.side):
        pre_cost += BREAK_CONTACT_COST

    # 4. Cumulative movement cost
    hex_costs = []
    move_cost = 0.0
    for i in range(1, len(path)):
        cost = compute_hex_entry_cost(state, ref, unit, path[i - 1], path[i])
        hex_costs.append(cost)
        move_cost += cost.effective_cost

    total_cost = pre_cost + move_cost
    cp_available = budget - unit.current_cpa_spent

    if total_cost > cp_available:
        return MoveValidation(
            is_valid=False, unit_id=unit_id, path=path,
            total_cp_cost=total_cost, cp_available=cp_available,
            hex_costs=hex_costs,
            blocked_reason=f"Insufficient CPA: need {total_cost}, have {cp_available}"
        )

    # 5. EZOC entry must be last hex in path
    ezoc_hexes = get_ezoc_hexes(state, unit.side)
    enters_ezoc = False
    ezoc_hex = None
    for i in range(1, len(path)):
        if path[i] in ezoc_hexes:
            enters_ezoc = True
            ezoc_hex = path[i]
            if i < len(path) - 1:
                return MoveValidation(
                    is_valid=False, unit_id=unit_id, path=path,
                    total_cp_cost=total_cost, cp_available=cp_available,
                    enters_ezoc=True, ezoc_hex=path[i],
                    hex_costs=hex_costs,
                    blocked_reason=f"EZOC entry at {path[i]} must be the last hex in path"
                )

    # 6. Stacking at destination
    dest = path[-1]
    ok, current, limit = check_stacking(state, ref, dest, unit.side, unit.stacking_points)
    if not ok:
        return MoveValidation(
            is_valid=False, unit_id=unit_id, path=path,
            total_cp_cost=total_cost, cp_available=cp_available,
            enters_ezoc=enters_ezoc, ezoc_hex=ezoc_hex,
            violates_stacking=True,
            hex_costs=hex_costs,
            blocked_reason=f"Stacking violation at {dest}: "
                           f"{current}+{unit.stacking_points} SP exceeds limit of {limit}"
        )

    return MoveValidation(
        is_valid=True, unit_id=unit_id, path=path,
        total_cp_cost=total_cost, cp_available=cp_available,
        enters_ezoc=enters_ezoc, ezoc_hex=ezoc_hex,
        hex_costs=hex_costs,
    )


# ════════════════════════════════════════
# EXECUTION
# ════════════════════════════════════════

def execute_move(
    state: GameState,
    ref: ReferenceData,
    unit_id: str,
    path: list[str],
) -> MoveResult:
    """
    Execute a movement after validation.
    Mutates state: updates unit hex, CPA spent, hex unit lists, contact status.
    """
    validation = validate_move(state, ref, unit_id, path)
    if not validation.is_valid:
        return MoveResult(
            success=False, unit_id=unit_id,
            description=f"Move invalid: {validation.blocked_reason}"
        )

    unit = state.units[unit_id]
    from_hex = unit.hex_id
    to_hex = path[-1]
    side_list_attr = "allied_unit_ids" if unit.side == Side.ALLIED else "axis_unit_ids"

    # Handle pre-move status changes
    contact_broken = False
    if unit.status == UnitStatus.ENGAGED:
        unit.status = UnitStatus.ACTIVE
        unit.current_cpa_spent += BREAK_ENGAGED_COST
    if unit.is_in_contact and is_hex_in_ezoc(state, unit.hex_id, unit.side):
        unit.is_in_contact = False
        unit.current_cpa_spent += BREAK_CONTACT_COST
        contact_broken = True

    # Record terrain types traversed for breakdown checks
    for hex_id_in_path in path[1:]:
        hex_st = _get_hex_state(state, hex_id_in_path)
        unit.terrains_traversed_this_stage.append(hex_st.terrain)

    # Spend movement CPA
    move_cp = sum(c.effective_cost for c in validation.hex_costs)
    unit.current_cpa_spent += int(move_cp) if move_cp == int(move_cp) else move_cp
    # Normalize to int if whole number
    if isinstance(unit.current_cpa_spent, float) and unit.current_cpa_spent == int(unit.current_cpa_spent):
        unit.current_cpa_spent = int(unit.current_cpa_spent)

    # Update hex unit lists — remove from origin
    if from_hex and from_hex in state.hexes:
        hex_units = getattr(state.hexes[from_hex], side_list_attr)
        if unit_id in hex_units:
            hex_units.remove(unit_id)

    # Update unit location
    unit.hex_id = to_hex
    unit.turns_in_position = 0

    # Add to destination hex (create HexState if needed)
    if to_hex not in state.hexes:
        state.hexes[to_hex] = HexState(
            hex_id=to_hex, terrain=DEFAULT_TERRAIN, road=DEFAULT_ROAD
        )
    dest_units = getattr(state.hexes[to_hex], side_list_attr)
    if unit_id not in dest_units:
        dest_units.append(unit_id)

    # Capture enemy supply dumps if no enemy units defend the hex
    enemy_side = Side.AXIS if unit.side == Side.ALLIED else Side.ALLIED
    enemy_list_attr = "axis_unit_ids" if enemy_side == Side.AXIS else "allied_unit_ids"
    enemy_ids = getattr(state.hexes[to_hex], enemy_list_attr)
    enemy_present = any(
        state.units.get(uid) and state.units[uid].status not in (
            UnitStatus.DESTROYED, UnitStatus.SURRENDERED, UnitStatus.WITHDRAWN)
        for uid in enemy_ids
    )
    if not enemy_present:
        for dump in state.hexes[to_hex].supply_dumps:
            if dump.side == enemy_side:
                old_side = dump.side
                dump.side = unit.side
                cap_desc = (
                    f"{unit.name} captures {old_side} dump '{dump.id}' at {to_hex}"
                )
                state.log_event(
                    "dump_captured", cap_desc,
                    unit_id=unit_id, dump_id=dump.id, hex_id=to_hex,
                )

    # Resolve minefield entry (enemy minefields only)
    minefield_result = None
    dest_state = state.hexes.get(to_hex)
    if dest_state and (dest_state.real_minefield or dest_state.fake_minefield) and dest_state.minefield_owner != unit.side:
        from cna_engine.engine.minefields import resolve_minefield_entry
        minefield_result = resolve_minefield_entry(state, unit_id, to_hex)

    # Set contact if entering EZOC
    contact_gained = False
    if validation.enters_ezoc:
        unit.is_in_contact = True
        contact_gained = True

    # Log event
    desc = f"{unit.name} moves {from_hex} → {to_hex} ({validation.total_cp_cost} CP)"
    if contact_gained:
        desc += " [enters EZOC — IN CONTACT]"
    if contact_broken:
        desc += " [broke contact]"
    if minefield_result:
        desc += f" [{minefield_result.description}]"
    state.log_event("movement", desc, unit_id=unit_id,
                    from_hex=from_hex, to_hex=to_hex,
                    path=list(path) if path else [])

    # Auto-consume fuel for motorized units
    if unit.is_motorized:
        fuel_rate = _MOTORIZATION_FUEL_RATE.get(unit.motorization, 1)
        if fuel_rate > 0:
            from cna_engine.engine.supply import consume_fuel
            consume_fuel(state, ref, unit_id, validation.total_cp_cost, fuel_rate)

    return MoveResult(
        success=True,
        unit_id=unit_id,
        from_hex=from_hex,
        to_hex=to_hex,
        path_taken=path,
        total_cp_spent=validation.total_cp_cost,
        contact_gained=contact_gained,
        contact_broken=contact_broken,
        description=desc,
    )


# ════════════════════════════════════════
# REACTION MOVEMENT
# ════════════════════════════════════════

def check_reaction_eligibility(state: GameState, unit_id: str) -> tuple[bool, str]:
    """
    Check if a unit is eligible for reaction movement.
    Must be: motorized, not in contact, not engaged, has CPA remaining.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return (False, f"Unit {unit_id} not found")
    if not unit.is_motorized:
        return (False, f"{unit.name} is not motorized")
    if unit.is_in_contact:
        return (False, f"{unit.name} is in contact")
    if unit.status == UnitStatus.ENGAGED:
        return (False, f"{unit.name} is engaged")
    if unit.status != UnitStatus.ACTIVE:
        return (False, f"{unit.name} status is {unit.status}, must be ACTIVE")
    remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
    if remaining <= 0:
        return (False, f"{unit.name} has no CPA remaining")
    return (True, f"{unit.name} eligible for reaction movement")


def attempt_reaction_move(
    state: GameState,
    ref: ReferenceData,
    unit_id: str,
    path: list[str],
    prevention_roll: Optional[int] = None,
) -> ReactionMoveResult:
    """
    Attempt a reaction move. Enemy rolls d6 to prevent.
    Prevention: roll of 1-2 prevents the reaction (simplified).
    prevention_roll can be overridden for testing.
    """
    eligible, reason = check_reaction_eligibility(state, unit_id)
    if not eligible:
        return ReactionMoveResult(
            success=False, unit_id=unit_id, eligible=False,
            blocked_reason=reason,
            description=f"Reaction move failed: {reason}"
        )

    # Prevention roll
    roll = prevention_roll if prevention_roll is not None else random.randint(1, 6)
    prevented = (roll <= 2)

    if prevented:
        unit = state.units[unit_id]
        desc = f"{unit.name} reaction move PREVENTED (roll: {roll})"
        state.log_event("reaction_prevented", desc, unit_id=unit_id)
        return ReactionMoveResult(
            success=False, unit_id=unit_id, eligible=True,
            prevented=True, prevention_roll=roll,
            description=desc,
        )

    # Execute the move
    move_result = execute_move(state, ref, unit_id, path)
    unit = state.units[unit_id]
    desc = f"{unit.name} reaction move "
    if move_result.success:
        desc += f"succeeds (roll: {roll}): {move_result.description}"
    else:
        desc += f"failed execution: {move_result.description}"

    return ReactionMoveResult(
        success=move_result.success,
        unit_id=unit_id,
        eligible=True,
        prevented=False,
        prevention_roll=roll,
        move_result=move_result,
        description=desc,
    )
