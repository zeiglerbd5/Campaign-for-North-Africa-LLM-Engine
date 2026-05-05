"""
CNA Engine — Vehicle Repair Module
Handles the VEHICLE_REPAIR phase of each OpStage.

Broken-down vehicles can be repaired if the unit is at or adjacent to
a repair facility, or by field repair (slower, limited).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import UnitStatus
from cna_engine.engine.combat import roll_d6
from cna_engine.engine.movement import get_neighbors


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Repair facility: d6 ≤ threshold to repair one broken-down step
FACILITY_REPAIR_THRESHOLD = 4     # Roll ≤ 4 at repair facility
FIELD_REPAIR_THRESHOLD = 2        # Roll ≤ 2 for field repair (no facility)

# CPA cost for attempting repair
REPAIR_CPA_COST = 1

# Only mechanized/motorized units have vehicles to repair
REPAIRABLE_MOTORIZATIONS = {"motorized", "mechanized", "historically_motorized"}

# Max repair attempts per unit per OpStage
MAX_REPAIR_ATTEMPTS = 1


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class RepairResult:
    """Result of a repair attempt on a single unit."""
    success: bool
    unit_id: str
    has_facility: bool
    threshold: int
    dice_roll: int
    strength_restored: int = 0
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class RepairPhaseResult:
    """Aggregated result of the Vehicle Repair phase."""
    game_turn: int
    op_stage: int
    side: str
    units_attempted: int = 0
    units_repaired: int = 0
    total_strength_restored: int = 0
    results: list[RepairResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# REPAIR FACILITY CHECK
# ════════════════════════════════════════

def _has_repair_facility(state: GameState, unit: Unit) -> bool:
    """Check if unit is at or adjacent to a repair facility."""
    if not unit.hex_id:
        return False

    # Check own hex
    hex_state = state.hexes.get(unit.hex_id)
    if hex_state and hex_state.has_repair_facility:
        return True

    # Check adjacent hexes
    for neighbor_id in get_neighbors(unit.hex_id):
        n_hex = state.hexes.get(neighbor_id)
        if n_hex and n_hex.has_repair_facility:
            return True

    return False


# ════════════════════════════════════════
# REPAIR ATTEMPT
# ════════════════════════════════════════

def attempt_repair(
    state: GameState,
    unit_id: str,
    dice_roll: Optional[int] = None,
) -> RepairResult:
    """
    Attempt to repair a broken-down unit.
    Unit must be motorized/mechanized and have taken losses.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return RepairResult(
            success=False, unit_id=unit_id, has_facility=False,
            threshold=0, dice_roll=0,
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Repair failed: unit not found",
        )

    # Must be motorized type
    if unit.motorization not in REPAIRABLE_MOTORIZATIONS:
        return RepairResult(
            success=False, unit_id=unit_id, has_facility=False,
            threshold=0, dice_roll=0,
            blocked_reason=f"Unit {unit_id} is {unit.motorization} (not motorized/mechanized)",
            description=f"Repair failed: not a vehicle unit",
        )

    # Must have taken losses (current < TOE)
    current_total = unit.current_strength.total
    toe_total = unit.toe_strength.total
    if current_total >= toe_total:
        return RepairResult(
            success=False, unit_id=unit_id, has_facility=False,
            threshold=0, dice_roll=0,
            blocked_reason="Unit at full strength",
            description=f"Repair failed: {unit.name} at full strength",
        )

    # Must be active or engaged
    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return RepairResult(
            success=False, unit_id=unit_id, has_facility=False,
            threshold=0, dice_roll=0,
            blocked_reason=f"Unit status is {unit.status}",
            description=f"Repair failed: unit is {unit.status}",
        )

    # CPA check
    cpa_available = unit.max_cpa_this_stage - unit.current_cpa_spent
    if cpa_available < REPAIR_CPA_COST:
        return RepairResult(
            success=False, unit_id=unit_id, has_facility=False,
            threshold=0, dice_roll=0,
            blocked_reason=f"Insufficient CPA ({cpa_available} < {REPAIR_CPA_COST})",
            description=f"Repair failed: insufficient CPA",
        )

    # Determine threshold
    has_facility = _has_repair_facility(state, unit)
    threshold = FACILITY_REPAIR_THRESHOLD if has_facility else FIELD_REPAIR_THRESHOLD

    roll = dice_roll if dice_roll is not None else roll_d6()
    repaired = roll <= threshold

    # Spend CPA
    unit.current_cpa_spent += REPAIR_CPA_COST

    strength_restored = 0
    if repaired:
        # Restore 1 strength point to the dominant type
        strength_restored = 1
        if unit.current_strength.armor > 0 or unit.toe_strength.armor > 0:
            unit.current_strength.armor = min(
                unit.current_strength.armor + 1,
                unit.toe_strength.armor)
        elif unit.current_strength.infantry > 0 or unit.toe_strength.infantry > 0:
            unit.current_strength.infantry = min(
                unit.current_strength.infantry + 1,
                unit.toe_strength.infantry)
        elif unit.current_strength.gun > 0 or unit.toe_strength.gun > 0:
            unit.current_strength.gun = min(
                unit.current_strength.gun + 1,
                unit.toe_strength.gun)
        elif unit.current_strength.recon > 0 or unit.toe_strength.recon > 0:
            unit.current_strength.recon = min(
                unit.current_strength.recon + 1,
                unit.toe_strength.recon)

    facility_str = "at facility" if has_facility else "field repair"
    desc = (f"{unit.name} repair ({facility_str}): "
            f"roll={roll} (need ≤{threshold}) → "
            f"{'REPAIRED +1 SP' if repaired else 'failed'}")

    state.log_event("repair", desc, unit_id=unit_id, repaired=repaired,
                    facility=has_facility)

    return RepairResult(
        success=repaired, unit_id=unit_id, has_facility=has_facility,
        threshold=threshold, dice_roll=roll,
        strength_restored=strength_restored,
        description=desc,
    )


# ════════════════════════════════════════
# VEHICLE REPAIR PHASE
# ════════════════════════════════════════

def execute_vehicle_repair_phase(
    state: GameState,
    side: str,
) -> RepairPhaseResult:
    """
    Execute the Vehicle Repair phase for one side.
    Auto-attempts repair on all eligible units.
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage
    results = []
    units_repaired = 0
    total_restored = 0

    for uid, unit in state.units.items():
        if unit.side != side:
            continue
        if unit.motorization not in REPAIRABLE_MOTORIZATIONS:
            continue
        if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
            continue
        if unit.current_strength.total >= unit.toe_strength.total:
            continue
        # CPA check
        if unit.max_cpa_this_stage - unit.current_cpa_spent < REPAIR_CPA_COST:
            continue

        result = attempt_repair(state, uid)
        results.append(result)
        if result.success:
            units_repaired += 1
            total_restored += result.strength_restored

    desc = (f"GT{gt} OpStage {op} Vehicle Repair ({side}): "
            f"{len(results)} attempts, {units_repaired} repaired, "
            f"+{total_restored} SP restored")

    state.log_event("repair_phase", desc, side=side, game_turn=gt)

    return RepairPhaseResult(
        game_turn=gt, op_stage=op, side=side,
        units_attempted=len(results),
        units_repaired=units_repaired,
        total_strength_restored=total_restored,
        results=results,
        description=desc,
    )
