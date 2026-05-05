"""
CNA Engine — Playbook Registry
Pre-written playbooks for each situation. Each playbook contains a focused
system prompt, a state filter function, and a command whitelist.
Stage 2 of the situation-action pipeline.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, UnitStatus, TerrainType
from cna_engine.orchestrator.situations import StateSignals

logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# PLAYBOOK DATA CLASS
# ════════════════════════════════════════

@dataclass
class Playbook:
    """A pre-written playbook for a specific situation."""
    situation: str               # e.g. "ATTACK_PREPARED"
    role: str                    # e.g. "front_line"
    system_prompt: str           # Complete system prompt for Stage 2
    state_filter: Callable[[GameState, str, StateSignals], str]
    applicable_commands: list[str] = field(default_factory=list)
    max_orders: int = 10
    priority_commands: list[str] = field(default_factory=list)


# ════════════════════════════════════════
# STATE FILTER FUNCTIONS
# ════════════════════════════════════════

def _filter_combat_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for combat playbooks: contact points, combat units, enemy at contact."""
    from cna_engine.engine.agent_interface import _get_contact_force_ratios
    from cna_engine.engine.movement import get_neighbors

    lines = []
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    # Contact points with ratios
    ratios = _get_contact_force_ratios(state, side)
    if ratios:
        lines.append("CONTACT POINTS:")
        for cr in ratios:
            lines.append(f"  {cr['hex_id']}: {cr['ratio']} (us {cr['friendly_sp']} vs them {cr['enemy_sp']})")

    # Our combat units — show ALL with exact IDs, tag those near contact
    contact_hexes = {cr["hex_id"] for cr in ratios}
    contact_adj = set()
    for ch in contact_hexes:
        contact_adj.add(ch)
        contact_adj.update(get_neighbors(ch))

    lines.append("\nYOUR UNITS (use exact unit_id in orders):")
    for uid, u in state.units.items():
        if u.side != side or u.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
            continue
        sp = u.current_strength
        near_tag = " **NEAR CONTACT**" if u.hex_id in contact_adj else ""
        engaged = " [engaged]" if u.status == UnitStatus.ENGAGED else ""
        lines.append(
            f"  unit_id={u.id}  name={u.name}  hex={u.hex_id}  "
            f"inf={sp.infantry} armor={sp.armor} gun={sp.gun} "
            f"CPA={u.effective_cpa}{engaged}{near_tag}"
        )

    # Enemy at contact — build target map
    lines.append("\nENEMY AT CONTACT:")
    target_hexes: dict[str, int] = {}  # hex_id -> total enemy SP
    for cr in ratios:
        hex_id = cr["hex_id"]
        for adj_id in get_neighbors(hex_id):
            adj = state.hexes.get(adj_id)
            if not adj:
                continue
            for uid in getattr(adj, enemy_attr):
                eu = state.units.get(uid)
                if eu and eu.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                    lines.append(f"  {eu.id} ({eu.name}) at {adj_id}: strength={eu.current_strength.total}")
                    target_hexes[adj_id] = target_hexes.get(adj_id, 0) + eu.current_strength.total

    # Compute barrage vs assault eligibility per target hex
    barrage_targets = {}
    assault_targets = {}
    for hex_id, enemy_sp in sorted(target_hexes.items(), key=lambda x: -x[1]):
        adj_gun_sp = 0
        adj_total_sp = 0
        for adj_id in get_neighbors(hex_id):
            adj = state.hexes.get(adj_id)
            if not adj:
                continue
            for uid in getattr(adj, friendly_attr):
                u = state.units.get(uid)
                if u and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                    adj_gun_sp += u.current_strength.gun
                    adj_total_sp += u.current_strength.total
        if adj_gun_sp > 0:
            barrage_targets[hex_id] = (enemy_sp, adj_gun_sp)
        if adj_total_sp > 0:
            assault_targets[hex_id] = (enemy_sp, adj_total_sp)

    if barrage_targets:
        lines.append("\nBARRAGE TARGETS (fire_barrage — requires adjacent gun units):")
        for hex_id, (esp, gsp) in barrage_targets.items():
            lines.append(f"  {hex_id}: {esp} enemy SP, {gsp} barrage points available")
    else:
        lines.append("\nNO BARRAGE TARGETS — no gun units adjacent to enemies.")

    if assault_targets:
        lines.append("\nASSAULT TARGETS (close_assault — requires adjacent units):")
        for hex_id, (esp, tsp) in assault_targets.items():
            ratio = tsp / esp if esp > 0 else 0
            lines.append(f"  {hex_id}: ratio {ratio:.1f}:1 (atk {tsp} vs def {esp})")
    else:
        lines.append("\nNO ASSAULT TARGETS — no units adjacent to enemies.")

    return "\n".join(lines)


def _filter_retreat_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for retreat playbooks: threatened units, fallback hexes."""
    from cna_engine.engine.movement import get_neighbors

    lines = []
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    # Retreat direction hint
    if side == Side.AXIS:
        lines.append("RETREAT DIRECTION: West toward Bardia/Tobruk (toward Map A)")
    else:
        lines.append("RETREAT DIRECTION: East toward Mersa Matruh/Alexandria (toward Map E)")

    # Full unit roster — LLM needs exact IDs for every order
    lines.append("\nYOUR UNITS (use exact unit_id in orders):")
    threatened_ids = set()
    for cr in signals.contact_force_ratios:
        hs = state.hexes.get(cr["hex_id"])
        if hs:
            threatened_ids.update(getattr(hs, friendly_attr))

    for uid, u in state.units.items():
        if u.side != side or u.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
            continue
        tag = " **THREATENED**" if uid in threatened_ids else ""
        engaged = " [engaged]" if u.status == UnitStatus.ENGAGED else ""
        lines.append(
            f"  unit_id={u.id}  name={u.name}  hex={u.hex_id}  "
            f"CPA={u.effective_cpa}{engaged}{tag}"
        )

    # Fallback hexes (hexes behind our lines without enemy)
    lines.append("\nFALLBACK POSITIONS:")
    shown = 0
    for hex_id, hs in state.hexes.items():
        if shown >= 5:
            break
        if not getattr(hs, friendly_attr) and not getattr(hs, enemy_attr):
            # Check it's not adjacent to enemy
            adj_enemy = any(
                getattr(state.hexes.get(adj, None), enemy_attr, [])
                for adj in get_neighbors(hex_id)
                if state.hexes.get(adj)
            )
            if not adj_enemy:
                water_note = ""
                if hs.terrain in (TerrainType.OASIS, TerrainType.BIR):
                    water_note = " [WATER]"
                elif hs.is_port:
                    water_note = " [PORT/WATER]"
                lines.append(f"  {hex_id} (terrain: {hs.terrain}){water_note}")
                shown += 1

    # Enemy approach
    lines.append("\nENEMY APPROACH:")
    for cr in signals.contact_force_ratios:
        lines.append(f"  At {cr['hex_id']}: enemy {cr['enemy_sp']} SP, ratio {cr['ratio']}")

    return "\n".join(lines)


def _filter_supply_state(state: GameState, side: str, signals: StateSignals, resource: str = "fuel") -> str:
    """Filter state for supply playbooks: critical units, dumps, trucks."""
    from cna_engine.engine.agent_interface import _get_supply_summary

    lines = []
    supply = _get_supply_summary(state, side)

    # Critical units — EXCLUDE destroyed/withdrawn
    if resource == "water":
        critical_ids = supply.get("water_critical_units", [])
        lines.append("WATER-CRITICAL UNITS:")
    elif resource == "fuel":
        critical_ids = supply.get("fuel_critical_units", [])
        lines.append("FUEL-CRITICAL UNITS:")
    else:
        critical_ids = supply.get("fuel_critical_units", []) + supply.get("water_critical_units", [])
        lines.append("SUPPLY-CRITICAL UNITS:")

    for uid in critical_ids[:8]:
        u = state.units.get(uid)
        if u and u.status == UnitStatus.ACTIVE:
            # Show which dumps are at unit's hex (co-location)
            hex_dumps = []
            hs = state.hexes.get(u.hex_id)
            if hs:
                for d in hs.supply_dumps:
                    if d.side == side:
                        hex_dumps.append(d.id)
            dump_info = f" dumps_here=[{','.join(hex_dumps)}]" if hex_dumps else ""
            lines.append(
                f"  unit_id={u.id} ({u.name}) at {u.hex_id}: "
                f"fuel={u.supply.fuel:.0f}/{u.supply.fuel_capacity:.0f} "
                f"water={u.supply.water:.0f}/{u.supply.water_capacity:.0f} "
                f"truck_pts={u.attached_truck_points} "
                f"CPA_remaining={u.effective_cpa:.1f}{dump_info}"
            )

    # Available dumps (only those with friendly units present — draw requires same hex)
    lines.append("\nSUPPLY DUMPS AT YOUR UNITS (draw_from_dump requires unit at SAME hex as dump):")
    dump_count = 0
    for hex_id, hs in state.hexes.items():
        if hs.supply_dumps:
            # Only show dumps at hexes with friendly units
            units_here = [
                uid for uid in (getattr(hs, "allied_unit_ids", []) if side == Side.ALLIED
                                else getattr(hs, "axis_unit_ids", []))
                if state.units.get(uid) and state.units[uid].status == UnitStatus.ACTIVE
            ]
            if not units_here:
                continue
            for dump in hs.supply_dumps:
                if dump.side == side:
                    lines.append(
                        f"  dump_id={dump.id} at {hex_id}: "
                        f"fuel={dump.fuel:.0f} water={dump.water:.0f} "
                        f"ammo={dump.ammo:.0f} units_here=[{','.join(units_here)}]"
                    )
                    dump_count += 1
    if dump_count == 0:
        lines.append("  (none — no friendly dumps at your units' hexes)")

    # Truck-equipped units — EXCLUDE destroyed
    lines.append("\nTRUCK-EQUIPPED UNITS (can truck_load/truck_unload):")
    shown = 0
    for uid, u in state.units.items():
        if shown >= 5:
            break
        if u.side == side and u.attached_truck_points > 0 and u.status == UnitStatus.ACTIVE:
            lines.append(
                f"  unit_id={u.id} ({u.name}) at {u.hex_id}: "
                f"truck_pts={u.attached_truck_points} "
                f"cargo_fuel={u.truck_cargo_fuel:.0f} cargo_water={u.truck_cargo_water:.0f} "
                f"CPA_remaining={u.effective_cpa:.1f}"
            )
            shown += 1
    if shown == 0:
        lines.append("  (none — use truck_attach first)")

    return "\n".join(lines)


def _filter_supply_water_state(state: GameState, side: str, signals: StateSignals) -> str:
    return _filter_supply_state(state, side, signals, resource="water")


def _filter_supply_fuel_state(state: GameState, side: str, signals: StateSignals) -> str:
    return _filter_supply_state(state, side, signals, resource="fuel")


def _filter_dump_building_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for dump building: front position, existing dumps, trucks."""
    from cna_engine.engine.agent_interface import _get_supply_summary
    from cna_engine.engine.movement import get_neighbors

    lines = []
    friendly_attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"

    # Front-line centroid (approximate)
    front_hexes = []
    for hex_id, hs in state.hexes.items():
        if getattr(hs, friendly_attr):
            for adj_id in get_neighbors(hex_id):
                adj = state.hexes.get(adj_id)
                if adj and getattr(adj, enemy_attr):
                    front_hexes.append(hex_id)
                    break

    if front_hexes:
        lines.append(f"FRONT LINE HEXES: {', '.join(front_hexes[:5])}")
    else:
        lines.append("FRONT LINE: No contact — building for future operations")

    # Existing dumps (own side only)
    lines.append("\nEXISTING DUMPS:")
    dump_count = 0
    for hex_id, hs in state.hexes.items():
        if hs.supply_dumps:
            for dump in hs.supply_dumps:
                if dump.side == side:
                    lines.append(f"  dump_id={dump.id} at {hex_id}: fuel={dump.fuel:.0f} water={dump.water:.0f} ammo={dump.ammo:.0f}")
                    dump_count += 1
    if dump_count == 0:
        lines.append("  (none)")

    # Trucks available
    supply = _get_supply_summary(state, side)
    lines.append(f"\nTRUCK POINTS: {supply.get('total_truck_points', 0)}")
    lines.append(f"SUPPLY POOL: {supply.get('supply_pool', {})}")

    # Port hexes
    lines.append("\nPORTS:")
    for hex_id, hs in state.hexes.items():
        if hs.is_port:
            lines.append(f"  {hs.port_name} at {hex_id}")

    return "\n".join(lines)


def _filter_convoy_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for convoy planning."""
    from cna_engine.engine.agent_interface import _get_naval_summary

    lines = []
    naval = _get_naval_summary(state, side)

    if side == Side.AXIS:
        lines.append("CONVOY STATUS:")
        lines.append(f"  Planned: {naval.get('planned_tonnage', {})}")
        lines.append(f"  Delivered: {naval.get('delivered_tonnage', {})}")
        lines.append(f"  Losses: {naval.get('losses_this_turn', 0)}")
    else:
        lines.append("FLEET STATUS:")
        lines.append(f"  Available: {naval.get('fleet_available', False)}")
        lines.append(f"  Sorties: {naval.get('sorties_remaining', 0)}")

    # Port info
    lines.append("\nPORTS:")
    for hex_id, hs in state.hexes.items():
        if hs.is_port:
            controller = "ours" if getattr(hs, "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids") else "enemy/empty"
            lines.append(f"  {hs.port_name} at {hex_id} [{controller}] capacity={hs.port_capacity}")

    return "\n".join(lines)


def _filter_consolidate_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for campaign consolidation."""
    lines = []

    # VP tally
    lines.append(f"VP MARGIN: {signals.vp_margin:+.1f} ({'leading' if signals.vp_leading else 'trailing'})")
    lines.append(f"OBJECTIVES HELD: {', '.join(signals.objectives_held) or 'none'}")
    if signals.objectives_contested:
        lines.append(f"CONTESTED: {', '.join(signals.objectives_contested)}")

    # Force disposition
    lines.append(f"\nFORCES: {signals.active_units} units, {signals.total_strength} SP")
    lines.append(f"ENEMY: {signals.enemy_units_sighted} sighted, {signals.enemy_total_strength} SP")

    # Supply overview
    lines.append(f"\nSUPPLY: fuel={signals.avg_fuel_pct:.0f}% water={signals.avg_water_pct:.0f}%")
    lines.append(f"CRITICAL: {signals.fuel_critical_count} fuel, {signals.water_critical_count} water")
    lines.append(f"DUMPS: {signals.dumps_count}")
    lines.append(f"OVEREXTENDED: {signals.overextended}")

    return "\n".join(lines)


def _filter_advance_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for advance/opportunity — show units with CPA and objectives."""
    from cna_engine.engine.agent_interface import _compute_suggested_moves

    lines = []

    lines.append("YOUR UNITS (use exact unit_id in orders):")
    friendly_units = []
    count = 0
    for uid, u in state.units.items():
        if count >= 10:
            break
        if (u.side == side
                and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN)
                and u.effective_cpa > 0):
            lines.append(f"  unit_id={u.id}  name={u.name}  hex={u.hex_id}  CPA={u.effective_cpa}")
            friendly_units.append({"id": uid, "cpa_remaining": u.effective_cpa})
            count += 1

    # Compute suggested destinations
    suggestions = _compute_suggested_moves(state, side, friendly_units)
    if suggestions:
        lines.append("\nSUGGESTED MOVES:")
        for uid, dest in suggestions.items():
            lines.append(f"  move_unit unit_id={uid} destination={dest}")

    # Objectives
    if signals.objectives_held:
        lines.append(f"\nHELD OBJECTIVES: {', '.join(signals.objectives_held)}")

    return "\n".join(lines)


def _filter_generic_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Generic minimal state filter — fallback for unspecialized playbooks."""
    lines = [
        f"FORCES: {signals.active_units} units, {signals.total_strength} SP",
        f"ENEMY: {signals.enemy_units_sighted} sighted, {signals.enemy_total_strength} SP",
        f"CONTACT: {signals.units_in_contact} points",
        f"SUPPLY: fuel={signals.avg_fuel_pct:.0f}% water={signals.avg_water_pct:.0f}%",
        f"VP: margin={signals.vp_margin:+.1f}",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════
# PLAYBOOK PROMPTS
# ════════════════════════════════════════

def _attack_prepared_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} front-line commander. Situation: ATTACK_PREPARED.
You have units adjacent to the enemy with favorable ratios. Execute the 3-step assault sequence:

1. BARRAGE: fire_barrage at enemy hex to pin and attrit defenders
2. ANTI-ARMOR: fire_anti_armor if enemy has armor/guns at that hex
3. CLOSE ASSAULT: close_assault the weakened enemy hex if ratio >= 2:1
4. PURSUE: move_unit to advance into or past the enemy position

Decision rules:
- Ratio >= 2:1 → assault immediately after barrage
- Ratio 1.5-2:1 → barrage first, then assault only if barrage succeeds
- Ratio < 1.5:1 → barrage only, reinforce next turn

COMMAND FORMAT — use exact hex_id and unit_id from the state:
  fire_barrage:    {{"target_hex": "hex_id", "target_class": "infantry|armor|gun|truck"}}
  fire_anti_armor: {{"target_hex": "hex_id"}}
  close_assault:   {{"target_hex": "hex_id"}}
  move_unit:       {{"unit_id": "exact_unit_id", "destination": "hex_id"}}

Only issue commands you are certain about. Do NOT issue truck or supply commands.
IMPORTANT: Only fire_barrage at hexes listed under BARRAGE TARGETS. If "NO BARRAGE TARGETS" is shown, do NOT issue any fire_barrage commands.
Only close_assault hexes listed under ASSAULT TARGETS.
Respond with JSON: {{"orders": [{{"command": "close_assault", "params": {{"target_hex": "<hex from ASSAULT TARGETS>"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _fighting_retreat_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} front-line commander. Situation: FIGHTING_RETREAT.
You are outnumbered and must withdraw while delaying the enemy.

CRITICAL: Retreat along routes with water access (coast road, ports, oasis/bir hexes).
Do NOT retreat into waterless desert interior — units at zero water lose 1 SP/turn to dehydration.
Coast road hexes (west to east): A3511(Tripoli) -> A3313(Homs) -> A3117(Misurata) -> A2836(Buerat) -> A2438(Sirte) -> A1437(El Agheila) -> B0406(Agedabia) -> B1403(Benghazi) -> B2703(Derna) -> B3405(Gazala) -> C0512(Tobruk) -> C1215(Bardia) -> C1714(Sollum) -> D0821(Sidi Barrani) -> D1822(Mersa Matruh) -> E1326(Alexandria).
West toward Tripoli (toward Map A). East toward Alexandria (toward Map E).
Prefer FALLBACK POSITIONS on or adjacent to the coast road.

Sequence:
1. BARRAGE: fire_barrage to slow enemy advance
2. BREAK CONTACT: break_contact for units not engaged, break_engaged for engaged units
3. RETREAT: move_unit toward defensive line ({{retreat_direction}})
4. SCREEN: leave one unit to cover the withdrawal if possible

Decision rules:
- Ratio >= 3:1 against → full withdraw immediately, don't waste time firing
- Ratio 2-3:1 against → barrage then withdraw
- Ratio ~1:1 → hold key terrain, barrage only

COMMAND FORMAT — use exact unit_id and hex_id from the state:
  fire_barrage:   {{"target_hex": "hex_id", "target_class": "infantry|armor|gun|truck"}}
  break_contact:  {{"unit_id": "exact_unit_id"}}
  break_engaged:  {{"unit_id": "exact_unit_id"}}
  move_unit:      {{"unit_id": "exact_unit_id", "destination": "hex_id"}}

Do NOT issue truck or supply commands. Prioritize unit survival.
IMPORTANT: Only fire_barrage at hexes listed under BARRAGE TARGETS. If "NO BARRAGE TARGETS" is shown, do NOT issue any fire_barrage commands.
Respond with JSON: {{"orders": [{{"command": "break_contact", "params": {{"unit_id": "unit_from_list"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _dump_building_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} logistics commander. Situation: DUMP_BUILDING.
Build supply dumps 2-3 hexes behind the front line for the next offensive.

Sequence:
1. truck_attach to a unit near a port or existing dump
2. truck_load supplies from the port/dump
3. create_dump at the unit's hex to establish a new supply point
4. truck_unload supplies into the dump

COMMAND FORMAT — use exact unit_id values from the state below:
  truck_attach: {{"unit_id": "exact_unit_id"}}
  truck_load:   {{"unit_id": "exact_unit_id", "supplies": {{"fuel": amount, "water": amount}}}}
  truck_unload: {{"unit_id": "exact_unit_id", "supplies": {{"fuel": amount, "water": amount}}}}
  create_dump:  {{"unit_id": "exact_unit_id", "dump_id": "new_dump_name"}}

IMPORTANT: unit_id must be a combat unit ID, NOT a depot name.

Do NOT issue combat commands. Focus only on logistics.
Respond with JSON: {{"orders": [{{"command": "truck_attach", "params": {{"unit_id": "unit_from_list"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _supply_critical_fuel_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} logistics commander. Situation: SUPPLY_CRITICAL_FUEL.
Motorized units are critically low on fuel. Without fuel, tanks defend at -2 column shifts.
ALSO address any water shortages — units at zero water take attrition casualties.

HOW SUPPLY WORKS — read carefully:
- draw_from_dump: A unit AT THE SAME HEX as a supply dump draws supplies FROM the dump.
  This is the PRIMARY way to resupply. The unit does NOT need truck points.
- truck_unload: Moves supplies from a unit's OWN truck cargo into its OWN internal storage.
  ONLY works on the unit that HAS a truck attached with cargo.
- truck_load: Moves from internal storage onto truck cargo. Use BEFORE create_dump.
- create_dump: Creates a new supply dump from the unit's truck cargo.

CORRECT RESUPPLY FLOW:
1. If a unit is at a hex with a dump → draw_from_dump for BOTH fuel AND water (1 CP each)
2. If a unit has truck cargo + truck pts → truck_unload (1 CP)
3. To create a new dump: truck_load on HQ → create_dump on HQ (2 CP)

COMMAND FORMAT — use exact unit_id and dump_id from the state below:
  draw_from_dump: {{"unit_id": "unit_at_dump_hex", "dump_id": "exact_dump_id", "supplies": {{"fuel": amount, "water": amount}}}}
  truck_unload:   {{"unit_id": "unit_with_truck_cargo", "supplies": {{"fuel": amount, "water": amount}}}}
  truck_load:     {{"unit_id": "unit_with_truck_pts", "supplies": {{"fuel": amount, "water": amount}}}}
  create_dump:    {{"unit_id": "unit_with_truck_cargo", "dump_id": "new_name", "fuel": amount, "water": amount}}

CRITICAL RULES:
- ONLY use unit_ids marked as ACTIVE in the state (never destroyed units)
- ONLY truck_unload on units that have truck_pts > 0 AND cargo > 0
- draw_from_dump requires the unit to be AT THE SAME HEX as the dump
- Check CPA_remaining before issuing orders (each order costs 1 CP)
- Address FUEL first, then WATER for any units that are low

Respond with JSON: {{"orders": [...], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _supply_critical_water_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} logistics commander. Situation: SUPPLY_CRITICAL_WATER.
Units at zero water will take attrition casualties. This is the highest priority.
ALSO address any fuel shortages — motorized units without fuel cannot move or fight effectively.

HOW SUPPLY WORKS — read carefully:
- draw_from_dump: A unit AT THE SAME HEX as a supply dump draws supplies FROM the dump.
  This is the PRIMARY way to resupply. The unit does NOT need truck points.
- truck_unload: Moves supplies from a unit's OWN truck cargo into its OWN internal storage.
  ONLY works on the unit that HAS a truck attached with cargo. Does NOT deliver to other units.
- truck_load: Moves supplies from a unit's internal storage onto its truck cargo.
  Use this BEFORE create_dump to prepare supplies for a new dump.
- create_dump: Creates a new supply dump at the unit's hex from its truck cargo.
  Other units at the same hex can then draw_from_dump.

CORRECT RESUPPLY FLOW:
1. If a unit is at a hex with a dump → draw_from_dump for BOTH water AND fuel (1 CP each)
2. If a unit has truck cargo + truck pts → truck_unload (1 CP)
3. To create a new dump: truck_load on HQ → create_dump on HQ (2 CP)
4. draw_water ONLY works on oasis/bir terrain

COMMAND FORMAT — use exact unit_id and dump_id from the state below:
  draw_from_dump: {{"unit_id": "unit_at_dump_hex", "dump_id": "exact_dump_id", "supplies": {{"water": amount, "fuel": amount}}}}
  truck_unload:   {{"unit_id": "unit_with_truck_cargo", "supplies": {{"water": amount, "fuel": amount}}}}
  truck_load:     {{"unit_id": "unit_with_truck_pts", "supplies": {{"water": amount, "fuel": amount}}}}
  create_dump:    {{"unit_id": "unit_with_truck_cargo", "dump_id": "new_name", "water": amount, "fuel": amount}}
  draw_water:     {{"unit_id": "unit_on_oasis"}}

CRITICAL RULES:
- ONLY use unit_ids marked as ACTIVE in the state (never destroyed units)
- ONLY truck_unload on units that have truck_pts > 0 AND cargo > 0
- draw_from_dump requires the unit to be AT THE SAME HEX as the dump
- Check CPA_remaining before issuing orders (each order costs 1 CP)
- Address WATER first, then FUEL for any motorized units that are low

Respond with JSON: {{"orders": [...], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _convoy_interdiction_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} naval commander. Situation: CONVOY_INTERDICTION.
Your fleet is available to interdict enemy convoys. Use fleet sorties to disrupt Axis supply.

PRIORITIES:
1. fleet_sortie to interdict Axis convoys — target the largest planned tonnage
2. Coordinate with air recon over convoy lanes for +1 interception bonus
3. unload_port for any pending deliveries at your own ports
4. Conserve sorties — fleet needs 3 turns to repair after engagement

Decision rules:
- If Axis planned tonnage > 30 → sortie immediately (high payoff)
- If Axis planned tonnage < 15 → consider saving sorties for next turn
- Always unload your own ports before ending the phase

COMMAND FORMAT:
  fleet_sortie:  {{"target_area": "port_hex_id"}}
  unload_port:   {{"port_hex": "port_hex_id"}}
  plan_convoy:   {{"port_hex": "port_hex_id", "tonnage": amount}}
  end_phase:     {{}}

Respond with JSON: {{"orders": [{{"command": "fleet_sortie", "params": {{"target_area": "hex_id"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _convoy_defense_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} naval/logistics commander. Situation: CONVOY_DEFENSE.
Your convoys suffered losses. Protect future shipments and recover supply shortfall.

PRIORITIES:
1. plan_convoy with replacement tonnage to compensate for losses
2. Route to safer ports if a lane was heavily interdicted
3. unload_port for any successfully delivered cargo
4. Consider assigning fighter CAP to convoy lanes (via air phase coordination)

Decision rules:
- If losses > 30% of planned → reroute to a different port
- If losses < 30% → increase tonnage on the same route to compensate
- Always prioritize the most critical supply type (check supply pool)
- Forward ports (Tobruk, Bardia) are riskier but save truck distance

COMMAND FORMAT:
  plan_convoy:   {{"port_hex": "port_hex_id", "tonnage": amount}}
  unload_port:   {{"port_hex": "port_hex_id"}}
  end_phase:     {{}}

Respond with JSON: {{"orders": [{{"command": "plan_convoy", "params": {{"port_hex": "hex_id", "tonnage": 30}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _convoy_planning_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} naval/logistics commander. Situation: CONVOY_PLANNING.
Plan convoy deliveries to maximize supply throughput.

For Axis: Use plan_convoy to schedule tonnage to ports.
For Allied: Manage port unloading with unload_port.

Rules:
- Match convoy schedule to operational tempo
- Send more tonnage when an offensive is planned
- Route to ports closest to the front to reduce truck distance

Respond with JSON: {{"orders": [{{"command": "...", "params": {{...}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _campaign_consolidate_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} Commander-in-Chief. Situation: CAMPAIGN_CONSOLIDATE.
Halt the advance. Focus on building supply infrastructure and resting units.

Priorities:
1. If VP leading: consolidate gains, build dumps, prepare defense
2. If VP trailing: prepare counterattack — but still build supply first
3. Move reserve units forward to reinforce the line
4. Create supply dumps behind front-line positions

Do not advance into enemy-held territory. Strengthen current positions.
Respond with JSON: {{"orders": [{{"command": "...", "params": {{...}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


# ════════════════════════════════════════
# FALLBACK PLAYBOOK PROMPTS
# ════════════════════════════════════════

def _defensive_hold_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} front-line commander. Situation: DEFENSIVE_HOLD.
Hold current positions. Barrage approaching enemy. Do not advance.

- fire_barrage ONLY at hexes listed under BARRAGE TARGETS. If "NO BARRAGE TARGETS" is shown, do NOT issue any fire_barrage commands.
- Do NOT move units forward
- Do NOT issue supply commands

COMMAND FORMAT:
  fire_barrage:    {{"target_hex": "hex_id", "target_class": "infantry|armor|gun|truck"}}
  fire_anti_armor: {{"target_hex": "hex_id"}}
  move_unit:       {{"unit_id": "exact_unit_id", "destination": "hex_id"}}

Respond with JSON: {{"orders": [{{"command": "fire_barrage", "params": {{"target_hex": "D0821", "target_class": "infantry"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _supply_flowing_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} logistics commander. Situation: SUPPLY_FLOWING.
Routine supply operations. Keep units topped off.

HOW SUPPLY WORKS:
- draw_from_dump: Unit AT a dump's hex draws supplies from it. PRIMARY resupply method.
- truck_unload: Moves a unit's OWN truck cargo into its internal storage. Requires truck_pts > 0.
- truck_load: Moves from internal to truck cargo. Use before create_dump.
- create_dump: Creates a dump at the unit's hex from truck cargo.

COMMAND FORMAT — use exact unit_id and dump_id values from the state below:
  draw_from_dump: {{"unit_id": "unit_at_dump_hex", "dump_id": "exact_dump_id", "supplies": {{"fuel": amount, "water": amount}}}}
  truck_unload:   {{"unit_id": "unit_with_truck", "supplies": {{"fuel": amount, "water": amount}}}}
  truck_load:     {{"unit_id": "unit_with_truck", "supplies": {{"fuel": amount, "water": amount}}}}
  create_dump:    {{"unit_id": "unit_id", "dump_id": "new_name", "fuel": amount, "water": amount}}

RULES: Only use ACTIVE units. Only truck_unload on units with truck_pts > 0.

Respond with JSON: {{"orders": [{{"command": "...", "params": {{...}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _advance_opportunity_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} front-line commander. Situation: ADVANCE_OPPORTUNITY.
No enemy contact. Advance toward objectives aggressively.

OBJECTIVES (capture by moving a unit onto the hex):
  D0821 Sidi Barrani (3VP)  C1714 Sollum (2VP)   C1215 Bardia (3VP)
  C0512 Tobruk (5VP)        B2703 Derna (3VP)     B1403 Benghazi (5VP)

RULES:
- Move ALL available units toward the nearest uncaptured objective
- Follow the coast road for water access: D0821->C1714->C1215->C0512->B2703->B1403
- If SUGGESTED MOVES are listed below, USE THEM — they path toward the best target
- Do NOT halt or consolidate when no enemy is present. Speed wins.

COMMAND FORMAT — use exact unit_id and hex_id from the state:
  move_unit: {{"unit_id": "exact_unit_id", "destination": "hex_id"}}

Respond with JSON: {{"orders": [{{"command": "move_unit", "params": {{"unit_id": "id_from_list", "destination": "target_hex"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _overextended_halt_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} front-line commander. Situation: OVEREXTENDED_HALT.
You have advanced too far from supply. Halt and wait for logistics.

- Do NOT advance further
- fire_barrage at any approaching enemy
- Hold current positions

Respond with JSON: {{"orders": [{{"command": "...", "params": {{...}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _air_superiority_held_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} air commander. Situation: AIR_SUPERIORITY_HELD.
You have air superiority. Exploit it aggressively with ground support.

PRIORITIES:
1. Fly bombing sorties against enemy concentrations
2. Strafe enemy trucks and supply dumps
3. Recon ahead of planned advances
4. Maintain 1 OCAP fighter over the front

Use fly_sortie for offensive missions. Use assign_mission for OCAP/DCAP.
Only use exact aircraft_ids from YOUR READY AIRCRAFT list.

Respond with JSON: {{"orders": [{{"command": "fly_sortie", "params": {{"aircraft_id": "id", "mission": "bombing", "target_hex": "hex", "target_class": "infantry"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _air_inferiority_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} air commander. Situation: AIR_INFERIORITY.
Enemy has air superiority. Conserve your aircraft and focus on defense and recon.

PRIORITIES:
1. Assign most fighters to DCAP (defend bases)
2. Fly only essential recon missions
3. Avoid bombing sorties unless critical — you can't afford losses
4. Use 1 fighter on OCAP only if you have 3+ fighters ready

Use assign_mission for defensive postures. Use fly_sortie sparingly for recon.
Only use exact aircraft_ids from YOUR READY AIRCRAFT list.

Respond with JSON: {{"orders": [{{"command": "assign_mission", "params": {{"aircraft_id": "id", "mission": "dcap"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _ground_support_urgent_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} air commander. Situation: GROUND_SUPPORT_URGENT.
Ground forces are engaged with the enemy and need immediate air support.

PRIORITIES:
1. Fly bombing sorties against enemy-held hexes listed under ACTIVE COMBAT
2. Fly strafing sorties against enemy trucks/supply near the front
3. Assign 1 fighter to OCAP to screen your bombers
4. Fly recon on adjacent unsighted hexes if spare aircraft remain

Target enemy hexes closest to your units in contact FIRST.
Use fly_sortie for all attack missions. Use assign_mission for OCAP/DCAP.
Only use exact aircraft_ids from YOUR READY AIRCRAFT list.

Respond with JSON: {{"orders": [{{"command": "fly_sortie", "params": {{"aircraft_id": "id", "mission": "bombing", "target_hex": "hex", "target_class": "infantry"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


def _filter_air_state(state: GameState, side: str, signals: StateSignals) -> str:
    """Filter state for air playbooks: ready aircraft, SGSUs, enemy positions."""
    from cna_engine.models.enums import AircraftStatus
    lines = []

    # Ready aircraft with IDs (the LLM needs exact IDs)
    from cna_engine.engine.air import get_aircraft_stats
    from cna_engine.engine.movement import _hex_distance
    lines.append("YOUR READY AIRCRAFT (use exact id in commands):")
    ready_count = 0
    # Build lookup: aircraft_id → (sgsu_hex, effective_range)
    ac_range_info: dict[str, tuple[str, int]] = {}
    for ac in state.aircraft.values():
        if ac.side == side and ac.status == AircraftStatus.READY:
            _, _, _, range_hexes = get_aircraft_stats(ac.aircraft_type_id)
            eff_range = range_hexes // 2
            sgsu = state.sgsus.get(ac.sgsu_id) if ac.sgsu_id else None
            sgsu_hex = sgsu.hex_id if sgsu else None
            ac_range_info[ac.id] = (sgsu_hex, eff_range)
            lines.append(f"  aircraft_id={ac.id} type={ac.aircraft_type_id} "
                         f"sgsu={ac.sgsu_id} bombload={ac.bombload_remaining} "
                         f"tacair={ac.tacair_remaining} range={eff_range}hex")
            ready_count += 1
    if ready_count == 0:
        lines.append("  (none ready)")

    # SGSUs
    lines.append("\nYOUR BASES:")
    for s in state.sgsus.values():
        if s.side == side:
            lines.append(f"  sgsu_id={s.id} at {s.hex_id}: {len(s.aircraft_ids)} aircraft, "
                         f"{'operational' if s.is_operational else 'NOT operational'}")

    # Enemy positions (for targeting) — only show hexes reachable by at least one aircraft
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
    lines.append("\nENEMY POSITIONS (reachable targets only):")
    shown = 0
    for hex_id, hs in state.hexes.items():
        if shown >= 8:
            break
        enemy_ids = getattr(hs, enemy_attr, [])
        if enemy_ids:
            units_desc = []
            for uid in enemy_ids[:3]:
                u = state.units.get(uid)
                if u and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                    units_desc.append(f"{uid}({u.unit_class})")
            if not units_desc:
                continue
            # Check which aircraft can reach this hex
            in_range_ac = []
            for ac_id, (sgsu_hex, eff_range) in ac_range_info.items():
                if sgsu_hex and _hex_distance(sgsu_hex, hex_id) <= eff_range:
                    in_range_ac.append(ac_id)
            if not in_range_ac:
                continue  # skip unreachable hexes entirely
            lines.append(f"  {hex_id}: {', '.join(units_desc)}  "
                         f"[IN RANGE of: {', '.join(in_range_ac)}]")
            shown += 1

    # Contact info for targeting
    if signals.units_in_contact > 0:
        lines.append(f"\nACTIVE COMBAT: {signals.units_in_contact} contact points")

    return "\n".join(lines)


def _air_parity_prompt(side: str) -> str:
    side_name = "Allied" if side == Side.ALLIED else "Axis"
    return f"""You are the {side_name} air commander. Situation: AIR_PARITY.
Balance air operations between recon, ground support, and air superiority.

AVAILABLE COMMANDS:
1. fly_sortie — Execute a complete mission (assign + fly + resolve):
   {{"aircraft_id": "exact_id", "mission": "bombing|strafing|recon", "target_hex": "hex_id", "target_class": "infantry|armor|gun|truck"}}
   - BOMBING: Uses bombload against ground targets. Best for infantry/fortified positions.
   - STRAFING: Uses tacair against soft targets. Best for trucks/supply dumps.
   - RECON: Sights hexes around target. No combat capability needed.

2. assign_mission — Set mission type (OCAP/DCAP for defensive air):
   {{"aircraft_id": "exact_id", "mission": "ocap|dcap"}}
   - OCAP: Offensive CAP — auto-intercepts enemy sorties within 6 hexes of base
   - DCAP: Defensive CAP — screens own base against enemy attacks

3. end_phase — End air operations when done.

PRIORITIES:
1. Assign 1-2 fighters to OCAP over the front line
2. Fly recon over unsighted enemy areas
3. Fly bombing/strafing sorties against enemy concentrations at contact points
4. Keep at least 1 fighter on DCAP at each base

RULES:
- Only use aircraft_ids listed under YOUR READY AIRCRAFT
- Each aircraft flies once per stage (assign_mission or fly_sortie, not both)
- Bombers (bombload > 0) → bombing missions; Fighters (tacair > 0, bombload=0) → OCAP/DCAP or strafing

Respond with JSON: {{"orders": [{{"command": "fly_sortie", "params": {{"aircraft_id": "id", "mission": "recon", "target_hex": "hex"}}}}], "reasoning": "..."}}
Maximum {{max_orders}} orders."""


# ════════════════════════════════════════
# PLAYBOOK REGISTRY
# ════════════════════════════════════════

class PlaybookRegistry:
    """Registry of playbooks keyed by (role, situation)."""

    def __init__(self, side: str):
        self.side = side
        self._playbooks: dict[tuple[str, str], Playbook] = {}
        self._build_registry()

    def _build_registry(self):
        side = self.side

        # ── Priority playbooks ──

        self._register(Playbook(
            situation="ATTACK_PREPARED",
            role="front_line",
            system_prompt=_attack_prepared_prompt(side),
            state_filter=_filter_combat_state,
            applicable_commands=["fire_barrage", "fire_anti_armor", "close_assault", "move_unit", "end_phase"],
            max_orders=8,
            priority_commands=["fire_barrage", "fire_anti_armor", "close_assault"],
        ))

        self._register(Playbook(
            situation="FIGHTING_RETREAT",
            role="front_line",
            system_prompt=_fighting_retreat_prompt(side),
            state_filter=_filter_retreat_state,
            applicable_commands=["fire_barrage", "break_contact", "break_engaged", "move_unit", "end_phase"],
            max_orders=8,
            priority_commands=["break_contact", "break_engaged"],
        ))

        self._register(Playbook(
            situation="DUMP_BUILDING",
            role="logistics",
            system_prompt=_dump_building_prompt(side),
            state_filter=_filter_dump_building_state,
            applicable_commands=["truck_attach", "truck_load", "truck_unload", "create_dump", "draw_from_supply_pool", "end_phase"],
            max_orders=6,
            priority_commands=["draw_from_supply_pool", "create_dump", "truck_load"],
        ))

        self._register(Playbook(
            situation="SUPPLY_CRITICAL_FUEL",
            role="logistics",
            system_prompt=_supply_critical_fuel_prompt(side),
            state_filter=_filter_supply_fuel_state,
            applicable_commands=["truck_attach", "truck_load", "truck_unload", "draw_from_dump", "draw_water", "draw_from_supply_pool", "create_dump", "move_unit", "end_phase"],
            max_orders=8,
            priority_commands=["draw_from_supply_pool", "draw_from_dump", "truck_unload", "create_dump", "draw_water"],
        ))

        self._register(Playbook(
            situation="SUPPLY_CRITICAL_WATER",
            role="logistics",
            system_prompt=_supply_critical_water_prompt(side),
            state_filter=_filter_supply_water_state,
            applicable_commands=["truck_attach", "truck_load", "truck_unload", "draw_from_dump", "draw_water", "draw_from_supply_pool", "create_dump", "move_unit", "end_phase"],
            max_orders=8,
            priority_commands=["draw_water", "draw_from_supply_pool", "draw_from_dump", "truck_unload", "create_dump"],
        ))

        self._register(Playbook(
            situation="CONVOY_PLANNING",
            role="logistics",
            system_prompt=_convoy_planning_prompt(side),
            state_filter=_filter_convoy_state,
            applicable_commands=["plan_convoy", "unload_port", "end_phase"],
            max_orders=4,
            priority_commands=["plan_convoy"],
        ))

        self._register(Playbook(
            situation="CONVOY_INTERDICTION",
            role="logistics",
            system_prompt=_convoy_interdiction_prompt(side),
            state_filter=_filter_convoy_state,
            applicable_commands=["fleet_sortie", "unload_port", "plan_convoy", "end_phase"],
            max_orders=4,
            priority_commands=["fleet_sortie"],
        ))

        self._register(Playbook(
            situation="CONVOY_DEFENSE",
            role="logistics",
            system_prompt=_convoy_defense_prompt(side),
            state_filter=_filter_convoy_state,
            applicable_commands=["plan_convoy", "unload_port", "end_phase"],
            max_orders=4,
            priority_commands=["plan_convoy"],
        ))

        self._register(Playbook(
            situation="CAMPAIGN_CONSOLIDATE",
            role="cinc",
            system_prompt=_campaign_consolidate_prompt(side),
            state_filter=_filter_consolidate_state,
            applicable_commands=["move_unit", "truck_attach", "truck_load", "truck_unload", "create_dump", "draw_from_supply_pool", "end_phase"],
            max_orders=8,
            priority_commands=["draw_from_supply_pool", "create_dump", "truck_load"],
        ))

        # ── Fallback playbooks ──

        self._register(Playbook(
            situation="DEFENSIVE_HOLD",
            role="front_line",
            system_prompt=_defensive_hold_prompt(side),
            state_filter=_filter_combat_state,
            applicable_commands=["fire_barrage", "fire_anti_armor", "move_unit", "end_phase"],
            max_orders=6,
            priority_commands=["fire_barrage"],
        ))

        self._register(Playbook(
            situation="SUPPLY_FLOWING",
            role="logistics",
            system_prompt=_supply_flowing_prompt(side),
            state_filter=lambda s, si, sig: _filter_supply_state(s, si, sig, resource="all"),
            applicable_commands=["truck_attach", "truck_load", "truck_unload", "draw_from_dump", "draw_water", "draw_from_supply_pool", "create_dump", "end_phase"],
            max_orders=6,
            priority_commands=["draw_from_dump", "draw_from_supply_pool", "truck_unload"],
        ))

        self._register(Playbook(
            situation="ADVANCE_OPPORTUNITY",
            role="front_line",
            system_prompt=_advance_opportunity_prompt(side),
            state_filter=_filter_advance_state,
            applicable_commands=["move_unit", "end_phase"],
            max_orders=10,
            priority_commands=["move_unit"],
        ))

        self._register(Playbook(
            situation="OVEREXTENDED_HALT",
            role="front_line",
            system_prompt=_overextended_halt_prompt(side),
            state_filter=_filter_combat_state,
            applicable_commands=["fire_barrage", "end_phase"],
            max_orders=4,
            priority_commands=["fire_barrage"],
        ))

        self._register(Playbook(
            situation="SUPPLY_CRITICAL_AMMO",
            role="logistics",
            system_prompt=_supply_critical_fuel_prompt(side).replace("FUEL", "AMMO").replace("fuel", "ammo"),
            state_filter=lambda s, si, sig: _filter_supply_state(s, si, sig, resource="ammo"),
            applicable_commands=["truck_attach", "truck_load", "truck_unload", "draw_from_dump", "end_phase"],
            max_orders=6,
            priority_commands=["truck_attach", "truck_load", "truck_unload"],
        ))

        self._register(Playbook(
            situation="AIR_PARITY",
            role="air",
            system_prompt=_air_parity_prompt(side),
            state_filter=_filter_air_state,
            applicable_commands=["assign_mission", "fly_sortie", "recon", "end_phase"],
            max_orders=8,
            priority_commands=["fly_sortie", "assign_mission"],
        ))

        self._register(Playbook(
            situation="AIR_SUPERIORITY_HELD",
            role="air",
            system_prompt=_air_superiority_held_prompt(side),
            state_filter=_filter_air_state,
            applicable_commands=["assign_mission", "fly_sortie", "recon", "end_phase"],
            max_orders=10,
            priority_commands=["fly_sortie"],
        ))

        self._register(Playbook(
            situation="AIR_INFERIORITY",
            role="air",
            system_prompt=_air_inferiority_prompt(side),
            state_filter=_filter_air_state,
            applicable_commands=["assign_mission", "fly_sortie", "recon", "end_phase"],
            max_orders=6,
            priority_commands=["assign_mission"],
        ))

        self._register(Playbook(
            situation="GROUND_SUPPORT_URGENT",
            role="air",
            system_prompt=_ground_support_urgent_prompt(side),
            state_filter=_filter_air_state,
            applicable_commands=["assign_mission", "fly_sortie", "recon", "end_phase"],
            max_orders=10,
            priority_commands=["fly_sortie"],
        ))

        # ── CinC fallbacks ──

        self._register(Playbook(
            situation="CAMPAIGN_ADVANCE",
            role="cinc",
            system_prompt=_advance_opportunity_prompt(side),
            state_filter=_filter_advance_state,
            applicable_commands=["move_unit", "end_phase"],
            max_orders=10,
            priority_commands=["move_unit"],
        ))

        self._register(Playbook(
            situation="CAMPAIGN_DEFEND",
            role="cinc",
            system_prompt=_defensive_hold_prompt(side),
            state_filter=_filter_combat_state,
            applicable_commands=["fire_barrage", "fire_anti_armor", "move_unit", "end_phase"],
            max_orders=6,
            priority_commands=["fire_barrage"],
        ))

        self._register(Playbook(
            situation="VP_CRISIS",
            role="cinc",
            system_prompt=_attack_prepared_prompt(side),
            state_filter=_filter_combat_state,
            applicable_commands=["fire_barrage", "fire_anti_armor", "close_assault", "move_unit", "end_phase"],
            max_orders=10,
            priority_commands=["close_assault", "fire_barrage"],
        ))

    def _register(self, playbook: Playbook):
        self._playbooks[(playbook.role, playbook.situation)] = playbook

    def get(self, role: str, situation: str) -> Optional[Playbook]:
        return self._playbooks.get((role, situation))

    def get_or_fallback(self, role: str, situation: str) -> Playbook:
        """Get playbook for (role, situation), falling back to safe defaults."""
        pb = self.get(role, situation)
        if pb:
            return pb

        # Fallback chains
        fallback_map = {
            "front_line": "DEFENSIVE_HOLD",
            "logistics": "SUPPLY_FLOWING",
            "air": "AIR_PARITY",
            "cinc": "CAMPAIGN_CONSOLIDATE",
        }

        fallback_sit = fallback_map.get(role, "DEFENSIVE_HOLD")
        fallback_role = role if role in fallback_map else "front_line"
        pb = self.get(fallback_role, fallback_sit)

        if pb:
            logger.info("Playbook fallback: (%s, %s) → (%s, %s)", role, situation, fallback_role, fallback_sit)
            return pb

        # Ultimate fallback: defensive hold
        logger.warning("No playbook found for (%s, %s), using DEFENSIVE_HOLD", role, situation)
        return self.get("front_line", "DEFENSIVE_HOLD")
