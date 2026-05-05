"""
CNA Engine — Patrol Phase Module
Handles the PATROL phase of each OpStage.

Light/recon units conduct patrol movement after the main movement phase.
Patrol movement is limited range, free (no CPA cost), and can trigger
encounters with enemy patrols.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, UnitStatus
from cna_engine.engine.combat import roll_d6
from cna_engine.engine.movement import get_neighbors


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Patrol range (max hexes a patrol can move)
PATROL_RANGE = 3

# Only these unit classes can conduct patrols
PATROL_ELIGIBLE_CLASSES = {"recon", "armor"}

# Minimum strength to patrol
MIN_PATROL_STRENGTH = 2

# Patrol encounter: d6 roll threshold to spot enemy patrol
PATROL_ENCOUNTER_THRESHOLD = 3  # Roll ≤ 3 to spot

# CPA cost: patrol movement is FREE (0 CPA)
PATROL_CPA_COST = 0


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class PatrolResult:
    """Result of a single patrol mission."""
    success: bool
    unit_id: str
    path: list[str] = field(default_factory=list)
    hexes_sighted: list[str] = field(default_factory=list)
    encounters: list[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class PatrolPhaseResult:
    """Aggregated result of the Patrol phase."""
    game_turn: int
    op_stage: int
    side: str
    patrols_sent: int = 0
    hexes_sighted: int = 0
    encounters: int = 0
    results: list[PatrolResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# PATROL ELIGIBILITY
# ════════════════════════════════════════

def can_patrol(state: GameState, unit_id: str) -> tuple[bool, str]:
    """Check if a unit is eligible for patrol. Returns (eligible, reason)."""
    unit = state.units.get(unit_id)
    if not unit:
        return False, "Unit not found"

    if unit.status not in (UnitStatus.ACTIVE,):
        return False, f"Unit status is {unit.status}"

    if unit.unit_class not in PATROL_ELIGIBLE_CLASSES:
        return False, f"Unit class {unit.unit_class} cannot patrol"

    if not unit.is_motorized:
        return False, "Unit is not motorized"

    if unit.current_strength.total < MIN_PATROL_STRENGTH:
        return False, f"Insufficient strength ({unit.current_strength.total} < {MIN_PATROL_STRENGTH})"

    if not unit.hex_id:
        return False, "Unit is not on the map"

    if unit.is_in_contact:
        return False, "Unit is in contact with enemy"

    return True, "Eligible"


# ════════════════════════════════════════
# PATROL EXECUTION
# ════════════════════════════════════════

def execute_patrol(
    state: GameState,
    unit_id: str,
    target_hex: str,
    encounter_roll: Optional[int] = None,
) -> PatrolResult:
    """
    Execute a patrol mission. Unit moves to target hex (within patrol range)
    and sights hexes along the way. May encounter enemy patrols.
    The unit returns to its original hex after patrolling.
    """
    unit = state.units.get(unit_id)
    eligible, reason = can_patrol(state, unit_id)
    if not eligible:
        return PatrolResult(
            success=False, unit_id=unit_id,
            blocked_reason=reason,
            description=f"Patrol failed: {reason}",
        )

    # Path validation: target must be reachable within PATROL_RANGE
    # Simple BFS to find path
    start = unit.hex_id
    if start == target_hex:
        return PatrolResult(
            success=False, unit_id=unit_id,
            blocked_reason="Target is same as current hex",
            description="Patrol failed: already at target",
        )

    # BFS pathfinding
    queue = [(start, [start])]
    visited = {start}
    found_path = None

    while queue:
        current, path = queue.pop(0)
        if len(path) - 1 > PATROL_RANGE:
            break
        if current == target_hex:
            found_path = path
            break
        for neighbor in get_neighbors(current):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    if not found_path:
        return PatrolResult(
            success=False, unit_id=unit_id,
            blocked_reason=f"Target {target_hex} not reachable within {PATROL_RANGE} hexes",
            description=f"Patrol failed: target out of range",
        )

    # Sight hexes along the path and at destination
    sighted = set()
    sighted_attr = "allied_sighted" if unit.side == Side.ALLIED else "axis_sighted"
    enemy_attr = "axis_unit_ids" if unit.side == Side.ALLIED else "allied_unit_ids"

    encounters = []
    for hex_id in found_path:
        sighted.add(hex_id)
        # Also sight adjacent hexes
        for adj in get_neighbors(hex_id):
            sighted.add(adj)

        # Mark as sighted
        hex_state = state.hexes.get(hex_id)
        if hex_state:
            setattr(hex_state, sighted_attr, True)

    # Check for encounters at target and along path
    for hex_id in found_path[1:]:  # Skip start hex
        hex_state = state.hexes.get(hex_id)
        if hex_state:
            for enemy_uid in getattr(hex_state, enemy_attr):
                enemy = state.units.get(enemy_uid)
                if enemy and enemy.status not in (UnitStatus.DESTROYED,
                                                   UnitStatus.WITHDRAWN):
                    roll = encounter_roll if encounter_roll is not None else roll_d6()
                    if roll <= PATROL_ENCOUNTER_THRESHOLD:
                        encounters.append(enemy_uid)

    # Mark all sighted hexes
    for hex_id in sighted:
        hex_state = state.hexes.get(hex_id)
        if hex_state:
            setattr(hex_state, sighted_attr, True)

    # Unit stays at original hex (patrol is round-trip)
    desc = (f"{unit.name} patrols to {target_hex} ({len(found_path)-1} hexes): "
            f"{len(sighted)} hexes sighted")
    if encounters:
        desc += f", {len(encounters)} encounters: {encounters}"

    state.log_event("patrol", desc, unit_id=unit_id, target=target_hex)

    return PatrolResult(
        success=True, unit_id=unit_id,
        path=found_path,
        hexes_sighted=sorted(sighted),
        encounters=encounters,
        description=desc,
    )


# ════════════════════════════════════════
# PATROL PHASE EXECUTION
# ════════════════════════════════════════

def execute_patrol_phase(
    state: GameState,
    side: str,
    patrol_orders: Optional[dict[str, str]] = None,
) -> PatrolPhaseResult:
    """
    Execute the Patrol phase for one side.
    patrol_orders: {unit_id: target_hex} — if None, eligible units
    patrol toward nearest enemy.
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage
    orders = patrol_orders or {}
    results = []
    total_sighted = set()
    total_encounters = 0

    for uid, target in orders.items():
        result = execute_patrol(state, uid, target)
        results.append(result)
        if result.success:
            total_sighted.update(result.hexes_sighted)
            total_encounters += len(result.encounters)

    desc = (f"GT{gt} OpStage {op} Patrol ({side}): "
            f"{len(results)} patrols, {len(total_sighted)} hexes sighted, "
            f"{total_encounters} encounters")

    state.log_event("patrol_phase", desc, side=side)

    return PatrolPhaseResult(
        game_turn=gt, op_stage=op, side=side,
        patrols_sent=len(results),
        hexes_sighted=len(total_sighted),
        encounters=total_encounters,
        results=results,
        description=desc,
    )
