"""
CNA Engine — Smart Mock LLM Client
State-aware mock that generates intelligent moves using direct
game state access. Same interface as OllamaClient (.chat(), .is_available())
but needs no live LLM. Uses per-role strategy functions.

Separate from MockLLMClient to avoid breaking existing orchestrator tests.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Optional

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import (
    Side, UnitStatus, AircraftStatus, MotorizationType,
)
from cna_engine.engine.movement import get_neighbors, parse_hex_id
from cna_engine.engine.agent_interface import ROLE_COMMANDS

from .config import OrchestratorConfig
from .llm_backend import LLMResponse

logger = logging.getLogger(__name__)


class SmartMockLLMClient:
    """
    Mock LLM client that generates intelligent, state-aware responses.
    Uses per-role strategy functions to produce valid commands referencing
    real unit IDs and hex IDs from the game state.

    Usage:
        client = SmartMockLLMClient(state)
        orch.setup(llm_client=client)
        # Client will produce real moves instead of end_phase stubs
    """

    def __init__(self, state: GameState, config: Optional[OrchestratorConfig] = None):
        self.state = state
        self.config = config or OrchestratorConfig()
        self.call_log: list[dict] = []

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Generate a state-aware response based on detected role."""
        self.call_log.append({"messages": messages, "json_mode": json_mode})

        role = self._detect_role(messages)
        side = self._detect_side(messages)

        if role == "commander":
            response_dict = _commander_strategy(self.state, side, messages)
        elif role == "ground":
            response_dict = _ground_strategy(self.state, side)
        elif role == "logistics":
            response_dict = _logistics_strategy(self.state, side)
        elif role == "air":
            response_dict = _air_strategy(self.state, side)
        elif role == "naval":
            response_dict = _naval_strategy(self.state, side)
        else:
            response_dict = _fallback_response(role)

        content = json.dumps(response_dict)
        return LLMResponse(
            content=content,
            parsed=response_dict,
            model="smart-mock",
            duration_ms=0,
        )

    def is_available(self) -> bool:
        return True

    def _detect_role(self, messages: list[dict]) -> str:
        """Detect agent role from system prompt."""
        system_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "").lower()
                break

        if "theater commander" in system_text or "general" in system_text:
            return "commander"
        elif "ground operations" in system_text:
            return "ground"
        elif "supply officer" in system_text or "logistics" in system_text:
            return "logistics"
        elif "air operations" in system_text:
            return "air"
        elif "naval" in system_text:
            return "naval"
        return "unknown"

    def _detect_side(self, messages: list[dict]) -> str:
        """Detect which side from system prompt."""
        system_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "").lower()
                break

        if "axis" in system_text:
            return "axis"
        return "allied"


# ════════════════════════════════════════
# PER-ROLE STRATEGY FUNCTIONS
# ════════════════════════════════════════

def _get_active_units(state: GameState, side: str) -> list[Unit]:
    """Get all active, on-map units for a side."""
    return [
        u for u in state.units.values()
        if u.side == side and u.status == UnitStatus.ACTIVE and u.hex_id
    ]


def _get_enemy_hexes(state: GameState, side: str) -> set[str]:
    """Get set of hex IDs containing enemy units."""
    enemy_side = "axis" if side == "allied" else "allied"
    hexes = set()
    for u in state.units.values():
        if u.side == enemy_side and u.status == UnitStatus.ACTIVE and u.hex_id:
            hexes.add(u.hex_id)
    return hexes


def _hex_distance_approx(hex1: str, hex2: str) -> int:
    """Approximate hex distance using parsed coordinates."""
    try:
        c1 = parse_hex_id(hex1)
        c2 = parse_hex_id(hex2)
        if c1.section != c2.section:
            return 99  # Different map sections
        dc = abs(c1.col - c2.col)
        dr = abs(c1.row - c2.row)
        return max(dc, dr, (dc + dr + 1) // 2)
    except (ValueError, AttributeError):
        return 99


def _find_closest_enemy_hex(state: GameState, unit: Unit, enemy_hexes: set[str]) -> Optional[str]:
    """Find the closest enemy-occupied hex to a unit."""
    if not unit.hex_id or not enemy_hexes:
        return None
    best_hex = None
    best_dist = 999
    for eh in enemy_hexes:
        d = _hex_distance_approx(unit.hex_id, eh)
        if d < best_dist:
            best_dist = d
            best_hex = eh
    return best_hex


def _find_step_toward(unit_hex: str, target_hex: str, state: GameState) -> Optional[str]:
    """Find the best adjacent hex that moves toward target."""
    neighbors = get_neighbors(unit_hex)
    best = None
    best_dist = _hex_distance_approx(unit_hex, target_hex)

    for n in neighbors:
        d = _hex_distance_approx(n, target_hex)
        if d < best_dist:
            # Prefer hexes that exist in state (valid map hexes)
            best_dist = d
            best = n

    return best


def _ground_strategy(state: GameState, side: str) -> dict:
    """
    Ground expert strategy:
    - Find units with CPA remaining
    - Find closest enemy
    - Recommend move_unit toward enemy or fire_barrage if adjacent
    """
    units = _get_active_units(state, side)
    enemy_hexes = _get_enemy_hexes(state, side)

    recs = []
    concerns = []

    for unit in units:
        if unit.effective_cpa <= 0:
            continue
        if unit.unit_class in ("hq", "truck", "sgsu", "supply_dump"):
            continue

        closest = _find_closest_enemy_hex(state, unit, enemy_hexes)
        if not closest:
            continue

        dist = _hex_distance_approx(unit.hex_id, closest)

        if dist <= 1:
            # Adjacent to enemy — recommend barrage then assault
            recs.append({
                "action": "fire_barrage",
                "params": {"target_hex": closest, "target_class": "infantry"},
                "reasoning": f"{unit.name} adjacent to enemy at {closest}, fire barrage",
            })
            recs.append({
                "action": "close_assault",
                "params": {"target_hex": closest},
                "reasoning": f"Assault enemy at {closest} after barrage",
            })
        elif unit.effective_cpa >= 2:
            # Move toward enemy
            step = _find_step_toward(unit.hex_id, closest, state)
            if step:
                recs.append({
                    "action": "move_unit",
                    "params": {"unit_id": unit.id, "destination": step},
                    "reasoning": f"Advance {unit.name} toward enemy (dist {dist})",
                })

        if len(recs) >= 6:
            break

    # Check for units in contact
    in_contact = [u for u in units if u.is_in_contact]
    if in_contact:
        concerns.append(f"{len(in_contact)} units in enemy contact")

    assessment = f"{len(units)} active units, {len(enemy_hexes)} enemy positions known"
    if recs:
        assessment += f". Recommending {len(recs)} actions."
    else:
        assessment += ". No immediate actions needed."

    return {
        "role": "ground",
        "assessment": assessment,
        "priority": "high" if recs else "medium",
        "recommendations": recs,
        "concerns": concerns,
    }


def _logistics_strategy(state: GameState, side: str) -> dict:
    """
    Logistics expert strategy:
    - Scan units for <25% fuel/water
    - Recommend draw_from_dump or flag concerns
    """
    units = _get_active_units(state, side)
    recs = []
    concerns = []
    critical_units = []

    for unit in units:
        f_cap = unit.supply.fuel_capacity or 1.0
        w_cap = unit.supply.water_capacity or 1.0
        f_pct = (unit.supply.fuel / f_cap) * 100.0
        w_pct = (unit.supply.water / w_cap) * 100.0

        if f_pct < 25 or w_pct < 25:
            critical_units.append(unit)
            # Look for nearby supply dump
            if unit.hex_id:
                hex_state = state.hexes.get(unit.hex_id)
                if hex_state and hex_state.supply_dumps:
                    dump = hex_state.supply_dumps[0]
                    if dump.side == side and dump.is_real:
                        supply_type = "fuel" if f_pct < w_pct else "water"
                        recs.append({
                            "action": "draw_from_dump",
                            "params": {
                                "unit_id": unit.id,
                                "dump_id": dump.id,
                                "supply_type": supply_type,
                                "amount": 5.0,
                            },
                            "reasoning": f"{unit.name} critically low on {supply_type} ({f_pct:.0f}% fuel, {w_pct:.0f}% water)",
                        })

            if f_pct < 10:
                concerns.append(f"{unit.name}: CRITICAL fuel ({f_pct:.0f}%)")
            if w_pct < 10:
                concerns.append(f"{unit.name}: CRITICAL water ({w_pct:.0f}%)")

    if critical_units:
        assessment = f"{len(critical_units)} of {len(units)} units critically low on supplies"
        priority = "high"
    else:
        assessment = f"All {len(units)} units adequately supplied"
        priority = "low"

    return {
        "role": "logistics",
        "assessment": assessment,
        "priority": priority,
        "recommendations": recs[:3],
        "concerns": concerns[:5],
    }


def _air_strategy(state: GameState, side: str) -> dict:
    """
    Air expert strategy:
    - If ready aircraft exist, issue fly_sortie orders for recon/bombing
    - Returns orders format for situation engine compatibility
    """
    ready_aircraft = [
        ac for ac in state.aircraft.values()
        if ac.side == side and ac.status == AircraftStatus.READY
    ]

    orders = []

    if ready_aircraft:
        # Find enemy-occupied hexes for targeting
        enemy_attr = "axis_unit_ids" if side == Side.ALLIED.value else "allied_unit_ids"
        enemy_hexes = []
        for hex_id, hs in state.hexes.items():
            if getattr(hs, enemy_attr, []):
                enemy_hexes.append(hex_id)

        for i, ac in enumerate(ready_aircraft[:3]):
            if enemy_hexes and i == 0:
                # First aircraft: recon over enemy position
                target = enemy_hexes[0]
                orders.append({
                    "command": "fly_sortie",
                    "params": {
                        "aircraft_id": ac.id,
                        "mission": "recon",
                        "target_hex": target,
                    },
                })
            elif enemy_hexes and ac.bombload_remaining > 0:
                # Bombers: bombing mission
                target = enemy_hexes[min(i, len(enemy_hexes) - 1)]
                orders.append({
                    "command": "fly_sortie",
                    "params": {
                        "aircraft_id": ac.id,
                        "mission": "bombing",
                        "target_hex": target,
                        "target_class": "infantry",
                    },
                })
            elif ac.tacair_remaining > 0 and not ac.bombload_remaining:
                # Fighters: assign OCAP
                orders.append({
                    "command": "assign_mission",
                    "params": {
                        "aircraft_id": ac.id,
                        "mission": "ocap",
                    },
                })

    orders.append({"command": "end_phase", "params": {}})

    # Build expert-format recommendations from orders (for General pipeline)
    recs = []
    for o in orders:
        if o["command"] != "end_phase":
            recs.append({
                "action": o["command"],
                "params": o["params"],
                "reasoning": f"Mock: {o['command']} with {o['params'].get('aircraft_id', '?')}",
            })

    return {
        # For Situation Engine (reads "orders")
        "orders": orders,
        "reasoning": f"Mock air strategy: {len(ready_aircraft)} aircraft ready, {len(orders)-1} missions assigned",
        # For Expert pipeline (reads "role", "assessment", "recommendations")
        "role": "air",
        "assessment": f"{len(ready_aircraft)} aircraft ready for operations",
        "priority": "medium" if ready_aircraft else "low",
        "recommendations": recs,
        "concerns": [],
    }


def _naval_strategy(state: GameState, side: str) -> dict:
    """
    Naval expert strategy:
    - Allied: recommend fleet_sortie if fleet available
    - Axis: recommend plan_convoy
    """
    recs = []
    concerns = []

    if side == "allied":
        fleet = state.cw_fleet
        if fleet.is_available and fleet.sorties_remaining > 0:
            recs.append({
                "action": "fleet_sortie",
                "params": {},
                "reasoning": f"Fleet available with {fleet.sorties_remaining} sorties remaining",
            })
            assessment = f"Fleet available, {fleet.sorties_remaining} sorties remaining"
            priority = "medium"
        else:
            assessment = "Fleet unavailable"
            priority = "low"
            if fleet.repair_turns_remaining > 0:
                concerns.append(f"Fleet in repair: {fleet.repair_turns_remaining} turns remaining")
    else:
        # Axis: convoy planning
        convoy = state.axis_convoy
        recs.append({
            "action": "plan_convoy",
            "params": {"tonnage": {"tripoli": 100.0}},
            "reasoning": "Schedule supply convoy to maintain logistics pipeline",
        })
        delivered = sum(convoy.actual_tonnage_delivered.values())
        assessment = f"Convoy status: {delivered:.0f} tons delivered this turn"
        priority = "medium"
        if convoy.losses_this_turn > 0:
            concerns.append(f"Convoy losses: {convoy.losses_this_turn:.0f} tons this turn")

    return {
        "role": "naval",
        "assessment": assessment,
        "priority": priority,
        "recommendations": recs,
        "concerns": concerns,
    }


def _commander_strategy(state: GameState, side: str, messages: list[dict]) -> dict:
    """
    Commander strategy:
    - Parse expert recommendations from the user message text
    - Pass through high-priority recommendations as orders (with params)
    - Always include end_phase
    """
    import ast
    import re

    orders = []
    reasoning_parts = []

    # Extract user message content (contains expert reports)
    user_text = ""
    for msg in messages:
        if msg.get("role") == "user":
            user_text = msg.get("content", "")
            break

    # Valid action keywords
    action_keywords = set()
    for role_cmds in ROLE_COMMANDS.values():
        action_keywords.update(role_cmds)

    # Parse "  - action_name: reasoning\n    params: {dict}" pairs
    action_pattern = re.compile(
        r'  - ([\w_]+):[^\n]*\n\s*params:\s*(\{[^\n]+\})',
        re.MULTILINE,
    )
    for match in action_pattern.finditer(user_text):
        action = match.group(1)
        params_str = match.group(2)
        if action not in action_keywords or action == "end_phase":
            continue
        try:
            params = ast.literal_eval(params_str)
        except (ValueError, SyntaxError):
            params = {}
        orders.append({"command": action, "params": params})
        reasoning_parts.append(f"Approved {action} from staff recommendation")

    # Fallback: actions without parseable params (just action name)
    simple_pattern = re.compile(r'  - ([\w_]+):')
    seen_actions = {o["command"] for o in orders}
    for match in simple_pattern.finditer(user_text):
        action = match.group(1)
        if action in action_keywords and action != "end_phase" and action not in seen_actions:
            orders.append({"command": action, "params": {}})
            reasoning_parts.append(f"Approved {action} from staff recommendation")
            seen_actions.add(action)

    # Check for "high" priority assessments
    if "(priority: high)" in user_text.lower():
        reasoning_parts.append("Acting on high-priority expert assessments")

    if not reasoning_parts:
        reasoning_parts.append("No actionable expert recommendations; ending phase")

    orders.append({"command": "end_phase", "params": {}})

    return {
        "orders": orders,
        "end_phase": True,
        "reasoning": ". ".join(reasoning_parts) + ".",
    }


def _fallback_response(role: str) -> dict:
    """Fallback for unknown roles."""
    return {
        "role": role,
        "assessment": f"Smart mock {role}: no strategy available",
        "priority": "low",
        "recommendations": [],
        "concerns": [],
    }
