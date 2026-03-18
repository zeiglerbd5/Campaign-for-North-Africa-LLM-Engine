"""
CNA Engine — Game Orchestrator
Top-level game loop that coordinates TurnRunner with the
multi-agent LLM system. Runs full games or individual turns/phases.
"""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, OpStagePhase, UnitStatus, AircraftStatus
from cna_engine.engine.turn_runner import TurnRunner, PausePoint, PhaseExecutionResult
from cna_engine.engine.patrol import can_patrol
from cna_engine.engine.agent_interface import (
    ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL,
)

from cna_engine.engine.scenario import (
    Reinforcement, process_reinforcements,
    AirReinforcement, process_air_reinforcements, get_air_reinforcement_schedule,
)
from cna_engine.models.serialization import save_state, load_state

from .config import OrchestratorConfig
from .llm_backend import OllamaClient, MockLLMClient, LLMError
from .experts import ExpertAgent
from .general import GeneralAgent, OrderResult, DecisionResult
from .memory import TurnMemory
from .doctrine import Doctrine
from .rag import StrategyRAG
from .situation_engine import SituationEngine

logger = logging.getLogger(__name__)


class GameLogger:
    """Writes structured JSONL records for every phase and turn boundary."""

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filepath = os.path.join(log_dir, f"game_{ts}.jsonl")
        self._fh = open(self.filepath, "a")

    def _write(self, record: dict):
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def log_phase(
        self,
        game_turn: int,
        op_stage: str,
        phase: str,
        sub_phase: str | None,
        side: str,
        skipped: bool = False,
        skip_reason: str = "",
        expert_recommendations: list[dict] | None = None,
        orders: list[dict] | None = None,
        timing: dict | None = None,
    ):
        record = {
            "type": "phase",
            "timestamp": datetime.now().isoformat(),
            "game_turn": game_turn,
            "op_stage": op_stage,
            "phase": phase,
            "sub_phase": sub_phase,
            "side": side,
            "skipped": skipped,
            "skip_reason": skip_reason,
            "expert_recommendations": expert_recommendations or [],
            "orders": orders or [],
            "timing": timing or {},
        }
        self._write(record)

    def log_turn_end(
        self,
        game_turn: int,
        elapsed_ms: int,
        interactive_phases: int,
        auto_phases: int,
        phases_skipped: int,
    ):
        record = {
            "type": "turn_end",
            "timestamp": datetime.now().isoformat(),
            "game_turn": game_turn,
            "elapsed_ms": elapsed_ms,
            "interactive_phases": interactive_phases,
            "auto_phases": auto_phases,
            "phases_skipped": phases_skipped,
        }
        self._write(record)

    def close(self):
        if self._fh and not self._fh.closed:
            self._fh.flush()
            self._fh.close()


# Map config expert names to agent_interface role constants
_EXPERT_ROLE_MAP = {
    "ground": ROLE_GROUND,
    "logistics": ROLE_LOGISTICS,
    "air": ROLE_AIR,
    "naval": ROLE_NAVAL,
}


@dataclass
class PhaseSummary:
    """Summary of a single interactive phase handled by the orchestrator."""
    side: str
    phase: str
    sub_phase: Optional[str]
    orders_issued: int = 0
    orders_succeeded: int = 0
    orders_failed: int = 0
    elapsed_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""
    results: list[OrderResult] = field(default_factory=list)


@dataclass
class TurnSummary:
    """Summary of a full game turn executed by the orchestrator."""
    game_turn: int
    phases_handled: int = 0
    auto_phases: int = 0
    interactive_phases: int = 0
    elapsed_ms: int = 0
    phase_summaries: list[PhaseSummary] = field(default_factory=list)


class GameOrchestrator:
    """
    Top-level orchestrator that drives the game loop.
    Connects TurnRunner (phase execution) with the multi-agent
    LLM system (decision-making).

    Usage:
        state = GameState(...)  # initialized scenario
        orch = GameOrchestrator(state)
        orch.setup()

        # Play one turn
        summary = orch.play_turn()

        # Or play the full campaign
        orch.play_game(max_turns=10)
    """

    def __init__(self, state: GameState, config: Optional[OrchestratorConfig] = None):
        self.state = state
        self.config = config or OrchestratorConfig()
        self.runner: Optional[TurnRunner] = None
        self.generals: dict[str, GeneralAgent] = {}
        self.situation_engines: dict[str, SituationEngine] = {}
        self.llm_client = None
        self.reinforcements: list = []
        self.air_reinforcements: list[AirReinforcement] = get_air_reinforcement_schedule()
        self.memory: TurnMemory = TurnMemory()
        self.doctrine: Optional[Doctrine] = None
        self.rag: Optional[StrategyRAG] = None
        self.save_dir: Optional[str] = None
        self.game_log: Optional[GameLogger] = None

    def setup(self, llm_client=None, reinforcements=None, memory=None, save_dir=None, log_dir="logs"):
        """
        Initialize the LLM client, expert agents, and generals for both sides.

        Args:
            llm_client: Optional pre-configured LLM client. If None,
                        creates an OllamaClient from config.
            reinforcements: Optional list of Reinforcement objects for the scenario.
            memory: Optional pre-existing TurnMemory (e.g., from a loaded save).
            save_dir: Optional directory for auto-saving after each turn.
            log_dir: Directory for JSONL game logs (default: "logs").
        """
        # Hard-switch: situation engine is mandatory. Override if disabled.
        if not self.config.use_situation_engine:
            logger.warning(
                "use_situation_engine=False is deprecated. "
                "The expert+general pipeline is no longer supported. "
                "Forcing use_situation_engine=True."
            )
            self.config.use_situation_engine = True

        if reinforcements is not None:
            self.reinforcements = reinforcements
        if memory is not None:
            self.memory = memory
        if save_dir is not None:
            self.save_dir = save_dir

        # Structured game log
        self.game_log = GameLogger(log_dir=log_dir)
        # LLM client
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.llm_client = OllamaClient(self.config)

        # Cross-game doctrine
        doctrine_path = os.path.join(log_dir, "doctrine.jsonl")
        self.doctrine = Doctrine(filepath=doctrine_path)

        # RAG pipeline — indexes rules, doctrine, and strategic playbook
        self.rag = StrategyRAG()
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        rules_paths = [
            os.path.join(base_dir, "files", "CNA_UNIFIED_RULES.md"),
            os.path.join(base_dir, "files", "CNA_AIR_RULES.md"),
        ]
        playbook_path = os.path.join(base_dir, "files", "strategic_playbook.jsonl")
        rag_ok = self.rag.build_index(
            rules_paths=[p for p in rules_paths if os.path.exists(p)],
            doctrine_path=doctrine_path if os.path.exists(doctrine_path) else None,
            playbook_path=playbook_path if os.path.exists(playbook_path) else None,
        )
        if rag_ok:
            logger.info("RAG pipeline initialized")
        else:
            logger.info("RAG pipeline unavailable — using doctrine fallback only")
            self.rag = None

        # TurnRunner
        self.runner = TurnRunner(self.state)

        # Create expert agents and generals for both sides
        for side in [Side.ALLIED, Side.AXIS]:
            side_str = side.value
            doctrine_context = self.doctrine.get_doctrine_text(side_str)
            experts = {}

            for expert_name in self.config.experts:
                role = _EXPERT_ROLE_MAP.get(expert_name)
                if role:
                    experts[role] = ExpertAgent(
                        role=role,
                        side=side_str,
                        llm_client=self.llm_client,
                        config=self.config,
                        doctrine_context=doctrine_context,
                        rag=self.rag,
                    )

            self.generals[side_str] = GeneralAgent(
                side=side_str,
                llm_client=self.llm_client,
                config=self.config,
                experts=experts,
                doctrine_context=doctrine_context,
                rag=self.rag,
            )

        # Create SituationEngine instances when enabled
        if self.config.use_situation_engine:
            for side in [Side.ALLIED, Side.AXIS]:
                side_str = side.value
                doctrine_context = self.doctrine.get_doctrine_text(side_str)
                self.situation_engines[side_str] = SituationEngine(
                    side=side_str,
                    llm_client=self.llm_client,
                    config=self.config,
                    doctrine_context=doctrine_context,
                    rag=self.rag,
                )
            logger.info("Situation engines created for both sides")

        logger.info(
            "Orchestrator setup complete: model=%s, experts=%s, doctrine=%s, rag=%s, situation_engine=%s",
            self.config.model, self.config.experts, doctrine_path,
            "enabled" if self.rag else "disabled",
            "enabled" if self.config.use_situation_engine else "disabled",
        )

    def play_turn(self) -> TurnSummary:
        """
        Execute one full game turn with LLM decisions.

        Loops: run_until_pause → general.decide → execute → advance
        until the turn number changes or game ends.
        """
        if not self.runner:
            raise RuntimeError("Call setup() before play_turn()")

        turn_start = time.monotonic()
        start_gt = self.state.turn.game_turn
        summary = TurnSummary(game_turn=start_gt)

        logger.info("=" * 60)
        logger.info("TURN GT%d START", start_gt)
        logger.info("=" * 60)

        # Process reinforcements at turn start
        if self.reinforcements:
            arrived = process_reinforcements(self.state, self.reinforcements)
            if arrived:
                logger.info("GT%d reinforcements: %s", start_gt, arrived)
        if self.air_reinforcements:
            air_arrived = process_air_reinforcements(self.state, self.air_reinforcements)
            if air_arrived:
                logger.info("GT%d air reinforcements: %s", start_gt, air_arrived)

        max_phases = 200  # safety limit
        for _ in range(max_phases):
            if self.runner.is_game_over():
                break
            if self.state.turn.game_turn != start_gt:
                break

            # Run auto-phases until we hit an interactive pause
            results = self.runner.run_until_pause()

            for result in results:
                if isinstance(result, PhaseExecutionResult) and result.auto_executed:
                    summary.auto_phases += 1

                elif isinstance(result, PausePoint):
                    # Handle the interactive phase
                    phase_start = time.monotonic()
                    phase_summary = self.play_phase(result)
                    phase_ms = int((time.monotonic() - phase_start) * 1000)
                    phase_summary.elapsed_ms = phase_ms

                    summary.interactive_phases += 1
                    summary.phase_summaries.append(phase_summary)

                    logger.info(
                        "Phase %s/%s (%s): %dms  orders=%d ok=%d fail=%d",
                        phase_summary.phase,
                        phase_summary.sub_phase or "-",
                        phase_summary.side,
                        phase_ms,
                        phase_summary.orders_issued,
                        phase_summary.orders_succeeded,
                        phase_summary.orders_failed,
                    )

                    # Run breakdown check after movement_combat
                    if result.sub_phase == OpStagePhase.MOVEMENT_COMBAT:
                        from cna_engine.engine.breakdown import execute_breakdown_segment
                        side_str = result.active_side
                        if isinstance(side_str, Side):
                            side_str = side_str.value
                        if side_str:
                            bd_result = execute_breakdown_segment(self.state, side_str)
                            if bd_result.units_broke_down > 0:
                                logger.info("Breakdown: %s", bd_result.description)

                    # Advance past the interactive phase
                    self.runner.advance()

            summary.phases_handled = summary.auto_phases + summary.interactive_phases

            # If run_until_pause returned nothing, we might be stuck
            if not results:
                break

        turn_ms = int((time.monotonic() - turn_start) * 1000)
        summary.elapsed_ms = turn_ms

        logger.info("-" * 60)
        logger.info(
            "TURN GT%d DONE: %dms (%.1fs)  interactive=%d  auto=%d",
            start_gt, turn_ms, turn_ms / 1000,
            summary.interactive_phases, summary.auto_phases,
        )
        if summary.phase_summaries:
            logger.info("Phase timing breakdown:")
            for ps in summary.phase_summaries:
                logger.info(
                    "  %s/%s (%s): %dms  orders=%d",
                    ps.phase, ps.sub_phase or "-", ps.side,
                    ps.elapsed_ms, ps.orders_issued,
                )
        logger.info("-" * 60)

        # Structured game log: turn boundary
        if self.game_log:
            phases_skipped = sum(
                1 for ps in summary.phase_summaries if ps.skipped
            )
            self.game_log.log_turn_end(
                game_turn=start_gt,
                elapsed_ms=turn_ms,
                interactive_phases=summary.interactive_phases,
                auto_phases=summary.auto_phases,
                phases_skipped=phases_skipped,
            )

        # Record turn in memory
        self.memory.record_turn(self.state, summary)

        # Auto-save if save_dir configured
        if self.save_dir:
            self._auto_save()

        return summary

    def _is_trivial_phase(self, pause_point: PausePoint) -> tuple[bool, str]:
        """
        Check if a phase has nothing actionable and can be skipped
        without calling the LLM.

        Returns:
            (is_trivial, reason) — reason explains why it was skipped.
        """
        sub = pause_point.sub_phase
        side_str = (
            pause_point.active_side.value
            if isinstance(pause_point.active_side, Side)
            else str(pause_point.active_side or "")
        )

        if sub == OpStagePhase.RESERVE or sub == "reserve":
            in_reserve = [
                u for u in self.state.units.values()
                if u.side == side_str and u.status == UnitStatus.IN_RESERVE
            ]
            if not in_reserve:
                return True, "no units in reserve"

        elif sub == OpStagePhase.FLEET or sub == "fleet":
            if side_str == Side.AXIS.value:
                return True, "axis has no fleet actions"
            fleet = self.state.cw_fleet
            if not fleet.is_available or fleet.sorties_remaining <= 0:
                return True, "fleet unavailable or 0 sorties"

        elif sub == OpStagePhase.AIR or sub == "air":
            # AIR is a shared phase (active_side=None), check both sides
            if side_str:
                ready_aircraft = [
                    a for a in self.state.aircraft.values()
                    if a.side == side_str and a.status == AircraftStatus.READY
                ]
            else:
                ready_aircraft = [
                    a for a in self.state.aircraft.values()
                    if a.status == AircraftStatus.READY
                ]
            if not ready_aircraft:
                return True, "no ready aircraft"

        elif sub == OpStagePhase.PATROL or sub == "patrol":
            has_patrol_eligible = any(
                can_patrol(self.state, uid)[0]
                for uid, u in self.state.units.items()
                if u.side == side_str
            )
            if not has_patrol_eligible:
                return True, "no patrol-eligible units"

        # movement_combat: never skip
        return False, ""

    def play_phase(self, pause_point: PausePoint) -> PhaseSummary:
        """
        Handle a single interactive phase.

        Determines the active side, calls the appropriate General's decide(),
        then executes the resulting orders.
        """
        # Check if this phase is trivial and can be skipped
        trivial, reason = self._is_trivial_phase(pause_point)
        if trivial:
            side_str = (
                pause_point.active_side.value
                if isinstance(pause_point.active_side, Side)
                else str(pause_point.active_side or Side.ALLIED.value)
            )
            logger.info(
                "Phase %s (%s) skipped: %s",
                pause_point.sub_phase, side_str, reason,
            )
            phase_str = str(pause_point.phase) if pause_point.phase else ""
            sub_str = str(pause_point.sub_phase) if pause_point.sub_phase else None
            if self.game_log:
                self.game_log.log_phase(
                    game_turn=self.state.turn.game_turn,
                    op_stage=str(self.state.turn.op_stage),
                    phase=phase_str,
                    sub_phase=sub_str,
                    side=side_str,
                    skipped=True,
                    skip_reason=reason,
                )
            return PhaseSummary(
                side=side_str,
                phase=phase_str,
                sub_phase=sub_str,
                orders_issued=0,
                skipped=True,
                skip_reason=reason,
            )

        # Shared AIR phase: run for both sides sequentially
        sub = pause_point.sub_phase
        if (sub == OpStagePhase.AIR or sub == "air") and not pause_point.active_side:
            return self._play_shared_air_phase(pause_point)

        active_side = pause_point.active_side
        if isinstance(active_side, Side):
            side_str = active_side.value
        else:
            side_str = str(active_side) if active_side else Side.ALLIED.value

        general = self.generals.get(side_str)
        if not general:
            logger.warning(
                "No general for side '%s' — skipping phase", side_str,
            )
            return PhaseSummary(
                side=side_str,
                phase=pause_point.phase or "",
                sub_phase=pause_point.sub_phase,
            )

        # Build memory context for this side
        memory_context = self.memory.get_context_text(side_str)

        # Get orders from SituationEngine or General
        if self.config.use_situation_engine and side_str in self.situation_engines:
            decision = self.situation_engines[side_str].decide(
                self.state, pause_point, memory_context=memory_context,
            )
        else:
            decision = general.decide(self.state, pause_point, memory_context=memory_context)

        # Execute the orders
        results = general.execute_orders(self.state, decision.orders)

        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)

        logger.info(
            "Phase %s (%s): %d orders, %d succeeded, %d failed",
            pause_point.sub_phase, side_str, len(results), succeeded, failed,
        )

        phase_str = str(pause_point.phase) if pause_point.phase else ""
        sub_str = str(pause_point.sub_phase) if pause_point.sub_phase else None

        # Build order outcome dicts for structured log
        order_outcomes = [
            {"command": r.command, "params": r.params,
             "success": r.success, "error": r.error}
            for r in results
        ]

        if self.game_log:
            self.game_log.log_phase(
                game_turn=self.state.turn.game_turn,
                op_stage=str(self.state.turn.op_stage),
                phase=phase_str,
                sub_phase=sub_str,
                side=side_str,
                expert_recommendations=decision.expert_recommendations,
                orders=order_outcomes,
                timing={
                    "experts_ms": decision.experts_ms,
                    "synthesis_ms": decision.synthesis_ms,
                },
            )

        phase_summary = PhaseSummary(
            side=side_str,
            phase=phase_str,
            sub_phase=sub_str,
            orders_issued=len(results),
            orders_succeeded=succeeded,
            orders_failed=failed,
            results=results,
        )

        # Record phase in memory
        self.memory.record_phase(self.state, phase_summary)

        return phase_summary

    def _play_shared_air_phase(self, pause_point: PausePoint) -> PhaseSummary:
        """Handle the shared AIR phase by running both sides sequentially."""
        from cna_engine.engine.air import execute_air_phase

        # Execute the air phase engine logic (reset aircraft, rearm)
        execute_air_phase(self.state)

        total_orders = 0
        total_succeeded = 0
        total_failed = 0
        all_results = []

        for side_enum in [Side.ALLIED, Side.AXIS]:
            side_str = side_enum.value

            # Skip if no ready aircraft for this side
            ready = [a for a in self.state.aircraft.values()
                     if a.side == side_str and a.status == AircraftStatus.READY]
            if not ready:
                continue

            general = self.generals.get(side_str)
            if not general:
                continue

            # Create a side-specific pause point for the general
            side_pp = PausePoint(
                game_turn=pause_point.game_turn,
                op_stage=pause_point.op_stage,
                phase=pause_point.phase,
                sub_phase=pause_point.sub_phase,
                active_side=side_str,
                awaiting=f"{side_str}_input",
                description=f"AIR phase for {side_str}",
            )

            memory_context = self.memory.get_context_text(side_str)

            if self.config.use_situation_engine and side_str in self.situation_engines:
                decision = self.situation_engines[side_str].decide(
                    self.state, side_pp, memory_context=memory_context,
                )
            else:
                decision = general.decide(self.state, side_pp, memory_context=memory_context)

            results = general.execute_orders(self.state, decision.orders)
            succeeded = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)

            total_orders += len(results)
            total_succeeded += succeeded
            total_failed += failed
            all_results.extend(results)

            logger.info(
                "AIR phase (%s): %d orders, %d succeeded, %d failed",
                side_str, len(results), succeeded, failed,
            )

            phase_str = str(pause_point.phase) if pause_point.phase else ""
            sub_str = str(pause_point.sub_phase) if pause_point.sub_phase else None

            if self.game_log:
                self.game_log.log_phase(
                    game_turn=self.state.turn.game_turn,
                    op_stage=str(self.state.turn.op_stage),
                    phase=phase_str,
                    sub_phase=sub_str,
                    side=side_str,
                    expert_recommendations=decision.expert_recommendations,
                    orders=[
                        {"command": r.command, "params": r.params,
                         "success": r.success, "error": r.error}
                        for r in results
                    ],
                    timing={
                        "experts_ms": decision.experts_ms,
                        "synthesis_ms": decision.synthesis_ms,
                    },
                )

        phase_str = str(pause_point.phase) if pause_point.phase else ""
        sub_str = str(pause_point.sub_phase) if pause_point.sub_phase else None

        phase_summary = PhaseSummary(
            side="both",
            phase=phase_str,
            sub_phase=sub_str,
            orders_issued=total_orders,
            orders_succeeded=total_succeeded,
            orders_failed=total_failed,
            results=all_results,
        )

        self.memory.record_phase(self.state, phase_summary)
        return phase_summary

    def play_game(self, max_turns: Optional[int] = None) -> list[TurnSummary]:
        """
        Run the full campaign (or up to max_turns).

        Returns list of TurnSummary for each turn played.
        """
        if not self.runner:
            raise RuntimeError("Call setup() before play_game()")

        turn_summaries = []
        turns_played = 0

        while not self.runner.is_game_over():
            if max_turns is not None and turns_played >= max_turns:
                break

            summary = self.play_turn()
            turn_summaries.append(summary)
            turns_played += 1

            logger.info(
                "Turn GT%d complete: %d interactive phases, %d auto phases",
                summary.game_turn, summary.interactive_phases, summary.auto_phases,
            )

        return turn_summaries

    def _auto_save(self):
        """Save current state to save_dir after each turn."""
        if not self.save_dir:
            return
        import os
        os.makedirs(self.save_dir, exist_ok=True)
        gt = self.state.turn.game_turn
        filepath = os.path.join(self.save_dir, f"gt{gt}.json")
        save_state(self.state, filepath)
        logger.info("Auto-saved to %s", filepath)

    @classmethod
    def load_from_save(
        cls,
        filepath: str,
        config: Optional[OrchestratorConfig] = None,
        memory_data: Optional[dict] = None,
    ) -> "GameOrchestrator":
        """
        Create an orchestrator from a saved game state file.

        Args:
            filepath: Path to saved JSON game state.
            config: Optional config override.
            memory_data: Optional serialized TurnMemory dict.

        Returns:
            GameOrchestrator with loaded state (call setup() before play).
        """
        state = load_state(filepath)
        orch = cls(state, config)
        if memory_data:
            orch.memory = TurnMemory.from_dict(memory_data)
        return orch
