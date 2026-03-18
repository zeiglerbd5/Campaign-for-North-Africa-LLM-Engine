"""
CNA Engine — LLM Agent Interface
Structured command protocol for LLM agents, role-based state filtering,
action validation, and response formatting.

Each agent role sees a filtered view of the game state relevant to their
responsibilities. Commands are validated before execution.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any

from cna_engine.models.game_state import GameState, Unit, HexState
from cna_engine.models.enums import (
    Side, UnitStatus, GamePhase, OpStagePhase, MotorizationType,
)
from cna_engine.models.serialization import state_summary


# ════════════════════════════════════════
# AGENT ROLES
# ════════════════════════════════════════

ROLE_COMMANDER = "commander"        # Strategic decisions, initiative, reserves
ROLE_GROUND = "ground"              # Movement and combat operations
ROLE_LOGISTICS = "logistics"        # Supply, trucks, dumps
ROLE_AIR = "air"                    # Air missions, aircraft management
ROLE_NAVAL = "naval"                # Fleet and convoy operations
ROLE_OBSERVER = "observer"          # Full read-only view (for analysis)


VALID_ROLES = frozenset({
    ROLE_COMMANDER, ROLE_GROUND, ROLE_LOGISTICS,
    ROLE_AIR, ROLE_NAVAL, ROLE_OBSERVER,
})


# ════════════════════════════════════════
# COMMAND TYPES
# ════════════════════════════════════════

# Commands each role can issue
ROLE_COMMANDS = {
    ROLE_COMMANDER: [
        "set_initiative", "place_reserve", "release_reserve",
        "attach_unit", "detach_unit", "end_phase",
    ],
    ROLE_GROUND: [
        "move_unit", "break_contact", "break_engaged",
        "fire_barrage", "fire_anti_armor", "close_assault",
        "reaction_move",
        "lay_minefield", "lay_fake_minefield", "clear_minefield",
        "end_phase",
    ],
    ROLE_LOGISTICS: [
        "consume_fuel", "expend_ammo", "check_supply",
        "truck_attach", "truck_detach", "truck_load", "truck_unload",
        "create_dump", "draw_from_dump", "draw_water", "draw_from_supply_pool",
        "check_supply_lines", "convoy_move", "end_phase",
    ],
    ROLE_AIR: [
        "assign_mission", "fly_sortie", "recon",
        "end_phase",
    ],
    ROLE_NAVAL: [
        "plan_convoy", "fleet_sortie", "unload_port",
        "end_phase",
    ],
    ROLE_OBSERVER: [],  # Read-only
}


# ════════════════════════════════════════
# COMMAND PARAMETER SCHEMAS (compact reference for LLM prompts)
# ════════════════════════════════════════

COMMAND_SCHEMAS = {
    "move_unit":        "unit_id (str), destination (str hex ID to move toward — path computed automatically)",
    "fire_barrage":     "target_hex (str enemy hex ID), target_class ('infantry'|'armor'|'gun'|'truck') — engine computes barrage points from friendly gun units in range",
    "fire_anti_armor":  "target_hex (str enemy hex ID) — engine computes AA points from friendly armor+gun units in range",
    "close_assault":    "target_hex (str enemy hex ID) — engine computes attacker/defender strengths from unit positions",
    "break_contact":    "unit_id (str) — unit must be engaged",
    "break_engaged":    "unit_id (str) — unit must be engaged",
    "reaction_move":    "unit_id (str), destination (str hex ID)",
    "place_reserve":    "unit_id (str)",
    "release_reserve":  "unit_id (str)",
    "attach_unit":      "unit_id (str), formation_id (str)",
    "detach_unit":      "unit_id (str)",
    "set_initiative":   "side (str, optional)",
    "consume_fuel":     "unit_id (str), cps_expended (float)",
    "expend_ammo":      "unit_id (str), action ('barrage'|'anti_armor'|'close_assault'|'anti_air')",
    "check_supply":     "unit_id (str)",
    "truck_attach":     "unit_id (str) — attaches an available truck to the unit; REQUIRED before truck_load/truck_unload",
    "truck_detach":     "unit_id (str)",
    "truck_load":       "unit_id (str), supplies ({fuel, water, ammo, stores}) — unit must have truck attached (truck_attach first)",
    "truck_unload":     "unit_id (str), supplies ({fuel, water, ammo, stores}) — unit must have truck attached",
    "create_dump":      "unit_id (str), dump_id (str) — creates supply dump at unit's hex",
    "draw_from_dump":   "unit_id (str), dump_id (str) — dump must exist at unit's SAME hex (dump_id = hex where dump was created)",
    "draw_water":       "unit_id (str) — ONLY works on oasis or bir terrain; does NOT work on clear/coastal/rough/sand. For desert water supply use truck_attach → truck_load → truck_unload chain instead",
    "draw_from_supply_pool": "unit_id (str), supplies ({fuel, water, ammo, stores}) — unit must be at a PORT hex; draws from Egypt/Tripoli supply pool",
    "check_supply_lines": "(no params)",
    "assign_mission":   "aircraft_id (str), mission (str), target_hex (str, optional)",
    "fly_sortie":       "aircraft_id (str), mission (str), target_hex (str)",
    "recon":            "aircraft_id (str), target_hex (str)",
    "plan_convoy":      "port (str — use exact port name from naval_summary, e.g. 'Alexandria', 'Tobruk', 'Tripoli'), tonnage (float)",
    "unload_port":      "port (str — exact port name), tonnage (float)",
    "fleet_sortie":     "target_area (str hex ID)",
    "lay_minefield":    "unit_id (str — ENGINEER only), hex_id (str — in or adjacent hex)",
    "lay_fake_minefield": "unit_id (str — ENGINEER only), hex_id (str — in or adjacent hex)",
    "clear_minefield":  "unit_id (str — ENGINEER only), hex_id (str — in or adjacent hex)",
    "convoy_move":      "unit_id (str — TRUCK/SGSU/REPLACEMENT/HQ unit), destination (str hex ID)",
    "end_phase":        "(no params)",
}


def format_command_reference(role: str) -> str:
    """
    Return a compact multi-line command reference for the given role,
    listing each available command with its parameter schema.
    """
    commands = ROLE_COMMANDS.get(role, [])
    if not commands:
        return ""
    lines = ["=== COMMAND REFERENCE ==="]
    for cmd in commands:
        schema = COMMAND_SCHEMAS.get(cmd, "")
        lines.append(f"  {cmd}({schema})")
    return "\n".join(lines)


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class CommandResult:
    """Result of executing an agent command."""
    success: bool
    command: str
    role: str
    side: str
    result: Optional[Any] = None
    error: Optional[str] = None
    description: str = ""


@dataclass
class StateView:
    """Filtered view of game state for an agent role."""
    role: str
    side: str
    game_turn: int
    op_stage: int
    phase: str
    sub_phase: Optional[str]
    active_side: Optional[str]
    available_commands: list[str] = field(default_factory=list)
    visible_units: list[dict] = field(default_factory=list)
    visible_hexes: list[dict] = field(default_factory=list)
    supply_summary: Optional[dict] = None
    air_summary: Optional[dict] = None
    naval_summary: Optional[dict] = None
    overview: Optional[dict] = None
    vp_summary: Optional[dict] = None
    description: str = ""


# ════════════════════════════════════════
# STATE VIEW GENERATION
# ════════════════════════════════════════

def get_state_view(
    state: GameState,
    role: str,
    side: str,
) -> StateView:
    """
    Generate a filtered view of the game state for an agent role.
    Each role sees only what's relevant to their responsibilities.
    """
    if role not in VALID_ROLES:
        return StateView(
            role=role, side=side,
            game_turn=state.turn.game_turn,
            op_stage=state.turn.op_stage,
            phase=state.turn.phase,
            sub_phase=state.turn.sub_phase,
            active_side=state.turn.active_side,
            description=f"Invalid role: {role}",
        )

    view = StateView(
        role=role, side=side,
        game_turn=state.turn.game_turn,
        op_stage=state.turn.op_stage,
        phase=state.turn.phase,
        sub_phase=state.turn.sub_phase,
        active_side=state.turn.active_side,
        available_commands=list(ROLE_COMMANDS.get(role, [])),
    )

    # Observer gets everything
    if role == ROLE_OBSERVER:
        view.overview = state_summary(state)
        view.visible_units = _get_all_units_view(state)
        view.visible_hexes = _get_all_hexes_view(state)
        view.description = "Full game state (observer)"
        return view

    # Friendly units visible to all roles
    view.visible_units = _get_units_for_side(state, side, role)

    # Sighted enemy units
    sighted_enemies = _get_sighted_enemy_units(state, side)
    view.visible_units.extend(sighted_enemies)

    # Visible hexes (friendly + sighted)
    view.visible_hexes = _get_visible_hexes(state, side)

    # Role-specific additions
    if role == ROLE_COMMANDER:
        view.overview = state_summary(state)
    elif role == ROLE_LOGISTICS:
        view.supply_summary = _get_supply_summary(state, side)
    elif role == ROLE_AIR:
        view.air_summary = _get_air_summary(state, side)
    elif role == ROLE_NAVAL:
        view.naval_summary = _get_naval_summary(state, side)

    # VP summary for all roles
    from cna_engine.engine.victory import build_vp_summary
    view.vp_summary = build_vp_summary(state, side)

    view.description = f"{role} view for {side}, GT{state.turn.game_turn}"
    return view


def _get_units_for_side(state: GameState, side: str, role: str) -> list[dict]:
    """Get friendly units filtered by role relevance."""
    units = []
    for uid, unit in state.units.items():
        if unit.side != side:
            continue
        if unit.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
            continue

        info = {
            "id": uid,
            "name": unit.name,
            "hex_id": unit.hex_id,
            "status": unit.status,
            "class": unit.unit_class,
            "strength": unit.current_strength.total,
        }

        if role in (ROLE_GROUND, ROLE_COMMANDER):
            cpa_remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
            info["cpa_remaining"] = cpa_remaining
            info["is_motorized"] = unit.is_motorized
            info["is_in_contact"] = unit.is_in_contact
            if unit.entrenchment_bonus > 0:
                info["entrenched"] = unit.entrenchment_bonus
            # Hide immovable units from ground expert — prevents wasted LLM orders
            if role == ROLE_GROUND and cpa_remaining <= 0:
                continue

        if role == ROLE_LOGISTICS:
            info["fuel"] = unit.supply.fuel
            info["fuel_capacity"] = unit.supply.fuel_capacity
            info["water"] = unit.supply.water
            info["water_capacity"] = unit.supply.water_capacity
            info["ammo"] = unit.supply.ammo
            info["stores"] = unit.supply.stores
            info["truck_points"] = unit.attached_truck_points
            info["has_truck"] = unit.attached_truck_points > 0

        units.append(info)

    # Compute suggested movement destinations for ground units (attacker only)
    if role == ROLE_GROUND and units and side == Side.ALLIED:
        suggestions = _compute_suggested_moves(state, side, units)
        for info in units:
            uid = info["id"]
            if uid in suggestions:
                info["suggested_move"] = suggestions[uid]

    return units


def _compute_suggested_moves(
    state: GameState, side: str, friendly_units: list[dict],
) -> dict[str, str]:
    """
    For each friendly unit with CPA, compute the best forward hex it can
    reach this phase by pathfinding toward the nearest enemy.
    Returns {unit_id: suggested_destination_hex}.
    """
    from cna_engine.engine.movement import find_path, _hex_distance
    from cna_engine.data.reference_data import ReferenceData

    # Collect enemy positions (on-map only)
    enemy_hexes = set()
    for uid, unit in state.units.items():
        if unit.side != side and unit.hex_id and unit.status not in (
            UnitStatus.DESTROYED, UnitStatus.WITHDRAWN,
            UnitStatus.NOT_YET_ARRIVED, UnitStatus.SURRENDERED,
        ):
            enemy_hexes.add(unit.hex_id)

    if not enemy_hexes:
        # Fallback: pathfind toward nearest uncontrolled objective hex
        from cna_engine.engine.victory import OBJECTIVE_HEXES
        target_hexes = set()
        friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
        for hex_id in OBJECTIVE_HEXES:
            hs = state.hexes.get(hex_id)
            if not hs or not getattr(hs, friendly_attr, []):
                target_hexes.add(hex_id)
        if not target_hexes:
            return {}
        enemy_hexes = target_hexes  # reuse pathfinding loop below

    ref = ReferenceData()
    suggestions: dict[str, str] = {}

    for info in friendly_units:
        uid = info["id"]
        unit = state.units.get(uid)
        if not unit or info.get("cpa_remaining", 0) <= 0:
            continue

        origin = unit.hex_id
        if not origin:
            continue
        # Find nearest enemy hex
        nearest = min(enemy_hexes, key=lambda h: _hex_distance(origin, h))

        # Pathfind toward it — returns partial path if can't reach fully
        path = find_path(state, ref, unit, nearest)
        if path and len(path) > 1:
            dest = path[-1]
            # Don't suggest current hex
            if dest != origin:
                suggestions[uid] = dest

    return suggestions


# ════════════════════════════════════════
# COMBAT HELPERS — gather forces, apply results
# ════════════════════════════════════════

def _gather_barrage_points(state: GameState, side: str, target_hex: str) -> int:
    """Sum gun SP from friendly units adjacent to (or in) the target hex."""
    from cna_engine.engine.movement import get_neighbors
    adj = set(get_neighbors(target_hex))
    total = 0
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    # Check adjacent hexes for friendly gun units
    for hex_id in adj:
        hs = state.hexes.get(hex_id)
        if not hs:
            continue
        for uid in getattr(hs, friendly_attr):
            unit = state.units.get(uid)
            if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                total += unit.current_strength.gun
    return total


def _gather_aa_points(state: GameState, side: str, target_hex: str) -> int:
    """Sum armor + gun SP from friendly units adjacent to the target hex."""
    from cna_engine.engine.movement import get_neighbors
    adj = set(get_neighbors(target_hex))
    total = 0
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    for hex_id in adj:
        hs = state.hexes.get(hex_id)
        if not hs:
            continue
        for uid in getattr(hs, friendly_attr):
            unit = state.units.get(uid)
            if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                total += unit.current_strength.armor + unit.current_strength.gun
    return total


def _gather_assault_strengths(
    state: GameState, side: str, target_hex: str,
) -> tuple[int, int, list[str], list[str], bool]:
    """
    Returns (atk_str, def_str, atk_ids, def_ids, all_defenders_pinned).
    Attackers = friendly units adjacent to target_hex.
    Defenders = enemy units IN target_hex.
    """
    from cna_engine.engine.movement import get_neighbors
    adj = set(get_neighbors(target_hex))
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    # Gather attackers from adjacent hexes
    atk_str = 0
    atk_ids = []
    for hex_id in adj:
        hs = state.hexes.get(hex_id)
        if not hs:
            continue
        for uid in getattr(hs, friendly_attr):
            unit = state.units.get(uid)
            if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                atk_str += unit.current_strength.total
                atk_ids.append(uid)

    # Gather defenders in target hex
    def_str = 0
    def_ids = []
    all_pinned = True
    ths = state.hexes.get(target_hex)
    if ths:
        for uid in getattr(ths, enemy_attr):
            unit = state.units.get(uid)
            if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                def_str += unit.current_strength.total
                def_ids.append(uid)
                if not unit.is_pinned:
                    all_pinned = False
    if not def_ids:
        all_pinned = False

    return atk_str, def_str, atk_ids, def_ids, all_pinned


_SP_CATEGORIES = ["infantry", "armor", "gun", "mg", "recon"]


def _distribute_sp_loss(unit: Unit, sp_to_lose: int) -> int:
    """
    Remove SP from the unit's largest strength category first.
    Returns actual SP removed (may be less if unit runs out).
    """
    removed = 0
    remaining = sp_to_lose
    cs = unit.current_strength
    while remaining > 0:
        # Find largest category with SP remaining
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
    # Check destruction
    if cs.total <= 0:
        unit.status = UnitStatus.DESTROYED
    return removed


def _apply_disorganization(unit: Unit, amount: int, reason: str, state: GameState):
    """Apply disorganization to a unit from combat effects."""
    if amount <= 0:
        return
    old = unit.disorganization
    unit.disorganization += amount
    state.log_event("disorganization",
                    f"{unit.name} disorg {old} → {unit.disorganization} ({reason})",
                    unit_id=unit.id)


def _apply_barrage_result(
    state: GameState, side: str, target_hex: str,
    target_class: str, result,
) -> dict:
    """Pin target-class units in hex, reduce SP from strongest first."""
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
    ths = state.hexes.get(target_hex)
    if not ths:
        return {"applied": False, "reason": "hex not found"}

    affected = []
    sp_lost_total = 0
    pinned_units = []

    # Find enemy units of the target class in the hex
    targets = []
    for uid in getattr(ths, enemy_attr):
        unit = state.units.get(uid)
        if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
            if unit.unit_class == target_class or target_class == "truck":
                targets.append(unit)

    # Sort by strength descending — apply losses to strongest first
    targets.sort(key=lambda u: u.current_strength.total, reverse=True)

    # Apply SP losses
    sp_remaining = result.strength_points_lost
    for unit in targets:
        if sp_remaining <= 0:
            break
        lost = _distribute_sp_loss(unit, sp_remaining)
        sp_remaining -= lost
        sp_lost_total += lost
        if lost > 0:
            affected.append(f"{unit.id} loses {lost} SP")
            _apply_disorganization(unit, 1, "barrage loss", state)
            if unit.status == UnitStatus.DESTROYED:
                state.log_event("unit_destroyed",
                    f"{unit.name} destroyed by barrage at {target_hex}",
                    unit_id=unit.id, cause="barrage", hex_id=target_hex)

    # Apply pinning
    if result.is_pinned:
        for unit in targets:
            if unit.status != UnitStatus.DESTROYED:
                unit.is_pinned = True
                pinned_units.append(unit.id)

    return {
        "applied": True,
        "sp_lost": sp_lost_total,
        "affected": affected,
        "pinned": pinned_units,
    }


def _apply_anti_armor_result(
    state: GameState, side: str, target_hex: str, result,
) -> dict:
    """Reduce armor_protection on enemy armor in hex, overflow to armor SP."""
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
    ths = state.hexes.get(target_hex)
    if not ths:
        return {"applied": False, "reason": "hex not found"}

    ap_remaining = result.armor_protection_lost
    affected = []

    # Find enemy armor units, sorted by armor_protection descending
    armor_units = []
    for uid in getattr(ths, enemy_attr):
        unit = state.units.get(uid)
        if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
            if unit.unit_class == "armor" or (unit.armor_protection and unit.armor_protection > 0):
                armor_units.append(unit)
    armor_units.sort(key=lambda u: (u.armor_protection or 0), reverse=True)

    for unit in armor_units:
        if ap_remaining <= 0:
            break
        ap = unit.armor_protection or 0
        if ap > 0:
            take = min(ap_remaining, ap)
            unit.armor_protection = ap - take
            ap_remaining -= take
            affected.append(f"{unit.id} loses {take} AP")
        # Overflow: if we still have AP to spend and this unit's AP is now 0,
        # convert remaining to armor SP loss (1 AP overflow = 1 armor SP)
        if ap_remaining > 0 and (unit.armor_protection or 0) <= 0:
            overflow = min(ap_remaining, unit.current_strength.armor)
            if overflow > 0:
                unit.current_strength.armor -= overflow
                unit.losses_taken += overflow
                ap_remaining -= overflow
                affected.append(f"{unit.id} loses {overflow} armor SP (overflow)")
                _apply_disorganization(unit, 1, "anti-armor loss", state)
                if unit.current_strength.total <= 0:
                    unit.status = UnitStatus.DESTROYED
                    affected.append(f"{unit.id} DESTROYED")
                    state.log_event("unit_destroyed",
                        f"{unit.name} destroyed by anti-armor fire at {target_hex}",
                        unit_id=unit.id, cause="anti_armor", hex_id=target_hex)

    return {
        "applied": True,
        "ap_lost": result.armor_protection_lost - ap_remaining,
        "affected": affected,
    }


def _execute_retreat(
    state: GameState, attacking_side: str, target_hex: str,
    def_ids: list[str], retreat_hexes: int,
) -> list[str]:
    """
    Move defenders away from attackers, 1 hex at a time, maximizing distance
    from the nearest attacker hex. Returns list of descriptions.
    """
    from cna_engine.engine.movement import get_neighbors, _hex_distance

    if retreat_hexes <= 0:
        return []

    # Find attacker hexes (adjacent to target with attacking side's units)
    friendly_attr = "allied_unit_ids" if attacking_side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if attacking_side == Side.ALLIED else "allied_unit_ids"
    attacker_hexes = set()
    for adj_id in get_neighbors(target_hex):
        hs = state.hexes.get(adj_id)
        if hs and getattr(hs, friendly_attr):
            attacker_hexes.add(adj_id)

    descriptions = []
    for uid in def_ids:
        unit = state.units.get(uid)
        if not unit or unit.status == UnitStatus.DESTROYED:
            continue

        current_hex = unit.hex_id
        for _ in range(retreat_hexes):
            neighbors = get_neighbors(current_hex)
            # Filter out enemy-occupied hexes
            valid = []
            for nh in neighbors:
                nhs = state.hexes.get(nh)
                if not nhs:
                    continue
                # Don't retreat into hexes with attacking side's units
                if getattr(nhs, friendly_attr):
                    continue
                valid.append(nh)

            if not valid:
                break  # Nowhere to retreat

            # Pick hex maximizing minimum distance from all attacker hexes
            def _min_dist_from_attackers(h):
                if not attacker_hexes:
                    return 0
                return min(_hex_distance(h, ah) for ah in attacker_hexes)

            best = max(valid, key=_min_dist_from_attackers)
            # Move unit
            old_hex = current_hex
            _move_unit_to_hex(state, unit, best)
            current_hex = best

        if current_hex != unit.hex_id:
            # Already moved by _move_unit_to_hex
            pass
        if current_hex != target_hex:
            descriptions.append(f"{unit.id} retreats to {current_hex}")

    return descriptions


def _move_unit_to_hex(state: GameState, unit: Unit, new_hex: str):
    """Move a unit from its current hex to a new hex, updating hex unit lists."""
    old_hex = unit.hex_id
    side_attr = "allied_unit_ids" if unit.side == Side.ALLIED else "axis_unit_ids"

    # Remove from old hex
    if old_hex and old_hex in state.hexes:
        uid_list = getattr(state.hexes[old_hex], side_attr)
        if unit.id in uid_list:
            uid_list.remove(unit.id)

    # Add to new hex
    unit.hex_id = new_hex
    if new_hex in state.hexes:
        uid_list = getattr(state.hexes[new_hex], side_attr)
        if unit.id not in uid_list:
            uid_list.append(unit.id)


import math


def _move_unit_to_hex(state: GameState, unit: Unit, new_hex: str) -> None:
    """Move a unit to a new hex, updating hex unit lists."""
    side_attr = "allied_unit_ids" if unit.side == Side.ALLIED else "axis_unit_ids"
    # Remove from old hex
    if unit.hex_id and unit.hex_id in state.hexes:
        old_list = getattr(state.hexes[unit.hex_id], side_attr)
        if unit.id in old_list:
            old_list.remove(unit.id)
    # Update unit location
    unit.hex_id = new_hex
    # Add to new hex
    if new_hex in state.hexes:
        new_list = getattr(state.hexes[new_hex], side_attr)
        if unit.id not in new_list:
            new_list.append(unit.id)


def _apply_close_assault_result(
    state: GameState, side: str, target_hex: str,
    atk_ids: list[str], def_ids: list[str], result,
) -> dict:
    """Apply % losses to all units on both sides, retreat defenders."""
    affected = []

    # Apply attacker losses
    atk_total_lost = 0
    for uid in atk_ids:
        unit = state.units.get(uid)
        if not unit or unit.status == UnitStatus.DESTROYED:
            continue
        sp_before = unit.current_strength.total
        sp_to_lose = math.ceil(sp_before * result.attacker_loss_percent / 100)
        if sp_to_lose > 0:
            lost = _distribute_sp_loss(unit, sp_to_lose)
            atk_total_lost += lost
            affected.append(f"{unit.id} (atk) loses {lost} SP ({result.attacker_loss_percent}%)")
            if lost > 0:
                _apply_disorganization(unit, 1, "assault loss", state)
            if unit.status == UnitStatus.DESTROYED:
                affected.append(f"{unit.id} DESTROYED")
                state.log_event("unit_destroyed",
                    f"{unit.name} destroyed in close assault at {target_hex}",
                    unit_id=unit.id, cause="close_assault", hex_id=target_hex)
        unit.combats_fought += 1

    # Apply defender losses
    def_total_lost = 0
    for uid in def_ids:
        unit = state.units.get(uid)
        if not unit or unit.status == UnitStatus.DESTROYED:
            continue
        sp_before = unit.current_strength.total
        sp_to_lose = math.ceil(sp_before * result.defender_loss_percent / 100)
        if sp_to_lose > 0:
            lost = _distribute_sp_loss(unit, sp_to_lose)
            def_total_lost += lost
            affected.append(f"{unit.id} (def) loses {lost} SP ({result.defender_loss_percent}%)")
            if lost > 0:
                _apply_disorganization(unit, 1, "assault loss", state)
            if unit.status == UnitStatus.DESTROYED:
                affected.append(f"{unit.id} DESTROYED")
                state.log_event("unit_destroyed",
                    f"{unit.name} destroyed in close assault at {target_hex}",
                    unit_id=unit.id, cause="close_assault", hex_id=target_hex)
        unit.combats_fought += 1

    # Execute retreat for surviving defenders
    retreat_descs = _execute_retreat(
        state, side, target_hex, def_ids, result.defender_retreat_hexes,
    )
    affected.extend(retreat_descs)

    # Apply disorganization for retreat/overrun
    if result.defender_retreat_hexes > 0:
        is_overrun = getattr(result, 'is_overrun', False)
        disorg_amount = 2 if is_overrun else 1
        for uid in def_ids:
            unit = state.units.get(uid)
            if unit and unit.status != UnitStatus.DESTROYED:
                _apply_disorganization(unit, disorg_amount,
                                       "overrun" if is_overrun else "retreat", state)

    # Attacker advance into vacated hex after successful assault
    if result.defender_retreat_hexes > 0 and atk_ids:
        ths = state.hexes.get(target_hex)
        enemy_attr_name = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
        remaining_def = sum(
            1 for uid in getattr(ths, enemy_attr_name, [])
            if state.units.get(uid) and state.units[uid].status
            in (UnitStatus.ACTIVE, UnitStatus.ENGAGED)
        ) if ths else 0

        if remaining_def == 0:
            lead = state.units.get(atk_ids[0])
            if lead and lead.status != UnitStatus.DESTROYED:
                _move_unit_to_hex(state, lead, target_hex)
                lead.turns_in_position = 0
                affected.append(f"{lead.id} advances into {target_hex}")
                state.log_event("advance_after_assault", f"{lead.name} advances into {target_hex}",
                                unit_id=lead.id, hex_id=target_hex)

    return {
        "applied": True,
        "attacker_sp_lost": atk_total_lost,
        "defender_sp_lost": def_total_lost,
        "retreat_hexes": result.defender_retreat_hexes,
        "affected": affected,
    }


def _compute_suggested_combat(state: GameState, side: str) -> list[str]:
    """
    Compute suggested combat targets for the LLM, similar to suggested_moves
    but for combat. Shows enemy hexes adjacent to friendly units with force ratios.
    """
    from cna_engine.engine.movement import get_neighbors

    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    suggestions = []
    seen_targets = set()

    for hex_id, hs in state.hexes.items():
        enemy_ids = getattr(hs, enemy_attr)
        if not enemy_ids:
            continue

        # Check if any adjacent hex has friendly units
        adj_friendly_sp = 0
        adj_gun_sp = 0
        for adj_id in get_neighbors(hex_id):
            adj_hs = state.hexes.get(adj_id)
            if not adj_hs:
                continue
            for uid in getattr(adj_hs, friendly_attr):
                unit = state.units.get(uid)
                if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                    adj_friendly_sp += unit.current_strength.total
                    adj_gun_sp += unit.current_strength.gun

        if adj_friendly_sp == 0:
            continue
        if hex_id in seen_targets:
            continue
        seen_targets.add(hex_id)

        # Sum defender strength
        def_sp = sum(
            state.units[uid].current_strength.total
            for uid in enemy_ids
            if uid in state.units and state.units[uid].status not in
            (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN)
        )
        if def_sp == 0:
            continue

        ratio = adj_friendly_sp / def_sp
        line = f"  TARGET {hex_id}: ASSAULT ratio {ratio:.1f}:1 (atk {adj_friendly_sp} vs def {def_sp})"
        if adj_gun_sp > 0:
            line += f"\n    -> BARRAGE first: {adj_gun_sp} BP vs infantry"
        suggestions.append(line)

    return suggestions


def _get_sighted_enemy_units(state: GameState, side: str) -> list[dict]:
    """Get enemy units in sighted hexes."""
    sighted_attr = "allied_sighted" if side == Side.ALLIED else "axis_sighted"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    units = []
    for hex_id, hex_state in state.hexes.items():
        if not getattr(hex_state, sighted_attr):
            continue
        for uid in getattr(hex_state, enemy_attr):
            unit = state.units.get(uid)
            if unit and unit.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                units.append({
                    "id": uid,
                    "name": unit.name,
                    "hex_id": unit.hex_id,
                    "status": unit.status,
                    "class": unit.unit_class,
                    "strength": unit.current_strength.total,
                    "enemy": True,
                })
    return units


def _get_visible_hexes(state: GameState, side: str) -> list[dict]:
    """Get hexes visible to a side (occupied or sighted)."""
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    sighted_attr = "allied_sighted" if side == Side.ALLIED else "axis_sighted"

    hexes = []
    for hex_id, hs in state.hexes.items():
        has_friendly = len(getattr(hs, friendly_attr)) > 0
        is_sighted = getattr(hs, sighted_attr)

        if has_friendly or is_sighted:
            info = {
                "hex_id": hex_id,
                "terrain": hs.terrain,
                "road": hs.road,
            }
            if hs.is_port:
                info["port"] = hs.port_name
            if hs.supply_dumps:
                info["dumps"] = len(hs.supply_dumps)
            hexes.append(info)
    return hexes


def _get_all_units_view(state: GameState) -> list[dict]:
    """Get all units (observer view)."""
    return [
        {
            "id": uid, "name": u.name, "side": u.side,
            "hex_id": u.hex_id, "status": u.status,
            "class": u.unit_class, "strength": u.current_strength.total,
        }
        for uid, u in state.units.items()
    ]


def _get_all_hexes_view(state: GameState) -> list[dict]:
    """Get all hexes (observer view)."""
    return [
        {
            "hex_id": hid, "terrain": hs.terrain, "road": hs.road,
            "allied_units": len(hs.allied_unit_ids),
            "axis_units": len(hs.axis_unit_ids),
            "dumps": len(hs.supply_dumps),
        }
        for hid, hs in state.hexes.items()
    ]


def _get_supply_summary(state: GameState, side: str) -> dict:
    """Get supply summary for logistics role."""
    units = [u for u in state.units.values()
             if u.side == side and u.status not in
             (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN, UnitStatus.NOT_YET_ARRIVED)]

    total_fuel = sum(u.supply.fuel for u in units)
    total_water = sum(u.supply.water for u in units)
    total_ammo = sum(u.supply.ammo for u in units)
    total_truck_fuel = sum(u.truck_cargo_fuel for u in units)
    total_truck_points = sum(u.attached_truck_points for u in units)

    fuel_critical = [u.id for u in units
                     if u.supply.fuel_capacity > 0 and
                     u.supply.fuel / u.supply.fuel_capacity < 0.25]
    water_critical = [u.id for u in units
                      if u.supply.water_capacity > 0 and
                      u.supply.water / u.supply.water_capacity < 0.25]

    pool = (state.allied_supply_in_egypt if side == Side.ALLIED
            else state.axis_supply_in_tripoli_boxes)

    return {
        "total_fuel": round(total_fuel, 1),
        "total_water": round(total_water, 1),
        "total_ammo": round(total_ammo, 1),
        "total_truck_fuel": round(total_truck_fuel, 1),
        "total_truck_points": total_truck_points,
        "fuel_critical_units": fuel_critical,
        "water_critical_units": water_critical,
        "supply_pool": dict(pool),
        "units_tracked": len(units),
    }


def _get_air_summary(state: GameState, side: str) -> dict:
    """Get air assets summary for air role, with individual aircraft details."""
    from cna_engine.models.enums import AircraftStatus
    aircraft = [a for a in state.aircraft.values()
                if a.side == side and a.status != AircraftStatus.NOT_YET_ARRIVED]
    ready_aircraft = [
        {"id": a.id, "type": a.aircraft_type_id, "sgsu": a.sgsu_id,
         "bombload": a.bombload_remaining, "tacair": a.tacair_remaining}
        for a in aircraft if a.status == AircraftStatus.READY
    ]
    return {
        "total_aircraft": len(aircraft),
        "ready": len(ready_aircraft),
        "ready_aircraft": ready_aircraft,
        "flew_this_stage": sum(1 for a in aircraft if a.status == AircraftStatus.FLEW_THIS_STAGE),
        "maintenance": sum(1 for a in aircraft if a.status == AircraftStatus.MAINTENANCE),
        "damaged": sum(1 for a in aircraft if a.status == AircraftStatus.DAMAGED),
        "destroyed": sum(1 for a in aircraft if a.status == AircraftStatus.DESTROYED),
        "sgsus": [
            {"id": s.id, "hex_id": s.hex_id, "aircraft": len(s.aircraft_ids),
             "operational": s.is_operational}
            for s in state.sgsus.values() if s.side == side
        ],
    }


def _get_naval_summary(state: GameState, side: str) -> dict:
    """Get naval summary for naval role."""
    if side == Side.ALLIED:
        fleet = state.cw_fleet
        return {
            "fleet_available": fleet.is_available,
            "sorties_remaining": fleet.sorties_remaining,
            "repair_turns": fleet.repair_turns_remaining,
            "ships_committed": fleet.ships_committed,
        }
    else:
        convoy = state.axis_convoy
        return {
            "planned_tonnage": dict(convoy.planned_tonnage),
            "delivered_tonnage": dict(convoy.actual_tonnage_delivered),
            "losses_this_turn": convoy.losses_this_turn,
        }


def _get_contact_force_ratios(state: GameState, side: str) -> list[dict]:
    """
    Compute force ratios at contact points — hexes where friendly units
    are adjacent to enemy units. Helps the LLM decide when to attack.
    """
    from cna_engine.engine.movement import get_neighbors

    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    contact_hexes = {}  # hex_id -> {friendly_sp, enemy_sp}

    for hex_id, hs in state.hexes.items():
        friendly_ids = getattr(hs, friendly_attr)
        if not friendly_ids:
            continue

        # Check if any adjacent hex has enemy units
        friendly_sp = sum(
            state.units[uid].current_strength.total
            for uid in friendly_ids
            if uid in state.units and state.units[uid].status not in
            (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN)
        )
        if friendly_sp == 0:
            continue

        for adj_id in get_neighbors(hex_id):
            adj = state.hexes.get(adj_id)
            if not adj:
                continue
            enemy_ids = getattr(adj, enemy_attr)
            if not enemy_ids:
                continue
            enemy_sp = sum(
                state.units[uid].current_strength.total
                for uid in enemy_ids
                if uid in state.units and state.units[uid].status not in
                (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN)
            )
            if enemy_sp > 0:
                key = hex_id
                if key not in contact_hexes:
                    contact_hexes[key] = {"friendly_sp": friendly_sp, "enemy_sp": 0}
                contact_hexes[key]["enemy_sp"] += enemy_sp

    results = []
    for hex_id, data in contact_hexes.items():
        enemy_sp = data["enemy_sp"]
        friendly_sp = data["friendly_sp"]
        if enemy_sp > 0:
            ratio_val = friendly_sp / enemy_sp
            ratio_str = f"{ratio_val:.1f}:1"
        else:
            ratio_str = "inf:1"
        results.append({
            "hex_id": hex_id,
            "friendly_sp": friendly_sp,
            "enemy_sp": enemy_sp,
            "ratio": ratio_str,
        })

    return results


# ════════════════════════════════════════
# COMMAND VALIDATION
# ════════════════════════════════════════

def validate_command(
    state: GameState,
    role: str,
    side: str,
    command: str,
    **kwargs,
) -> CommandResult:
    """
    Validate an agent command before execution.
    Checks role authorization, phase appropriateness, and basic parameters.
    """
    # Check role
    if role not in VALID_ROLES:
        return CommandResult(
            success=False, command=command, role=role, side=side,
            error=f"Invalid role: {role}",
        )

    # Check command is valid for role
    valid_commands = ROLE_COMMANDS.get(role, [])
    if command not in valid_commands:
        return CommandResult(
            success=False, command=command, role=role, side=side,
            error=f"Command '{command}' not available for role '{role}'. "
                  f"Valid: {valid_commands}",
        )

    # Check it's this side's turn (for action commands)
    if command != "end_phase" and state.turn.active_side:
        if state.turn.active_side != side:
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Not {side}'s turn (active side: {state.turn.active_side})",
            )

    # Command-specific validation
    if command == "move_unit":
        unit_id = kwargs.get("unit_id")
        if not unit_id or unit_id not in state.units:
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Unit '{unit_id}' not found",
            )
        unit = state.units[unit_id]
        if unit.side != side:
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Cannot command enemy unit {unit_id}",
            )
        if unit.status in (UnitStatus.DESTROYED, UnitStatus.SURRENDERED, UnitStatus.WITHDRAWN):
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Unit {unit_id} is {unit.status} — cannot issue orders",
            )
        if unit.hex_id is None:
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Unit {unit_id} is off-map — cannot move",
            )

    return CommandResult(
        success=True, command=command, role=role, side=side,
        description=f"Command '{command}' validated for {role}/{side}",
    )


# ════════════════════════════════════════
# COMMAND EXECUTION
# ════════════════════════════════════════

def _coerce_params(command: str, kwargs: dict) -> dict:
    """
    Coerce LLM-provided parameter types to what engine functions expect.
    LLMs (especially smaller ones) often emit numbers as strings in JSON.
    """
    # Parameter type specs: {command: {param: type}}
    _FLOAT_PARAMS = {
        "plan_convoy": {"tonnage"},
        "unload_port": {"tonnage"},
        "consume_fuel": {"cps_expended", "fuel_rate"},
        "draw_water": {"amount"},
        "create_dump": {"fuel", "water", "ammo", "stores"},
    }
    _INT_PARAMS = {
        "fly_sortie": {"flak_points"},
        "fleet_sortie": {"ships"},
        "truck_attach": {"truck_points"},
        "truck_detach": {"truck_points"},
        "expend_ammo": {"count"},
    }

    float_keys = _FLOAT_PARAMS.get(command, set())
    int_keys = _INT_PARAMS.get(command, set())

    for key in float_keys:
        if key in kwargs:
            try:
                kwargs[key] = float(kwargs[key])
            except (ValueError, TypeError):
                pass

    for key in int_keys:
        if key in kwargs:
            try:
                kwargs[key] = int(kwargs[key])
            except (ValueError, TypeError):
                pass

    # Coerce 'supplies' to a proper dict (truck_load, truck_unload, draw_from_dump).
    # LLMs produce three variants:
    #   dict  {"water": 10.0}         → correct, just coerce values to float
    #   str   "water"                 → means "load water", convert to {"water": 5.0}
    #   list  ["fuel", "water"]       → means "load these", convert to {k: 5.0 for k in list}
    _SUPPLY_DEFAULT = 5.0
    _VALID_SUPPLY_KEYS = {"fuel", "water", "ammo", "stores"}
    if command in ("truck_load", "truck_unload", "draw_from_dump", "draw_from_supply_pool"):
        supplies = kwargs.get("supplies")
        if isinstance(supplies, dict):
            for k, v in supplies.items():
                try:
                    supplies[k] = float(v)
                except (ValueError, TypeError):
                    pass
        elif isinstance(supplies, str):
            # Single supply name string → dict with default amount
            key = supplies.strip().lower()
            if key in _VALID_SUPPLY_KEYS:
                kwargs["supplies"] = {key: _SUPPLY_DEFAULT}
            else:
                kwargs["supplies"] = {}
        elif isinstance(supplies, list):
            # List of supply names → dict with default amounts
            kwargs["supplies"] = {
                k: _SUPPLY_DEFAULT for k in supplies
                if isinstance(k, str) and k.strip().lower() in _VALID_SUPPLY_KEYS
            }

    # Ensure 'destination' is a string (move_unit, reaction_move)
    if command in ("move_unit", "reaction_move"):
        dest = kwargs.get("destination")
        if dest is not None:
            kwargs["destination"] = str(dest).strip()

    # Remap wrong param names for air commands (recon, fly_sortie, assign_mission).
    # LLMs often send unit_id/destination instead of aircraft_id/target_hex.
    if command in ("recon", "fly_sortie", "assign_mission"):
        if "aircraft_id" not in kwargs and "unit_id" in kwargs:
            kwargs["aircraft_id"] = kwargs.pop("unit_id")
        if "target_hex" not in kwargs:
            for alt_key in ("destination", "target", "hex_id", "hex"):
                if alt_key in kwargs:
                    kwargs["target_hex"] = kwargs.pop(alt_key)
                    break
        # Strip stray keys the LLM may invent (e.g. recon_type)
        _VALID_AIR_KEYS = {"aircraft_id", "target_hex", "mission", "target_class"}
        kwargs = {k: v for k, v in kwargs.items() if k in _VALID_AIR_KEYS}

    # Remap combat command params: target/hex_id/destination → target_hex
    # Strip old-format params (barrage_points, aa_points, attacker_strength) silently
    if command in ("fire_barrage", "fire_anti_armor", "close_assault"):
        if "target_hex" not in kwargs:
            for alt_key in ("target", "hex_id", "destination", "hex"):
                if alt_key in kwargs:
                    kwargs["target_hex"] = kwargs.pop(alt_key)
                    break
        if "target_hex" in kwargs:
            kwargs["target_hex"] = str(kwargs["target_hex"]).strip()
        # Strip old numeric params the LLM may still send — engine computes these
        for stale in ("barrage_points", "aa_points", "attacker_strength",
                      "defender_strength", "terrain_shifts"):
            kwargs.pop(stale, None)

    return kwargs


def execute_command(
    state: GameState,
    role: str,
    side: str,
    command: str,
    **kwargs,
) -> CommandResult:
    """
    Validate and execute an agent command.
    Routes to appropriate engine function.
    """
    # Coerce parameter types before validation/execution
    kwargs = _coerce_params(command, kwargs)

    # Validate first
    validation = validate_command(state, role, side, command, **kwargs)
    if not validation.success:
        return validation

    result = None

    try:
        if command == "move_unit":
            from cna_engine.engine.movement import execute_move, find_path
            from cna_engine.data.reference_data import ReferenceData
            ref = kwargs.get("ref", ReferenceData())
            unit = state.units[kwargs["unit_id"]]
            destination = kwargs.get("destination")
            if not destination:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="move_unit requires a 'destination' hex ID",
                )
            if destination == unit.hex_id:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"Unit already at {destination} — pick a different destination",
                )
            path = find_path(state, ref, unit, destination)
            if path is None:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No valid path from {unit.hex_id} to {destination}",
                )
            result = execute_move(state, ref, kwargs["unit_id"], path)

        elif command == "fire_barrage":
            from cna_engine.engine.combat import resolve_barrage
            from cna_engine.data.reference_data import ReferenceData
            target_hex = kwargs.get("target_hex")
            target_class = kwargs.get("target_class", "infantry")
            if not target_hex:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="fire_barrage requires 'target_hex'",
                )
            # Validate target hex has enemies
            ths = state.hexes.get(target_hex)
            enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
            if not ths or not getattr(ths, enemy_attr):
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No enemy units in target hex {target_hex}",
                )
            barrage_points = _gather_barrage_points(state, side, target_hex)
            if barrage_points <= 0:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No friendly gun units adjacent to {target_hex} (0 barrage points)",
                )
            # Get terrain shifts
            ref = ReferenceData()
            terrain_info = ref.terrain.get(ths.terrain)
            terrain_shifts = terrain_info.barrage_shift_val if terrain_info else 0
            fort_level = ths.fort_level if ths else 0
            terrain_shifts -= fort_level  # Forts shift LEFT (worse for attacker)
            # Supply penalty for firing gun units
            from cna_engine.engine.supply import compute_supply_combat_modifiers
            from cna_engine.engine.movement import get_neighbors as _get_neighbors
            _adj = set(_get_neighbors(target_hex))
            _friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
            _gun_uids = []
            for _hex_id in _adj:
                _hs = state.hexes.get(_hex_id)
                if not _hs:
                    continue
                for _uid in getattr(_hs, _friendly_attr):
                    _u = state.units.get(_uid)
                    if _u and _u.current_strength.gun > 0:
                        _gun_uids.append(_uid)
            supply_shift = compute_supply_combat_modifiers(_gun_uids, state)
            terrain_shifts += supply_shift
            # Entrenchment bonus for defenders
            _enemy_attr_b = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
            _max_entrench = 0
            for _uid in getattr(ths, _enemy_attr_b, []):
                _u = state.units.get(_uid)
                if _u:
                    _max_entrench = max(_max_entrench, _u.entrenchment_bonus)
            terrain_shifts -= _max_entrench
            crt_result = resolve_barrage(
                target_class, barrage_points, terrain_shifts, kwargs.get("dice_roll"),
            )
            application = _apply_barrage_result(state, side, target_hex, target_class, crt_result)
            state.log_event("barrage_result", crt_result.description,
                target_hex=target_hex, bp=barrage_points, target_class=target_class,
                dice_roll=crt_result.dice_roll,
                sp_lost=crt_result.strength_points_lost,
                is_pinned=crt_result.is_pinned)
            # Auto-expend ammo for contributing gun units
            from cna_engine.engine.supply import expend_ammo
            for _uid in _gun_uids:
                expend_ammo(state, _uid, "barrage")
            result = {
                "crt": crt_result,
                "barrage_points": barrage_points,
                "terrain_shifts": terrain_shifts,
                "application": application,
                "description": crt_result.description + " | " + ", ".join(application.get("affected", [])),
            }

        elif command == "consume_fuel":
            from cna_engine.engine.supply import consume_fuel
            ref = kwargs.get("ref", ReferenceData())
            result = consume_fuel(
                state, ref, kwargs["unit_id"],
                kwargs["cps_expended"], kwargs.get("fuel_rate", 1),
            )

        elif command == "check_supply":
            from cna_engine.engine.supply import check_supply_status
            result = check_supply_status(state, kwargs["unit_id"])

        elif command == "truck_attach":
            from cna_engine.engine.supply import execute_truck_attach
            result = execute_truck_attach(
                state, kwargs["unit_id"],
                kwargs.get("truck_points", 1),
            )

        elif command == "create_dump":
            from cna_engine.engine.supply import create_supply_dump
            result = create_supply_dump(
                state, kwargs["unit_id"], kwargs["dump_id"],
                kwargs.get("fuel", 0), kwargs.get("water", 0),
                kwargs.get("ammo", 0), kwargs.get("stores", 0),
            )

        elif command == "place_reserve":
            from cna_engine.engine.organization import place_in_reserve
            result = place_in_reserve(state, kwargs["unit_id"])

        elif command == "release_reserve":
            from cna_engine.engine.organization import release_from_reserve
            result = release_from_reserve(state, kwargs["unit_id"])

        elif command == "attach_unit":
            from cna_engine.engine.organization import attach_unit_to_formation
            result = attach_unit_to_formation(
                state, kwargs["unit_id"], kwargs["formation_id"],
            )

        elif command == "assign_mission":
            from cna_engine.engine.air import assign_mission
            result = assign_mission(
                state, kwargs["aircraft_id"], kwargs["mission"],
                kwargs.get("target_hex"),
            )

        elif command == "fly_sortie":
            from cna_engine.engine.air import fly_sortie
            result = fly_sortie(
                state, kwargs["aircraft_id"], kwargs["mission"],
                kwargs["target_hex"], kwargs.get("target_class", "infantry"),
                kwargs.get("interceptor_id"),
                kwargs.get("flak_points", 0),
            )

        elif command == "plan_convoy":
            from cna_engine.engine.naval import plan_convoy
            result = plan_convoy(state, kwargs["port"], kwargs["tonnage"])

        elif command == "fleet_sortie":
            from cna_engine.engine.naval import execute_fleet_sortie
            result = execute_fleet_sortie(
                state, kwargs["target_area"], kwargs.get("ships", 1),
            )

        elif command == "check_supply_lines":
            from cna_engine.engine.supply_lines import check_supply_lines
            result = check_supply_lines(state, side)

        elif command == "break_contact":
            from cna_engine.engine.movement import break_contact
            result = break_contact(state, kwargs["unit_id"])

        elif command == "break_engaged":
            from cna_engine.engine.movement import break_engaged
            result = break_engaged(state, kwargs["unit_id"])

        elif command == "fire_anti_armor":
            from cna_engine.engine.combat import resolve_anti_armor
            from cna_engine.data.reference_data import ReferenceData
            target_hex = kwargs.get("target_hex")
            if not target_hex:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="fire_anti_armor requires 'target_hex'",
                )
            ths = state.hexes.get(target_hex)
            enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
            if not ths or not getattr(ths, enemy_attr):
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No enemy units in target hex {target_hex}",
                )
            aa_points = _gather_aa_points(state, side, target_hex)
            if aa_points <= 0:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No friendly armor/gun units adjacent to {target_hex} (0 AA points)",
                )
            ref = ReferenceData()
            terrain_info = ref.terrain.get(ths.terrain)
            terrain_shifts = terrain_info.anti_armor_shift_val if terrain_info else 0
            fort_level = ths.fort_level if ths else 0
            terrain_shifts -= fort_level  # Forts shift LEFT (worse for attacker)
            # Supply penalty for firing armor/gun units
            from cna_engine.engine.supply import compute_supply_combat_modifiers as _compute_scm_aa
            from cna_engine.engine.movement import get_neighbors as _get_neighbors_aa
            _adj_aa = set(_get_neighbors_aa(target_hex))
            _friendly_attr_aa = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
            _aa_uids = []
            for _hex_id in _adj_aa:
                _hs = state.hexes.get(_hex_id)
                if not _hs:
                    continue
                for _uid in getattr(_hs, _friendly_attr_aa):
                    _u = state.units.get(_uid)
                    if _u and (_u.current_strength.armor + _u.current_strength.gun) > 0:
                        _aa_uids.append(_uid)
            supply_shift = _compute_scm_aa(_aa_uids, state)
            terrain_shifts += supply_shift
            # Entrenchment bonus for defenders
            _enemy_attr_aa = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
            _max_entrench_aa = 0
            for _uid in getattr(ths, _enemy_attr_aa, []):
                _u = state.units.get(_uid)
                if _u:
                    _max_entrench_aa = max(_max_entrench_aa, _u.entrenchment_bonus)
            terrain_shifts -= _max_entrench_aa
            # Phasing player = side whose turn it is
            is_phasing = (state.turn.active_side == side)
            crt_result = resolve_anti_armor(
                aa_points, is_phasing, terrain_shifts, kwargs.get("dice_roll"),
            )
            application = _apply_anti_armor_result(state, side, target_hex, crt_result)
            state.log_event("anti_armor_result", crt_result.description,
                target_hex=target_hex, aa_points=aa_points,
                dice_roll=crt_result.dice_roll,
                ap_destroyed=crt_result.armor_protection_lost,
                overflow_sp=application.get("ap_lost", 0) - crt_result.armor_protection_lost
                if application.get("ap_lost", 0) > crt_result.armor_protection_lost else 0)
            # Auto-expend ammo for contributing armor/gun units
            from cna_engine.engine.supply import expend_ammo as _expend_ammo_aa
            for _uid in _aa_uids:
                _expend_ammo_aa(state, _uid, "anti_armor")
            result = {
                "crt": crt_result,
                "aa_points": aa_points,
                "terrain_shifts": terrain_shifts,
                "application": application,
                "description": crt_result.description + " | " + ", ".join(application.get("affected", [])),
            }

        elif command == "close_assault":
            from cna_engine.engine.combat import resolve_close_assault
            from cna_engine.data.reference_data import ReferenceData
            target_hex = kwargs.get("target_hex")
            if not target_hex:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="close_assault requires 'target_hex'",
                )
            ths = state.hexes.get(target_hex)
            enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
            if not ths or not getattr(ths, enemy_attr):
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No enemy units in target hex {target_hex}",
                )
            atk_str, def_str, atk_ids, def_ids, all_pinned = _gather_assault_strengths(
                state, side, target_hex,
            )
            if atk_str <= 0:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No friendly units adjacent to {target_hex} to assault",
                )
            ref = ReferenceData()
            terrain_info = ref.terrain.get(ths.terrain)
            terrain_shifts = terrain_info.close_assault_shift_val if terrain_info else 0
            fort_level = ths.fort_level if ths else 0
            terrain_shifts -= fort_level  # Forts shift LEFT (worse for attacker)
            # Supply penalty for attacking units
            from cna_engine.engine.supply import compute_supply_combat_modifiers as _compute_scm_ca
            supply_shift = _compute_scm_ca(atk_ids, state)
            terrain_shifts += supply_shift
            # Entrenchment bonus for defenders
            _max_entrench_ca = 0
            for uid in def_ids:
                u = state.units.get(uid)
                if u:
                    _max_entrench_ca = max(_max_entrench_ca, u.entrenchment_bonus)
            terrain_shifts -= _max_entrench_ca

            # Disorganized attackers (any attacker disorg >= 3): -1 column shift
            from cna_engine.engine.organization import DISORG_ATTACK_SHIFT_THRESHOLD, DISORG_DEFEND_SHIFT_THRESHOLD
            for uid in atk_ids:
                u = state.units.get(uid)
                if u and u.disorganization >= DISORG_ATTACK_SHIFT_THRESHOLD:
                    terrain_shifts -= 1
                    break

            # Disorganized defenders (any defender disorg >= 5): +1 column shift (favors attacker)
            for uid in def_ids:
                u = state.units.get(uid)
                if u and u.disorganization >= DISORG_DEFEND_SHIFT_THRESHOLD:
                    terrain_shifts += 1
                    break

            crt_result = resolve_close_assault(
                atk_str, def_str, terrain_shifts, all_pinned, kwargs.get("dice_roll"),
            )
            application = _apply_close_assault_result(
                state, side, target_hex, atk_ids, def_ids, crt_result,
            )
            state.log_event("close_assault", crt_result.description,
                target_hex=target_hex, atk_ids=atk_ids, def_ids=def_ids,
                differential=crt_result.differential,
                dice_roll=crt_result.dice_roll,
                atk_loss_pct=crt_result.attacker_loss_percent,
                def_loss_pct=crt_result.defender_loss_percent,
                retreat_hexes=crt_result.defender_retreat_hexes,
                is_overrun=crt_result.is_overrun)
            # Auto-expend ammo for attacking units
            from cna_engine.engine.supply import expend_ammo as _expend_ammo_ca
            for _uid in atk_ids:
                _expend_ammo_ca(state, _uid, "close_assault")
            result = {
                "crt": crt_result,
                "atk_strength": atk_str,
                "def_strength": def_str,
                "atk_ids": atk_ids,
                "def_ids": def_ids,
                "terrain_shifts": terrain_shifts,
                "application": application,
                "description": crt_result.description + " | " + ", ".join(application.get("affected", [])),
            }

        elif command == "reaction_move":
            from cna_engine.engine.movement import attempt_reaction_move, find_path
            from cna_engine.data.reference_data import ReferenceData
            ref = kwargs.get("ref", ReferenceData())
            unit = state.units.get(kwargs["unit_id"])
            if not unit:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"Unit '{kwargs['unit_id']}' not found",
                )
            destination = kwargs.get("destination")
            if not destination:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="reaction_move requires a 'destination' hex ID",
                )
            if destination == unit.hex_id:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"Unit already at {destination} — pick a different destination",
                )
            path = find_path(state, ref, unit, destination)
            if path is None:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No valid path from {unit.hex_id} to {destination}",
                )
            result = attempt_reaction_move(
                state, ref, kwargs["unit_id"], path,
                kwargs.get("prevention_roll"),
            )

        elif command == "recon":
            from cna_engine.engine.air import resolve_recon
            result = resolve_recon(
                state, kwargs["aircraft_id"], kwargs["target_hex"],
            )

        elif command == "truck_detach":
            from cna_engine.engine.supply import execute_truck_detach
            result = execute_truck_detach(
                state, kwargs["unit_id"],
                kwargs.get("truck_points", 1),
            )

        elif command == "truck_load":
            from cna_engine.engine.supply import execute_truck_load
            result = execute_truck_load(
                state, kwargs["unit_id"], **kwargs.get("supplies", {}),
            )

        elif command == "truck_unload":
            from cna_engine.engine.supply import execute_truck_unload
            result = execute_truck_unload(
                state, kwargs["unit_id"], **kwargs.get("supplies", {}),
            )

        elif command == "draw_from_dump":
            from cna_engine.engine.supply import draw_from_dump
            result = draw_from_dump(
                state, kwargs["unit_id"], kwargs["dump_id"],
                **kwargs.get("supplies", {}),
            )

        elif command == "draw_water":
            from cna_engine.engine.supply import draw_water_from_terrain
            result = draw_water_from_terrain(
                state, kwargs["unit_id"], kwargs.get("amount", 5.0),
            )

        elif command == "draw_from_supply_pool":
            from cna_engine.engine.supply import draw_from_supply_pool
            supplies = kwargs.get("supplies", {})
            result = draw_from_supply_pool(
                state, kwargs["unit_id"],
                fuel=supplies.get("fuel", 0.0),
                water=supplies.get("water", 0.0),
                ammo=supplies.get("ammo", 0.0),
                stores=supplies.get("stores", 0.0),
            )

        elif command == "detach_unit":
            from cna_engine.engine.organization import detach_unit_from_formation
            result = detach_unit_from_formation(state, kwargs["unit_id"])

        elif command == "set_initiative":
            # Commander manually sets initiative (rare — usually auto-rolled)
            winner = kwargs.get("side", side)
            state.turn.initiative_side = winner
            result = {"initiative_side": winner}

        elif command == "expend_ammo":
            from cna_engine.engine.supply import expend_ammo
            result = expend_ammo(
                state, kwargs["unit_id"],
                kwargs["action"], kwargs.get("count", 1),
            )

        elif command == "unload_port":
            from cna_engine.engine.naval import unload_at_port
            result = unload_at_port(
                state, kwargs["port"], kwargs["tonnage"],
            )

        elif command == "lay_minefield":
            from cna_engine.engine.minefields import lay_minefield as _lay_mine
            result = _lay_mine(state, kwargs["unit_id"], kwargs["hex_id"])

        elif command == "lay_fake_minefield":
            from cna_engine.engine.minefields import lay_fake_minefield as _lay_fake
            result = _lay_fake(state, kwargs["unit_id"], kwargs["hex_id"])

        elif command == "clear_minefield":
            from cna_engine.engine.minefields import clear_minefield as _clear_mine
            result = _clear_mine(state, kwargs["unit_id"], kwargs["hex_id"],
                                  kwargs.get("dice_roll"))

        elif command == "convoy_move":
            from cna_engine.engine.movement import find_path, execute_move
            from cna_engine.data.reference_data import ReferenceData
            from cna_engine.models.enums import UnitClass as _UC, OpStagePhase as _OSP
            ref = kwargs.get("ref", ReferenceData())
            unit = state.units.get(kwargs["unit_id"])
            if not unit:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"Unit '{kwargs['unit_id']}' not found",
                )
            eligible_classes = {_UC.TRUCK, _UC.SGSU, _UC.REPLACEMENT, _UC.HQ}
            if unit.unit_class not in eligible_classes:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"{unit.name} ({unit.unit_class}) not eligible for convoy movement — must be TRUCK/SGSU/REPLACEMENT/HQ",
                )
            if state.turn.sub_phase != _OSP.CONVOY_MOVEMENT:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"convoy_move only valid during CONVOY_MOVEMENT phase (current: {state.turn.sub_phase})",
                )
            destination = kwargs.get("destination")
            if not destination:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error="convoy_move requires a 'destination' hex ID",
                )
            if destination == unit.hex_id:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"Unit already at {destination}",
                )
            path = find_path(state, ref, unit, destination)
            if path is None:
                return CommandResult(
                    success=False, command=command, role=role, side=side,
                    error=f"No valid path from {unit.hex_id} to {destination}",
                )
            result = execute_move(state, ref, kwargs["unit_id"], path)

        elif command == "end_phase":
            result = {"acknowledged": True, "phase": state.turn.phase}

        else:
            return CommandResult(
                success=False, command=command, role=role, side=side,
                error=f"Command '{command}' not yet implemented",
            )

    except Exception as e:
        return CommandResult(
            success=False, command=command, role=role, side=side,
            error=f"Execution error: {str(e)}",
        )

    # Check if the result object itself reports failure
    # (e.g., DumpOpResult, MoveResult, TruckOpResult with success=False)
    if hasattr(result, "success") and not result.success:
        error_msg = (
            getattr(result, "blocked_reason", None)
            or getattr(result, "description", None)
            or f"Command '{command}' failed"
        )
        return CommandResult(
            success=False, command=command, role=role, side=side,
            error=error_msg, result=result,
        )

    return CommandResult(
        success=True, command=command, role=role, side=side,
        result=result,
        description=f"Command '{command}' executed successfully",
    )


# ════════════════════════════════════════
# PROMPT GENERATION
# ════════════════════════════════════════

def generate_agent_prompt(
    state: GameState,
    role: str,
    side: str,
) -> str:
    """
    Generate a structured prompt for an LLM agent with their current
    state view and available actions.
    """
    view = get_state_view(state, role, side)

    lines = [
        f"=== CNA AGENT: {role.upper()} ({side.upper()}) ===",
        f"Game Turn: GT{view.game_turn} | OpStage: {view.op_stage}",
        f"Phase: {view.phase}" + (f" > {view.sub_phase}" if view.sub_phase else ""),
        f"Active Side: {view.active_side or 'N/A'}",
        "",
        f"Available Commands: {', '.join(view.available_commands)}",
        "",
    ]

    # Units section
    if view.visible_units:
        lines.append(f"=== YOUR UNITS ({len(view.visible_units)}) ===")
        for u in view.visible_units:
            marker = " [ENEMY]" if u.get("enemy") else ""
            contact = " [CONTACT]" if u.get("is_in_contact") else ""
            suggested = ""
            if u.get("suggested_move"):
                suggested = f" → MOVE TO: {u['suggested_move']}"
            lines.append(
                f"  [{u['id']}] {u['name']}: {u.get('hex_id','off-map')} "
                f"({u['class']}, {u['strength']} SP, {u['status']})"
                f"{contact}{marker}{suggested}"
            )
        lines.append("")

    # Role-specific sections
    if view.supply_summary:
        s = view.supply_summary
        lines.append("=== SUPPLY STATUS ===")
        lines.append(f"  Total fuel: {s['total_fuel']} | Water: {s['total_water']} "
                      f"| Ammo: {s['total_ammo']}")
        lines.append(f"  Truck points: {s['total_truck_points']} | "
                      f"Truck fuel: {s['total_truck_fuel']}")
        if s['fuel_critical_units']:
            lines.append(f"  FUEL CRITICAL: {s['fuel_critical_units']}")
        if s['water_critical_units']:
            lines.append(f"  WATER CRITICAL: {s['water_critical_units']}")
        lines.append(f"  Supply pool: {s['supply_pool']}")
        lines.append("  Focus on the 3-5 most critical units. Recommend at most 5 supply actions this phase.")
        # Show supply dump IDs for logistics role
        dump_lines = []
        for h in view.visible_hexes:
            if h.get("dumps", 0) > 0:
                dump_lines.append(f"    {h['hex_id']}: {h['dumps']} dump(s)")
        if dump_lines:
            lines.append("  Supply dumps:")
            lines.extend(dump_lines)
        lines.append("")

    if view.air_summary:
        a = view.air_summary
        lines.append("=== AIR ASSETS ===")
        lines.append(f"  Aircraft: {a['total_aircraft']} total, "
                      f"{a['ready']} ready, {a['maintenance']} maintenance, "
                      f"{a['damaged']} damaged, {a['destroyed']} destroyed")
        if a.get("ready_aircraft"):
            lines.append("  Ready aircraft:")
            for ac in a["ready_aircraft"]:
                lines.append(f"    id={ac['id']} type={ac['type']} sgsu={ac['sgsu']} "
                             f"bombload={ac['bombload']} tacair={ac['tacair']}")
        if a.get("sgsus"):
            lines.append("  Bases:")
            for s in a["sgsus"]:
                lines.append(f"    {s['id']} at {s['hex_id']}: {s['aircraft']} aircraft, "
                             f"{'operational' if s['operational'] else 'NOT operational'}")
        lines.append("")

    if view.naval_summary:
        n = view.naval_summary
        lines.append("=== NAVAL STATUS ===")
        if side == Side.ALLIED:
            lines.append(f"  Fleet: {'available' if n['fleet_available'] else 'unavailable'}, "
                          f"{n['sorties_remaining']} sorties remaining")
        else:
            lines.append(f"  Planned tonnage: {n['planned_tonnage']}")
            lines.append(f"  Losses: {n['losses_this_turn']}")
        lines.append("")

    # Force ratios at contact points (for ground and commander roles)
    if role in (ROLE_GROUND, ROLE_COMMANDER):
        contact_info = _get_contact_force_ratios(state, side)
        if contact_info:
            lines.append("=== FORCE RATIOS AT CONTACT ===")
            for info in contact_info:
                lines.append(
                    f"  {info['hex_id']}: friendly {info['friendly_sp']} SP vs "
                    f"enemy {info['enemy_sp']} SP — ratio {info['ratio']}"
                )
            lines.append("")

    # Suggested combat targets (for ground and commander roles)
    if role in (ROLE_GROUND, ROLE_COMMANDER):
        combat_suggestions = _compute_suggested_combat(state, side)
        if combat_suggestions:
            lines.append("=== SUGGESTED COMBAT ===")
            lines.extend(combat_suggestions)
            lines.append("")

    # Command reference with parameter schemas
    cmd_ref = format_command_reference(role)
    if cmd_ref:
        lines.append(cmd_ref)
        lines.append("")

    lines.append("Issue commands as JSON: {\"command\": \"...\", \"params\": {...}}")
    lines.append("IMPORTANT: Use exact unit IDs from the state view (e.g., 'cw_2rtr'), NOT hex IDs or unit names.")

    return "\n".join(lines)
