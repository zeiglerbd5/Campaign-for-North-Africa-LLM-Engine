"""
CNA Engine — Situation Taxonomy & Signal Extraction
Classifies the battlefield state into discrete situations that map to
pre-written playbooks. Enables the two-stage situation-action pipeline.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, UnitStatus, MotorizationType

logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# SITUATION TAXONOMY
# ════════════════════════════════════════

class SituationCategory(str, Enum):
    FRONT_LINE = "front_line"
    LOGISTICS = "logistics"
    REAR_AREA = "rear_area"
    AIR = "air"
    CINC = "cinc"


# All recognized situations, grouped by category
SITUATION_TAXONOMY: dict[str, dict[str, str]] = {
    # Front-line situations
    "ADVANCE_OPPORTUNITY": "No enemy contact — advance toward objectives freely",
    "ATTACK_PREPARED": "Adjacent to enemy with favorable or even ratios — execute planned assault",
    "ATTACK_IMMINENT": "Enemy approaching, contact expected soon — prepare positions",
    "DEFENSIVE_HOLD": "Hold current positions, barrage approaching enemy",
    "DEFENSIVE_SIEGE": "Defending fortified position against superior force",
    "BREAKTHROUGH_EXPLOIT": "Enemy line broken — exploit with mobile units",
    "FIGHTING_RETREAT": "Outnumbered, withdraw toward defensive line while delaying",
    "ROUT": "Catastrophic losses, no coherent defense possible — flee to safety",
    "OVEREXTENDED_HALT": "Advanced too far from supply — halt and consolidate",

    # Logistics situations
    "CONVOY_PLANNING": "Plan convoy deliveries to ports",
    "DUMP_BUILDING": "Build supply dumps behind front line for next offensive",
    "SUPPLY_FLOWING": "Routine supply operations — truck load/unload, monitor levels",
    "SUPPLY_CRITICAL_FUEL": "Emergency fuel resupply to motorized units",
    "SUPPLY_CRITICAL_AMMO": "Emergency ammo resupply to combat units",
    "SUPPLY_CRITICAL_WATER": "Emergency water resupply — units will take casualties without it",
    "PORT_DEGRADED": "Port capacity reduced — adjust convoy planning",
    "DUMP_THREATENED": "Supply dump in danger of enemy capture",
    "DUMP_CAPTURED": "Enemy captured our dump — redirect logistics",

    # Rear-area situations
    "CONSTRUCTION_PRIORITY": "Build or repair infrastructure",
    "REINFORCEMENT_ARRIVING": "New units arriving — position them appropriately",
    "REPAIR_OPERATIONS": "Units under repair, manage replacement flow",
    "REAR_THREAT": "Enemy forces threatening rear areas",

    # Air situations
    "AIR_SUPERIORITY_HELD": "We control the air — use for ground support",
    "AIR_PARITY": "Even air situation — balance recon and interdiction",
    "AIR_INFERIORITY": "Enemy air dominance — prioritize air defense",
    "CONVOY_INTERDICTION": "Interdict enemy convoys with air assets",
    "CONVOY_DEFENSE": "Protect our convoys from enemy air attack",
    "GROUND_SUPPORT_URGENT": "Ground forces need immediate air support",
    "MALTA_DECISION": "Strategic decision on Malta operations",

    # CinC (Commander-in-Chief) situations
    "CAMPAIGN_ADVANCE": "Overall offensive — push forward on all fronts",
    "CAMPAIGN_CONSOLIDATE": "Halt advance, build up supply, absorb replacements",
    "CAMPAIGN_DEFEND": "Overall defensive posture — hold what we have",
    "CAMPAIGN_COUNTERATTACK": "Launch counterattack to regain lost ground",
    "VP_CRISIS": "VP deficit critical — need drastic action",
    "COORDINATION_NEEDED": "Multiple fronts need synchronized action",
}

# Map situation names to their categories
SITUATION_TO_CATEGORY: dict[str, str] = {}
_FRONT_LINE = {
    "ADVANCE_OPPORTUNITY", "ATTACK_PREPARED", "ATTACK_IMMINENT",
    "DEFENSIVE_HOLD", "DEFENSIVE_SIEGE", "BREAKTHROUGH_EXPLOIT",
    "FIGHTING_RETREAT", "ROUT", "OVEREXTENDED_HALT",
}
_LOGISTICS = {
    "CONVOY_PLANNING", "DUMP_BUILDING", "SUPPLY_FLOWING",
    "SUPPLY_CRITICAL_FUEL", "SUPPLY_CRITICAL_AMMO", "SUPPLY_CRITICAL_WATER",
    "PORT_DEGRADED", "DUMP_THREATENED", "DUMP_CAPTURED",
}
_REAR_AREA = {
    "CONSTRUCTION_PRIORITY", "REINFORCEMENT_ARRIVING",
    "REPAIR_OPERATIONS", "REAR_THREAT",
}
_AIR = {
    "AIR_SUPERIORITY_HELD", "AIR_PARITY", "AIR_INFERIORITY",
    "CONVOY_INTERDICTION", "CONVOY_DEFENSE", "GROUND_SUPPORT_URGENT",
    "MALTA_DECISION",
}
_CINC = {
    "CAMPAIGN_ADVANCE", "CAMPAIGN_CONSOLIDATE", "CAMPAIGN_DEFEND",
    "CAMPAIGN_COUNTERATTACK", "VP_CRISIS", "COORDINATION_NEEDED",
}
for _name in _FRONT_LINE:
    SITUATION_TO_CATEGORY[_name] = SituationCategory.FRONT_LINE
for _name in _LOGISTICS:
    SITUATION_TO_CATEGORY[_name] = SituationCategory.LOGISTICS
for _name in _REAR_AREA:
    SITUATION_TO_CATEGORY[_name] = SituationCategory.REAR_AREA
for _name in _AIR:
    SITUATION_TO_CATEGORY[_name] = SituationCategory.AIR
for _name in _CINC:
    SITUATION_TO_CATEGORY[_name] = SituationCategory.CINC


# ════════════════════════════════════════
# SITUATION LABEL
# ════════════════════════════════════════

@dataclass
class SituationLabel:
    """Result of situation classification."""
    role: str                          # "front_line", "logistics", "air", "cinc"
    situation: str                     # e.g. "ATTACK_PREPARED"
    confidence: float = 1.0            # 0.0 - 1.0
    reasoning: str = ""                # Why this was chosen
    secondary_situation: str = ""      # Optional second situation (for multi-role phases)
    deterministic: bool = False        # True if classified by rule, not LLM


# ════════════════════════════════════════
# STATE SIGNALS — Pure Python extraction
# ════════════════════════════════════════

@dataclass
class StateSignals:
    """Compact battlefield signals extracted from GameState. Pure Python, no LLM."""
    # Identity
    side: str = ""
    phase: str = ""
    game_turn: int = 0
    op_stage: int = 0

    # Forces
    active_units: int = 0
    total_strength: int = 0
    enemy_units_sighted: int = 0
    enemy_total_strength: int = 0

    # Contact
    units_in_contact: int = 0
    units_adjacent_to_enemy: int = 0
    contact_force_ratios: list[dict] = field(default_factory=list)
    best_assault_ratio: float = 0.0

    # Supply
    avg_fuel_pct: float = 0.0
    avg_water_pct: float = 0.0
    fuel_critical_count: int = 0
    water_critical_count: int = 0
    any_zero_water: bool = False
    any_zero_fuel: bool = False
    dumps_count: int = 0
    nearest_dump_distance: int = 999

    # VP
    objectives_held: list[str] = field(default_factory=list)
    objectives_contested: list[str] = field(default_factory=list)
    vp_margin: float = 0.0
    vp_leading: bool = False

    # Momentum
    units_lost_last_turn: int = 0
    enemy_units_lost_last_turn: int = 0
    advance_or_retreat: str = "static"  # "advance", "retreat", "static"

    # Overextension
    furthest_unit_from_port: int = 0
    overextended: bool = False

    # Stalemate detection
    consecutive_failed_advances: int = 0

    # Motorized force info
    motorized_count: int = 0
    motorized_fuel_critical: int = 0

    # Air
    aircraft_ready: int = 0
    aircraft_total: int = 0
    enemy_aircraft_ready: int = 0
    enemy_aircraft_total: int = 0
    air_superiority_ratio: float = 1.0   # friendly_ready / enemy_ready
    fighters_ready: int = 0
    bombers_ready: int = 0
    enemy_fighters_ready: int = 0
    enemy_bombers_ready: int = 0
    sgsus_operational: int = 0
    unsighted_enemy_hexes: int = 0       # enemy hexes without recent recon
    ground_support_needed: bool = False   # True if units_in_contact > 0 and friendly bombers available

    # Naval / Fleet
    fleet_available: bool = False
    fleet_sorties_remaining: int = 0
    fleet_under_repair: bool = False
    convoy_tonnage_planned: float = 0.0
    convoy_tonnage_delivered: float = 0.0
    convoy_losses: float = 0.0
    ports_held: int = 0
    ports_enemy: int = 0
    nearest_port_distance: int = 999
    supply_pool_fuel: float = 0.0
    supply_pool_water: float = 0.0


def extract_signals(state: GameState, side: str, phase: str) -> StateSignals:
    """
    Extract compact battlefield signals from full game state.
    Reuses existing helpers from agent_interface.py and engine modules.
    Pure Python — no LLM calls.
    """
    from cna_engine.engine.agent_interface import (
        _get_supply_summary, _get_contact_force_ratios, _get_sighted_enemy_units,
    )
    from cna_engine.engine.movement import get_neighbors, _hex_distance

    signals = StateSignals(
        side=side,
        phase=phase,
        game_turn=state.turn.game_turn,
        op_stage=state.turn.op_stage,
    )

    # ── Forces ──
    friendly_units = [
        u for u in state.units.values()
        if u.side == side and u.status not in
        (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN, UnitStatus.NOT_YET_ARRIVED)
    ]
    signals.active_units = len(friendly_units)
    signals.total_strength = sum(u.current_strength.total for u in friendly_units)

    enemy_sighted = _get_sighted_enemy_units(state, side)
    signals.enemy_units_sighted = len(enemy_sighted)
    signals.enemy_total_strength = sum(e.get("strength", 0) for e in enemy_sighted)

    # ── Contact ──
    contact_ratios = _get_contact_force_ratios(state, side)
    signals.contact_force_ratios = contact_ratios
    signals.units_in_contact = len(contact_ratios)

    # Count units adjacent to enemy
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
    adj_count = 0
    for hex_id, hs in state.hexes.items():
        if not getattr(hs, friendly_attr):
            continue
        for adj_id in get_neighbors(hex_id):
            adj = state.hexes.get(adj_id)
            if adj and getattr(adj, enemy_attr):
                adj_count += len(getattr(hs, friendly_attr))
                break
    signals.units_adjacent_to_enemy = adj_count

    # Best assault ratio
    if contact_ratios:
        best = max(
            (float(cr["ratio"].replace(":1", "")) if "inf" not in cr["ratio"] else 99.0)
            for cr in contact_ratios
        )
        signals.best_assault_ratio = best

    # ── Supply ──
    supply_summary = _get_supply_summary(state, side)
    fuel_critical_ids = supply_summary.get("fuel_critical_units", [])
    water_critical_ids = supply_summary.get("water_critical_units", [])
    signals.fuel_critical_count = len(fuel_critical_ids)
    signals.water_critical_count = len(water_critical_ids)

    # Average fuel/water percentages
    if friendly_units:
        fuel_pcts = []
        water_pcts = []
        for u in friendly_units:
            f_cap = u.supply.fuel_capacity or 1.0
            w_cap = u.supply.water_capacity or 1.0
            fuel_pcts.append(u.supply.fuel / f_cap)
            water_pcts.append(u.supply.water / w_cap)
        signals.avg_fuel_pct = round(sum(fuel_pcts) / len(fuel_pcts) * 100, 1)
        signals.avg_water_pct = round(sum(water_pcts) / len(water_pcts) * 100, 1)

    # Zero checks
    signals.any_zero_water = any(
        u.supply.water <= 0 and u.supply.water_capacity > 0
        for u in friendly_units
    )
    signals.any_zero_fuel = any(
        u.supply.fuel <= 0 and u.supply.fuel_capacity > 0
        for u in friendly_units
    )

    # Dumps
    signals.dumps_count = sum(
        len(hs.supply_dumps) for hs in state.hexes.values()
        if getattr(hs, friendly_attr)
    )

    # Nearest dump distance from front (approx)
    front_hexes = [
        hex_id for hex_id, hs in state.hexes.items()
        if getattr(hs, friendly_attr) and any(
            getattr(state.hexes.get(adj, None), enemy_attr, [])
            for adj in get_neighbors(hex_id)
            if state.hexes.get(adj)
        )
    ]
    dump_hexes = [
        hex_id for hex_id, hs in state.hexes.items()
        if hs.supply_dumps
    ]
    if front_hexes and dump_hexes:
        min_dist = min(
            _hex_distance(fh, dh)
            for fh in front_hexes
            for dh in dump_hexes
        )
        signals.nearest_dump_distance = min_dist

    # ── VP ──
    try:
        from cna_engine.engine.victory import assess_victory
        vp = assess_victory(state)
        signals.vp_margin = vp.margin if side == Side.ALLIED else -vp.margin
        signals.vp_leading = (vp.leading_side == side)
        # Objectives
        side_vp = vp.allied_vp if side == Side.ALLIED else vp.axis_vp
        signals.objectives_held = list(side_vp.objectives_held)
        # Contested = objectives where both sides have nearby units
        for obj in vp.objectives:
            if obj.controller is None and obj.hex_id:
                signals.objectives_contested.append(obj.hex_id)
    except Exception:
        pass

    # ── Momentum ──
    # Count recent losses from event_log
    current_gt = state.turn.game_turn
    for event in state.event_log:
        if event.get("gt") == current_gt - 1:
            etype = event.get("type", "")
            if etype in ("unit_destroyed", "sp_lost"):
                if event.get("side") == side:
                    signals.units_lost_last_turn += 1
                else:
                    signals.enemy_units_lost_last_turn += 1

    # ── Stalemate detection ──
    # Count consecutive recent turns where move_unit orders all failed
    # (0 successful moves). Scan backwards from current GT.
    failed_streak = 0
    for check_gt in range(current_gt - 1, max(0, current_gt - 10), -1):
        gt_moves = [
            e for e in state.event_log
            if e.get("gt") == check_gt
            and e.get("type") == "move_unit"
            and e.get("side", e.get("unit_side", "")) == side
        ]
        gt_successes = [e for e in gt_moves if e.get("success", True)]
        if gt_moves and not gt_successes:
            failed_streak += 1
        else:
            break
    signals.consecutive_failed_advances = failed_streak

    # ── Overextension ──
    # Find port hexes for this side
    port_hexes = [
        hex_id for hex_id, hs in state.hexes.items()
        if hs.is_port and getattr(hs, friendly_attr)
    ]
    if port_hexes and friendly_units:
        max_dist = 0
        for u in friendly_units:
            if u.hex_id:
                d = min(_hex_distance(u.hex_id, ph) for ph in port_hexes)
                max_dist = max(max_dist, d)
        signals.furthest_unit_from_port = max_dist
        # >30 hexes ≈ 60 MP equivalent → overextended
        signals.overextended = max_dist > 30

    # ── Motorized info ──
    for u in friendly_units:
        if u.motorization in (
            MotorizationType.MOTORIZED,
            MotorizationType.HISTORICALLY_MOTORIZED,
            MotorizationType.MECHANIZED,
        ):
            signals.motorized_count += 1
            if u.id in fuel_critical_ids:
                signals.motorized_fuel_critical += 1

    # ── Air ──
    from cna_engine.models.enums import AircraftStatus
    from cna_engine.engine.air import get_aircraft_stats

    enemy_side = "axis" if side == Side.ALLIED else "allied"
    sighted_attr = "allied_sighted" if side == Side.ALLIED else "axis_sighted"

    for ac in state.aircraft.values():
        if ac.status in (AircraftStatus.DESTROYED, AircraftStatus.NOT_YET_ARRIVED):
            continue
        _, tacair, bombload, _ = get_aircraft_stats(ac.aircraft_type_id)
        is_fighter = tacair > 0 and bombload == 0
        is_bomber = bombload > 0

        if ac.side == side:
            signals.aircraft_total += 1
            if ac.status == AircraftStatus.READY:
                signals.aircraft_ready += 1
                if is_fighter:
                    signals.fighters_ready += 1
                if is_bomber:
                    signals.bombers_ready += 1
        else:
            signals.enemy_aircraft_total += 1
            if ac.status == AircraftStatus.READY:
                signals.enemy_aircraft_ready += 1
                if is_fighter:
                    signals.enemy_fighters_ready += 1
                if is_bomber:
                    signals.enemy_bombers_ready += 1

    # Air superiority ratio
    if signals.enemy_aircraft_ready > 0:
        signals.air_superiority_ratio = round(
            signals.aircraft_ready / signals.enemy_aircraft_ready, 2)
    elif signals.aircraft_ready > 0:
        signals.air_superiority_ratio = 99.0  # Total superiority

    # Operational SGSUs
    signals.sgsus_operational = sum(
        1 for s in state.sgsus.values()
        if s.side == side and s.is_operational
    )

    # Unsighted enemy hexes (enemy units on map but not sighted by us)
    for u in state.units.values():
        if u.side == enemy_side and u.status == UnitStatus.ACTIVE and u.hex_id:
            hs = state.hexes.get(u.hex_id)
            if hs and not getattr(hs, sighted_attr, False):
                signals.unsighted_enemy_hexes += 1

    # Ground support needed
    signals.ground_support_needed = (
        signals.units_in_contact > 0 and signals.bombers_ready > 0
    )

    # ── Naval / Fleet ──
    fleet = state.cw_fleet
    convoy = state.axis_convoy

    if side == Side.ALLIED:
        signals.fleet_available = fleet.is_available
        signals.fleet_sorties_remaining = fleet.sorties_remaining
        signals.fleet_under_repair = fleet.repair_turns_remaining > 0
    else:
        signals.convoy_tonnage_planned = sum(convoy.planned_tonnage.values())
        signals.convoy_tonnage_delivered = sum(convoy.actual_tonnage_delivered.values())
        signals.convoy_losses = convoy.losses_this_turn

    # Port counting (reuse friendly_attr/enemy_attr already in scope)
    for hex_id, hs in state.hexes.items():
        if hs.is_port:
            if getattr(hs, friendly_attr):
                signals.ports_held += 1
            elif getattr(hs, enemy_attr):
                signals.ports_enemy += 1

    # Nearest friendly port to front (reuse front_hexes already computed)
    port_hexes_friendly = [
        hex_id for hex_id, hs in state.hexes.items()
        if hs.is_port and getattr(hs, friendly_attr)
    ]
    if front_hexes and port_hexes_friendly:
        signals.nearest_port_distance = min(
            _hex_distance(fh, ph) for fh in front_hexes for ph in port_hexes_friendly
        )

    # Supply pool
    if hasattr(state, 'supply_pool') and state.supply_pool:
        signals.supply_pool_fuel = getattr(state.supply_pool, 'fuel', 0.0)
        signals.supply_pool_water = getattr(state.supply_pool, 'water', 0.0)

    return signals


# ════════════════════════════════════════
# DETERMINISTIC OVERRIDES
# ════════════════════════════════════════

def deterministic_classify(signals: StateSignals) -> Optional[SituationLabel]:
    """
    Check for deterministic situation overrides that don't need LLM.
    Returns SituationLabel if a clear rule matches, else None.
    """
    # ── Air phase deterministic rules (check first — ground rules don't apply) ──
    if signals.phase == "air":
        if signals.aircraft_ready == 0:
            return SituationLabel(
                role="air", situation="AIR_PARITY",
                confidence=0.95,
                reasoning="No ready aircraft — end phase immediately",
                deterministic=True,
            )
        if signals.air_superiority_ratio >= 2.0:
            return SituationLabel(
                role="air", situation="AIR_SUPERIORITY_HELD",
                confidence=0.95,
                reasoning=f"Air superiority {signals.air_superiority_ratio:.1f}:1 — "
                          f"{signals.aircraft_ready} ready vs {signals.enemy_aircraft_ready} enemy",
                deterministic=True,
            )
        if signals.air_superiority_ratio <= 0.5:
            return SituationLabel(
                role="air", situation="AIR_INFERIORITY",
                confidence=0.95,
                reasoning=f"Air inferiority {signals.air_superiority_ratio:.1f}:1 — "
                          f"{signals.aircraft_ready} ready vs {signals.enemy_aircraft_ready} enemy",
                deterministic=True,
            )
        if signals.ground_support_needed:
            return SituationLabel(
                role="air", situation="GROUND_SUPPORT_URGENT",
                confidence=0.9,
                reasoning=f"{signals.units_in_contact} units in contact, "
                          f"{signals.bombers_ready} bombers available",
                deterministic=True,
            )
        # Default: parity
        return SituationLabel(
            role="air", situation="AIR_PARITY",
            confidence=0.85,
            reasoning=f"Air roughly even: {signals.aircraft_ready} vs {signals.enemy_aircraft_ready}",
            deterministic=True,
        )

    # ── Fleet/Convoy phase deterministic rules ──
    if signals.phase == "fleet":
        if signals.side == Side.ALLIED or signals.side == "allied":
            if signals.fleet_available and signals.fleet_sorties_remaining > 0:
                return SituationLabel(
                    role="logistics", situation="CONVOY_INTERDICTION",
                    confidence=0.9,
                    reasoning=f"Fleet available with {signals.fleet_sorties_remaining} sorties",
                    deterministic=True,
                )
            return SituationLabel(
                role="logistics", situation="CONVOY_PLANNING",
                confidence=0.85,
                reasoning="Fleet unavailable — manage port unloading",
                deterministic=True,
            )
        else:  # Axis
            if signals.convoy_losses > 0:
                return SituationLabel(
                    role="logistics", situation="CONVOY_DEFENSE",
                    confidence=0.9,
                    reasoning=f"Lost {signals.convoy_losses:.0f} tons — protect convoys",
                    deterministic=True,
                )
            return SituationLabel(
                role="logistics", situation="CONVOY_PLANNING",
                confidence=0.9,
                reasoning="Plan convoy deliveries to forward ports",
                deterministic=True,
            )

    # Emergency: supply crisis — check both water AND fuel
    water_crisis = signals.any_zero_water
    fuel_crisis = signals.any_zero_fuel and signals.motorized_fuel_critical > 0
    if water_crisis or fuel_crisis:
        # Water takes priority label, but carry fuel as secondary (and vice versa)
        if water_crisis:
            return SituationLabel(
                role="logistics",
                situation="SUPPLY_CRITICAL_WATER",
                confidence=1.0,
                reasoning="Units at zero water — immediate resupply required"
                          + ("; ALSO fuel crisis on motorized units" if fuel_crisis else ""),
                secondary_situation="SUPPLY_CRITICAL_FUEL" if fuel_crisis else "",
                deterministic=True,
            )
        else:
            return SituationLabel(
                role="logistics",
                situation="SUPPLY_CRITICAL_FUEL",
                confidence=1.0,
                reasoning="Motorized units at zero fuel — immobilized without resupply",
                deterministic=True,
            )

    # No active units → VP crisis
    if signals.active_units == 0:
        return SituationLabel(
            role="cinc",
            situation="VP_CRISIS",
            confidence=1.0,
            reasoning="No active units remaining",
            deterministic=True,
        )

    # Overextended with low fuel → halt
    if signals.overextended and signals.avg_fuel_pct < 30:
        return SituationLabel(
            role="front_line",
            situation="OVEREXTENDED_HALT",
            confidence=0.95,
            reasoning=f"Furthest unit {signals.furthest_unit_from_port} hexes from port, fuel at {signals.avg_fuel_pct}%",
            deterministic=True,
        )

    # No enemy contact → advance or supply flowing
    if signals.enemy_units_sighted == 0 and signals.units_in_contact == 0:
        if signals.phase in ("movement_combat",):
            return SituationLabel(
                role="front_line",
                situation="ADVANCE_OPPORTUNITY",
                confidence=0.9,
                reasoning="No enemy sighted or in contact — advance freely",
                deterministic=True,
            )
        else:
            return SituationLabel(
                role="logistics",
                situation="SUPPLY_FLOWING",
                confidence=0.9,
                reasoning="No enemy contact — routine supply operations",
                deterministic=True,
            )

    # CinC: VP deficit → offensive posture instead of consolidation
    if signals.vp_margin < -3 and signals.active_units > 0:
        return SituationLabel(
            role="cinc",
            situation="CAMPAIGN_ADVANCE",
            confidence=0.85,
            reasoning=f"VP deficit ({signals.vp_margin:.1f}) — offensive needed to close gap",
            deterministic=True,
        )

    return None


# ════════════════════════════════════════
# CLASSIFIER PROMPTS
# ════════════════════════════════════════

def build_classifier_system_prompt(side: str, phase: str, role_hint: str = "") -> str:
    """Build the ~400 token system prompt for situation classification."""
    # Build taxonomy description filtered by likely role
    if role_hint == "front_line":
        situations = {k: v for k, v in SITUATION_TAXONOMY.items()
                      if k in _FRONT_LINE}
    elif role_hint == "logistics":
        situations = {k: v for k, v in SITUATION_TAXONOMY.items()
                      if k in _LOGISTICS}
    elif role_hint == "air":
        situations = {k: v for k, v in SITUATION_TAXONOMY.items()
                      if k in _AIR}
    elif role_hint == "cinc":
        situations = {k: v for k, v in SITUATION_TAXONOMY.items()
                      if k in _CINC}
    else:
        # Show front-line + logistics for movement_combat
        situations = {k: v for k, v in SITUATION_TAXONOMY.items()
                      if k in _FRONT_LINE or k in _LOGISTICS}

    taxonomy_lines = "\n".join(
        f"  {name}: {desc}" for name, desc in situations.items()
    )

    side_name = "Allied (Commonwealth)" if side == Side.ALLIED else "Axis (Italian/German)"

    return f"""You are the {side_name} situation classifier for Campaign for North Africa.
Your job: read battlefield signals and classify the current situation.

Phase: {phase}

SITUATIONS:
{taxonomy_lines}

Respond with JSON only:
{{"role": "front_line|logistics|air|cinc", "situation": "SITUATION_NAME", "confidence": 0.0-1.0, "reasoning": "brief explanation", "secondary_situation": "SITUATION_NAME or empty"}}"""


def build_classifier_user_prompt(signals: StateSignals) -> str:
    """Build the ~150 token user prompt with battlefield signals."""
    lines = [
        f"side={signals.side} turn=GT{signals.game_turn} phase={signals.phase}",
        f"active_units={signals.active_units} strength={signals.total_strength}",
        f"enemy_sighted={signals.enemy_units_sighted} enemy_strength={signals.enemy_total_strength}",
        f"contact_count={signals.units_in_contact} adjacent={signals.units_adjacent_to_enemy}",
        f"best_ratio={signals.best_assault_ratio:.1f}:1",
        f"fuel_pct={signals.avg_fuel_pct:.0f}% water_pct={signals.avg_water_pct:.0f}%",
        f"fuel_critical={signals.fuel_critical_count} water_critical={signals.water_critical_count}",
        f"zero_water={signals.any_zero_water} zero_fuel={signals.any_zero_fuel}",
        f"dumps={signals.dumps_count} dump_dist={signals.nearest_dump_distance}",
        f"vp_margin={signals.vp_margin:+.1f} vp_leading={signals.vp_leading}",
        f"objectives_held={','.join(signals.objectives_held) or 'none'}",
        f"lost_last_turn={signals.units_lost_last_turn} enemy_lost={signals.enemy_units_lost_last_turn}",
        f"overextended={signals.overextended} motorized={signals.motorized_count}",
    ]

    # Air signals (only include when populated)
    if signals.aircraft_ready > 0 or signals.enemy_aircraft_ready > 0:
        lines.append(f"aircraft_ready={signals.aircraft_ready} "
                     f"fighters={signals.fighters_ready} bombers={signals.bombers_ready}")
        lines.append(f"enemy_aircraft={signals.enemy_aircraft_ready} "
                     f"enemy_fighters={signals.enemy_fighters_ready} "
                     f"enemy_bombers={signals.enemy_bombers_ready}")
        lines.append(f"air_ratio={signals.air_superiority_ratio:.1f}:1 "
                     f"bases={signals.sgsus_operational} "
                     f"unsighted={signals.unsighted_enemy_hexes}")

    # Naval signals
    if signals.fleet_available or signals.convoy_tonnage_planned > 0:
        lines.append(f"fleet_available={signals.fleet_available} "
                     f"sorties={signals.fleet_sorties_remaining} "
                     f"fleet_repair={signals.fleet_under_repair}")
        lines.append(f"convoy_planned={signals.convoy_tonnage_planned:.0f} "
                     f"convoy_delivered={signals.convoy_tonnage_delivered:.0f} "
                     f"convoy_losses={signals.convoy_losses:.0f}")
        lines.append(f"ports_held={signals.ports_held} ports_enemy={signals.ports_enemy} "
                     f"port_dist={signals.nearest_port_distance}")

    return "\n".join(lines)


def parse_classifier_response(parsed: dict) -> SituationLabel:
    """Parse LLM classifier response into SituationLabel."""
    situation = parsed.get("situation", "DEFENSIVE_HOLD")
    role = parsed.get("role", "front_line")
    confidence = float(parsed.get("confidence", 0.5))
    reasoning = parsed.get("reasoning", "")
    secondary = parsed.get("secondary_situation", "")

    # Validate situation exists in taxonomy
    if situation not in SITUATION_TAXONOMY:
        logger.warning("LLM returned unknown situation '%s', falling back to DEFENSIVE_HOLD", situation)
        situation = "DEFENSIVE_HOLD"
        role = "front_line"

    return SituationLabel(
        role=role,
        situation=situation,
        confidence=confidence,
        reasoning=reasoning,
        secondary_situation=secondary,
        deterministic=False,
    )
