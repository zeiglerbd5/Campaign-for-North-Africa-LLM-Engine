"""
CNA Engine — Naval & Convoy Module
Convoy planning, fleet sorties, interception, port unloading,
and tonnage distribution mechanics.

The Convoy Arrival phase is shared; Fleet and Convoy Movement
are per-side phases in each OpStage.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import (
    GameState, ConvoyState,
)
from cna_engine.engine.combat import roll_d6


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Convoy interception probabilities (d6 roll needed per condition)
INTERCEPTION_BASE_THRESHOLD = 3       # Roll ≤ 3 to intercept
INTERCEPTION_RECON_BONUS = 1          # +1 if convoy lane reconned
INTERCEPTION_CAP_PENALTY = -1         # -1 if convoy has CAP

# Convoy loss percentages (d6)
CONVOY_LOSS_TABLE = {
    1: 0.10,    # 10% tonnage lost
    2: 0.15,
    3: 0.20,
    4: 0.25,
    5: 0.30,
    6: 0.40,    # 40% tonnage lost
}

# Fleet sortie limits
MAX_FLEET_SORTIES_PER_TURN = 2
FLEET_REPAIR_TURNS = 3            # Turns to repair after heavy engagement

# Port efficiency default (tons per GT)
DEFAULT_PORT_EFFICIENCY = 100

# Supply types distributed from convoy tonnage
SUPPLY_DISTRIBUTION_RATIOS = {
    "fuel": 0.35,
    "water": 0.15,
    "ammo": 0.30,
    "stores": 0.20,
}


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class ConvoyPlanResult:
    """Result of planning a convoy shipment."""
    success: bool
    destination_port: str
    planned_tonnage: float
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class FleetSortieResult:
    """Result of a CW fleet sortie."""
    success: bool
    target_area: str
    ships_committed: int
    interception_attempted: bool = False
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class InterceptionResult:
    """Result of convoy interception attempt."""
    success: bool
    convoy_intercepted: bool
    interception_roll: int
    threshold: int
    tonnage_lost: float = 0.0
    tonnage_delivered: float = 0.0
    loss_roll: int = 0
    loss_percentage: float = 0.0
    description: str = ""


@dataclass
class PortUnloadResult:
    """Result of unloading supplies at a port."""
    success: bool
    port_name: str
    hex_id: str
    tonnage_unloaded: float = 0.0
    supplies_distributed: dict = field(default_factory=dict)
    overflow_tonnage: float = 0.0
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class ConvoyPhaseResult:
    """Aggregated result of the Convoy Arrival phase."""
    game_turn: int
    op_stage: int
    convoys_planned: int = 0
    tonnage_shipped: float = 0.0
    tonnage_lost: float = 0.0
    tonnage_delivered: float = 0.0
    interceptions: list[InterceptionResult] = field(default_factory=list)
    port_unloads: list[PortUnloadResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# CONVOY PLANNING
# ════════════════════════════════════════

def plan_convoy(
    state: GameState,
    destination_port: str,
    tonnage: float,
) -> ConvoyPlanResult:
    """
    Plan a convoy shipment to a destination port.
    Axis convoys ship supplies from Tripoli/Benghazi to forward ports.
    """
    # Check port exists
    port_hex = None
    for hex_id, hex_state in state.hexes.items():
        if hex_state.is_port and hex_state.port_name and \
           hex_state.port_name.lower() == destination_port.lower():
            port_hex = hex_id
            break

    if not port_hex:
        return ConvoyPlanResult(
            success=False, destination_port=destination_port,
            planned_tonnage=tonnage,
            blocked_reason=f"Port '{destination_port}' not found",
            description=f"Convoy planning failed: port not found",
        )

    # Record planned convoy
    state.axis_convoy.planned_tonnage[destination_port] = tonnage

    desc = f"Convoy planned: {tonnage:.0f} tons to {destination_port}"
    state.log_event("convoy_plan", desc, port=destination_port, tonnage=tonnage)

    return ConvoyPlanResult(
        success=True, destination_port=destination_port,
        planned_tonnage=tonnage,
        description=desc,
    )


# ════════════════════════════════════════
# FLEET SORTIES
# ════════════════════════════════════════

def execute_fleet_sortie(
    state: GameState,
    target_area: str,
    ships: int = 1,
) -> FleetSortieResult:
    """
    Execute a CW fleet sortie for convoy interception.
    Fleet must be available and not under repair.
    """
    fleet = state.cw_fleet

    if not fleet.is_available:
        return FleetSortieResult(
            success=False, target_area=target_area, ships_committed=ships,
            blocked_reason="Fleet is not available",
            description="Fleet sortie failed: not available",
        )

    if fleet.repair_turns_remaining > 0:
        return FleetSortieResult(
            success=False, target_area=target_area, ships_committed=ships,
            blocked_reason=f"Fleet under repair ({fleet.repair_turns_remaining} turns remaining)",
            description="Fleet sortie failed: under repair",
        )

    if fleet.sorties_remaining <= 0:
        return FleetSortieResult(
            success=False, target_area=target_area, ships_committed=ships,
            blocked_reason="No sorties remaining this turn",
            description="Fleet sortie failed: no sorties",
        )

    fleet.sorties_remaining -= 1
    fleet.ships_committed = ships
    fleet.current_hex = target_area

    desc = f"CW Fleet sortie to {target_area} ({ships} ships)"
    state.log_event("fleet_sortie", desc, target_area=target_area,
                    ships=ships)

    return FleetSortieResult(
        success=True, target_area=target_area, ships_committed=ships,
        interception_attempted=True,
        description=desc,
    )


# ════════════════════════════════════════
# CONVOY INTERCEPTION
# ════════════════════════════════════════

def attempt_interception(
    state: GameState,
    port: str,
    planned_tonnage: float,
    is_reconned: bool = False,
    has_cap: bool = False,
    interception_roll: Optional[int] = None,
    loss_roll: Optional[int] = None,
) -> InterceptionResult:
    """
    Attempt to intercept a convoy heading to a port.
    Fleet must have sortied to the appropriate area.
    """
    threshold = INTERCEPTION_BASE_THRESHOLD
    if is_reconned:
        threshold += INTERCEPTION_RECON_BONUS
    if has_cap:
        threshold += INTERCEPTION_CAP_PENALTY

    threshold = max(1, min(6, threshold))

    roll = interception_roll if interception_roll is not None else roll_d6()
    intercepted = roll <= threshold

    tonnage_lost = 0.0
    loss_pct = 0.0
    l_roll = 0

    if intercepted:
        l_roll = loss_roll if loss_roll is not None else roll_d6()
        loss_pct = CONVOY_LOSS_TABLE.get(l_roll, 0.20)
        tonnage_lost = round(planned_tonnage * loss_pct, 1)
        state.axis_convoy.losses_this_turn += tonnage_lost

    tonnage_delivered = max(0, planned_tonnage - tonnage_lost)

    desc = (f"Convoy to {port}: {planned_tonnage:.0f} tons, "
            f"interception roll={roll} (need ≤{threshold}): "
            f"{'INTERCEPTED' if intercepted else 'safe'}")
    if intercepted:
        desc += f" — {loss_pct*100:.0f}% lost ({tonnage_lost:.0f} tons)"

    state.log_event("convoy_interception", desc, port=port,
                    intercepted=intercepted, tonnage_lost=tonnage_lost)

    return InterceptionResult(
        success=True,
        convoy_intercepted=intercepted,
        interception_roll=roll,
        threshold=threshold,
        tonnage_lost=tonnage_lost,
        tonnage_delivered=tonnage_delivered,
        loss_roll=l_roll,
        loss_percentage=loss_pct,
        description=desc,
    )


# ════════════════════════════════════════
# PORT UNLOADING
# ════════════════════════════════════════

def unload_at_port(
    state: GameState,
    port_name: str,
    tonnage: float,
    max_efficiency: Optional[float] = None,
) -> PortUnloadResult:
    """
    Unload convoy tonnage at a port. Distributes supplies according to
    standard ratios. Capped by port efficiency.
    """
    # Find port hex
    port_hex = None
    for hex_id, hex_state in state.hexes.items():
        if hex_state.is_port and hex_state.port_name and \
           hex_state.port_name.lower() == port_name.lower():
            port_hex = hex_id
            break

    if not port_hex:
        return PortUnloadResult(
            success=False, port_name=port_name, hex_id="",
            blocked_reason=f"Port '{port_name}' not found",
            description=f"Unload failed: port not found",
        )

    # Apply port efficiency cap
    efficiency = max_efficiency or DEFAULT_PORT_EFFICIENCY
    actual_tonnage = min(tonnage, efficiency)
    overflow = max(0, tonnage - efficiency)

    # Distribute supplies
    supplies = {}
    for supply_type, ratio in SUPPLY_DISTRIBUTION_RATIOS.items():
        amount = round(actual_tonnage * ratio, 1)
        supplies[supply_type] = amount

    # Add to supply pool (Axis supplies go to Tripoli boxes)
    for supply_type, amount in supplies.items():
        if supply_type in state.axis_supply_in_tripoli_boxes:
            state.axis_supply_in_tripoli_boxes[supply_type] += amount

    # Record delivered tonnage
    state.axis_convoy.actual_tonnage_delivered[port_name] = (
        state.axis_convoy.actual_tonnage_delivered.get(port_name, 0) + actual_tonnage
    )

    desc = (f"Unloaded {actual_tonnage:.0f} tons at {port_name}: "
            f"fuel={supplies.get('fuel',0):.0f} ammo={supplies.get('ammo',0):.0f} "
            f"water={supplies.get('water',0):.0f} stores={supplies.get('stores',0):.0f}")
    if overflow > 0:
        desc += f" ({overflow:.0f} tons overflow — port at capacity)"

    state.log_event("port_unload", desc, port=port_name,
                    tonnage_unloaded=actual_tonnage)

    return PortUnloadResult(
        success=True, port_name=port_name, hex_id=port_hex,
        tonnage_unloaded=actual_tonnage,
        supplies_distributed=supplies,
        overflow_tonnage=overflow,
        description=desc,
    )


# ════════════════════════════════════════
# CONVOY PHASE EXECUTION
# ════════════════════════════════════════

def execute_convoy_phase(
    state: GameState,
    interception_rolls: Optional[dict] = None,
    loss_rolls: Optional[dict] = None,
) -> ConvoyPhaseResult:
    """
    Execute the Convoy Arrival phase.
    1. Process all planned convoys
    2. Check for interceptions (if fleet sortied)
    3. Unload at destination ports
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage
    int_rolls = interception_rolls or {}
    l_rolls = loss_rolls or {}

    total_shipped = 0.0
    total_lost = 0.0
    total_delivered = 0.0
    interceptions = []
    port_unloads = []
    convoys_planned = len(state.axis_convoy.planned_tonnage)

    fleet_active = (state.cw_fleet.is_available and
                    state.cw_fleet.ships_committed > 0)

    for port, tonnage in state.axis_convoy.planned_tonnage.items():
        total_shipped += tonnage

        # Check interception
        if fleet_active:
            is_reconned = port in state.axis_convoy.lanes_reconned
            has_cap = len(state.axis_convoy.cap_assigned) > 0

            intercept = attempt_interception(
                state, port, tonnage,
                is_reconned=is_reconned,
                has_cap=has_cap,
                interception_roll=int_rolls.get(port),
                loss_roll=l_rolls.get(port),
            )
            interceptions.append(intercept)
            delivered = intercept.tonnage_delivered
            total_lost += intercept.tonnage_lost
        else:
            delivered = tonnage

        total_delivered += delivered

        # Unload
        if delivered > 0:
            unload = unload_at_port(state, port, delivered)
            port_unloads.append(unload)

    desc = (f"GT{gt} Convoy Phase: {convoys_planned} convoys, "
            f"{total_shipped:.0f} tons shipped, {total_lost:.0f} lost, "
            f"{total_delivered:.0f} delivered")

    state.log_event("convoy_phase", desc, game_turn=gt, op_stage=op)

    return ConvoyPhaseResult(
        game_turn=gt, op_stage=op,
        convoys_planned=convoys_planned,
        tonnage_shipped=total_shipped,
        tonnage_lost=total_lost,
        tonnage_delivered=total_delivered,
        interceptions=interceptions,
        port_unloads=port_unloads,
        description=desc,
    )


# ════════════════════════════════════════
# FLEET MANAGEMENT
# ════════════════════════════════════════

def reset_fleet_for_turn(state: GameState):
    """Reset fleet state at start of a new game turn."""
    fleet = state.cw_fleet
    if fleet.repair_turns_remaining > 0:
        fleet.repair_turns_remaining -= 1
        if fleet.repair_turns_remaining == 0:
            fleet.is_available = True

    fleet.sorties_remaining = MAX_FLEET_SORTIES_PER_TURN
    fleet.ships_committed = 0
    fleet.current_hex = None


def reset_convoy_for_turn(state: GameState):
    """Reset convoy state at start of a new game turn."""
    state.axis_convoy = ConvoyState()
