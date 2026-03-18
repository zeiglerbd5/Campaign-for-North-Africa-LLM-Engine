"""
CNA Engine — Organization Phase Module
Unit attachment/detachment between formations, truck point allocation,
reserve placement/release, CPA reset, and status management.

The Organization phase occurs at the start of each OpStage before
combat operations begin. Per [6.0] NJH restructured SoP.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit, Formation
from cna_engine.models.enums import (
    Side, UnitStatus, MotorizationType,
)
from cna_engine.engine.movement import get_neighbors, hex_has_active_enemy


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

ATTACH_UNIT_COST = 1.0      # CP to attach a unit to a formation
DETACH_UNIT_COST = 1.0      # CP to detach a unit from a formation
PLACE_IN_RESERVE_COST = 0   # Reserve placement is free during Organization
RELEASE_RESERVE_COST = 0    # Reserve release during Reserve phase is free

# Cohesion thresholds
COHESION_DEGRADE_THRESHOLD = 6    # -1 cohesion per this many CPA spent
COHESION_RECOVERY_AMOUNT = 1      # +1 per rested OpStage (cap at 0)
COHESION_RECOVERY_DEEP = 2        # +2 if cohesion <= COHESION_DEEP_THRESHOLD
COHESION_DEEP_THRESHOLD = -10     # Recovery increases below this
COHESION_FLOOR = -30              # Hard floor — cohesion cannot drop below this
COHESION_SURRENDER_CONTACT = -17  # Surrender if in contact
COHESION_SURRENDER_ADJACENT = -26 # Surrender if enemy adjacent
COHESION_NO_MOVEMENT = -26        # Cannot move at all
SUPPLY_STRESS_THRESHOLD = 0.10    # Supply below 10% capacity triggers penalty
SUPPLY_STRESS_MAX_PER_OPSTAGE = 1 # Max combined supply penalty per OpStage

# Disorganization constants
DISORG_PER_COMBAT_LOSS = 1
DISORG_PER_RETREAT = 1
DISORG_PER_OVERRUN = 2
DISORG_RECOVERY_RESTED = 1
DISORG_RECOVERY_FACILITY = 2
DISORG_ATTACK_SHIFT_THRESHOLD = 3   # disorg >= 3: -1 column when attacking
DISORG_DEFEND_SHIFT_THRESHOLD = 5   # disorg >= 5: -1 column when defending


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class AttachResult:
    """Result of attaching a unit to a formation."""
    success: bool
    unit_id: str
    formation_id: str
    previous_formation_id: Optional[str] = None
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class ReserveResult:
    """Result of placing/releasing a unit in reserve."""
    success: bool
    unit_id: str
    operation: str              # "place" or "release"
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class CohesionUpdateResult:
    """Result of a single unit's cohesion update."""
    unit_id: str
    old_cohesion: int
    new_cohesion: int
    cpa_penalty: int = 0
    water_penalty: int = 0
    ammo_penalty: int = 0
    recovery: int = 0
    description: str = ""


@dataclass
class OrganizationPhaseResult:
    """Aggregated result of the Organization phase."""
    game_turn: int
    op_stage: int
    units_reset: int = 0
    sighting_cleared: int = 0
    cohesion_changes: int = 0
    units_surrendered: int = 0
    description: str = ""


# ════════════════════════════════════════
# UNIT ATTACHMENT / DETACHMENT
# ════════════════════════════════════════

def attach_unit_to_formation(
    state: GameState,
    unit_id: str,
    formation_id: str,
) -> AttachResult:
    """
    Attach a unit to a formation. The unit must be on-map and same side.
    If already attached elsewhere, detaches first.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return AttachResult(
            success=False, unit_id=unit_id, formation_id=formation_id,
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Attach failed: unit not found",
        )

    formation = state.formations.get(formation_id)
    if not formation:
        return AttachResult(
            success=False, unit_id=unit_id, formation_id=formation_id,
            blocked_reason=f"Formation {formation_id} not found",
            description=f"Attach failed: formation not found",
        )

    if unit.side != formation.side:
        return AttachResult(
            success=False, unit_id=unit_id, formation_id=formation_id,
            blocked_reason=f"Side mismatch: unit={unit.side}, formation={formation.side}",
            description=f"Attach failed: side mismatch",
        )

    previous = unit.attached_to_id

    # Remove from old formation's attached list if any
    if previous and previous in state.formations:
        old_fm = state.formations[previous]
        if unit_id in old_fm.unit_ids:
            old_fm.unit_ids.remove(unit_id)

    # Attach to new formation
    unit.attached_to_id = formation_id
    if unit_id not in formation.unit_ids:
        formation.unit_ids.append(unit_id)

    desc = f"{unit.name} attached to {formation.name}"
    if previous:
        desc += f" (was: {previous})"
    state.log_event("attach_unit", desc, unit_id=unit_id,
                    formation_id=formation_id)

    return AttachResult(
        success=True, unit_id=unit_id, formation_id=formation_id,
        previous_formation_id=previous,
        description=desc,
    )


def detach_unit_from_formation(
    state: GameState,
    unit_id: str,
) -> AttachResult:
    """
    Detach a unit from its current attached formation.
    Returns to parent formation if one exists.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return AttachResult(
            success=False, unit_id=unit_id, formation_id="",
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Detach failed: unit not found",
        )

    if not unit.attached_to_id:
        return AttachResult(
            success=False, unit_id=unit_id, formation_id="",
            blocked_reason=f"{unit.name} is not attached to any formation",
            description=f"Detach failed: not attached",
        )

    old_formation_id = unit.attached_to_id

    # Remove from formation's unit list
    if old_formation_id in state.formations:
        old_fm = state.formations[old_formation_id]
        if unit_id in old_fm.unit_ids:
            old_fm.unit_ids.remove(unit_id)

    unit.attached_to_id = None

    # Return to parent formation if exists
    if unit.parent_formation_id and unit.parent_formation_id in state.formations:
        parent = state.formations[unit.parent_formation_id]
        if unit_id not in parent.unit_ids:
            parent.unit_ids.append(unit_id)

    desc = f"{unit.name} detached from {old_formation_id}"
    state.log_event("detach_unit", desc, unit_id=unit_id,
                    formation_id=old_formation_id)

    return AttachResult(
        success=True, unit_id=unit_id, formation_id=old_formation_id,
        previous_formation_id=old_formation_id,
        description=desc,
    )


# ════════════════════════════════════════
# RESERVE MANAGEMENT
# ════════════════════════════════════════

def place_in_reserve(
    state: GameState,
    unit_id: str,
) -> ReserveResult:
    """
    Place a unit in reserve during the Organization phase.
    Unit must be ACTIVE and not in contact.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return ReserveResult(
            success=False, unit_id=unit_id, operation="place",
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Reserve failed: unit not found",
        )

    if unit.status != UnitStatus.ACTIVE:
        return ReserveResult(
            success=False, unit_id=unit_id, operation="place",
            blocked_reason=f"{unit.name} must be ACTIVE to enter reserve (status: {unit.status})",
            description=f"Reserve failed: not active",
        )

    if unit.is_in_contact:
        return ReserveResult(
            success=False, unit_id=unit_id, operation="place",
            blocked_reason=f"{unit.name} is in contact — cannot enter reserve",
            description=f"Reserve failed: in contact",
        )

    unit.status = UnitStatus.IN_RESERVE

    desc = f"{unit.name} placed in reserve"
    state.log_event("place_reserve", desc, unit_id=unit_id)

    return ReserveResult(
        success=True, unit_id=unit_id, operation="place",
        description=desc,
    )


def release_from_reserve(
    state: GameState,
    unit_id: str,
) -> ReserveResult:
    """
    Release a unit from reserve. Unit becomes ACTIVE.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return ReserveResult(
            success=False, unit_id=unit_id, operation="release",
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Release failed: unit not found",
        )

    if unit.status != UnitStatus.IN_RESERVE:
        return ReserveResult(
            success=False, unit_id=unit_id, operation="release",
            blocked_reason=f"{unit.name} is not in reserve (status: {unit.status})",
            description=f"Release failed: not in reserve",
        )

    unit.status = UnitStatus.ACTIVE

    desc = f"{unit.name} released from reserve"
    state.log_event("release_reserve", desc, unit_id=unit_id)

    return ReserveResult(
        success=True, unit_id=unit_id, operation="release",
        description=desc,
    )


# ════════════════════════════════════════
# COHESION
# ════════════════════════════════════════

_INACTIVE_STATUSES = frozenset({
    UnitStatus.DESTROYED, UnitStatus.SURRENDERED,
    UnitStatus.WITHDRAWN, UnitStatus.NOT_YET_ARRIVED,
})


def apply_cohesion_changes(state: GameState) -> list[CohesionUpdateResult]:
    """
    Apply cohesion changes based on CPA spent (before CPA reset).
    Called BEFORE CPA reset in the Organization phase.

    Rules:
    - Spent CPA > 0: cohesion -= cpa_spent // COHESION_DEGRADE_THRESHOLD
    - Spent CPA == 0 (rested): recover toward 0 (+2 if deeply negative, else +1)
    - Supply below 10% capacity: -1 (water or ammo), capped at -1 combined
    - Hard floor at COHESION_FLOOR (-30)
    """
    results = []
    for unit in state.units.values():
        if unit.status in _INACTIVE_STATUSES:
            continue

        old_cohesion = unit.cohesion
        cpa_penalty = 0
        water_penalty = 0
        ammo_penalty = 0
        recovery = 0

        if unit.current_cpa_spent > 0:
            cpa_penalty = unit.current_cpa_spent // COHESION_DEGRADE_THRESHOLD
            unit.cohesion -= cpa_penalty
        else:
            # Rested — recover toward 0 (faster if deeply negative)
            if unit.cohesion < 0:
                recovery = COHESION_RECOVERY_DEEP if unit.cohesion <= COHESION_DEEP_THRESHOLD else COHESION_RECOVERY_AMOUNT
                unit.cohesion = min(0, unit.cohesion + recovery)

            # Disorganization recovery when rested
            if unit.disorganization > 0:
                from cna_engine.engine.repair import _has_repair_facility
                disorg_rec = DISORG_RECOVERY_FACILITY if _has_repair_facility(state, unit) else DISORG_RECOVERY_RESTED
                old_disorg = unit.disorganization
                unit.disorganization = max(0, unit.disorganization - disorg_rec)
                if unit.disorganization != old_disorg:
                    state.log_event("disorg_recovery",
                                    f"{unit.name} disorg {old_disorg} → {unit.disorganization}",
                                    unit_id=unit.id)

        # Supply stress — capped at SUPPLY_STRESS_MAX_PER_OPSTAGE combined
        supply_penalty = 0
        if unit.supply.water_capacity > 0 and unit.supply.water < unit.supply.water_capacity * SUPPLY_STRESS_THRESHOLD:
            water_penalty = 1
            supply_penalty += 1

        if supply_penalty < SUPPLY_STRESS_MAX_PER_OPSTAGE:
            if unit.supply.ammo_capacity > 0 and unit.supply.ammo < unit.supply.ammo_capacity * SUPPLY_STRESS_THRESHOLD:
                ammo_penalty = 1
                supply_penalty += 1

        supply_penalty = min(supply_penalty, SUPPLY_STRESS_MAX_PER_OPSTAGE)
        unit.cohesion -= supply_penalty

        # Enforce cohesion floor
        unit.cohesion = max(COHESION_FLOOR, unit.cohesion)

        if unit.cohesion != old_cohesion:
            desc = f"{unit.name} cohesion {old_cohesion} → {unit.cohesion}"
            state.log_event("cohesion_change", desc, unit_id=unit.id,
                            old=old_cohesion, new=unit.cohesion)
            results.append(CohesionUpdateResult(
                unit_id=unit.id, old_cohesion=old_cohesion,
                new_cohesion=unit.cohesion,
                cpa_penalty=cpa_penalty, water_penalty=water_penalty,
                ammo_penalty=ammo_penalty, recovery=recovery,
                description=desc,
            ))

    return results


def check_surrenders(state: GameState) -> list[str]:
    """
    Check for unit surrenders based on cohesion thresholds.
    Returns list of unit IDs that surrendered.
    """
    from cna_engine.engine.movement import is_hex_in_ezoc

    surrendered = []
    for unit in list(state.units.values()):
        if unit.status in _INACTIVE_STATUSES:
            continue
        if not unit.hex_id:
            continue

        should_surrender = False

        # Cohesion <= -26 and in EZOC → surrender
        if unit.cohesion <= COHESION_SURRENDER_ADJACENT and is_hex_in_ezoc(state, unit.hex_id, unit.side):
            should_surrender = True

        # Cohesion <= -17 and in contact → surrender
        if unit.cohesion <= COHESION_SURRENDER_CONTACT and unit.is_in_contact:
            should_surrender = True

        if should_surrender:
            unit.status = UnitStatus.SURRENDERED
            desc = f"{unit.name} SURRENDERS (cohesion {unit.cohesion})"
            state.log_event("surrender", desc, unit_id=unit.id, hex_id=unit.hex_id)
            surrendered.append(unit.id)

    return surrendered


# ════════════════════════════════════════
# ORGANIZATION PHASE EXECUTION
# ════════════════════════════════════════

def execute_organization_phase(state: GameState) -> OrganizationPhaseResult:
    """
    Execute the Organization phase at the start of each OpStage.
    1. Reset CPA spent to 0 for all units
    2. Clear has_acted_this_stage flags
    3. Clear pinned status
    4. Clear hex sighting flags
    5. Log phase execution
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage

    units_reset = 0
    sighting_cleared = 0

    # Apply cohesion changes BEFORE CPA reset
    cohesion_results = apply_cohesion_changes(state)
    surrendered = check_surrenders(state)

    for unit in state.units.values():
        if unit.status in (UnitStatus.DESTROYED, UnitStatus.SURRENDERED,
                           UnitStatus.WITHDRAWN, UnitStatus.NOT_YET_ARRIVED):
            continue
        unit.current_cpa_spent = 0
        unit.has_acted_this_stage = False
        unit.is_pinned = False
        unit.terrains_traversed_this_stage = []
        # Track turns in position (once per GT, on OpStage 1)
        if state.turn.op_stage == 1:
            if unit.last_hex_id is not None and unit.hex_id == unit.last_hex_id:
                unit.turns_in_position += 1
            else:
                unit.turns_in_position = 0
            unit.last_hex_id = unit.hex_id
        units_reset += 1

    # ── Clean destroyed/surrendered/withdrawn units from hex unit lists ──
    # Dead units lingering in hex lists block pathfinding (movement.py treats
    # them as enemy-occupied) and inflate contact counts.
    cleaned_units = 0
    for hex_state in state.hexes.values():
        for side_list in (hex_state.allied_unit_ids, hex_state.axis_unit_ids):
            before = len(side_list)
            side_list[:] = [
                uid for uid in side_list
                if uid in state.units and state.units[uid].status not in _INACTIVE_STATUSES
            ]
            cleaned_units += before - len(side_list)
    if cleaned_units > 0:
        state.log_event("hex_cleanup",
                        f"Removed {cleaned_units} inactive unit IDs from hex lists",
                        game_turn=gt, op_stage=op)

    # ── Separate co-located enemy units ──
    # Units sharing a hex with enemies (from before the stacking fix) get pushed out.
    # The minority side (fewer SP) is displaced to the nearest safe adjacent hex.
    separated_units = 0
    for hex_id, hex_state in list(state.hexes.items()):
        allied_active = [uid for uid in hex_state.allied_unit_ids
                         if uid in state.units and state.units[uid].status
                         in (UnitStatus.ACTIVE, UnitStatus.ENGAGED)]
        axis_active = [uid for uid in hex_state.axis_unit_ids
                       if uid in state.units and state.units[uid].status
                       in (UnitStatus.ACTIVE, UnitStatus.ENGAGED)]
        if not allied_active or not axis_active:
            continue

        # Minority side gets pushed out (fewer SP)
        allied_sp = sum(state.units[u].stacking_points for u in allied_active)
        axis_sp = sum(state.units[u].stacking_points for u in axis_active)
        push_side = Side.AXIS if axis_sp <= allied_sp else Side.ALLIED
        push_ids = axis_active if push_side == Side.AXIS else allied_active
        side_attr = "axis_unit_ids" if push_side == Side.AXIS else "allied_unit_ids"

        # Find safe adjacent hex and move each unit
        for uid in push_ids:
            unit = state.units[uid]
            neighbors = get_neighbors(hex_id)
            safe = [n for n in neighbors if n in state.hexes
                    and not hex_has_active_enemy(state, n, unit.side)]
            if safe:
                # Prefer hex with friendly units already
                best = next((n for n in safe
                            if getattr(state.hexes[n], side_attr)), safe[0])
                # Move unit out of shared hex
                old_list = getattr(hex_state, side_attr)
                if uid in old_list:
                    old_list.remove(uid)
                unit.hex_id = best
                dest_list = getattr(state.hexes[best], side_attr)
                if uid not in dest_list:
                    dest_list.append(uid)
                separated_units += 1

    if separated_units > 0:
        state.log_event("hex_separation",
                        f"Separated {separated_units} units from shared enemy hexes",
                        game_turn=gt, op_stage=op)

    for hex_state in state.hexes.values():
        if hex_state.allied_sighted or hex_state.axis_sighted:
            hex_state.allied_sighted = False
            hex_state.axis_sighted = False
            sighting_cleared += 1

    desc = (f"GT{gt} OpStage {op} Organization: "
            f"{units_reset} units reset, {sighting_cleared} hexes sighting cleared, "
            f"{len(cohesion_results)} cohesion changes, {len(surrendered)} surrendered")
    state.log_event("organization_phase", desc, game_turn=gt, op_stage=op)

    return OrganizationPhaseResult(
        game_turn=gt, op_stage=op,
        units_reset=units_reset,
        sighting_cleared=sighting_cleared,
        cohesion_changes=len(cohesion_results),
        units_surrendered=len(surrendered),
        description=desc,
    )
