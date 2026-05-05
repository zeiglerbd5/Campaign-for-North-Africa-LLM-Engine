"""
CNA Engine — Turn Runner / Game Orchestrator
Executes the full Sequence of Play, calling each phase's engine
module in order. This is the main game loop.

The turn runner advances through phases automatically for
non-interactive phases (weather, organization, stores expenditure)
and pauses at interactive phases (movement, combat, air, naval)
for agent input.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import (
    Side, GamePhase, OpStagePhase, UnitClass, UnitStatus,
)
from cna_engine.engine.sequence_of_play import (
    SequenceOfPlay, resolve_initiative, get_initiative_ratings,
)
from cna_engine.engine.combat import roll_d6


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class PhaseExecutionResult:
    """Result of executing a single phase."""
    phase: str
    sub_phase: Optional[str]
    active_side: Optional[str]
    auto_executed: bool
    result: Optional[object] = None
    description: str = ""


@dataclass
class TurnSummary:
    """Summary of a complete game turn."""
    game_turn: int
    phases_executed: int = 0
    events: list[str] = field(default_factory=list)
    victory_snapshot: Optional[object] = None
    description: str = ""


@dataclass
class PausePoint:
    """Indicates the turn runner has paused for agent input."""
    game_turn: int
    op_stage: int
    phase: str
    sub_phase: Optional[str]
    active_side: Optional[str]
    awaiting: str   # "allied_input", "axis_input", "both_input"
    description: str = ""


# ════════════════════════════════════════
# TURN RUNNER
# ════════════════════════════════════════

class TurnRunner:
    """
    Orchestrates the game by advancing through the Sequence of Play
    and calling appropriate engine modules at each phase.

    Usage:
        runner = TurnRunner(state)
        while not runner.is_game_over():
            result = runner.execute_next_phase()
            if isinstance(result, PausePoint):
                # Get agent input, apply commands
                pass
            runner.advance()  # Move to next phase
    """

    def __init__(self, state: GameState):
        self.state = state
        self.sop = SequenceOfPlay(state)

    def is_game_over(self) -> bool:
        return self.sop.is_game_over()

    def get_current_description(self) -> str:
        return self.sop.get_current_phase_description()

    def advance(self) -> dict:
        """Advance the SoP to the next phase."""
        return self.sop.advance()

    # ════════════════════════════════════════
    # PHASE EXECUTION
    # ════════════════════════════════════════

    def execute_current_phase(
        self,
        dice_overrides: Optional[dict] = None,
    ) -> PhaseExecutionResult | PausePoint:
        """
        Execute the current phase. Auto-phases run automatically.
        Interactive phases return a PausePoint.
        """
        t = self.state.turn
        overrides = dice_overrides or {}

        # Top-level phases
        if t.phase == GamePhase.STORES_EXPENDITURE:
            return self._execute_stores_expenditure()

        elif t.phase == GamePhase.STRATEGIC_STAGE:
            return self._execute_strategic_stage()

        elif t.phase == GamePhase.END_OF_TURN:
            return self._execute_end_of_turn()

        # OpStage sub-phases
        elif t.phase == GamePhase.OP_STAGE:
            return self._execute_opstage_phase(overrides)

        return PhaseExecutionResult(
            phase=t.phase, sub_phase=t.sub_phase,
            active_side=t.active_side, auto_executed=False,
            description="Unknown phase",
        )

    # ── Top-level phases ──

    def _execute_stores_expenditure(self) -> PhaseExecutionResult:
        """Execute the Stores Expenditure phase at start of GT."""
        from cna_engine.engine.supply import execute_stores_expenditure
        from cna_engine.data.reference_data import ReferenceData

        ref = ReferenceData()
        result = execute_stores_expenditure(self.state, ref)

        return PhaseExecutionResult(
            phase=GamePhase.STORES_EXPENDITURE, sub_phase=None,
            active_side=None, auto_executed=True,
            result=result,
            description=result.description,
        )

    def _execute_strategic_stage(self) -> PhaseExecutionResult:
        """Execute the Strategic Stage (replacements, strategic moves)."""
        from cna_engine.engine.replacements import execute_replacement_phase

        # Process replacements for both sides
        allied_r = execute_replacement_phase(self.state, Side.ALLIED)
        axis_r = execute_replacement_phase(self.state, Side.AXIS)

        desc = (f"Strategic Stage: {allied_r.description} | {axis_r.description}")

        return PhaseExecutionResult(
            phase=GamePhase.STRATEGIC_STAGE, sub_phase=None,
            active_side=None, auto_executed=True,
            result={"allied": allied_r, "axis": axis_r},
            description=desc,
        )

    def _execute_end_of_turn(self) -> PhaseExecutionResult:
        """Execute End of Turn processing."""
        from cna_engine.engine.victory import assess_victory
        from cna_engine.engine.naval import reset_fleet_for_turn, reset_convoy_for_turn

        # Victory assessment
        victory = assess_victory(self.state)

        # Reset fleet and convoy for next turn
        reset_fleet_for_turn(self.state)
        reset_convoy_for_turn(self.state)

        desc = f"End of GT{self.state.turn.game_turn}: {victory.description}"

        return PhaseExecutionResult(
            phase=GamePhase.END_OF_TURN, sub_phase=None,
            active_side=None, auto_executed=True,
            result=victory,
            description=desc,
        )

    # ── OpStage sub-phases ──

    def _execute_opstage_phase(
        self,
        overrides: dict,
    ) -> PhaseExecutionResult | PausePoint:
        """Route to the appropriate OpStage sub-phase handler."""
        sub = self.state.turn.sub_phase

        if sub == OpStagePhase.WEATHER:
            return self._execute_weather(overrides)
        elif sub == OpStagePhase.ORGANIZATION:
            return self._execute_organization()
        elif sub == OpStagePhase.CONVOY_ARRIVAL:
            return self._execute_convoy_arrival(overrides)
        elif sub == OpStagePhase.FLEET:
            return self._pause_for_input("naval")
        elif sub == OpStagePhase.AIR:
            return self._pause_for_input("air")
        elif sub == OpStagePhase.INITIATIVE:
            return self._execute_initiative(overrides)
        elif sub == OpStagePhase.RESERVE:
            return self._pause_for_input("reserve")
        elif sub == OpStagePhase.MOVEMENT_COMBAT:
            return self._pause_for_input("movement_combat")
        elif sub == OpStagePhase.VEHICLE_REPAIR:
            return self._execute_vehicle_repair()
        elif sub == OpStagePhase.CONVOY_MOVEMENT:
            return self._execute_convoy_movement()
        elif sub == OpStagePhase.PATROL:
            return self._pause_for_input("patrol")

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE, sub_phase=sub,
            active_side=self.state.turn.active_side,
            auto_executed=False,
            description=f"Unknown sub-phase: {sub}",
        )

    def _execute_weather(self, overrides: dict) -> PhaseExecutionResult:
        """Determine weather for this OpStage."""
        from cna_engine.engine.weather import determine_weather

        roll = overrides.get("weather_roll")
        result = determine_weather(self.state, dice_roll=roll)

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE, sub_phase=OpStagePhase.WEATHER,
            active_side=None, auto_executed=True,
            result=result,
            description=result.description,
        )

    def _execute_organization(self) -> PhaseExecutionResult:
        """Execute the Organization phase."""
        from cna_engine.engine.organization import execute_organization_phase

        result = execute_organization_phase(self.state)

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE, sub_phase=OpStagePhase.ORGANIZATION,
            active_side=None, auto_executed=True,
            result=result,
            description=result.description,
        )

    def _execute_convoy_arrival(self, overrides: dict) -> PhaseExecutionResult:
        """Execute the Convoy Arrival phase."""
        from cna_engine.engine.naval import execute_convoy_phase

        int_rolls = overrides.get("interception_rolls")
        loss_rolls = overrides.get("loss_rolls")
        result = execute_convoy_phase(self.state,
                                      interception_rolls=int_rolls,
                                      loss_rolls=loss_rolls)

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE, sub_phase=OpStagePhase.CONVOY_ARRIVAL,
            active_side=None, auto_executed=True,
            result=result,
            description=result.description,
        )

    def _execute_initiative(self, overrides: dict) -> PhaseExecutionResult:
        """Resolve initiative for this OpStage."""
        a_roll = overrides.get("allied_initiative_roll", roll_d6())
        x_roll = overrides.get("axis_initiative_roll", roll_d6())

        winner = resolve_initiative(self.state, a_roll, x_roll)

        # Handle tie (re-roll once with random)
        if winner is None:
            a_roll = roll_d6()
            x_roll = roll_d6()
            winner = resolve_initiative(self.state, a_roll, x_roll)

        # Default to Axis if still tied
        if winner is None:
            winner = Side.AXIS

        self.state.turn.initiative_side = winner

        a_rating, x_rating = get_initiative_ratings(self.state.turn.game_turn)
        desc = (f"Initiative: Allied roll={a_roll} (need ≤{a_rating}), "
                f"Axis roll={x_roll} (need ≤{x_rating}) → {winner.upper()} wins")

        self.state.log_event("initiative", desc, winner=winner,
                             allied_roll=a_roll, axis_roll=x_roll)

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE, sub_phase=OpStagePhase.INITIATIVE,
            active_side=None, auto_executed=True,
            result={"winner": winner, "allied_roll": a_roll, "axis_roll": x_roll},
            description=desc,
        )

    def _execute_vehicle_repair(self) -> PhaseExecutionResult:
        """Execute Vehicle Repair phase for active side."""
        from cna_engine.engine.repair import execute_vehicle_repair_phase

        side = self.state.turn.active_side
        if not side:
            return PhaseExecutionResult(
                phase=GamePhase.OP_STAGE,
                sub_phase=OpStagePhase.VEHICLE_REPAIR,
                active_side=None, auto_executed=True,
                description="No active side for repair",
            )

        result = execute_vehicle_repair_phase(self.state, side)

        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE,
            sub_phase=OpStagePhase.VEHICLE_REPAIR,
            active_side=side, auto_executed=True,
            result=result,
            description=result.description,
        )

    def _execute_convoy_movement(self) -> PhaseExecutionResult | PausePoint:
        """
        Convoy movement phase. If eligible logistics units exist
        (TRUCK, SGSU, REPLACEMENT, HQ with ACTIVE status and CPA remaining),
        pause for agent input. Otherwise auto-execute.
        """
        side = self.state.turn.active_side
        eligible_classes = {UnitClass.TRUCK, UnitClass.SGSU, UnitClass.REPLACEMENT, UnitClass.HQ}

        has_eligible = False
        for unit in self.state.units.values():
            if unit.side != side:
                continue
            if unit.status != UnitStatus.ACTIVE:
                continue
            if unit.unit_class not in eligible_classes:
                continue
            if not unit.hex_id:
                continue
            remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
            if remaining > 0:
                has_eligible = True
                break

        if has_eligible:
            return self._pause_for_input("convoy_movement")

        desc = f"Convoy Movement ({side}): no eligible logistics units"
        return PhaseExecutionResult(
            phase=GamePhase.OP_STAGE,
            sub_phase=OpStagePhase.CONVOY_MOVEMENT,
            active_side=side, auto_executed=True,
            description=desc,
        )

    # ── Interactive phases ──

    def _pause_for_input(self, phase_type: str) -> PausePoint:
        """Return a PausePoint indicating agent input is needed."""
        t = self.state.turn
        side = t.active_side

        if side:
            awaiting = f"{side}_input"
        else:
            awaiting = "both_input"

        desc = f"Awaiting {phase_type} input"
        if side:
            desc += f" from {side}"

        return PausePoint(
            game_turn=t.game_turn,
            op_stage=t.op_stage,
            phase=t.phase,
            sub_phase=t.sub_phase,
            active_side=side,
            awaiting=awaiting,
            description=desc,
        )

    # ════════════════════════════════════════
    # AUTO-RUN (non-interactive phases only)
    # ════════════════════════════════════════

    def auto_run_phase(
        self,
        dice_overrides: Optional[dict] = None,
    ) -> PhaseExecutionResult | PausePoint:
        """
        Execute current phase and auto-advance if it was automated.
        Returns the result. If a PausePoint, the caller must handle
        interactive input before calling advance().
        """
        result = self.execute_current_phase(dice_overrides)

        if isinstance(result, PhaseExecutionResult) and result.auto_executed:
            self.advance()

        return result

    def run_until_pause(
        self,
        max_steps: int = 100,
        dice_overrides: Optional[dict] = None,
    ) -> list[PhaseExecutionResult | PausePoint]:
        """
        Run auto-phases until an interactive phase is reached or max_steps hit.
        Returns list of all phase results.
        """
        results = []
        for _ in range(max_steps):
            if self.is_game_over():
                break

            result = self.execute_current_phase(dice_overrides)
            results.append(result)

            if isinstance(result, PausePoint):
                break

            if isinstance(result, PhaseExecutionResult) and result.auto_executed:
                self.advance()
            else:
                break

        return results

    def skip_interactive_phase(self):
        """Skip the current interactive phase (agent chose to pass)."""
        self.advance()

    # ════════════════════════════════════════
    # FULL TURN EXECUTION (for testing)
    # ════════════════════════════════════════

    def execute_full_turn(
        self,
        dice_overrides: Optional[dict] = None,
        max_phases: int = 200,
    ) -> TurnSummary:
        """
        Execute a full game turn, auto-skipping interactive phases.
        Useful for testing and simulation. NOT for agent play.
        """
        start_gt = self.state.turn.game_turn
        summary = TurnSummary(game_turn=start_gt)

        for _ in range(max_phases):
            if self.is_game_over():
                break
            if self.state.turn.game_turn != start_gt:
                # We've advanced past this GT
                break

            result = self.execute_current_phase(dice_overrides)
            summary.phases_executed += 1

            if isinstance(result, PausePoint):
                summary.events.append(f"SKIP: {result.description}")
                self.advance()
            elif isinstance(result, PhaseExecutionResult):
                summary.events.append(result.description)
                self.advance()

        # End of turn assessment
        if self.state.turn.phase == GamePhase.END_OF_TURN:
            result = self.execute_current_phase(dice_overrides)
            if isinstance(result, PhaseExecutionResult):
                summary.victory_snapshot = result.result
                summary.events.append(result.description)
                summary.phases_executed += 1
            self.advance()

        summary.description = (f"GT{start_gt}: {summary.phases_executed} phases executed")

        return summary
