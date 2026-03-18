"""
CNA Engine — Supply Line Tracing Module
BFS-based path tracing from unit to supply source through hexes.
Checks for enemy ZOC interdiction along the supply path.
Determines supply line status: connected, interdicted, or severed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

from cna_engine.models.game_state import GameState, Unit, HexState
from cna_engine.models.enums import Side, UnitStatus
from cna_engine.engine.movement import (
    get_neighbors, is_hex_in_ezoc, get_ezoc_hexes,
)


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Supply line status levels
SUPPLY_CONNECTED = "connected"          # Clear path to source
SUPPLY_INTERDICTED = "interdicted"      # Path exists but passes through EZOC
SUPPLY_SEVERED = "severed"              # No path to source

# Maximum BFS distance (hexes) for supply line tracing
# Full 5-section map: Alexandria→Tripoli = 131 hexes, longest supply line ~80
MAX_SUPPLY_TRACE_DISTANCE = 100

# Effects of supply line status
INTERDICTED_CPA_PENALTY = 2     # CPA reduced when interdicted
SEVERED_CPA_PENALTY = 4         # CPA reduced when severed
SEVERED_COMBAT_SHIFT = -1       # Column shift in combat when severed


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class SupplyLineResult:
    """Result of tracing a supply line for a single unit."""
    unit_id: str
    status: str                     # SUPPLY_CONNECTED, INTERDICTED, SEVERED
    source_hex: Optional[str] = None  # Hex of supply source reached
    source_type: Optional[str] = None # "port", "dump", "depot", "edge"
    path: list[str] = field(default_factory=list)
    path_length: int = 0
    ezoc_hexes_in_path: list[str] = field(default_factory=list)
    enemy_units_blocking: list[str] = field(default_factory=list)
    cpa_penalty: int = 0
    combat_shift: int = 0
    description: str = ""


@dataclass
class SupplyLineCheckResult:
    """Aggregated supply line check for all units of a side."""
    side: str
    total_units: int = 0
    connected: int = 0
    interdicted: int = 0
    severed: int = 0
    unit_results: list[SupplyLineResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# SUPPLY SOURCE IDENTIFICATION
# ════════════════════════════════════════

def _get_supply_sources(state: GameState, side: str) -> set[str]:
    """
    Get all hex IDs that are supply sources for a given side.
    Sources include: ports, hexes with real supply dumps, map-edge depots.
    """
    sources = set()

    for hex_id, hex_state in state.hexes.items():
        # Ports controlled by the side
        if hex_state.is_port:
            # Check if port has friendly units and no enemy units
            friendly = (hex_state.allied_unit_ids if side == Side.ALLIED
                        else hex_state.axis_unit_ids)
            enemy = (hex_state.axis_unit_ids if side == Side.ALLIED
                     else hex_state.allied_unit_ids)
            if friendly or not enemy:
                sources.add(hex_id)

        # Hexes with real supply dumps belonging to this side
        for dump in hex_state.supply_dumps:
            if dump.is_real and dump.side == side:
                total_supply = dump.fuel + dump.water + dump.ammo + dump.stores
                if total_supply > 0:
                    sources.add(hex_id)
                    break

    return sources


def _is_hex_blocked(state: GameState, hex_id: str, side: str) -> bool:
    """Check if a hex is blocked by enemy units (occupied, not just EZOC)."""
    hex_state = state.hexes.get(hex_id)
    if not hex_state:
        return False

    enemy_list = (hex_state.axis_unit_ids if side == Side.ALLIED
                  else hex_state.allied_unit_ids)

    for uid in enemy_list:
        unit = state.units.get(uid)
        if unit and unit.status in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
            return True
    return False


# ════════════════════════════════════════
# BFS SUPPLY LINE TRACING
# ════════════════════════════════════════

def trace_supply_line(
    state: GameState,
    unit_id: str,
    max_distance: int = MAX_SUPPLY_TRACE_DISTANCE,
) -> SupplyLineResult:
    """
    Trace a supply line from a unit back to the nearest supply source.
    Uses BFS to find the shortest path. Tracks EZOC hexes along the path.

    Returns:
    - CONNECTED if a clear path exists (no EZOC hexes)
    - INTERDICTED if a path exists but passes through enemy ZOC
    - SEVERED if no path exists within max_distance
    """
    unit = state.units.get(unit_id)
    if not unit:
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_SEVERED,
            description=f"Unit {unit_id} not found",
        )

    if not unit.hex_id:
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_SEVERED,
            description=f"{unit.name} is not on the map",
        )

    sources = _get_supply_sources(state, unit.side)
    if not sources:
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_SEVERED,
            description=f"No supply sources for {unit.side}",
            cpa_penalty=SEVERED_CPA_PENALTY,
            combat_shift=SEVERED_COMBAT_SHIFT,
        )

    # Check if unit is already at a supply source
    if unit.hex_id in sources:
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_CONNECTED,
            source_hex=unit.hex_id, source_type="dump",
            path=[unit.hex_id], path_length=0,
            description=f"{unit.name} at supply source {unit.hex_id}",
        )

    # BFS from unit's hex outward
    ezoc_hexes = get_ezoc_hexes(state, unit.side)

    # Queue entries: (hex_id, path_so_far, ezoc_hexes_in_path)
    queue = deque()
    queue.append((unit.hex_id, [unit.hex_id], []))
    visited = {unit.hex_id}

    best_clean_path = None       # Path with no EZOC
    best_interdicted_path = None # Path through EZOC

    while queue:
        current, path, ezoc_in_path = queue.popleft()

        if len(path) > max_distance:
            continue

        for neighbor in get_neighbors(current):
            if neighbor in visited:
                continue
            visited.add(neighbor)

            # Skip hexes occupied by active enemy units
            if _is_hex_blocked(state, neighbor, unit.side):
                continue

            new_path = path + [neighbor]
            new_ezoc = list(ezoc_in_path)
            if neighbor in ezoc_hexes:
                new_ezoc.append(neighbor)

            # Check if we reached a supply source
            if neighbor in sources:
                if not new_ezoc and best_clean_path is None:
                    best_clean_path = (neighbor, new_path, new_ezoc)
                elif new_ezoc and best_interdicted_path is None:
                    best_interdicted_path = (neighbor, new_path, new_ezoc)

                # If we found a clean path, no need to continue
                if best_clean_path:
                    break
            else:
                queue.append((neighbor, new_path, new_ezoc))

        if best_clean_path:
            break

    # Determine result
    if best_clean_path:
        source_hex, path, ezoc_list = best_clean_path
        source_type = _identify_source_type(state, source_hex)
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_CONNECTED,
            source_hex=source_hex, source_type=source_type,
            path=path, path_length=len(path) - 1,
            description=f"{unit.name}: connected to {source_type} at {source_hex} "
                        f"({len(path)-1} hexes)",
        )

    if best_interdicted_path:
        source_hex, path, ezoc_list = best_interdicted_path
        source_type = _identify_source_type(state, source_hex)
        return SupplyLineResult(
            unit_id=unit_id, status=SUPPLY_INTERDICTED,
            source_hex=source_hex, source_type=source_type,
            path=path, path_length=len(path) - 1,
            ezoc_hexes_in_path=ezoc_list,
            cpa_penalty=INTERDICTED_CPA_PENALTY,
            description=f"{unit.name}: interdicted path to {source_type} at {source_hex} "
                        f"({len(path)-1} hexes, {len(ezoc_list)} EZOC hexes)",
        )

    # No path found
    return SupplyLineResult(
        unit_id=unit_id, status=SUPPLY_SEVERED,
        cpa_penalty=SEVERED_CPA_PENALTY,
        combat_shift=SEVERED_COMBAT_SHIFT,
        description=f"{unit.name}: supply line SEVERED (no path within {max_distance} hexes)",
    )


def _identify_source_type(state: GameState, hex_id: str) -> str:
    """Identify what kind of supply source a hex is."""
    hex_state = state.hexes.get(hex_id)
    if not hex_state:
        return "unknown"
    if hex_state.is_port:
        return "port"
    for dump in hex_state.supply_dumps:
        if dump.is_real:
            return "dump"
    return "depot"


# ════════════════════════════════════════
# BULK SUPPLY LINE CHECK
# ════════════════════════════════════════

def check_supply_lines(
    state: GameState,
    side: str,
    max_distance: int = MAX_SUPPLY_TRACE_DISTANCE,
) -> SupplyLineCheckResult:
    """
    Check supply lines for all active units of a side.
    Returns aggregated results.
    """
    results = []
    connected = 0
    interdicted = 0
    severed = 0

    for unit in state.units.values():
        if unit.side != side:
            continue
        if unit.status in (UnitStatus.DESTROYED, UnitStatus.SURRENDERED,
                           UnitStatus.WITHDRAWN, UnitStatus.NOT_YET_ARRIVED):
            continue
        if not unit.hex_id:
            continue

        result = trace_supply_line(state, unit.id, max_distance)
        results.append(result)

        if result.status == SUPPLY_CONNECTED:
            connected += 1
        elif result.status == SUPPLY_INTERDICTED:
            interdicted += 1
        else:
            severed += 1

    total = len(results)
    desc = (f"{side} supply lines: {total} units — "
            f"{connected} connected, {interdicted} interdicted, {severed} severed")
    state.log_event("supply_line_check", desc, side=side)

    return SupplyLineCheckResult(
        side=side,
        total_units=total,
        connected=connected,
        interdicted=interdicted,
        severed=severed,
        unit_results=results,
        description=desc,
    )
