"""
CNA Engine — Turn Memory
Rolling history of recent turns for LLM context injection.
Agents get compact summaries of what happened in previous turns
so they can make informed decisions with cross-turn context.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side

logger = logging.getLogger(__name__)


@dataclass
class TurnRecord:
    """Record of events from a single game turn."""
    game_turn: int
    orders_given: list[dict] = field(default_factory=list)      # [{side, command, params, success}]
    outcomes: list[str] = field(default_factory=list)            # Human-readable events
    unit_losses: dict[str, int] = field(default_factory=dict)    # {side: SP lost}
    positions_changed: list[dict] = field(default_factory=list)  # [{unit_id, from_hex, to_hex}]
    supply_snapshot: dict = field(default_factory=dict)           # {allied: {fuel,...}, axis: {fuel,...}}
    feedback: list[str] = field(default_factory=list)            # Outcome-aware tactical lessons


class TurnMemory:
    """
    Rolling window of recent turn history for LLM context injection.
    Keeps the last `max_turns` turns of compact summaries.
    Budget: ~400 chars per turn x 5 turns = 2000 chars max.
    """

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.records: list[TurnRecord] = []
        self._current_record: Optional[TurnRecord] = None

    def record_phase(self, state: GameState, phase_summary) -> None:
        """
        Accumulate phase results into the current turn record.
        Called after each interactive phase within a turn.

        Args:
            state: Current game state.
            phase_summary: PhaseSummary from orchestrator.play_phase().
        """
        gt = state.turn.game_turn

        # Lazily create current turn record
        if self._current_record is None or self._current_record.game_turn != gt:
            self._current_record = TurnRecord(game_turn=gt)

        rec = self._current_record

        # Extract orders from phase results
        for result in getattr(phase_summary, "results", []):
            order = {
                "side": getattr(phase_summary, "side", "unknown"),
                "command": getattr(result, "command", "?"),
                "params": getattr(result, "params", {}),
                "success": getattr(result, "success", False),
            }
            error = getattr(result, "error", None)
            if error:
                order["error"] = str(error)[:120]  # Truncate for budget
            rec.orders_given.append(order)

        # Build outcome summary
        succeeded = getattr(phase_summary, "orders_succeeded", 0)
        failed = getattr(phase_summary, "orders_failed", 0)
        side = getattr(phase_summary, "side", "?")
        phase = getattr(phase_summary, "sub_phase", None) or getattr(phase_summary, "phase", "?")
        rec.outcomes.append(
            f"{side} {phase}: {succeeded} succeeded, {failed} failed"
        )

    def record_turn(self, state: GameState, turn_summary=None) -> None:
        """
        Finalize and store the current turn record.
        Called at the end of play_turn(). Captures supply snapshot
        and evicts oldest record if over max_turns.

        Args:
            state: Game state after turn completion.
            turn_summary: Optional TurnSummary from orchestrator.
        """
        gt = state.turn.game_turn

        # Use the accumulated record, or create a minimal one
        if self._current_record is not None and self._current_record.game_turn == gt:
            rec = self._current_record
        else:
            rec = TurnRecord(game_turn=gt)

        # Capture supply snapshot
        rec.supply_snapshot = self._capture_supply(state)

        # Capture unit losses (sum current losses_taken per side)
        rec.unit_losses = self._capture_losses(state)

        # Add turn-level outcome if turn_summary provided
        if turn_summary is not None:
            interactive = getattr(turn_summary, "interactive_phases", 0)
            auto = getattr(turn_summary, "auto_phases", 0)
            rec.outcomes.insert(0, f"GT{gt}: {interactive} interactive, {auto} auto phases")

        # Generate outcome feedback by comparing against previous turns
        rec.feedback = self._generate_outcome_feedback(rec)

        # Store and evict
        self.records.append(rec)
        if len(self.records) > self.max_turns:
            self.records = self.records[-self.max_turns:]

        self._current_record = None
        logger.debug("TurnMemory: recorded GT%d (%d records stored, %d feedback items)",
                      gt, len(self.records), len(rec.feedback))

    def get_context_text(self, side: str, max_chars: int = 2000) -> str:
        """
        Generate compact history text for a General's prompt.

        Args:
            side: "allied" or "axis" — filters to show orders/events
                  relevant to this side.
            max_chars: Maximum character budget for the context block.

        Returns:
            Formatted text block suitable for prompt injection.
        """
        if not self.records:
            return ""

        lines = []
        for rec in self.records:
            lines.append(f"--- GT{rec.game_turn} ---")

            # Orders summary (filter by side)
            side_orders = [o for o in rec.orders_given if o.get("side") == side]
            if side_orders:
                succeeded = sum(1 for o in side_orders if o.get("success"))
                failed = len(side_orders) - succeeded
                cmds = [o.get("command", "?") for o in side_orders if o.get("command") != "end_phase"]
                if cmds:
                    lines.append(f"  Orders: {', '.join(cmds)} ({succeeded} ok, {failed} fail)")
                else:
                    lines.append(f"  Orders: end_phase only")

            # Key outcomes
            for outcome in rec.outcomes[:3]:  # Limit per turn
                lines.append(f"  {outcome}")

            # Supply snapshot for this side
            supply = rec.supply_snapshot.get(side, {})
            if supply:
                fuel = supply.get("avg_fuel_pct", 0)
                water = supply.get("avg_water_pct", 0)
                critical = supply.get("critical_units", 0)
                lines.append(f"  Supply: fuel {fuel:.0f}%, water {water:.0f}%, {critical} critical")

            # Losses
            losses = rec.unit_losses.get(side, 0)
            if losses:
                lines.append(f"  Losses: {losses} SP")

            # Outcome feedback (tactical lessons from this turn)
            for fb in rec.feedback:
                lines.append(f"  Note: {fb}")

        text = "\n".join(lines)

        # Truncate to budget
        if len(text) > max_chars:
            text = text[:max_chars - 3] + "..."

        return text

    def get_context_for_expert(self, side: str, role: str, max_chars: int = 1000) -> str:
        """
        Generate role-filtered context for an expert agent.
        Shows only the information relevant to a specific domain.

        Args:
            side: "allied" or "axis".
            role: Expert role (e.g., "ground", "logistics", "air", "naval").
            max_chars: Maximum character budget.

        Returns:
            Compact context text filtered for the expert's domain.
        """
        if not self.records:
            return ""

        # Map roles to relevant commands
        role_commands = {
            "ground": {"move_unit", "fire_barrage", "fire_anti_armor", "close_assault",
                        "break_contact", "break_engaged", "reaction_move"},
            "logistics": {"consume_fuel", "expend_ammo", "check_supply", "truck_attach",
                          "truck_detach", "truck_load", "truck_unload", "create_dump",
                          "draw_from_dump", "draw_water", "check_supply_lines"},
            "air": {"assign_mission", "fly_sortie", "recon"},
            "naval": {"plan_convoy", "fleet_sortie", "unload_port"},
        }
        relevant_cmds = role_commands.get(role, set())

        lines = []
        for rec in self.records:
            # Filter orders to this role's commands
            role_orders = [
                o for o in rec.orders_given
                if o.get("side") == side and o.get("command") in relevant_cmds
            ]

            if not role_orders and role != "logistics":
                continue

            lines.append(f"GT{rec.game_turn}:")

            if role_orders:
                cmds = [f"{o['command']}({'ok' if o['success'] else 'fail'})"
                        for o in role_orders]
                lines.append(f"  {', '.join(cmds)}")

            # Role-specific supply info for logistics
            if role == "logistics":
                supply = rec.supply_snapshot.get(side, {})
                if supply:
                    critical = supply.get("critical_units", 0)
                    lines.append(f"  Critical supply: {critical} units")

            # Losses for ground expert
            if role == "ground":
                losses = rec.unit_losses.get(side, 0)
                if losses:
                    lines.append(f"  Losses: {losses} SP")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars - 3] + "..."

        return text

    def to_dict(self) -> dict:
        """Serialize memory for save/load."""
        return {
            "max_turns": self.max_turns,
            "records": [
                {
                    "game_turn": r.game_turn,
                    "orders_given": r.orders_given,
                    "outcomes": r.outcomes,
                    "unit_losses": r.unit_losses,
                    "positions_changed": r.positions_changed,
                    "supply_snapshot": r.supply_snapshot,
                    "feedback": r.feedback,
                }
                for r in self.records
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> TurnMemory:
        """Deserialize memory from saved dict."""
        mem = cls(max_turns=data.get("max_turns", 5))
        for rd in data.get("records", []):
            rec = TurnRecord(
                game_turn=rd.get("game_turn", 0),
                orders_given=rd.get("orders_given", []),
                outcomes=rd.get("outcomes", []),
                unit_losses=rd.get("unit_losses", {}),
                positions_changed=rd.get("positions_changed", []),
                supply_snapshot=rd.get("supply_snapshot", {}),
                feedback=rd.get("feedback", []),
            )
            mem.records.append(rec)
        return mem

    def clear(self) -> None:
        """Reset all memory."""
        self.records.clear()
        self._current_record = None

    # ── Internal helpers ──

    def _generate_outcome_feedback(self, current: TurnRecord) -> list[str]:
        """
        Compare the just-finalized turn record against previous turns
        to produce concise tactical feedback lessons.

        Checks:
        - Supply trends (fuel/water improving or declining)
        - Combat effectiveness (failure ratio, which commands failed)
        - Critical warnings (units nearing 0 supply, recurring failures)
        """
        feedback = []

        # Need at least one previous record to compare
        if self.records:
            prev = self.records[-1]

            for side in ["allied", "axis"]:
                cur_supply = current.supply_snapshot.get(side, {})
                prev_supply = prev.supply_snapshot.get(side, {})

                if not cur_supply or not prev_supply:
                    continue

                # Supply trend: fuel
                cur_fuel = cur_supply.get("avg_fuel_pct", 0)
                prev_fuel = prev_supply.get("avg_fuel_pct", 0)
                fuel_delta = cur_fuel - prev_fuel

                # Supply trend: water
                cur_water = cur_supply.get("avg_water_pct", 0)
                prev_water = prev_supply.get("avg_water_pct", 0)
                water_delta = cur_water - prev_water

                if fuel_delta < -10:
                    feedback.append(
                        f"{side} fuel declining ({prev_fuel:.0f}% -> {cur_fuel:.0f}%) — prioritize resupply"
                    )
                if water_delta < -10:
                    feedback.append(
                        f"{side} water declining ({prev_water:.0f}% -> {cur_water:.0f}%) — prioritize draws"
                    )

                # Critical units warning
                critical = cur_supply.get("critical_units", 0)
                if critical > 0:
                    feedback.append(
                        f"{side} has {critical} unit(s) at critical supply (<25%)"
                    )

        # Combat effectiveness: per-side failure analysis
        for side in ["allied", "axis"]:
            side_orders = [o for o in current.orders_given if o.get("side") == side]
            if not side_orders:
                continue

            failed = [o for o in side_orders if not o.get("success")]
            total = len(side_orders)
            fail_count = len(failed)

            if fail_count > 0 and total > 0:
                fail_pct = (fail_count / total) * 100
                # Identify which commands failed and why
                failed_details = []
                for o in failed:
                    cmd = o.get("command", "?")
                    if cmd == "end_phase":
                        continue
                    error = o.get("error", "")
                    if error:
                        failed_details.append(f"{cmd} ({error})")
                    else:
                        failed_details.append(cmd)
                if failed_details:
                    # Deduplicate identical failure messages
                    seen = []
                    for d in failed_details:
                        if d not in seen:
                            seen.append(d)
                    feedback.append(
                        f"{side} {fail_count}/{total} orders failed: {'; '.join(seen)}"
                    )

        return feedback

    @staticmethod
    def _capture_supply(state: GameState) -> dict:
        """Capture per-side supply statistics."""
        snapshot = {}
        for side_val in ["allied", "axis"]:
            active = [
                u for u in state.units.values()
                if u.side == side_val and u.status == "active" and u.hex_id
            ]
            if not active:
                snapshot[side_val] = {"avg_fuel_pct": 0, "avg_water_pct": 0, "critical_units": 0}
                continue

            fuel_pcts = []
            water_pcts = []
            critical = 0
            for u in active:
                f_cap = u.supply.fuel_capacity or 1.0
                w_cap = u.supply.water_capacity or 1.0
                f_pct = (u.supply.fuel / f_cap) * 100.0
                w_pct = (u.supply.water / w_cap) * 100.0
                fuel_pcts.append(f_pct)
                water_pcts.append(w_pct)
                if f_pct < 25 or w_pct < 25:
                    critical += 1

            snapshot[side_val] = {
                "avg_fuel_pct": sum(fuel_pcts) / len(fuel_pcts) if fuel_pcts else 0,
                "avg_water_pct": sum(water_pcts) / len(water_pcts) if water_pcts else 0,
                "critical_units": critical,
            }
        return snapshot

    @staticmethod
    def _capture_losses(state: GameState) -> dict:
        """Capture total losses per side."""
        losses = {}
        for side_val in ["allied", "axis"]:
            total = sum(
                u.losses_taken for u in state.units.values()
                if u.side == side_val
            )
            losses[side_val] = total
        return losses
