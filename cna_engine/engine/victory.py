"""
CNA Engine — Victory Conditions Module
Tracks objective hexes, victory points, and scenario end conditions.

VP are awarded for controlling key locations, destroying enemy units,
and maintaining supply lines.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import Side, UnitStatus


# ════════════════════════════════════════
# OBJECTIVE HEXES
# ════════════════════════════════════════

# Objective hex → (VP value, description)
# Full theater from Alexandria to Tripoli
OBJECTIVE_HEXES: dict[str, tuple[int, str]] = {
    # Egypt (Map D-E)
    "E1326": (5, "Alexandria"),
    "D1822": (3, "Mersa Matruh"),
    "D0821": (3, "Sidi Barrani"),
    "C1714": (2, "Sollum"),
    # Cyrenaica (Map B-C)
    "C1215": (3, "Bardia"),
    "C0512": (5, "Tobruk"),
    "B3405": (2, "Gazala"),
    "B2703": (3, "Derna"),
    "B1403": (5, "Benghazi"),
    # Cyrenaica-Tripolitania border (Map A-B)
    "B0406": (2, "Agedabia"),
    "A1437": (3, "El Agheila"),
    # Tripolitania (Map A)
    "A2438": (2, "Sirte"),
    "A3117": (2, "Misurata"),
    "A3511": (5, "Tripoli"),
}

# VP for destroying enemy units (per strength point destroyed)
VP_PER_STRENGTH_POINT_DESTROYED = 0.5

# VP for supply: bonus if all supply lines connected
VP_SUPPLY_LINES_BONUS = 3

# VP penalty for each severed supply line
VP_SEVERED_PENALTY = -1

# Campaign end: last GT (Sep 1940 to Jan 1943 = 111 turns)
CAMPAIGN_END_GT = 111

# Decisive victory threshold (lead needed)
DECISIVE_VICTORY_MARGIN = 15
MARGINAL_VICTORY_MARGIN = 5


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class ObjectiveStatus:
    """Status of a single objective hex."""
    hex_id: str
    name: str
    vp_value: int
    controller: Optional[str] = None  # Side that controls it, or None if contested
    allied_units: int = 0
    axis_units: int = 0


@dataclass
class VictoryPointTally:
    """VP breakdown for one side."""
    side: str
    objective_vp: int = 0
    destruction_vp: float = 0.0
    supply_vp: int = 0
    total_vp: float = 0.0
    objectives_held: list[str] = field(default_factory=list)


@dataclass
class VictoryAssessment:
    """Overall victory assessment."""
    game_turn: int
    allied_vp: VictoryPointTally = field(default_factory=lambda: VictoryPointTally(side=Side.ALLIED))
    axis_vp: VictoryPointTally = field(default_factory=lambda: VictoryPointTally(side=Side.AXIS))
    objectives: list[ObjectiveStatus] = field(default_factory=list)
    margin: float = 0.0
    leading_side: Optional[str] = None
    result: str = ""  # "decisive_allied", "marginal_allied", "draw", etc.
    is_campaign_over: bool = False
    description: str = ""


# ════════════════════════════════════════
# OBJECTIVE CONTROL
# ════════════════════════════════════════

def get_hex_controller(state: GameState, hex_id: str) -> Optional[str]:
    """
    Determine which side controls a hex.
    Control = have units in hex AND no enemy units present.
    """
    hex_state = state.hexes.get(hex_id)
    if not hex_state:
        return None

    has_allied = len(hex_state.allied_unit_ids) > 0
    has_axis = len(hex_state.axis_unit_ids) > 0

    if has_allied and not has_axis:
        return Side.ALLIED
    elif has_axis and not has_allied:
        return Side.AXIS
    else:
        return None  # Contested or empty


def get_objective_statuses(state: GameState) -> list[ObjectiveStatus]:
    """Get the status of all objective hexes."""
    statuses = []
    for hex_id, (vp, name) in OBJECTIVE_HEXES.items():
        hex_state = state.hexes.get(hex_id)
        allied_count = len(hex_state.allied_unit_ids) if hex_state else 0
        axis_count = len(hex_state.axis_unit_ids) if hex_state else 0
        controller = get_hex_controller(state, hex_id)

        statuses.append(ObjectiveStatus(
            hex_id=hex_id, name=name, vp_value=vp,
            controller=controller,
            allied_units=allied_count,
            axis_units=axis_count,
        ))
    return statuses


# ════════════════════════════════════════
# VP CALCULATION
# ════════════════════════════════════════

def calculate_destruction_vp(state: GameState, side: str) -> float:
    """
    Calculate VP from destroying enemy units.
    Side earns VP for enemy strength points destroyed.
    """
    enemy_side = Side.AXIS if side == Side.ALLIED else Side.ALLIED
    total_losses = 0

    for unit in state.units.values():
        if unit.side != enemy_side:
            continue
        total_losses += unit.losses_taken

    return round(total_losses * VP_PER_STRENGTH_POINT_DESTROYED, 1)


def calculate_vp(state: GameState, side: str) -> VictoryPointTally:
    """Calculate total VP for a side."""
    tally = VictoryPointTally(side=side)

    # Objective VP
    for hex_id, (vp, name) in OBJECTIVE_HEXES.items():
        controller = get_hex_controller(state, hex_id)
        if controller == side:
            tally.objective_vp += vp
            tally.objectives_held.append(name)

    # Destruction VP
    tally.destruction_vp = calculate_destruction_vp(state, side)

    # Supply VP (bonus/penalty would need supply line check)
    # Simple version: check if any friendly units are without supply
    # This is a placeholder — integrate with supply_lines module for full check
    tally.supply_vp = 0

    tally.total_vp = tally.objective_vp + tally.destruction_vp + tally.supply_vp
    return tally


# ════════════════════════════════════════
# VICTORY ASSESSMENT
# ════════════════════════════════════════

def assess_victory(state: GameState) -> VictoryAssessment:
    """
    Assess current victory conditions.
    Can be called at any time for a snapshot, or at campaign end for final result.
    """
    gt = state.turn.game_turn
    is_over = gt > CAMPAIGN_END_GT

    allied_tally = calculate_vp(state, Side.ALLIED)
    axis_tally = calculate_vp(state, Side.AXIS)
    objectives = get_objective_statuses(state)

    # Log objective control changes (compare to previous call)
    prev = getattr(state, '_last_objective_control', {})
    for obj in objectives:
        old_ctrl = prev.get(obj.hex_id)
        new_ctrl = obj.controller
        if old_ctrl != new_ctrl:
            state.log_event("objective_changed",
                f"{obj.name} ({obj.hex_id}, {obj.vp_value}VP): "
                f"{old_ctrl or 'uncontrolled'} → {new_ctrl or 'uncontrolled'}",
                hex_id=obj.hex_id, vp_value=obj.vp_value,
                old_controller=old_ctrl, new_controller=new_ctrl)
    state._last_objective_control = {
        obj.hex_id: obj.controller for obj in objectives
    }

    margin = allied_tally.total_vp - axis_tally.total_vp
    if margin > 0:
        leading = Side.ALLIED
    elif margin < 0:
        leading = Side.AXIS
    else:
        leading = None

    abs_margin = abs(margin)
    if abs_margin >= DECISIVE_VICTORY_MARGIN:
        winner = "allied" if margin > 0 else "axis"
        result = f"decisive_{winner}"
    elif abs_margin >= MARGINAL_VICTORY_MARGIN:
        winner = "allied" if margin > 0 else "axis"
        result = f"marginal_{winner}"
    else:
        result = "draw"

    desc = (f"GT{gt} Victory: Allied={allied_tally.total_vp:.1f} VP "
            f"({', '.join(allied_tally.objectives_held) or 'no objectives'}) vs "
            f"Axis={axis_tally.total_vp:.1f} VP "
            f"({', '.join(axis_tally.objectives_held) or 'no objectives'})")
    if is_over:
        desc += f" — CAMPAIGN OVER: {result.upper().replace('_', ' ')}"
    else:
        desc += f" — {result.replace('_', ' ')}"

    state.log_event("victory_assessment", desc, game_turn=gt,
                    allied_vp=allied_tally.total_vp,
                    axis_vp=axis_tally.total_vp)

    return VictoryAssessment(
        game_turn=gt,
        allied_vp=allied_tally,
        axis_vp=axis_tally,
        objectives=objectives,
        margin=margin,
        leading_side=leading,
        result=result,
        is_campaign_over=is_over,
        description=desc,
    )


def build_vp_summary(state: GameState, side: str) -> dict:
    """Build a compact VP summary dict for agent state views."""
    assessment = assess_victory(state)
    side_vp = assessment.allied_vp if side == Side.ALLIED else assessment.axis_vp
    enemy_vp = assessment.axis_vp if side == Side.ALLIED else assessment.allied_vp
    objectives = []
    for obj in assessment.objectives:
        if obj.controller == side:
            status = "HELD"
        elif obj.controller is not None:
            status = "ENEMY"
        elif obj.allied_units > 0 and obj.axis_units > 0:
            status = "CONTESTED"
        else:
            status = "EMPTY"
        objectives.append({
            "name": obj.name, "hex_id": obj.hex_id,
            "vp": obj.vp_value, "status": status,
        })
    return {
        "your_vp": round(side_vp.total_vp, 1),
        "enemy_vp": round(enemy_vp.total_vp, 1),
        "margin": round(assessment.margin if side == Side.ALLIED else -assessment.margin, 1),
        "winning": assessment.leading_side == side,
        "objectives": objectives,
    }
