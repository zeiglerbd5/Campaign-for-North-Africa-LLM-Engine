"""
CNA Engine — Vehicle Breakdown Module
Handles the BREAKDOWN segment within Movement & Combat.

Motorized/mechanized units risk vehicle breakdown when traversing
bad terrain (sand seas, rough, mountains, wadis, escarpments).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import UnitStatus
from cna_engine.engine.combat import roll_d6


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

BREAKDOWN_TERRAINS = frozenset({"sand_sea", "rough", "mountain", "wadi", "escarpment"})
BREAKDOWN_THRESHOLD = 5          # d6 >= 5 = breakdown (33% chance base)
BREAKDOWN_SP_LOSS = 1
BREAKDOWN_COHESION_PENALTY = -1
BREAKDOWN_MOTORIZATIONS = frozenset({"motorized", "mechanized", "historically_motorized"})


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class BreakdownResult:
    """Result of a breakdown check on a single unit."""
    unit_id: str
    checked: bool
    broke_down: bool
    bad_terrain_count: int = 0
    dice_roll: int = 0
    threshold: int = BREAKDOWN_THRESHOLD
    sp_lost: int = 0
    cohesion_penalty: int = 0
    description: str = ""


@dataclass
class BreakdownPhaseResult:
    """Aggregated result of the Breakdown segment for one side."""
    game_turn: int
    op_stage: int
    side: str
    units_checked: int = 0
    units_broke_down: int = 0
    total_sp_lost: int = 0
    results: list[BreakdownResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# SP LOSS HELPER
# ════════════════════════════════════════

_SP_CATEGORIES = ["armor", "infantry", "gun", "mg", "recon"]


def _breakdown_sp_loss(unit: Unit, sp_to_lose: int) -> int:
    """
    Remove SP from the unit, armor first, then largest category.
    Returns actual SP removed.
    """
    removed = 0
    remaining = sp_to_lose
    cs = unit.current_strength

    # Try armor first
    if cs.armor > 0 and remaining > 0:
        take = min(remaining, cs.armor)
        cs.armor -= take
        remaining -= take
        removed += take

    # Then largest remaining category
    while remaining > 0:
        cats = [(getattr(cs, cat), cat) for cat in _SP_CATEGORIES]
        cats.sort(key=lambda x: x[0], reverse=True)
        best_val, best_cat = cats[0]
        if best_val <= 0:
            break
        take = min(remaining, best_val)
        setattr(cs, best_cat, best_val - take)
        remaining -= take
        removed += take

    unit.losses_taken += removed
    if cs.total <= 0:
        unit.status = UnitStatus.DESTROYED
    return removed


# ════════════════════════════════════════
# CORE BREAKDOWN CHECK
# ════════════════════════════════════════

def check_breakdown(
    state: GameState,
    unit_id: str,
    terrains_traversed: list[str],
    dice_roll: Optional[int] = None,
) -> BreakdownResult:
    """
    Check if a motorized unit breaks down after traversing terrain.

    - Count bad terrain hexes traversed this stage
    - If 0 bad terrain → no check needed
    - Effective threshold = max(3, BREAKDOWN_THRESHOLD - bad_terrain_count // 3)
      (more bad terrain = lower threshold = higher risk)
    - Roll d6; if >= threshold → lose 1 SP (armor first), -1 cohesion
    """
    unit = state.units.get(unit_id)
    if not unit:
        return BreakdownResult(
            unit_id=unit_id, checked=False, broke_down=False,
            description=f"Unit {unit_id} not found",
        )

    # Count bad terrain hexes
    bad_count = sum(1 for t in terrains_traversed if t in BREAKDOWN_TERRAINS)
    if bad_count == 0:
        return BreakdownResult(
            unit_id=unit_id, checked=False, broke_down=False,
            bad_terrain_count=0,
            description=f"{unit.name}: no bad terrain traversed",
        )

    # Effective threshold: more bad terrain → lower threshold → easier to break down
    threshold = max(3, BREAKDOWN_THRESHOLD - bad_count // 3)

    roll = dice_roll if dice_roll is not None else roll_d6()
    broke_down = roll >= threshold

    sp_lost = 0
    cohesion_penalty = 0

    if broke_down:
        sp_lost = _breakdown_sp_loss(unit, BREAKDOWN_SP_LOSS)
        cohesion_penalty = BREAKDOWN_COHESION_PENALTY
        unit.cohesion += cohesion_penalty

        desc = (f"{unit.name} BREAKDOWN: roll={roll} (>={threshold}), "
                f"-{sp_lost} SP, {cohesion_penalty} cohesion "
                f"({bad_count} bad terrain hexes)")
        state.log_event("breakdown", desc, unit_id=unit_id,
                        roll=roll, threshold=threshold, sp_lost=sp_lost)
    else:
        desc = (f"{unit.name}: roll={roll} (<{threshold}), no breakdown "
                f"({bad_count} bad terrain hexes)")

    return BreakdownResult(
        unit_id=unit_id, checked=True, broke_down=broke_down,
        bad_terrain_count=bad_count, dice_roll=roll,
        threshold=threshold, sp_lost=sp_lost,
        cohesion_penalty=cohesion_penalty,
        description=desc,
    )


# ════════════════════════════════════════
# PHASE EXECUTOR
# ════════════════════════════════════════

def execute_breakdown_segment(
    state: GameState,
    side: str,
) -> BreakdownPhaseResult:
    """
    Execute the Breakdown segment for one side.
    Checks all motorized ACTIVE/ENGAGED units that traversed terrain this stage.
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage
    results = []
    units_broke = 0
    total_sp = 0

    for uid, unit in state.units.items():
        if unit.side != side:
            continue
        if unit.motorization not in BREAKDOWN_MOTORIZATIONS:
            continue
        if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
            continue

        terrains = getattr(unit, 'terrains_traversed_this_stage', [])
        if not terrains:
            continue

        result = check_breakdown(state, uid, terrains)
        if not result.checked:
            continue
        results.append(result)
        if result.broke_down:
            units_broke += 1
            total_sp += result.sp_lost

    desc = (f"GT{gt} OpStage {op} Breakdown ({side}): "
            f"{len(results)} checked, {units_broke} broke down, "
            f"-{total_sp} SP lost")

    return BreakdownPhaseResult(
        game_turn=gt, op_stage=op, side=side,
        units_checked=len(results),
        units_broke_down=units_broke,
        total_sp_lost=total_sp,
        results=results,
        description=desc,
    )
