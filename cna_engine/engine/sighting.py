"""
CNA Engine — Sighting & Fog of War Module
Ground-level sighting rules: visibility range by terrain, unit size,
and weather. Separate from air reconnaissance (handled in air.py).

Sighting determines what each side can see at the ground level.
Sighting flags expire at the end of each OpStage (handled by SoP).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, HexState, Unit
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, Weather,
)
from cna_engine.engine.movement import get_neighbors


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Base sighting range (hexes) by terrain of the OBSERVER's hex
BASE_SIGHTING_RANGE = {
    TerrainType.CLEAR: 3,
    TerrainType.COASTAL: 3,
    TerrainType.ROUGH: 2,
    TerrainType.ESCARPMENT: 4,    # Elevated terrain — better view
    TerrainType.MOUNTAIN: 4,
    TerrainType.SAND_SEA: 2,
    TerrainType.WADI: 1,
    TerrainType.OASIS: 2,
    TerrainType.BIR: 2,
    TerrainType.VILLAGE: 2,
    TerrainType.MAJOR_CITY: 2,
    TerrainType.SALT_MARSH: 2,
    TerrainType.SWAMP: 1,
    TerrainType.DELTA: 2,
    TerrainType.SEA: 3,
}

# Weather modifiers to sighting range
WEATHER_SIGHTING_MODIFIER = {
    Weather.CLEAR: 0,
    Weather.HOT: 0,         # Heat haze doesn't reduce in game terms
    Weather.MUD: -1,        # Reduced visibility
    Weather.SANDSTORM: -2,  # Severely reduced
}

# Minimum sighting range (always see own hex + adjacent)
MIN_SIGHTING_RANGE = 1

# Recon units get a bonus
RECON_SIGHTING_BONUS = 2

# Units in contact always sight the EZOC source
CONTACT_AUTO_SIGHT = True


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class SightingResult:
    """Result of a sighting check for a single unit."""
    unit_id: str
    side: str
    sighting_range: int
    hexes_sighted: list[str] = field(default_factory=list)
    enemies_spotted: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SightingPhaseResult:
    """Aggregated result of sighting for all units of a side."""
    side: str
    units_checked: int = 0
    total_hexes_sighted: int = 0
    total_enemies_spotted: int = 0
    results: list[SightingResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# SIGHTING RANGE CALCULATION
# ════════════════════════════════════════

def get_sighting_range(
    state: GameState,
    unit: Unit,
) -> int:
    """Calculate a unit's sighting range based on terrain, weather, and unit type."""
    if not unit.hex_id or unit.hex_id not in state.hexes:
        return 0

    hex_state = state.hexes[unit.hex_id]
    base_range = BASE_SIGHTING_RANGE.get(hex_state.terrain, 2)

    # Weather modifier
    weather_mod = WEATHER_SIGHTING_MODIFIER.get(
        state.turn.current_weather, 0)

    # Recon bonus
    recon_bonus = RECON_SIGHTING_BONUS if unit.unit_class == "recon" else 0

    total = base_range + weather_mod + recon_bonus
    return max(MIN_SIGHTING_RANGE, total)


# ════════════════════════════════════════
# SIGHTING CHECK
# ════════════════════════════════════════

def check_sighting_for_unit(
    state: GameState,
    unit_id: str,
) -> SightingResult:
    """
    Perform sighting check for a single unit.
    Uses BFS to find all hexes within sighting range, then marks
    them as sighted and identifies visible enemy units.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return SightingResult(unit_id=unit_id, side="", sighting_range=0,
                              description=f"Unit {unit_id} not found")

    if not unit.hex_id or unit.hex_id not in state.hexes:
        return SightingResult(unit_id=unit_id, side=unit.side, sighting_range=0,
                              description=f"Unit {unit_id} is not on the map")

    if unit.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN,
                       UnitStatus.NOT_YET_ARRIVED):
        return SightingResult(unit_id=unit_id, side=unit.side, sighting_range=0,
                              description=f"Unit {unit_id} cannot sight ({unit.status})")

    sight_range = get_sighting_range(state, unit)

    # BFS from unit's hex
    sighted_hexes = set()
    enemies_spotted = []
    queue = [(unit.hex_id, 0)]
    visited = {unit.hex_id}

    while queue:
        current, dist = queue.pop(0)
        sighted_hexes.add(current)

        if dist < sight_range:
            for neighbor in get_neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    # Terrain LOS blocking: rough/mountain/city reduce penetration
                    n_hex = state.hexes.get(neighbor)
                    if n_hex and n_hex.terrain in (TerrainType.MOUNTAIN,
                                                    TerrainType.MAJOR_CITY):
                        # These terrains block LOS — can sight INTO but not through
                        sighted_hexes.add(neighbor)
                        # Don't continue BFS beyond this hex
                    else:
                        queue.append((neighbor, dist + 1))

    # Mark hexes as sighted and find enemies
    sighted_attr = "allied_sighted" if unit.side == Side.ALLIED else "axis_sighted"
    enemy_attr = "axis_unit_ids" if unit.side == Side.ALLIED else "allied_unit_ids"

    for hex_id in sighted_hexes:
        hex_state = state.hexes.get(hex_id)
        if hex_state:
            setattr(hex_state, sighted_attr, True)
            # Check for enemy units
            for uid in getattr(hex_state, enemy_attr):
                enemy = state.units.get(uid)
                if enemy and enemy.status not in (UnitStatus.DESTROYED,
                                                   UnitStatus.WITHDRAWN):
                    if uid not in enemies_spotted:
                        enemies_spotted.append(uid)

    desc = (f"Unit {unit_id} sights {len(sighted_hexes)} hexes "
            f"(range {sight_range}), {len(enemies_spotted)} enemies spotted")

    return SightingResult(
        unit_id=unit_id, side=unit.side,
        sighting_range=sight_range,
        hexes_sighted=sorted(sighted_hexes),
        enemies_spotted=enemies_spotted,
        description=desc,
    )


# ════════════════════════════════════════
# BULK SIGHTING (PHASE-LEVEL)
# ════════════════════════════════════════

def execute_sighting_check(
    state: GameState,
    side: str,
) -> SightingPhaseResult:
    """
    Perform sighting checks for all active units of a side.
    Called during the per-side portion of each OpStage.
    """
    results = []
    total_hexes = set()
    total_enemies = set()

    for uid, unit in state.units.items():
        if unit.side != side:
            continue
        if unit.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN,
                           UnitStatus.NOT_YET_ARRIVED, UnitStatus.SURRENDERED):
            continue

        result = check_sighting_for_unit(state, uid)
        results.append(result)
        total_hexes.update(result.hexes_sighted)
        total_enemies.update(result.enemies_spotted)

    desc = (f"{side} sighting: {len(results)} units checked, "
            f"{len(total_hexes)} hexes sighted, {len(total_enemies)} enemies spotted")
    state.log_event("sighting", desc, side=side)

    return SightingPhaseResult(
        side=side,
        units_checked=len(results),
        total_hexes_sighted=len(total_hexes),
        total_enemies_spotted=len(total_enemies),
        results=results,
        description=desc,
    )
