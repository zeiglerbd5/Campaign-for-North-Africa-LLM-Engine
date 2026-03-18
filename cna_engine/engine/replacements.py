"""
CNA Engine — Replacements Module
Handles replacement point allocation and absorption during the
Strategic Stage of each Game Turn.

Replacement points arrive in pools and are absorbed by depleted units
to restore lost strength points.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import Side, UnitStatus


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Max replacement points absorbed per unit per GT
MAX_REPLACEMENTS_PER_UNIT = 2

# Replacement pool production per GT (default — scenario can override)
DEFAULT_ALLIED_PRODUCTION = {
    "infantry": 3,
    "armor": 1,
    "gun": 1,
}
DEFAULT_AXIS_PRODUCTION = {
    "infantry": 2,
    "armor": 1,
    "gun": 0,
}

# Unit must be active or in reserve to receive replacements
ELIGIBLE_STATUSES = {UnitStatus.ACTIVE, UnitStatus.IN_RESERVE, UnitStatus.ENGAGED}

# Unit must not be in contact to receive replacements
REQUIRES_NOT_IN_CONTACT = True


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class ReplacementResult:
    """Result of absorbing replacements into a single unit."""
    success: bool
    unit_id: str
    unit_name: str
    strength_type: str
    points_absorbed: int
    strength_before: int
    strength_after: int
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class ReplacementPhaseResult:
    """Aggregated result of the replacement phase."""
    game_turn: int
    side: str
    pool_before: dict = field(default_factory=dict)
    pool_after: dict = field(default_factory=dict)
    production_added: dict = field(default_factory=dict)
    units_reinforced: int = 0
    total_points_absorbed: int = 0
    results: list[ReplacementResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# REPLACEMENT ABSORPTION
# ════════════════════════════════════════

def _get_dominant_type(unit: Unit) -> Optional[str]:
    """Determine the unit's dominant strength type (for replacement allocation)."""
    toe = unit.toe_strength
    if toe.armor > 0:
        return "armor"
    elif toe.infantry > 0:
        return "infantry"
    elif toe.gun > 0:
        return "gun"
    elif toe.recon > 0:
        return "recon"
    return None


def _get_deficit(unit: Unit, strength_type: str) -> int:
    """How many strength points the unit is below TOE for a given type."""
    current = getattr(unit.current_strength, strength_type, 0)
    toe = getattr(unit.toe_strength, strength_type, 0)
    return max(0, toe - current)


def absorb_replacements(
    state: GameState,
    unit_id: str,
    points: int = 1,
) -> ReplacementResult:
    """
    Absorb replacement points into a depleted unit.
    Points come from the side's replacement pool.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name="",
            strength_type="", points_absorbed=0,
            strength_before=0, strength_after=0,
            blocked_reason=f"Unit {unit_id} not found",
            description="Replacement failed: unit not found",
        )

    if unit.status not in ELIGIBLE_STATUSES:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name=unit.name,
            strength_type="", points_absorbed=0,
            strength_before=unit.current_strength.total,
            strength_after=unit.current_strength.total,
            blocked_reason=f"Unit status {unit.status} not eligible",
            description=f"Replacement failed: {unit.name} is {unit.status}",
        )

    if REQUIRES_NOT_IN_CONTACT and unit.is_in_contact:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name=unit.name,
            strength_type="", points_absorbed=0,
            strength_before=unit.current_strength.total,
            strength_after=unit.current_strength.total,
            blocked_reason="Unit is in contact with enemy",
            description=f"Replacement failed: {unit.name} in contact",
        )

    # Determine strength type
    stype = _get_dominant_type(unit)
    if not stype:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name=unit.name,
            strength_type="", points_absorbed=0,
            strength_before=unit.current_strength.total,
            strength_after=unit.current_strength.total,
            blocked_reason="No replaceable strength type",
            description=f"Replacement failed: {unit.name} has no strength type",
        )

    # Check deficit
    deficit = _get_deficit(unit, stype)
    if deficit <= 0:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name=unit.name,
            strength_type=stype, points_absorbed=0,
            strength_before=unit.current_strength.total,
            strength_after=unit.current_strength.total,
            blocked_reason="Unit at full strength",
            description=f"Replacement failed: {unit.name} at full TOE",
        )

    # Cap by MAX_REPLACEMENTS_PER_UNIT and deficit
    actual_points = min(points, deficit, MAX_REPLACEMENTS_PER_UNIT)

    # Check pool
    pool = (state.allied_replacement_pool if unit.side == Side.ALLIED
            else state.axis_replacement_pool)

    # Map recon → infantry pool for replacement purposes
    pool_key = stype if stype != "recon" else "infantry"
    available = pool.get(pool_key, 0)

    if available <= 0:
        return ReplacementResult(
            success=False, unit_id=unit_id, unit_name=unit.name,
            strength_type=stype, points_absorbed=0,
            strength_before=unit.current_strength.total,
            strength_after=unit.current_strength.total,
            blocked_reason=f"No {pool_key} replacements in pool",
            description=f"Replacement failed: no {pool_key} in pool",
        )

    actual_points = min(actual_points, available)
    strength_before = unit.current_strength.total

    # Apply
    current_val = getattr(unit.current_strength, stype)
    toe_val = getattr(unit.toe_strength, stype)
    new_val = min(current_val + actual_points, toe_val)
    setattr(unit.current_strength, stype, new_val)

    # Deduct from pool
    pool[pool_key] -= actual_points

    strength_after = unit.current_strength.total

    desc = (f"{unit.name} absorbs {actual_points} {stype} replacement(s): "
            f"{strength_before} → {strength_after} SP")

    state.log_event("replacement", desc, unit_id=unit_id,
                    points=actual_points, stype=stype)

    return ReplacementResult(
        success=True, unit_id=unit_id, unit_name=unit.name,
        strength_type=stype, points_absorbed=actual_points,
        strength_before=strength_before,
        strength_after=strength_after,
        description=desc,
    )


# ════════════════════════════════════════
# PRODUCTION & PHASE EXECUTION
# ════════════════════════════════════════

def add_production(
    state: GameState,
    side: str,
    production: Optional[dict[str, int]] = None,
):
    """
    Add replacement point production to a side's pool.
    Called during Strategic Stage.
    """
    pool = (state.allied_replacement_pool if side == Side.ALLIED
            else state.axis_replacement_pool)

    defaults = (DEFAULT_ALLIED_PRODUCTION if side == Side.ALLIED
                else DEFAULT_AXIS_PRODUCTION)
    prod = production or defaults

    for stype, amount in prod.items():
        pool[stype] = pool.get(stype, 0) + amount


def execute_replacement_phase(
    state: GameState,
    side: str,
    allocation: Optional[dict[str, int]] = None,
) -> ReplacementPhaseResult:
    """
    Execute the replacement phase for one side during Strategic Stage.
    1. Add production to pool
    2. Auto-allocate to most depleted units (or use provided allocation)
    allocation: {unit_id: points} — if None, auto-allocate.
    """
    gt = state.turn.game_turn
    pool = (state.allied_replacement_pool if side == Side.ALLIED
            else state.axis_replacement_pool)

    pool_before = dict(pool)

    # Add production
    defaults = (DEFAULT_ALLIED_PRODUCTION if side == Side.ALLIED
                else DEFAULT_AXIS_PRODUCTION)
    add_production(state, side)
    production_added = dict(defaults)

    results = []
    units_reinforced = 0
    total_absorbed = 0

    if allocation:
        # Use explicit allocation
        for uid, points in allocation.items():
            r = absorb_replacements(state, uid, points)
            results.append(r)
            if r.success:
                units_reinforced += 1
                total_absorbed += r.points_absorbed
    else:
        # Auto-allocate: prioritize most depleted units
        eligible_units = []
        for uid, unit in state.units.items():
            if unit.side != side:
                continue
            if unit.status not in ELIGIBLE_STATUSES:
                continue
            if unit.is_in_contact:
                continue
            stype = _get_dominant_type(unit)
            if stype:
                deficit = _get_deficit(unit, stype)
                if deficit > 0:
                    eligible_units.append((uid, deficit, stype))

        # Sort by deficit (largest first)
        eligible_units.sort(key=lambda x: -x[1])

        for uid, deficit, stype in eligible_units:
            pool_key = stype if stype != "recon" else "infantry"
            if pool.get(pool_key, 0) <= 0:
                continue
            r = absorb_replacements(state, uid, min(deficit, MAX_REPLACEMENTS_PER_UNIT))
            results.append(r)
            if r.success:
                units_reinforced += 1
                total_absorbed += r.points_absorbed

    pool_after = dict(pool)

    desc = (f"GT{gt} Replacements ({side}): "
            f"+{production_added} produced, "
            f"{units_reinforced} units reinforced, "
            f"+{total_absorbed} SP total. "
            f"Pool: {pool_after}")

    state.log_event("replacement_phase", desc, side=side, game_turn=gt)

    return ReplacementPhaseResult(
        game_turn=gt, side=side,
        pool_before=pool_before,
        pool_after=pool_after,
        production_added=production_added,
        units_reinforced=units_reinforced,
        total_points_absorbed=total_absorbed,
        results=results,
        description=desc,
    )
