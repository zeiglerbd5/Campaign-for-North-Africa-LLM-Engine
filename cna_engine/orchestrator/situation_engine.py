"""
CNA Engine — Situation-Action Engine
Two-stage pipeline: classify the situation, then execute a pre-written playbook.
Drop-in replacement for GeneralAgent.decide().

Pipeline:
  Stage 0: extract_signals(state, side, phase)          [pure Python]
  Stage 1: classify_situation(signals)                   [deterministic or LLM]
  Between: registry.get_or_fallback(role, situation)     [pure Python]
           playbook.state_filter(state, side, signals)   [pure Python]
  Stage 2: execute_playbook(playbook, filtered_state)    [LLM]
  Validate: _validate_orders() against engine rules      [pure Python]
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, UnitStatus
from cna_engine.engine.agent_interface import (
    validate_command, ROLE_COMMANDS,
    ROLE_COMMANDER, ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL,
)
from cna_engine.engine.movement import get_neighbors, parse_hex_id
from cna_engine.engine.turn_runner import PausePoint

from .config import OrchestratorConfig
from .general import DecisionResult, _COMMAND_TO_ROLE
from .llm_backend import LLMError
from .situations import (
    StateSignals, SituationLabel,
    extract_signals, deterministic_classify,
    build_classifier_system_prompt, build_classifier_user_prompt,
    parse_classifier_response, SITUATION_TO_CATEGORY,
)
from .playbooks import Playbook, PlaybookRegistry
from .tool_schemas import build_tool_list, create_tool_handler

logger = logging.getLogger(__name__)


# Map phase sub_phase → primary role for classification
_PHASE_TO_ROLE: dict[str, str] = {
    "movement_combat": "front_line",
    "air": "air",
    "fleet": "logistics",     # naval/convoy is logistics-adjacent
    "reserve": "front_line",
    "patrol": "front_line",
}

# Phases where supply is also relevant (run multi-role)
_MULTI_ROLE_PHASES = {"movement_combat", "convoy_movement"}

# Thinking budget per situation — complex decisions get more reasoning,
# simple ones skip thinking entirely to keep turns fast.
_THINK_BUDGETS: dict[str, object] = {
    # Complex — full thinking budget
    "ATTACK_PREPARED": "budget_tokens:4000",
    "FIGHTING_RETREAT": "budget_tokens:4000",
    "GROUND_SUPPORT_URGENT": "budget_tokens:3000",
    "COUNTERATTACK": "budget_tokens:4000",
    "BREAKOUT": "budget_tokens:4000",
    # Moderate — light thinking
    "AIR_PARITY": "budget_tokens:2000",
    "AIR_SUPERIORITY_HELD": "budget_tokens:1500",
    "AIR_INFERIORITY": "budget_tokens:2000",
    "CONVOY_INTERDICTION": "budget_tokens:1500",
    # Simple — no thinking needed
    "DEFENSIVE_HOLD": False,
    "ADVANCE_OPPORTUNITY": False,
    "PATROL_DETERMINISTIC": False,
    "CONVOY_DETERMINISTIC": False,
    "OVEREXTENDED_HALT": False,
}


def _think_budget_for_situation(situation: str) -> object:
    """Return the think parameter for a given situation label."""
    return _THINK_BUDGETS.get(situation, "budget_tokens:2000")


def _port_dist(state: "GameState", side: "Side", hex_id: str) -> int:
    """Return hex distance from *hex_id* to the nearest friendly port (no enemies)."""
    from cna_engine.engine.movement import _hex_distance
    enemy_attr = "axis_unit_ids" if side == Side.ALLIED else "allied_unit_ids"
    best = 999
    for hid, hs in state.hexes.items():
        if hs.is_port and not getattr(hs, enemy_attr, []):
            best = min(best, _hex_distance(hex_id, hid))
    return best


class SituationEngine:
    """
    Two-stage situation-action pipeline.
    Drop-in replacement for GeneralAgent.decide().
    """

    def __init__(
        self,
        side: str,
        llm_client,
        config: OrchestratorConfig,
        doctrine_context: str = "",
        rag=None,
    ):
        self.side = side
        self.llm_client = llm_client
        self.config = config
        self.doctrine_context = doctrine_context
        self.rag = rag
        self.registry = PlaybookRegistry(side)
        # Track consecutive retreat turns to break death spirals
        self._consecutive_retreat_turns: int = 0

    def decide(
        self,
        state: GameState,
        pause_point: PausePoint,
        memory_context: str = "",
    ) -> DecisionResult:
        """
        Two-stage decision pipeline. Same signature and return type as
        GeneralAgent.decide().
        """
        decide_start = time.monotonic()
        phase = pause_point.sub_phase or str(pause_point.phase)
        phase_desc = pause_point.description or f"Phase: {phase}"

        logger.info("SituationEngine (%s) decide START: %s", self.side, phase_desc)

        # Stage 0: Extract signals (pure Python)
        signals = extract_signals(state, self.side, phase)
        logger.info(
            "SituationEngine (%s) signals: units=%d contact=%d fuel=%.0f%% water=%.0f%%",
            self.side, signals.active_units, signals.units_in_contact,
            signals.avg_fuel_pct, signals.avg_water_pct,
        )

        # Special case: movement_combat needs both combat AND logistics
        if phase in _MULTI_ROLE_PHASES:
            decision = self._decide_movement_combat(state, signals, memory_context, phase=phase)
        else:
            decision = self._decide_single_role(state, signals, phase, memory_context)

        total_ms = int((time.monotonic() - decide_start) * 1000)
        logger.info(
            "SituationEngine (%s) decide DONE: %dms  orders=%d",
            self.side, total_ms, len(decision.orders),
        )

        # Log to event_log
        state.log_event(
            "situation_engine",
            f"SituationEngine ({self.side}): {len(decision.orders)} orders in {total_ms}ms",
            side=self.side,
            phase=phase,
            elapsed_ms=total_ms,
        )

        return decision

    def _decide_single_role(
        self,
        state: GameState,
        signals: StateSignals,
        phase: str,
        memory_context: str,
    ) -> DecisionResult:
        """Single-role decision for non-movement_combat phases."""

        # ── Deterministic PATROL: break_contact + retreat, no LLM needed ──
        if phase == "patrol":
            return self._decide_patrol_deterministic(state, signals)

        primary_role = _PHASE_TO_ROLE.get(phase, "front_line")

        # Stage 1: Classify
        classify_start = time.monotonic()
        label = self._classify(signals, primary_role, state=state)
        classify_ms = int((time.monotonic() - classify_start) * 1000)

        logger.info(
            "SituationEngine (%s) classified: %s/%s (conf=%.2f, %s) %dms",
            self.side, label.role, label.situation, label.confidence,
            "deterministic" if label.deterministic else "llm",
            classify_ms,
        )

        # Between stages: get playbook and filter state
        playbook = self.registry.get_or_fallback(label.role, label.situation)
        filtered_state = playbook.state_filter(state, self.side, signals)

        # Stage 2: Execute playbook
        self._current_state = state  # needed by tool-calling mode
        exec_start = time.monotonic()
        orders = self._execute_playbook(playbook, label, filtered_state, signals)
        exec_ms = int((time.monotonic() - exec_start) * 1000)

        # Validate (tool-calling mode pre-validates, but safety net still runs)
        orders = self._validate_orders(state, orders, playbook)

        # Ensure end_phase
        if not orders or orders[-1].get("command") != "end_phase":
            orders.append({"command": "end_phase", "params": {}})

        return DecisionResult(
            orders=orders,
            expert_recommendations=[{
                "role": label.role,
                "situation": label.situation,
                "confidence": label.confidence,
                "reasoning": label.reasoning,
            }],
            experts_ms=classify_ms,
            synthesis_ms=exec_ms,
        )

    def _decide_movement_combat(
        self,
        state: GameState,
        signals: StateSignals,
        memory_context: str,
        phase: str = "movement_combat",
    ) -> DecisionResult:
        """
        Special multi-role handler for movement_combat and convoy_movement phases.
        For convoy_movement: deterministic supply only (no LLM).
        For movement_combat: up to 3 LLM calls (classify + logistics + combat).
        Supply orders execute first, then combat orders.
        """
        total_classify_ms = 0
        total_exec_ms = 0
        all_labels = []
        supply_orders = []
        combat_orders = []

        # ── Proactive port resupply: top up water at ports every turn ──
        port_resupply = self._compute_proactive_port_resupply(state)
        supply_orders.extend(port_resupply)

        # ── Check if supply needs attention (deterministic) ──
        # Three tiers: CRITICAL (emergency), LOW (proactive), OK (skip)
        supply_resource = None
        supply_label = None
        supply_severity = None

        # CRITICAL: any unit at zero, or >50% of army critical (<25% cap)
        if signals.any_zero_water or signals.water_critical_count > signals.active_units * 0.5:
            supply_resource = "water"
            supply_severity = "critical"
            supply_label = SituationLabel(
                role="logistics", situation="SUPPLY_CRITICAL_WATER",
                confidence=1.0, reasoning="Water crisis detected", deterministic=True,
            )
        elif (signals.any_zero_fuel and signals.motorized_fuel_critical > 0) or \
                signals.fuel_critical_count > signals.active_units * 0.5:
            supply_resource = "fuel"
            supply_severity = "critical"
            supply_label = SituationLabel(
                role="logistics", situation="SUPPLY_CRITICAL_FUEL",
                confidence=1.0, reasoning="Fuel crisis detected", deterministic=True,
            )
        # LOW: average water below 60% or any critical units — act before crisis
        elif signals.avg_water_pct < 60 or signals.water_critical_count > 0:
            supply_resource = "water"
            supply_severity = "low"
            supply_label = SituationLabel(
                role="logistics", situation="SUPPLY_LOW_WATER",
                confidence=0.9, reasoning=f"Water at {signals.avg_water_pct:.0f}%, {signals.water_critical_count} critical units", deterministic=True,
            )
        elif signals.avg_fuel_pct < 40 or signals.fuel_critical_count > 0:
            supply_resource = "fuel"
            supply_severity = "low"
            supply_label = SituationLabel(
                role="logistics", situation="SUPPLY_LOW_FUEL",
                confidence=0.9, reasoning=f"Fuel at {signals.avg_fuel_pct:.0f}%, {signals.fuel_critical_count} critical units", deterministic=True,
            )

        # ── Deterministic supply orders (no LLM needed) ──
        if supply_label and supply_resource:
            all_labels.append(supply_label)
            max_supply = 20 if supply_severity == "critical" else 10
            # LOW tier uses a higher threshold to catch units before they're truly critical
            crit_thresh = 0.30 if supply_severity == "critical" else 0.70
            logger.info(
                "SituationEngine (%s) supply %s: %s",
                self.side, supply_severity, supply_label.situation,
            )
            supply_orders.extend(self._compute_emergency_supply_orders(
                state, signals, supply_resource, max_orders=max_supply,
                critical_threshold=crit_thresh,
            ))

        # ── Deterministic CONVOY_MOVEMENT: supply-only, skip LLM ──
        if phase == "convoy_movement":
            return self._decide_convoy_deterministic(
                state, signals, supply_orders, all_labels,
                supply_severity,
            )

        # ── Classify front-line situation ──
        # Supply orders run independently (above) — let the classifier decide
        # operational posture based on force ratios, contact, and objectives.
        classify_start = time.monotonic()
        front_label = self._classify_front_line(signals, state)

        # Fix 3: Consecutive retreat breaker — if we've been retreating for
        # 5+ turns with no contact, something is wrong. Hold instead.
        if front_label.situation == "FIGHTING_RETREAT":
            self._consecutive_retreat_turns += 1
            if (self._consecutive_retreat_turns >= 5
                    and signals.units_in_contact == 0):
                front_label = SituationLabel(
                    role="front_line", situation="DEFENSIVE_HOLD",
                    confidence=1.0,
                    reasoning=(
                        f"Retreat breaker: {self._consecutive_retreat_turns} consecutive "
                        f"retreat turns with no contact — hold position"
                    ),
                    deterministic=True,
                )
                self._consecutive_retreat_turns = 0
        else:
            self._consecutive_retreat_turns = 0

        total_classify_ms += int((time.monotonic() - classify_start) * 1000)
        all_labels.append(front_label)

        logger.info(
            "SituationEngine (%s) front-line: %s (conf=%.2f, %s)",
            self.side, front_label.situation, front_label.confidence,
            "deterministic" if front_label.deterministic else "llm",
        )

        # ── Run front-line playbook (always runs, independent budget) ──
        combat_playbook = self.registry.get_or_fallback(
            front_label.role, front_label.situation,
        )
        remaining_budget = combat_playbook.max_orders

        self._current_state = state  # needed by tool-calling mode
        filtered_combat = combat_playbook.state_filter(state, self.side, signals)
        exec_start = time.monotonic()
        raw_combat = self._execute_playbook(
            combat_playbook, front_label, filtered_combat, signals,
            max_orders_override=remaining_budget,
            supply_severity=supply_severity,
        )
        total_exec_ms += int((time.monotonic() - exec_start) * 1000)
        combat_orders = self._validate_orders(state, raw_combat, combat_playbook)

        # ── Post-combat advance: move idle units toward objectives ──
        # Skip advance only when retreating or early-game Axis defence.
        # Per-unit water/port-distance filters in _compute_advance_orders
        # handle individual unit readiness.
        if (front_label.situation == "FIGHTING_RETREAT"
                or (self.side == Side.AXIS and signals.game_turn <= 20)):
            advance_orders = []
        else:
            # Exempt units already given orders (supply OR combat) so we don't
            # duplicate moves for units the LLM already handled.
            ordered_unit_ids: set[str] = set()
            for order in supply_orders + combat_orders:
                uid = order.get("params", {}).get("unit_id")
                if uid:
                    ordered_unit_ids.add(uid)

            # Throttle advance only when taking heavy losses or outnumbered
            if (signals.units_lost_last_turn >= 2
                    or signals.total_strength
                    < signals.enemy_total_strength * 0.7):
                advance_budget = 2
            else:
                advance_budget = 4
            advance_orders = self._compute_advance_orders(
                state, signals, max_orders=advance_budget,
                exempt_uids=frozenset(ordered_unit_ids),
            )
            # Validate advance orders (no playbook whitelist — just engine checks)
            advance_orders = self._validate_orders(state, advance_orders, playbook=None)

        # ── Combine: supply first, then combat, then advance ──
        all_orders = supply_orders + combat_orders + advance_orders

        # Ensure end_phase
        if not all_orders or all_orders[-1].get("command") != "end_phase":
            all_orders.append({"command": "end_phase", "params": {}})

        return DecisionResult(
            orders=all_orders,
            expert_recommendations=[
                {
                    "role": lbl.role,
                    "situation": lbl.situation,
                    "confidence": lbl.confidence,
                    "reasoning": lbl.reasoning,
                }
                for lbl in all_labels
            ],
            experts_ms=total_classify_ms,
            synthesis_ms=total_exec_ms,
        )

    # ──────────────────────────────────────────────
    # Deterministic PATROL and CONVOY helpers
    # ──────────────────────────────────────────────

    def _decide_patrol_deterministic(
        self,
        state: GameState,
        signals: StateSignals,
    ) -> DecisionResult:
        """
        Deterministic patrol handler — no LLM needed.
        - break_contact for all engaged friendly units
        - move_unit toward rear for units in FIGHTING_RETREAT posture
        """
        from cna_engine.engine.agent_interface import _compute_suggested_moves
        from cna_engine.engine.movement import _hex_distance

        orders: list[dict] = []

        # Break contact for all friendly units in contact
        for uid, u in state.units.items():
            if u.side != self.side or u.status != UnitStatus.ACTIVE:
                continue
            if not u.hex_id:
                continue
            if u.is_in_contact and u.effective_cpa >= 2.0:
                orders.append({
                    "command": "break_contact",
                    "params": {"unit_id": uid},
                })

        # Retreat moves: move units away from enemy toward rear
        # Determine rear direction based on side
        if self.side == Side.AXIS:
            # Axis retreats west — toward lower global column (sections A, B)
            retreat_sign = -1
        else:
            # Allied retreats east — toward higher global column (sections D, E)
            retreat_sign = 1

        # Find friendly ports for retreat targets
        enemy_id_attr = "axis_unit_ids" if self.side == Side.ALLIED else "allied_unit_ids"
        port_hexes = [
            hid for hid, hs in state.hexes.items()
            if hs.is_port and not getattr(hs, enemy_id_attr, [])
        ]

        # Units that just broke contact: move toward nearest rear port
        broke_contact_uids = {o["params"]["unit_id"] for o in orders if o["command"] == "break_contact"}
        if port_hexes and broke_contact_uids:
            for uid in broke_contact_uids:
                u = state.units.get(uid)
                if not u or not u.hex_id:
                    continue
                # Remaining CPA after break_contact (costs 2)
                remaining_cpa = u.effective_cpa - 2.0
                if remaining_cpa < 2.0:
                    continue
                # Find nearest port in the retreat direction
                best_port, best_dist = None, 999
                for ph in port_hexes:
                    d = _hex_distance(u.hex_id, ph)
                    if d < best_dist:
                        best_dist, best_port = d, ph
                if best_port and best_dist <= 50:
                    orders.append({
                        "command": "move_unit",
                        "params": {"unit_id": uid, "destination": best_port},
                    })

        # Ensure end_phase
        orders.append({"command": "end_phase", "params": {}})

        # Validate
        orders = self._validate_orders(state, orders, playbook=None)
        if not orders or orders[-1].get("command") != "end_phase":
            orders.append({"command": "end_phase", "params": {}})

        label = SituationLabel(
            role="front_line", situation="PATROL_DETERMINISTIC",
            confidence=1.0,
            reasoning=f"Deterministic patrol: {len(broke_contact_uids)} break_contact",
            deterministic=True,
        )
        logger.info(
            "SituationEngine (%s) PATROL deterministic: %d orders "
            "(%d break_contact, %d retreat moves)",
            self.side, len(orders),
            len(broke_contact_uids),
            len([o for o in orders if o.get("command") == "move_unit"]),
        )

        return DecisionResult(
            orders=orders,
            expert_recommendations=[{
                "role": label.role,
                "situation": label.situation,
                "confidence": label.confidence,
                "reasoning": label.reasoning,
            }],
            experts_ms=0,
            synthesis_ms=0,
        )

    def _decide_convoy_deterministic(
        self,
        state: GameState,
        signals: StateSignals,
        supply_orders: list[dict],
        all_labels: list[SituationLabel],
        supply_severity: str | None,
    ) -> DecisionResult:
        """
        Deterministic convoy_movement handler — no LLM needed.
        Uses already-computed supply orders from _decide_movement_combat().
        Supply logic (port resupply, emergency supply, shuttle) already ran;
        just wrap them up without invoking the front-line LLM playbook.
        """
        all_orders = list(supply_orders)

        # Ensure end_phase
        if not all_orders or all_orders[-1].get("command") != "end_phase":
            all_orders.append({"command": "end_phase", "params": {}})

        # Build a summary label if none exist yet
        if not all_labels:
            all_labels.append(SituationLabel(
                role="logistics", situation="CONVOY_DETERMINISTIC",
                confidence=1.0,
                reasoning="Deterministic convoy movement — supply only",
                deterministic=True,
            ))

        logger.info(
            "SituationEngine (%s) CONVOY deterministic: %d supply orders "
            "(severity=%s, 0ms LLM)",
            self.side, len(supply_orders), supply_severity or "ok",
        )

        return DecisionResult(
            orders=all_orders,
            expert_recommendations=[
                {
                    "role": lbl.role,
                    "situation": lbl.situation,
                    "confidence": lbl.confidence,
                    "reasoning": lbl.reasoning,
                }
                for lbl in all_labels
            ],
            experts_ms=0,
            synthesis_ms=0,
        )

    # ──────────────────────────────────────
    # Deterministic supply & advance helpers
    # ──────────────────────────────────────

    # Known water source hexes — oasis/bir terrain is now encoded in hex_database;
    # these sets are kept empty for backward compatibility with code that references them.
    _OASIS_HEXES: set[str] = set()
    _BIR_HEXES: set[str] = set()
    _WATER_SOURCE_HEXES = _OASIS_HEXES | _BIR_HEXES

    def _compute_emergency_supply_orders(
        self,
        state: GameState,
        signals: StateSignals,
        resource: str,
        max_orders: int = 8,
        critical_threshold: float = 0.30,
    ) -> list[dict]:
        """
        Pure-Python emergency supply orders — replaces the LLM for crisis
        situations where the correct action is algorithmic.

        Priority order:
          P0. draw_water at oasis/bir hexes (instant, unlimited at oasis)
          P1. draw_from_supply_pool at port hexes (huge pool: 5000/1000)
          P2. draw_from_dump at existing dump hexes
          P3. Move logistics units toward water sources for next-turn draw
          P4. (existing) Create dumps from internal supply

        Args:
            critical_threshold: Units below this fraction of capacity are
                considered in need.  0.30 for emergencies, 0.70 for proactive.
        """
        orders: list[dict] = []
        cp_budget: dict[str, float] = {}   # uid → CP already allocated
        created_dumps: dict[str, str] = {}  # hex_id → dump_id (dumps we'll create)

        def rcpa(uid: str, unit) -> float:
            return unit.effective_cpa - cp_budget.get(uid, 0.0)

        def charge(uid: str, cost: float):
            cp_budget[uid] = cp_budget.get(uid, 0.0) + cost

        def budget_left() -> bool:
            return len(orders) < max_orders

        # ── Gather ACTIVE friendly units (on-map only) ──
        friendly: dict[str, object] = {}
        critical: list[tuple[str, object, float, float]] = []
        for uid, u in state.units.items():
            if u.side != self.side or u.status != UnitStatus.ACTIVE:
                continue
            if not u.hex_id:
                continue
            friendly[uid] = u
            level = getattr(u.supply, resource, 0.0)
            cap = getattr(u.supply, f"{resource}_capacity", 1.0)
            if cap > 0 and (level / cap) < critical_threshold:
                critical.append((uid, u, level, cap))
        critical.sort(key=lambda x: x[2])  # lowest level first

        if not critical:
            return []

        # ── Identify hexes where critical units are clustered ──
        critical_hexes: set[str] = {u.hex_id for _, u, _, _ in critical}

        # ════ PRIORITY 0: draw_water at oasis/bir hexes ════
        # Any friendly unit on an oasis or bir hex can draw water for 1 CP
        if resource == "water":
            for uid, u in friendly.items():
                if not budget_left():
                    break
                if rcpa(uid, u) < 1.0:
                    continue
                hs = state.hexes.get(u.hex_id)
                if not hs:
                    continue
                terrain = hs.terrain.lower().replace(" ", "_") if isinstance(hs.terrain, str) else hs.terrain
                from cna_engine.models.enums import TerrainType
                if terrain in (TerrainType.OASIS, TerrainType.BIR):
                    orders.append({
                        "command": "draw_water",
                        "params": {"unit_id": uid, "amount": u.supply.water_capacity},
                    })
                    charge(uid, 1.0)

        # ════ PRIORITY 1: draw_from_supply_pool at port hexes ════
        # Units at ports can draw from the huge Egypt/Tripoli supply pools
        for uid, u in friendly.items():
            if not budget_left():
                break
            if rcpa(uid, u) < 1.0:
                continue
            hs = state.hexes.get(u.hex_id)
            if not hs or not hs.is_port:
                continue
            # Draw the resource we're critically short of
            level = getattr(u.supply, resource, 0.0)
            cap = getattr(u.supply, f"{resource}_capacity", 1.0)
            needed = cap - level
            if needed <= 0:
                continue
            orders.append({
                "command": "draw_from_supply_pool",
                "params": {"unit_id": uid, "supplies": {resource: needed}},
            })
            charge(uid, 1.0)

        # ════ PRIORITY 2: draw_from_dump at existing dump hexes ════
        # B1. Critical units at hexes with existing dumps
        for uid, u, level, cap in critical:
            if not budget_left():
                break
            if rcpa(uid, u) < 1.0:
                continue
            needed = cap - level

            hs = state.hexes.get(u.hex_id)
            if not hs:
                continue
            for dump in hs.supply_dumps:
                dump_amount = getattr(dump, resource, 0.0)
                if dump.side == self.side and dump_amount > 0:
                    orders.append({
                        "command": "draw_from_dump",
                        "params": {
                            "unit_id": uid,
                            "dump_id": dump.id,
                            "supplies": {resource: min(needed, dump_amount)},
                        },
                    })
                    charge(uid, 1.0)
                    break

        # ════ PRIORITY 1b: Move units toward nearest port to resupply ════
        # Send low-water units toward the nearest port to draw from the supply pool.
        # Triggers whenever the supply system is active (LOW or CRITICAL tier).
        if resource == "water" and budget_left():
            from cna_engine.engine.movement import _hex_distance
            # Find port hexes without enemy presence
            enemy_id_attr = "axis_unit_ids" if self.side == Side.ALLIED else "allied_unit_ids"
            port_hexes = [
                hid for hid, hs in state.hexes.items()
                if hs.is_port and not getattr(hs, enemy_id_attr, [])
            ]
            if port_hexes:
                # Send critical units toward nearest port (lowest water first)
                candidates = []
                for uid, u, level, cap in critical:
                    if rcpa(uid, u) < 2.0:
                        continue
                    if u.hex_id in self._WATER_SOURCE_HEXES:
                        continue
                    hs = state.hexes.get(u.hex_id)
                    if hs and hs.is_port:
                        continue
                    # Find closest port for this unit
                    best_d, best_ph = 999, None
                    for ph in port_hexes:
                        d = _hex_distance(u.hex_id, ph)
                        if d < best_d:
                            best_d, best_ph = d, ph
                    if best_ph and best_d <= 50:
                        candidates.append((level / cap, uid, best_ph))
                # Sort by water% (lowest first) — send up to half the army
                candidates.sort()
                max_send = max(3, len(critical) // 2)
                sent = 0
                for _pct, uid, dest in candidates:
                    if sent >= max_send or not budget_left():
                        break
                    orders.append({
                        "command": "move_unit",
                        "params": {"unit_id": uid, "destination": dest},
                    })
                    charge(uid, 2.0)
                    sent += 1

        # ════ PRIORITY 3: Supply shuttle — cycle units between water sources and front ════
        # Designated shuttle units fetch water and deliver it to the front line.
        if resource == "water" and budget_left():
            from cna_engine.engine.movement import _hex_distance
            from cna_engine.models.enums import TerrainType as TT

            # Identify "front cluster" — hex with most critical-water units
            from collections import Counter
            crit_hex_counts = Counter(u.hex_id for _, u, _, _ in critical)
            front_hex = crit_hex_counts.most_common(1)[0][0] if crit_hex_counts else None

            # Contact hexes (for exclusion)
            contact_hexes: set[str] = set()
            for cr in signals.contact_force_ratios:
                contact_hexes.add(cr["hex_id"])
                contact_hexes.update(get_neighbors(cr["hex_id"]))

            # Valid water source hexes (oasis/bir that still have the terrain)
            valid_water_hexes: list[str] = []
            for wh in self._WATER_SOURCE_HEXES:
                if wh in state.hexes:
                    whs = state.hexes[wh]
                    wt = whs.terrain.lower().replace(" ", "_") if isinstance(whs.terrain, str) else whs.terrain
                    if wt in (TT.OASIS, TT.BIR):
                        valid_water_hexes.append(wh)

            # Port hexes without enemy presence
            enemy_id_attr = "axis_unit_ids" if self.side == Side.ALLIED else "allied_unit_ids"
            port_hexes = [
                hid for hid, hs in state.hexes.items()
                if hs.is_port and not getattr(hs, enemy_id_attr, [])
            ]

            # ── Select up to 2 shuttle candidates ──
            # Prefer HQ/artillery (smaller, less combat value), high CPA, closer to water
            shuttle_candidates = []
            for uid, u in friendly.items():
                if rcpa(uid, u) < 5.0:
                    continue
                if u.hex_id in contact_hexes:
                    continue
                # Shuttle preference score: HQ/GUN preferred, then CPA, then proximity to water
                cls = u.unit_class.lower() if isinstance(u.unit_class, str) else u.unit_class
                class_bonus = 10 if cls in ("hq", "gun") else 0
                min_water_dist = 999
                for wh in valid_water_hexes + port_hexes:
                    d = _hex_distance(u.hex_id, wh)
                    if d < min_water_dist:
                        min_water_dist = d
                shuttle_candidates.append((
                    uid, u,
                    class_bonus + rcpa(uid, u) - min_water_dist * 0.5,
                ))
            shuttle_candidates.sort(key=lambda x: -x[2])  # best score first

            shuttles_issued = 0
            for uid, u, _score in shuttle_candidates:
                if shuttles_issued >= 3 or not budget_left():
                    break

                hs = state.hexes.get(u.hex_id)
                if not hs:
                    continue
                terrain = hs.terrain.lower().replace(" ", "_") if isinstance(hs.terrain, str) else hs.terrain
                at_water = terrain in (TT.OASIS, TT.BIR)
                at_port = hs.is_port
                has_cargo = getattr(u, "truck_cargo_water", 0.0) > 0

                if at_water or at_port:
                    # P0/P1 already drew water — now load and deliver
                    if u.attached_truck_points <= 0 and budget_left():
                        orders.append({
                            "command": "truck_attach",
                            "params": {"unit_id": uid},
                        })
                        charge(uid, 0.5)

                    cargo_water = getattr(u.supply, "water", 0.0) - 1.0
                    if cargo_water > 0 and budget_left():
                        orders.append({
                            "command": "truck_load",
                            "params": {"unit_id": uid, "supplies": {"water": min(cargo_water, 20.0)}},
                        })
                        charge(uid, 1.0)

                    if front_hex and budget_left():
                        orders.append({
                            "command": "move_unit",
                            "params": {"unit_id": uid, "destination": front_hex},
                        })
                        charge(uid, 3.0)

                    if budget_left():
                        dump_id = f"shuttle_wat_{u.hex_id}_{uid[-4:]}"
                        orders.append({
                            "command": "create_dump",
                            "params": {
                                "unit_id": uid, "dump_id": dump_id,
                                "fuel": 0, "water": min(cargo_water, 20.0) if cargo_water > 0 else 0,
                                "ammo": 0, "stores": 0,
                            },
                        })
                        charge(uid, 1.0)
                    shuttles_issued += 1

                elif has_cargo:
                    # Has truck cargo water from a previous turn — deliver to front
                    cargo = getattr(u, "truck_cargo_water", 0.0)
                    if front_hex and budget_left():
                        orders.append({
                            "command": "move_unit",
                            "params": {"unit_id": uid, "destination": front_hex},
                        })
                        charge(uid, 3.0)
                    if budget_left():
                        dump_id = f"shuttle_wat_{u.hex_id}_{uid[-4:]}"
                        orders.append({
                            "command": "create_dump",
                            "params": {
                                "unit_id": uid, "dump_id": dump_id,
                                "fuel": 0, "water": cargo,
                                "ammo": 0, "stores": 0,
                            },
                        })
                        charge(uid, 1.0)
                    shuttles_issued += 1

                else:
                    # Not at water, no cargo — move toward nearest water source
                    best_dest = None
                    best_dist = 999
                    for wh in valid_water_hexes + port_hexes:
                        d = _hex_distance(u.hex_id, wh)
                        if d < best_dist:
                            best_dist = d
                            best_dest = wh
                    if best_dest and best_dist <= 50 and budget_left():
                        orders.append({
                            "command": "move_unit",
                            "params": {"unit_id": uid, "destination": best_dest},
                        })
                        charge(uid, 2.0)
                        shuttles_issued += 1

        # ════ PRIORITY 4: Create supply sources from internal stores ════

        # A1. Units with truck cargo but no truck points → attach + create_dump
        for uid, u in friendly.items():
            if not budget_left():
                break
            cargo = getattr(u, f"truck_cargo_{resource}", 0.0)
            if cargo <= 0:
                continue
            if u.hex_id not in critical_hexes:
                continue

            # Need truck_attach if no trucks
            if u.attached_truck_points <= 0:
                if rcpa(uid, u) < 1.5:  # 0.5 attach + 1.0 create
                    continue
                orders.append({
                    "command": "truck_attach",
                    "params": {"unit_id": uid},
                })
                charge(uid, 0.5)

            if not budget_left():
                break

            # create_dump from truck cargo
            dump_id = f"emrg_{resource[:3]}_{u.hex_id}"
            dump_params = {
                "unit_id": uid, "dump_id": dump_id,
                "fuel": 0, "water": 0, "ammo": 0, "stores": 0,
            }
            dump_params[resource] = cargo
            orders.append({"command": "create_dump", "params": dump_params})
            charge(uid, 1.0)
            created_dumps[u.hex_id] = dump_id

        # A2. Units with decent internal supply → attach + load + create_dump
        # Find the unit with the most internal supply at a critical hex
        donors = []
        for uid, u in friendly.items():
            internal = getattr(u.supply, resource, 0.0)
            if internal >= 3.0 and u.hex_id in critical_hexes:
                donors.append((uid, u, internal))
        donors.sort(key=lambda x: -x[2])  # most supply first

        for uid, u, internal in donors:
            if not budget_left():
                break
            if u.hex_id in created_dumps:
                continue  # already created a dump at this hex

            # Calculate CP needed: attach(0.5 if needed) + load(1.0) + create(1.0)
            needs_attach = u.attached_truck_points <= 0
            cp_needed = (0.5 if needs_attach else 0) + 2.0
            if rcpa(uid, u) < cp_needed:
                continue

            if needs_attach:
                orders.append({
                    "command": "truck_attach",
                    "params": {"unit_id": uid},
                })
                charge(uid, 0.5)
                if not budget_left():
                    break

            load_amount = min(internal - 1.0, 20.0)  # keep 1.0 for unit
            if load_amount <= 0:
                continue
            orders.append({
                "command": "truck_load",
                "params": {"unit_id": uid, "supplies": {resource: load_amount}},
            })
            charge(uid, 1.0)
            if not budget_left():
                break

            dump_id = f"emrg_{resource[:3]}_{u.hex_id}"
            dump_params = {
                "unit_id": uid, "dump_id": dump_id,
                "fuel": 0, "water": 0, "ammo": 0, "stores": 0,
            }
            dump_params[resource] = load_amount
            orders.append({"command": "create_dump", "params": dump_params})
            charge(uid, 1.0)
            created_dumps[u.hex_id] = dump_id

        # ════ PHASE B (remaining): Draw from newly-created dumps ════

        for uid, u, level, cap in critical:
            if not budget_left():
                break
            if rcpa(uid, u) < 1.0:
                continue
            needed = cap - level

            # Check newly-created dumps (from Phase A / Priority 4)
            if u.hex_id in created_dumps:
                orders.append({
                    "command": "draw_from_dump",
                    "params": {
                        "unit_id": uid,
                        "dump_id": created_dumps[u.hex_id],
                        "supplies": {resource: min(needed, 20.0)},
                    },
                })
                charge(uid, 1.0)
                continue

            # Unit has cargo + needs truck → attach + unload
            cargo = getattr(u, f"truck_cargo_{resource}", 0.0)
            if cargo > 0 and budget_left():
                if u.attached_truck_points <= 0 and rcpa(uid, u) >= 1.5:
                    orders.append({
                        "command": "truck_attach",
                        "params": {"unit_id": uid},
                    })
                    charge(uid, 0.5)
                if u.attached_truck_points > 0 or any(
                    o["command"] == "truck_attach" and o["params"].get("unit_id") == uid
                    for o in orders
                ):
                    if budget_left() and rcpa(uid, u) >= 1.0:
                        orders.append({
                            "command": "truck_unload",
                            "params": {
                                "unit_id": uid,
                                "supplies": {resource: min(needed, cargo)},
                            },
                        })
                        charge(uid, 1.0)

        logger.info(
            "SituationEngine (%s) deterministic supply [%s]: %d orders "
            "(critical=%d, dumps_created=%d)",
            self.side, resource, len(orders), len(critical), len(created_dumps),
        )
        return orders

    def _compute_proactive_port_resupply(
        self,
        state: GameState,
    ) -> list[dict]:
        """
        Issue draw_from_supply_pool for any friendly unit sitting at a port
        with water below 80% capacity.  Runs every turn (not just during crisis)
        to prevent units from advancing into the desert without topping up.
        """
        orders: list[dict] = []
        for uid, u in state.units.items():
            if u.side != self.side or u.status != UnitStatus.ACTIVE:
                continue
            if not u.hex_id:
                continue
            hs = state.hexes.get(u.hex_id)
            if not hs or not hs.is_port:
                continue
            w_cap = u.supply.water_capacity
            if w_cap <= 0:
                continue
            if (u.supply.water / w_cap) >= 0.80:
                continue
            needed = w_cap - u.supply.water
            orders.append({
                "command": "draw_from_supply_pool",
                "params": {"unit_id": uid, "supplies": {"water": needed}},
            })
        if orders:
            logger.info(
                "SituationEngine (%s) proactive port resupply: %d units topped up",
                self.side, len(orders),
            )
        return orders

    def _compute_advance_orders(
        self,
        state: GameState,
        signals: StateSignals,
        max_orders: int = 4,
        exempt_uids: frozenset[str] = frozenset(),
    ) -> list[dict]:
        """
        Generate move orders for units NOT in contact that have CPA remaining.
        Called after combat playbook to keep the advance going.

        Units in *exempt_uids* (e.g. supply shuttles) are skipped so they
        aren't pushed away from their water-run routes.
        """
        from cna_engine.engine.agent_interface import _compute_suggested_moves

        # Find units with CPA that aren't at contact hexes
        contact_hexes: set[str] = set()
        for cr in signals.contact_force_ratios:
            contact_hexes.add(cr["hex_id"])
            contact_hexes.update(get_neighbors(cr["hex_id"]))

        idle_units = []
        for uid, u in state.units.items():
            if (u.side == self.side
                    and u.status == UnitStatus.ACTIVE
                    and u.hex_id
                    and u.effective_cpa > 0
                    and u.hex_id not in contact_hexes
                    and uid not in exempt_uids):
                # Water-aware gate: don't advance units that are genuinely low
                w_cap = u.supply.water_capacity
                if w_cap > 0 and (u.supply.water / w_cap) < 0.50:
                    continue
                # Supply-line gate: don't advance beyond supply reach.
                # Axis has worse logistics (few trucks, long coast road) so shorter leash.
                max_port_dist = 20 if self.side == Side.AXIS else 30
                if _port_dist(state, self.side, u.hex_id) > max_port_dist:
                    continue
                idle_units.append({"id": uid, "cpa_remaining": u.effective_cpa})

        if not idle_units:
            return []

        suggestions = _compute_suggested_moves(state, self.side, idle_units[:max_orders])
        orders = []
        for uid, dest in suggestions.items():
            if len(orders) >= max_orders:
                break
            # Skip if unit is already at the suggested destination
            unit = state.units.get(uid)
            if unit and unit.hex_id == dest:
                continue
            orders.append({
                "command": "move_unit",
                "params": {"unit_id": uid, "destination": dest},
            })

        if orders:
            logger.info(
                "SituationEngine (%s) advance orders: %d idle units moving",
                self.side, len(orders),
            )
        return orders

    def _classify(self, signals: StateSignals, role_hint: str,
                   state: Optional[GameState] = None) -> SituationLabel:
        """
        Stage 1: Classify the situation.
        First tries Axis strategy override, then deterministic rules,
        then falls back to LLM.
        """
        # Side-specific strategy overrides (need state for turn check)
        if state and role_hint == "front_line":
            allied_override = self._allied_strategy_override(signals, state)
            if allied_override is not None:
                return allied_override
            axis_override = self._axis_strategy_override(signals, state)
            if axis_override is not None:
                return axis_override

        # Try deterministic first
        det = deterministic_classify(signals)
        if det and det.confidence >= 0.9:
            return det

        # LLM classification
        return self._classify_with_llm(signals, role_hint)

    # ── Allied strategy override ──

    def _allied_strategy_override(
        self, signals: StateSignals, state: GameState,
    ) -> Optional[SituationLabel]:
        """
        Deterministic Allied overrides based on historical phases:
          Phase 1 (GT1-12): Graziani's advance — screen and fall back toward
                            Mersa Matruh. All border units motorized for fast
                            withdrawal. Let logistics advantage grow as Italians
                            overextend their supply lines.
          Phase 2 (GT13-24): Operation Compass — aggressive counterattack west.
          Phase 3 (GT25+):  Consolidate gains — hold unless 2:1+ ratio.
        Returns a SituationLabel if an override applies, else None.
        """
        if self.side != Side.ALLIED:
            return None

        gt = signals.game_turn

        # ── Phase 1: Screening withdrawal (GT1-12) ──
        # Allies are outnumbered. Screen the border and fall back toward
        # Mersa Matruh / Alexandria. Let the Italians stretch their supply
        # lines while Allied logistics shorten. Do not attack.
        if gt <= 12:
            # Water crisis — always retreat
            if signals.avg_water_pct < 30:
                return SituationLabel(
                    role="front_line",
                    situation="FIGHTING_RETREAT",
                    confidence=1.0,
                    reasoning=(
                        f"Allied Screen (GT{gt}): water at "
                        f"{signals.avg_water_pct:.0f}% — retreat to resupply"
                    ),
                    deterministic=True,
                )
            if signals.units_in_contact > 0:
                # Fall back — do not get decisively engaged
                if signals.best_assault_ratio >= 2.0:
                    # Overwhelming local advantage — opportunistic attack
                    return SituationLabel(
                        role="front_line",
                        situation="ATTACK_PREPARED",
                        confidence=0.9,
                        reasoning=(
                            f"Allied Screen (GT{gt}): ratio "
                            f"{signals.best_assault_ratio:.2f}:1 — opportunistic strike"
                        ),
                        deterministic=True,
                    )
                # Otherwise screen and withdraw — don't hold ground
                return SituationLabel(
                    role="front_line",
                    situation="FIGHTING_RETREAT",
                    confidence=1.0,
                    reasoning=(
                        f"Allied Screen (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 — screen and fall back "
                        f"toward Mersa Matruh, shorten supply lines"
                    ),
                    deterministic=True,
                )
            # No contact — hold position, don't advance into Italian army
            return SituationLabel(
                role="front_line",
                situation="DEFENSIVE_HOLD",
                confidence=0.9,
                reasoning=f"Allied Screen (GT{gt}): no contact, hold position and await Operation Compass",
                deterministic=True,
            )

        # ── Phase 2: Operation Compass (GT13-24) ──
        if gt <= 24:
            if signals.avg_water_pct < 30 and signals.units_in_contact > 0:
                return SituationLabel(
                    role="front_line",
                    situation="FIGHTING_RETREAT",
                    confidence=1.0,
                    reasoning=(
                        f"Allied Compass (GT{gt}): water at "
                        f"{signals.avg_water_pct:.0f}% — retreat to resupply"
                    ),
                    deterministic=True,
                )
            if signals.units_in_contact > 0:
                if signals.best_assault_ratio >= 0.5:
                    return SituationLabel(
                        role="front_line",
                        situation="ATTACK_PREPARED",
                        confidence=1.0,
                        reasoning=(
                            f"Allied Compass (GT{gt}): ratio "
                            f"{signals.best_assault_ratio:.2f}:1 — press the attack"
                        ),
                        deterministic=True,
                    )
                return SituationLabel(
                    role="front_line",
                    situation="DEFENSIVE_HOLD",
                    confidence=1.0,
                    reasoning=(
                        f"Allied Compass (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 — hold, regroup"
                    ),
                    deterministic=True,
                )
            # No contact — advance west
            return None  # Fall through to ADVANCE_OPPORTUNITY

        # ── Phase 3: Consolidation (GT25+) ──
        if signals.units_in_contact > 0:
            if signals.best_assault_ratio >= 2.0:
                return SituationLabel(
                    role="front_line",
                    situation="ATTACK_PREPARED",
                    confidence=0.95,
                    reasoning=(
                        f"Allied Phase 3 (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 ≥ 2.0, attack"
                    ),
                    deterministic=True,
                )
            return SituationLabel(
                role="front_line",
                situation="DEFENSIVE_HOLD",
                confidence=0.95,
                reasoning=(
                    f"Allied Phase 3 (GT{gt}): ratio "
                    f"{signals.best_assault_ratio:.2f}:1 < 2.0, hold gains"
                ),
                deterministic=True,
            )

        # No override — fall through to existing rules / LLM
        return None

    # ── Axis phased-strategy override ──

    def _axis_strategy_override(
        self, signals: StateSignals, state: GameState,
    ) -> Optional[SituationLabel]:
        """
        Deterministic Axis overrides based on historical phases:
          Phase 1 (GT1-12): Graziani's advance — cautious push east toward
                            Sidi Barrani, but strictly limited by supply lines.
                            Italian divisions have almost no mobility; most must
                            march on foot. Nearly all trucks committed to hauling
                            supplies up the coast road. Do NOT outrun supply.
          Phase 2 (GT13-24): Operation Compass defense — Allies counterattack.
                             Fall back to Bardia/Tobruk fortifications, fight
                             delaying actions to buy time.
          Phase 3 (GT25+):  Hold defensive positions. Counterattack only at 2:1+.
        Returns a SituationLabel if an override applies, else None.
        """
        if self.side != Side.AXIS:
            return None

        gt = signals.game_turn

        # ── Phase 1: Graziani's Advance (GT1-12) ──
        # Cautious advance east. Supply is the limiting factor, not combat.
        # Stop advancing if water drops or units are too far from ports.
        if gt <= 12:
            # Supply crisis — halt only if genuinely dehydrated AND overextended
            if signals.avg_water_pct < 30 and getattr(signals, 'overextended', False):
                return SituationLabel(
                    role="front_line",
                    situation="DEFENSIVE_HOLD",
                    confidence=1.0,
                    reasoning=(
                        f"Axis Advance (GT{gt}): water at "
                        f"{signals.avg_water_pct:.0f}% and overextended — halt advance, resupply"
                    ),
                    deterministic=True,
                )
            if signals.units_in_contact > 0:
                # Attack if favorable, but don't waste supply on bad odds
                if signals.best_assault_ratio >= 1.5:
                    return SituationLabel(
                        role="front_line",
                        situation="ATTACK_PREPARED",
                        confidence=1.0,
                        reasoning=(
                            f"Axis Advance (GT{gt}): ratio "
                            f"{signals.best_assault_ratio:.2f}:1 — attack"
                        ),
                        deterministic=True,
                    )
                # Outnumbered or even — hold and bring up supply
                return SituationLabel(
                    role="front_line",
                    situation="DEFENSIVE_HOLD",
                    confidence=1.0,
                    reasoning=(
                        f"Axis Advance (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 — hold, bring up supply"
                    ),
                    deterministic=True,
                )
            # No contact — advance cautiously (fall through to ADVANCE_OPPORTUNITY)
            return None

        # ── Phase 2: Operation Compass Defense (GT13-24) ──
        # Allies are counterattacking. Fall back to fortified positions,
        # fight delaying actions. Don't waste units in hopeless attacks.
        if gt <= 24:
            if signals.units_in_contact > 0:
                if signals.best_assault_ratio >= 2.0:
                    return SituationLabel(
                        role="front_line",
                        situation="ATTACK_PREPARED",
                        confidence=0.9,
                        reasoning=(
                            f"Axis Defense (GT{gt}): ratio "
                            f"{signals.best_assault_ratio:.2f}:1 — counterattack"
                        ),
                        deterministic=True,
                    )
                # Fall back toward Bardia/Tobruk
                return SituationLabel(
                    role="front_line",
                    situation="FIGHTING_RETREAT",
                    confidence=1.0,
                    reasoning=(
                        f"Axis Defense (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 — fall back toward "
                        f"Bardia/Tobruk, fight delaying action"
                    ),
                    deterministic=True,
                )
            # No contact — hold fortified position
            return SituationLabel(
                role="front_line",
                situation="DEFENSIVE_HOLD",
                confidence=0.9,
                reasoning=f"Axis Defense (GT{gt}): hold position, await Allied advance",
                deterministic=True,
            )

        # ── Phase 3: Consolidation (GT25+) ──
        if signals.units_in_contact > 0:
            if signals.best_assault_ratio >= 2.0:
                return SituationLabel(
                    role="front_line",
                    situation="ATTACK_PREPARED",
                    confidence=0.95,
                    reasoning=(
                        f"Axis Phase 3 (GT{gt}): ratio "
                        f"{signals.best_assault_ratio:.2f}:1 ≥ 2.0, counterattack"
                    ),
                    deterministic=True,
                )
            return SituationLabel(
                role="front_line",
                situation="DEFENSIVE_HOLD",
                confidence=0.95,
                reasoning=(
                    f"Axis Phase 3 (GT{gt}): ratio "
                    f"{signals.best_assault_ratio:.2f}:1 < 2.0, hold"
                ),
                deterministic=True,
            )

        # No override — fall through to existing rules / LLM
        return None

    def _classify_front_line(self, signals: StateSignals, state: GameState) -> SituationLabel:
        """
        Classify front-line situation only, ignoring supply overrides.
        Used in the multi-role movement_combat handler where supply
        is handled separately — we always need a front-line decision.
        """
        # Only check front-line deterministic rules (not supply ones)
        if signals.active_units == 0:
            return SituationLabel(
                role="cinc", situation="VP_CRISIS",
                confidence=1.0, reasoning="No active units",
                deterministic=True,
            )

        # ── Side-specific strategy overrides ──
        allied_override = self._allied_strategy_override(signals, state)
        if allied_override is not None:
            return allied_override
        axis_override = self._axis_strategy_override(signals, state)
        if axis_override is not None:
            return axis_override

        if signals.overextended and (signals.avg_fuel_pct < 30 or signals.avg_water_pct < 30):
            return SituationLabel(
                role="front_line", situation="OVEREXTENDED_HALT",
                confidence=0.95,
                reasoning=(
                    f"Overextended, fuel at {signals.avg_fuel_pct}%, "
                    f"water at {signals.avg_water_pct}%"
                ),
                deterministic=True,
            )

        # ── Deterministic retreat / hold rules (contact situations) ──

        # Rule A: Outnumbered at contact — retreat (unless winning on VP)
        if (signals.units_in_contact > 0
                and signals.best_assault_ratio < 0.75):
            # If winning or even on VP, hold ground instead of retreating
            if signals.vp_leading or signals.vp_margin >= 0:
                return SituationLabel(
                    role="front_line", situation="DEFENSIVE_HOLD",
                    confidence=0.90,
                    reasoning=(
                        f"Outnumbered (ratio {signals.best_assault_ratio:.2f}:1) "
                        f"but winning VP ({signals.vp_margin:+.1f}) — hold ground"
                    ),
                    deterministic=True,
                )
            return SituationLabel(
                role="front_line", situation="FIGHTING_RETREAT",
                confidence=0.95,
                reasoning=(
                    f"Outnumbered at contact: best ratio "
                    f"{signals.best_assault_ratio:.2f}:1 (<0.75)"
                ),
                deterministic=True,
            )

        # Rule B: Strategically overwhelmed — retreat (unless winning on VP)
        if (signals.units_in_contact > 0
                and signals.total_strength
                < signals.enemy_total_strength * 0.6):
            if signals.vp_leading or signals.vp_margin >= 0:
                return SituationLabel(
                    role="front_line", situation="DEFENSIVE_HOLD",
                    confidence=0.85,
                    reasoning=(
                        f"Army outnumbered ({signals.total_strength} vs "
                        f"{signals.enemy_total_strength} SP) but winning VP "
                        f"({signals.vp_margin:+.1f}) — hold ground"
                    ),
                    deterministic=True,
                )
            return SituationLabel(
                role="front_line", situation="FIGHTING_RETREAT",
                confidence=0.90,
                reasoning=(
                    f"Army overwhelmed: {signals.total_strength} SP vs "
                    f"{signals.enemy_total_strength} enemy (<60%)"
                ),
                deterministic=True,
            )

        # Rule C: VP deficit forces aggression
        if (signals.vp_margin < -3
                and signals.units_in_contact > 0
                and signals.best_assault_ratio >= 1.0):
            return SituationLabel(
                role="front_line", situation="ATTACK_PREPARED",
                confidence=0.85,
                reasoning=(
                    f"VP deficit ({signals.vp_margin:.1f}), "
                    f"ratio {signals.best_assault_ratio:.1f}:1 — attack to close gap"
                ),
                deterministic=True,
            )

        # Rule D: Favorable ratio → attack
        if (signals.units_in_contact > 0
                and signals.best_assault_ratio >= 1.5):
            return SituationLabel(
                role="front_line", situation="ATTACK_PREPARED",
                confidence=0.85,
                reasoning=f"Favorable ratio {signals.best_assault_ratio:.1f}:1",
                deterministic=True,
            )

        # Rule E: Stalemate detection — break advance loops
        if (getattr(signals, 'consecutive_failed_advances', 0) >= 3
                and signals.units_in_contact == 0):
            return SituationLabel(
                role="logistics", situation="SUPPLY_FLOWING",
                confidence=0.85,
                reasoning=(
                    f"{signals.consecutive_failed_advances} consecutive failed advances "
                    f"with no contact — switch to supply ops to break loop"
                ),
                deterministic=True,
            )

        if signals.enemy_units_sighted == 0 and signals.units_in_contact == 0:
            return SituationLabel(
                role="front_line", situation="ADVANCE_OPPORTUNITY",
                confidence=0.9,
                reasoning="No enemy sighted or in contact",
                deterministic=True,
            )

        # Enemy sighted but no contact — cautious advance or hold
        if signals.units_in_contact == 0 and signals.enemy_units_sighted > 0:
            if signals.total_strength >= signals.enemy_total_strength * 0.8:
                return SituationLabel(
                    role="front_line", situation="ADVANCE_OPPORTUNITY",
                    confidence=0.80,
                    reasoning=(
                        f"Enemy sighted but no contact, strength ratio favorable "
                        f"({signals.total_strength} vs {signals.enemy_total_strength}) — advance"
                    ),
                    deterministic=True,
                )
            return SituationLabel(
                role="front_line", situation="DEFENSIVE_HOLD",
                confidence=0.80,
                reasoning=(
                    f"Enemy sighted but no contact, outnumbered "
                    f"({signals.total_strength} vs {signals.enemy_total_strength}) — hold"
                ),
                deterministic=True,
            )

        # Fall through to LLM for contact situations
        return self._classify_with_llm(signals, "front_line")

    def _classify_with_llm(self, signals: StateSignals, role_hint: str) -> SituationLabel:
        """Call LLM for situation classification."""
        system_prompt = build_classifier_system_prompt(
            self.side, signals.phase, role_hint=role_hint,
        )
        user_prompt = build_classifier_user_prompt(signals)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = self.llm_client.chat(messages, json_mode=True)
            parsed = response.parsed or {}
            return parse_classifier_response(parsed)
        except (LLMError, Exception) as e:
            logger.warning(
                "SituationEngine (%s) classifier LLM failed: %s — using fallback",
                self.side, e,
            )
            return self._fallback_classify(signals, role_hint)

    def _fallback_classify(self, signals: StateSignals, role: str) -> SituationLabel:
        """Deterministic fallback when LLM classifier fails. VP-aware."""
        if role == "front_line":
            # Losing → attack/advance; winning → hold
            if signals.vp_margin < -3 and signals.best_assault_ratio >= 1.0:
                situation = "ATTACK_PREPARED"
            elif signals.vp_margin < 0 and signals.units_in_contact == 0:
                situation = "ADVANCE_OPPORTUNITY"
            else:
                situation = "DEFENSIVE_HOLD"
        elif role == "cinc":
            situation = "CAMPAIGN_ADVANCE" if signals.vp_margin < -3 else "CAMPAIGN_CONSOLIDATE"
        else:
            fallback_map = {
                "logistics": "SUPPLY_FLOWING",
                "air": "AIR_PARITY",
            }
            situation = fallback_map.get(role, "DEFENSIVE_HOLD")
        return SituationLabel(
            role=role,
            situation=situation,
            confidence=0.3,
            reasoning=f"Fallback classification (VP margin: {signals.vp_margin:.1f})",
            deterministic=True,
        )

    def _execute_playbook(
        self,
        playbook: Playbook,
        label: SituationLabel,
        filtered_state: str,
        signals: StateSignals,
        max_orders_override: Optional[int] = None,
        supply_severity: Optional[str] = None,
    ) -> list[dict]:
        """
        Stage 2: Execute a playbook by calling the LLM with the playbook's
        focused prompt and filtered state.
        """
        # Tool-calling mode: delegate to agentic loop
        if self.config.tool_calling:
            return self._execute_playbook_tools(
                playbook, label, filtered_state, signals, max_orders_override,
                supply_severity=supply_severity,
            )

        max_orders = max_orders_override or playbook.max_orders

        # Build retreat direction for fighting retreat
        retreat_direction = ""
        if self.side == Side.AXIS:
            retreat_direction = (
                "West toward Tripoli (move toward Map sections C, B, A). "
                "Move units westward along the coast road toward Tripoli."
            )
        else:
            retreat_direction = (
                "East toward Alexandria (move toward Map sections D, E). "
                "Move units eastward along the coast road toward Alexandria."
            )

        # Format system prompt with dynamic values
        system = playbook.system_prompt
        system = system.replace("{max_orders}", str(max_orders))
        system = system.replace("{retreat_direction}", retreat_direction)

        # Query RAG for situationally relevant rules/doctrine/playbook
        rag_context = ""
        if self.rag and self.rag.is_available:
            try:
                from .rag import build_situation_query
                query = build_situation_query(label.situation, playbook.role, signals)
                results = self.rag.query(query, side=self.side, k=5)
                rag_context = self.rag.format_context(results, max_chars=2000)
                if rag_context:
                    logger.info(
                        "SituationEngine (%s) RAG: %d results, %d chars for %s",
                        self.side, len(results), len(rag_context), label.situation,
                    )
            except Exception as e:
                logger.warning("SituationEngine (%s) RAG query failed: %s", self.side, e)

        # Build user message
        user_lines = [
            f"Situation: {label.situation} (confidence: {label.confidence:.1f})",
            f"Reasoning: {label.reasoning}",
            "",
        ]
        if rag_context:
            user_lines.append(rag_context)
            user_lines.append("")
        # Strategic context (VP awareness)
        if signals.vp_margin != 0 or signals.objectives_held:
            winning = "WINNING" if signals.vp_leading else "LOSING"
            user_lines.append(f"STRATEGIC SITUATION: You are {winning} by {abs(signals.vp_margin):.1f} VP.")
            if signals.objectives_held:
                user_lines.append(f"Objectives held: {', '.join(signals.objectives_held)}")
            if signals.objectives_contested:
                user_lines.append(f"Contested objectives: {', '.join(signals.objectives_contested)}")
            user_lines.append("")
        # Supply advisory — inform LLM without gating decisions
        if supply_severity:
            n_critical = signals.water_critical_count + signals.fuel_critical_count
            user_lines.append(
                f"SUPPLY ADVISORY ({supply_severity.upper()}): "
                f"avg water {signals.avg_water_pct:.0f}%, "
                f"{n_critical} units critically low. "
                f"Supply orders are being handled separately — "
                f"focus your orders on operational objectives."
            )
            user_lines.append("")
        user_lines.extend([
            "=== BATTLEFIELD STATE ===",
            filtered_state,
            "",
            f"Issue your orders (maximum {max_orders}). Commands allowed: {', '.join(playbook.applicable_commands)}",
        ])
        if playbook.priority_commands:
            user_lines.append(f"Consider these first: {', '.join(playbook.priority_commands)}")
        user_lines.append(
            "\nIMPORTANT: Check unit position before issuing move_unit orders "
            "and DO NOT issue move_unit to a unit's current hex."
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

        # Scale thinking budget by situation complexity
        think_budget = _think_budget_for_situation(label.situation)

        try:
            response = self.llm_client.chat(messages, json_mode=True, think=think_budget)
            parsed = response.parsed or {}

            if self.config.log_llm_calls:
                logger.debug(
                    "SituationEngine (%s) playbook response [%s]: %s",
                    self.side, label.situation, parsed,
                )

            orders = []
            for order in parsed.get("orders", []):
                cmd = order.get("command", "")
                params = order.get("params", {})
                if cmd:
                    orders.append({"command": cmd, "params": params})

            # Retry if any move_unit targets the unit's current hex
            if self._current_state and self._has_same_hex_moves(orders):
                logger.info(
                    "SituationEngine (%s) same-hex move detected — retrying",
                    self.side,
                )
                response = self.llm_client.chat(messages, json_mode=True, think=think_budget)
                parsed = response.parsed or {}
                orders = []
                for order in parsed.get("orders", []):
                    cmd = order.get("command", "")
                    params = order.get("params", {})
                    if cmd:
                        orders.append({"command": cmd, "params": params})

            # Deduplicate: keep first move_unit per unit_id
            seen_move_uids: set[str] = set()
            deduped = []
            for order in orders:
                if order.get("command") == "move_unit":
                    uid = order.get("params", {}).get("unit_id", "")
                    if uid in seen_move_uids:
                        continue
                    seen_move_uids.add(uid)
                deduped.append(order)

            return deduped[:max_orders]

        except (LLMError, Exception) as e:
            logger.error(
                "SituationEngine (%s) playbook LLM failed for %s: %s",
                self.side, label.situation, e,
            )
            return [{"command": "end_phase", "params": {}}]

    def _execute_playbook_tools(
        self,
        playbook: Playbook,
        label: SituationLabel,
        filtered_state: str,
        signals: StateSignals,
        max_orders_override: Optional[int] = None,
        supply_severity: Optional[str] = None,
    ) -> list[dict]:
        """
        Stage 2 (tool-calling variant): Execute a playbook via iterative
        tool calls instead of a single-shot JSON response.

        The LLM can inspect units, check hexes, and issue orders one at a
        time with validation feedback after each call.
        """
        max_orders = max_orders_override or playbook.max_orders
        state = self._current_state

        # Build retreat direction for fighting retreat
        retreat_direction = ""
        if self.side == Side.AXIS:
            retreat_direction = (
                "West toward Tripoli (move toward Map sections C, B, A). "
                "Move units westward along the coast road toward Tripoli."
            )
        else:
            retreat_direction = (
                "East toward Alexandria (move toward Map sections D, E). "
                "Move units eastward along the coast road toward Alexandria."
            )

        # Build system prompt — reuse playbook prompt but adapt for tool mode
        system = playbook.system_prompt
        system = system.replace("{max_orders}", str(max_orders))
        system = system.replace("{retreat_direction}", retreat_direction)

        # Strip any "Respond with JSON:" tail and add tool-calling guidance
        for marker in ["Respond with JSON:", "Respond with a JSON"]:
            idx = system.rfind(marker)
            if idx != -1:
                system = system[:idx].rstrip()

        system += (
            "\n\nYou have tools to inspect units, check hexes, and issue orders. "
            "Use inspect_unit and check_hex to gather information, then "
            "issue_order to submit each order individually. Each order is "
            "validated immediately — if rejected, read the error and try a "
            f"different approach. You may issue up to {max_orders} orders. "
            "When done, call issue_order with command='end_phase'."
        )

        # Query RAG for situationally relevant rules/doctrine/playbook
        rag_context = ""
        if self.rag and self.rag.is_available:
            try:
                from .rag import build_situation_query
                query = build_situation_query(label.situation, playbook.role, signals)
                results = self.rag.query(query, side=self.side, k=5)
                rag_context = self.rag.format_context(results, max_chars=2000)
                if rag_context:
                    logger.info(
                        "SituationEngine (%s) RAG: %d results, %d chars for %s [tools]",
                        self.side, len(results), len(rag_context), label.situation,
                    )
            except Exception as e:
                logger.warning("SituationEngine (%s) RAG query failed: %s", self.side, e)

        # Build user message
        user_lines = [
            f"Situation: {label.situation} (confidence: {label.confidence:.1f})",
            f"Reasoning: {label.reasoning}",
            "",
        ]
        if rag_context:
            user_lines.append(rag_context)
            user_lines.append("")
        # Strategic context (VP awareness)
        if signals.vp_margin != 0 or signals.objectives_held:
            winning = "WINNING" if signals.vp_leading else "LOSING"
            user_lines.append(f"STRATEGIC SITUATION: You are {winning} by {abs(signals.vp_margin):.1f} VP.")
            if signals.objectives_held:
                user_lines.append(f"Objectives held: {', '.join(signals.objectives_held)}")
            if signals.objectives_contested:
                user_lines.append(f"Contested objectives: {', '.join(signals.objectives_contested)}")
            user_lines.append("")
        user_lines.extend([
            "=== BATTLEFIELD STATE ===",
            filtered_state,
            "",
            f"Commands allowed: {', '.join(playbook.applicable_commands)}",
        ])
        if playbook.priority_commands:
            user_lines.append(
                f"Consider these first: {', '.join(playbook.priority_commands)}"
            )
        user_lines.append(
            "\nIMPORTANT: Check unit position before issuing move_unit orders "
            "and DO NOT issue move_unit to a unit's current hex."
        )
        user_lines.append(
            "\nUse inspect_unit and check_hex to examine the battlefield, "
            "then issue_order for each order."
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

        # Set up tool handler with mutable order accumulator
        accumulated_orders: list[dict] = []
        handler = create_tool_handler(
            state, self.side, playbook, accumulated_orders, signals,
        )

        tools = build_tool_list()

        try:
            response = self.llm_client.chat_with_tools(
                messages=messages,
                tools=tools,
                max_iterations=self.config.tool_max_iterations,
                timeout=self.config.tool_timeout,
                on_tool_call=handler,
            )

            logger.info(
                "SituationEngine (%s) tool-calling [%s]: %d iterations, "
                "%d tool calls, %d orders queued, stop=%s (%dms)",
                self.side, label.situation, response.iterations,
                len(response.tool_results), len(accumulated_orders),
                response.stop_reason, response.duration_ms,
            )

            if not accumulated_orders:
                logger.warning(
                    "SituationEngine (%s) tool-calling produced 0 orders — "
                    "falling back to end_phase",
                    self.side,
                )
                return [{"command": "end_phase", "params": {}}]

            return accumulated_orders[:max_orders]

        except Exception as e:
            logger.error(
                "SituationEngine (%s) tool-calling failed for %s: %s",
                self.side, label.situation, e,
            )
            return [{"command": "end_phase", "params": {}}]

    def _has_same_hex_moves(self, orders: list[dict]) -> bool:
        """Check if any move_unit order targets the unit's current hex."""
        state = self._current_state
        if not state:
            return False
        for order in orders:
            if order.get("command") == "move_unit":
                uid = order.get("params", {}).get("unit_id", "")
                dest = order.get("params", {}).get("destination", "")
                unit = state.units.get(uid)
                if unit and unit.hex_id == dest:
                    return True
        return False

    def _validate_orders(
        self,
        state: GameState,
        orders: list[dict],
        playbook: Playbook | None = None,
    ) -> list[dict]:
        """
        Validate orders against engine rules AND playbook whitelist.
        Silently drops invalid orders.  If playbook is None, skip whitelist check
        (used for deterministic orders that bypass the playbook).
        """
        valid = []
        seen_truck_attach: set[str] = set()
        whitelist = set(playbook.applicable_commands) if playbook else None

        for order in orders:
            command = order.get("command", "")
            params = order.get("params", {})

            # Guard: params must be dict
            if not isinstance(params, dict):
                logger.warning(
                    "SituationEngine (%s): command '%s' has non-dict params — skipping",
                    self.side, command,
                )
                continue

            if command == "end_phase":
                valid.append(order)
                continue

            # ── Pre-check: target unit must be active ──
            uid = params.get("unit_id", "")
            if uid:
                unit = state.units.get(uid)
                if unit and unit.status in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN, UnitStatus.SURRENDERED):
                    logger.info(
                        "SituationEngine (%s): '%s' for '%s' skipped — unit %s",
                        self.side, command, uid, unit.status.name,
                    )
                    continue

            # ── Pre-check: truck_load/truck_unload require truck points ──
            if command in ("truck_load", "truck_unload") and uid:
                unit = state.units.get(uid)
                if unit and unit.attached_truck_points <= 0:
                    logger.info(
                        "SituationEngine (%s): '%s' for '%s' skipped — no truck points",
                        self.side, command, uid,
                    )
                    continue

            # ── Pre-check: fire_barrage requires adjacent gun units ──
            if command == "fire_barrage":
                from cna_engine.engine.movement import get_neighbors
                target_hex = params.get("target_hex", "")
                friendly_attr = "allied_unit_ids" if self.side == Side.ALLIED else "axis_unit_ids"
                adj_gun_sp = 0
                for adj_id in get_neighbors(target_hex):
                    adj = state.hexes.get(adj_id)
                    if adj:
                        for u_id in getattr(adj, friendly_attr):
                            u = state.units.get(u_id)
                            if u and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                                adj_gun_sp += u.current_strength.gun
                if adj_gun_sp <= 0:
                    logger.info(
                        "SituationEngine (%s): fire_barrage at '%s' skipped — no adjacent gun units",
                        self.side, target_hex,
                    )
                    continue

            # ── Pre-check: fire_anti_armor requires adjacent armor/gun AND enemy at target ──
            if command == "fire_anti_armor":
                from cna_engine.engine.movement import get_neighbors as _get_neighbors_aa_v
                target_hex = params.get("target_hex", "")
                # Check enemy presence at target
                enemy_attr = "axis_unit_ids" if self.side == Side.ALLIED else "allied_unit_ids"
                ths = state.hexes.get(target_hex)
                if not ths or not getattr(ths, enemy_attr, []):
                    logger.info(
                        "SituationEngine (%s): fire_anti_armor at '%s' skipped — no enemy units",
                        self.side, target_hex,
                    )
                    continue
                # Check friendly armor/gun adjacent
                friendly_attr = "allied_unit_ids" if self.side == Side.ALLIED else "axis_unit_ids"
                aa_sp = 0
                for adj_id in _get_neighbors_aa_v(target_hex):
                    adj = state.hexes.get(adj_id)
                    if adj:
                        for u_id in getattr(adj, friendly_attr):
                            u = state.units.get(u_id)
                            if u and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                                aa_sp += u.current_strength.armor + u.current_strength.gun
                if aa_sp <= 0:
                    logger.info(
                        "SituationEngine (%s): fire_anti_armor at '%s' skipped — no adjacent armor/gun",
                        self.side, target_hex,
                    )
                    continue

            # ── Pre-check: close_assault requires enemy at target ──
            if command == "close_assault":
                target_hex = params.get("target_hex", "")
                enemy_attr = "axis_unit_ids" if self.side == Side.ALLIED else "allied_unit_ids"
                ths = state.hexes.get(target_hex)
                if not ths or not getattr(ths, enemy_attr, []):
                    logger.info(
                        "SituationEngine (%s): close_assault at '%s' skipped — no enemy units",
                        self.side, target_hex,
                    )
                    continue

            # Pre-filter: fly_sortie out-of-range check
            if command == "fly_sortie":
                from cna_engine.engine.air import get_aircraft_stats
                from cna_engine.engine.movement import _hex_distance
                ac_id = params.get("aircraft_id", "")
                target_hex = params.get("target_hex", "")
                ac = state.aircraft.get(ac_id)
                if ac and ac.sgsu_id and target_hex:
                    sgsu = state.sgsus.get(ac.sgsu_id)
                    if sgsu and sgsu.hex_id:
                        _, _, _, range_hexes = get_aircraft_stats(ac.aircraft_type_id)
                        eff_range = range_hexes // 2
                        dist = _hex_distance(sgsu.hex_id, target_hex)
                        if dist > eff_range:
                            logger.info(
                                "SituationEngine (%s): fly_sortie '%s' → '%s' skipped — "
                                "out of range (dist=%d, range=%d)",
                                self.side, ac_id, target_hex, dist, eff_range,
                            )
                            continue

            # Whitelist enforcement (skip if no playbook)
            if whitelist and command not in whitelist:
                logger.info(
                    "SituationEngine (%s): command '%s' not in whitelist %s — dropping",
                    self.side, command, playbook.situation if playbook else "?",
                )
                continue

            # Deduplicate truck_attach
            if command == "truck_attach":
                if uid in seen_truck_attach:
                    continue
                unit = state.units.get(uid)
                if unit and unit.attached_truck_points > 0:
                    logger.info(
                        "SituationEngine (%s): truck_attach for '%s' skipped — already has truck",
                        self.side, uid,
                    )
                    continue
                seen_truck_attach.add(uid)

            # Engine validation
            role = _COMMAND_TO_ROLE.get(command)
            if not role:
                logger.warning(
                    "SituationEngine (%s): unknown command '%s' — skipping",
                    self.side, command,
                )
                continue

            result = validate_command(state, role, self.side, command, **params)
            if result.success:
                valid.append(order)
            else:
                logger.warning(
                    "SituationEngine (%s): '%s' validation failed: %s — skipping",
                    self.side, command, result.error,
                )

        return valid
