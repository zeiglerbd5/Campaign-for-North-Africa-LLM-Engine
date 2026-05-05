"""
CNA Engine — General Agent (Commander / Synthesizer)
Sequentially consults domain experts, then synthesizes their
recommendations into executable orders.
"""
from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.engine.agent_interface import (
    execute_command, validate_command,
    ROLE_COMMANDER, ROLE_COMMANDS,
)
from cna_engine.models.game_state import GameState
from cna_engine.models.serialization import state_summary
from cna_engine.engine.turn_runner import PausePoint

from .config import OrchestratorConfig
from .experts import ExpertAgent, ExpertRecommendation, get_experts_for_phase
from .prompts import build_general_system_prompt, build_general_user_message
from .llm_backend import LLMError

logger = logging.getLogger(__name__)

# Phases where the General gets deep thinking (qwen3 budget_tokens).
# These are the "big decisions" — movement, combat, maneuver.
BIG_DECISION_PHASES = {"movement_combat"}


def _is_big_decision_phase(pause_point: PausePoint) -> bool:
    """Return True if this pause_point's sub_phase warrants deep thinking."""
    return pause_point.sub_phase in BIG_DECISION_PHASES


# Reverse map: command → role that owns it
_COMMAND_TO_ROLE: dict[str, str] = {}
for _role, _cmds in ROLE_COMMANDS.items():
    for _cmd in _cmds:
        if _cmd != "end_phase":  # end_phase is shared across roles
            _COMMAND_TO_ROLE[_cmd] = _role
# end_phase can be issued by any role, default to commander
_COMMAND_TO_ROLE["end_phase"] = ROLE_COMMANDER


@dataclass
class OrderResult:
    """Result of executing a single order."""
    command: str
    params: dict
    success: bool
    error: Optional[str] = None
    result: Optional[object] = None


@dataclass
class DecisionResult:
    """Bundled output from GeneralAgent.decide()."""
    orders: list[dict]
    expert_recommendations: list[dict] = field(default_factory=list)
    experts_ms: int = 0
    synthesis_ms: int = 0


class GeneralAgent:
    """
    The General (commander) agent. Consults domain experts, then
    synthesizes their recommendations into final executable orders.
    """

    def __init__(
        self,
        side: str,
        llm_client,
        config: OrchestratorConfig,
        experts: dict[str, ExpertAgent],
        doctrine_context: str = "",
        rag=None,
    ):
        """
        Args:
            side: "allied" or "axis".
            llm_client: OllamaClient or MockLLMClient.
            config: Orchestrator config.
            experts: Dict mapping role string to ExpertAgent instance.
            doctrine_context: Cross-game doctrine text for prompt injection.
            rag: Optional StrategyRAG instance for retrieval-augmented context.
        """
        self.side = side
        self.llm_client = llm_client
        self.config = config
        self.experts = experts
        self.doctrine_context = doctrine_context
        self.rag = rag

    def decide(
        self,
        state: GameState,
        pause_point: PausePoint,
        memory_context: str = "",
    ) -> DecisionResult:
        """
        Sequential consultation followed by synthesized orders.

        1. Determine relevant experts for this phase
        2. Query each expert sequentially
        3. Build General's context: overview + all expert recommendations
        4. Call LLM for final decision
        5. Parse orders list
        6. Validate each command
        7. Return DecisionResult with orders + expert data
        """
        decide_start = time.monotonic()
        phase_desc = pause_point.description or f"Phase: {pause_point.sub_phase}"
        logger.info(
            "General (%s) decide START: %s", self.side, phase_desc,
        )

        # Step 1: Determine relevant experts
        relevant_roles = get_experts_for_phase(pause_point)
        logger.info(
            "General (%s) consulting experts: %s", self.side, relevant_roles,
        )

        # Step 2: Query experts (parallel when multiple, sequential for one)
        active_roles = [r for r in relevant_roles if r in self.experts]
        expert_recs: list[ExpertRecommendation] = []

        if len(active_roles) <= 1:
            # Single expert — no thread overhead
            for role in active_roles:
                rec = self.experts[role].assess(
                    state, pause_point, memory_context=memory_context,
                )
                expert_recs.append(rec)
                logger.info(
                    "General (%s): received %s assessment (priority=%s, %d recs)",
                    self.side, role, rec.priority, len(rec.recommendations),
                )
        else:
            # Multiple experts — run in parallel
            rec_by_role: dict[str, ExpertRecommendation] = {}
            with ThreadPoolExecutor(max_workers=len(active_roles)) as pool:
                future_to_role = {
                    pool.submit(
                        self.experts[role].assess,
                        state, pause_point, memory_context=memory_context,
                    ): role
                    for role in active_roles
                }
                for future in as_completed(future_to_role):
                    role = future_to_role[future]
                    rec = future.result()
                    rec_by_role[role] = rec

            # Preserve original role order
            for role in active_roles:
                rec = rec_by_role[role]
                expert_recs.append(rec)
                logger.info(
                    "General (%s): received %s assessment (priority=%s, %d recs)",
                    self.side, role, rec.priority, len(rec.recommendations),
                )

        experts_ms = int((time.monotonic() - decide_start) * 1000)
        logger.info(
            "General (%s) expert consultations done: %dms for %d experts",
            self.side, experts_ms, len(expert_recs),
        )

        # Step 3: Build General's context
        summary = state_summary(state)
        overview_text = _format_summary(summary, state=state)

        rec_dicts = [r.to_dict() for r in expert_recs]

        # Query RAG for situationally relevant context
        rag_context = ""
        if self.rag and self.rag.is_available:
            try:
                from .rag import build_general_query
                # Build VP trend description for query
                vp_summary = ""
                try:
                    from cna_engine.engine.victory import assess_victory
                    vp = assess_victory(state)
                    if vp.leading_side == self.side:
                        vp_summary = f"winning by {abs(vp.margin):.0f} VP, consolidate"
                    elif vp.leading_side:
                        vp_summary = f"losing by {abs(vp.margin):.0f} VP, need aggressive attack"
                    else:
                        vp_summary = "tied, need to push for advantage"
                except Exception:
                    pass
                query = build_general_query(self.side, phase_desc, vp_summary)
                results = self.rag.query(query, side=self.side, k=5)
                rag_context = self.rag.format_context(results, max_chars=2000)
            except Exception as e:
                logger.warning("General (%s) RAG query failed: %s", self.side, e)

        user_message = build_general_user_message(
            overview_text, rec_dicts, phase_desc, memory_context,
        )
        system_prompt = build_general_system_prompt(
            self.side, phase_desc,
            doctrine_context=self.doctrine_context,
            rag_context=rag_context,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # Step 4: Call LLM for final decision
        # Enable deep thinking for "big decision" phases (movement_combat).
        # Ollama accepts think=true/false only; vLLM uses "budget_tokens:N".
        think = None
        if _is_big_decision_phase(pause_point):
            think = True
        if think:
            logger.info("General (%s) enabling thinking for %s", self.side, pause_point.sub_phase)

        synth_start = time.monotonic()
        try:
            response = self.llm_client.chat(messages, json_mode=True, think=think)
            parsed = response.parsed or {}
        except (LLMError, Exception) as e:
            elapsed_ms = int((time.monotonic() - decide_start) * 1000)
            logger.error(
                "General (%s) LLM call failed after %dms: %s",
                self.side, elapsed_ms, e,
            )
            # Fallback: just end the phase
            return DecisionResult(
                orders=[{"command": "end_phase", "params": {}}],
                expert_recommendations=rec_dicts,
                experts_ms=experts_ms,
            )

        synth_ms = int((time.monotonic() - synth_start) * 1000)
        logger.info(
            "General (%s) synthesis done: %dms", self.side, synth_ms,
        )

        if self.config.log_llm_calls:
            logger.debug("General (%s) full response: %s", self.side, parsed)

        # Step 5: Parse orders
        orders = self._parse_orders(parsed)

        # Step 6: Validate each command
        orders = self._validate_orders(state, orders)

        # Always end with end_phase if not already present
        if not orders or orders[-1].get("command") != "end_phase":
            orders.append({"command": "end_phase", "params": {}})

        total_ms = int((time.monotonic() - decide_start) * 1000)
        logger.info(
            "General (%s) decide DONE: %dms total  experts=%dms  synthesis=%dms  orders=%d",
            self.side, total_ms, experts_ms, synth_ms, len(orders),
        )

        return DecisionResult(
            orders=orders,
            expert_recommendations=rec_dicts,
            experts_ms=experts_ms,
            synthesis_ms=synth_ms,
        )

    def execute_orders(
        self,
        state: GameState,
        orders: list[dict],
    ) -> list[OrderResult]:
        """
        Execute validated orders through agent_interface.execute_command().

        Returns list of OrderResult for each command attempted.
        """
        results = []

        for order in orders:
            command = order.get("command", "")
            params = order.get("params", {})

            # Determine which role owns this command
            role = _COMMAND_TO_ROLE.get(command, ROLE_COMMANDER)

            try:
                cmd_result = execute_command(
                    state, role, self.side, command, **params,
                )
                results.append(OrderResult(
                    command=command,
                    params=params,
                    success=cmd_result.success,
                    error=cmd_result.error,
                    result=cmd_result.result,
                ))

                if not cmd_result.success:
                    logger.warning(
                        "General (%s) command '%s' failed: %s",
                        self.side, command, cmd_result.error,
                    )
            except Exception as e:
                logger.error(
                    "General (%s) command '%s' raised exception: %s",
                    self.side, command, e,
                )
                results.append(OrderResult(
                    command=command,
                    params=params,
                    success=False,
                    error=str(e),
                ))

        return results

    def _parse_orders(self, parsed: dict) -> list[dict]:
        """Parse the General's LLM response into a list of order dicts."""
        orders = []
        for order in parsed.get("orders", []):
            cmd = order.get("command", "")
            params = order.get("params", {})
            if cmd:
                orders.append({"command": cmd, "params": params})
        return orders

    def _validate_orders(
        self,
        state: GameState,
        orders: list[dict],
    ) -> list[dict]:
        """Validate each order; drop invalid ones with warnings."""
        valid = []
        seen_truck_attach: set[str] = set()  # track unit_ids already truck_attached this phase

        for order in orders:
            command = order.get("command", "")
            params = order.get("params", {})

            # Guard: params must be a dict for **unpacking
            if not isinstance(params, dict):
                logger.warning(
                    "General (%s): command '%s' has non-dict params (%s) — skipping",
                    self.side, command, type(params).__name__,
                )
                continue

            if command == "end_phase":
                valid.append(order)
                continue

            # Deduplicate truck_attach: skip if unit already has truck or was attached this phase
            if command == "truck_attach":
                uid = params.get("unit_id", "")
                if uid in seen_truck_attach:
                    logger.info(
                        "General (%s): duplicate truck_attach for '%s' — skipping",
                        self.side, uid,
                    )
                    continue
                unit = state.units.get(uid)
                if unit and unit.attached_truck_points > 0:
                    logger.info(
                        "General (%s): truck_attach for '%s' skipped — already has %d truck points",
                        self.side, uid, unit.attached_truck_points,
                    )
                    continue
                seen_truck_attach.add(uid)

            # Determine role for this command
            role = _COMMAND_TO_ROLE.get(command)
            if not role:
                logger.warning(
                    "General (%s): unknown command '%s' — skipping",
                    self.side, command,
                )
                continue

            result = validate_command(state, role, self.side, command, **params)
            if result.success:
                valid.append(order)
            else:
                logger.warning(
                    "General (%s): command '%s' validation failed: %s — skipping",
                    self.side, command, result.error,
                )

        return valid


def _format_summary(summary: dict, state: GameState = None) -> str:
    """Format state_summary dict into readable text for the General."""
    lines = [
        f"Turn: GT{summary.get('turn', '?')} | OpStage: {summary.get('op_stage', '?')}",
        f"Date: {summary.get('date', 'unknown')} | Weather: {summary.get('weather', 'unknown')}",
        f"Initiative: {summary.get('initiative', 'undecided')}",
        "",
        "Allied Forces:",
        f"  Units: {summary.get('allied', {}).get('active_units', 0)} active, "
        f"strength {summary.get('allied', {}).get('total_strength', 0)}",
        f"  Formations: {summary.get('allied', {}).get('formations', 0)}",
        f"  Aircraft: {summary.get('aircraft', {}).get('allied', 0)}",
        "",
        "Axis Forces:",
        f"  Units: {summary.get('axis', {}).get('active_units', 0)} active, "
        f"strength {summary.get('axis', {}).get('total_strength', 0)}",
        f"  Formations: {summary.get('axis', {}).get('formations', 0)}",
        f"  Aircraft: {summary.get('aircraft', {}).get('axis', 0)}",
    ]

    # Inject VP scores if state is available
    if state is not None:
        try:
            from cna_engine.engine.victory import assess_victory
            vp = assess_victory(state)
            lines.append("")
            lines.append("=== VICTORY POINTS ===")
            lines.append(
                f"  Allied: {vp.allied_vp.total_vp:.1f} VP "
                f"(objectives: {vp.allied_vp.objective_vp}, "
                f"destruction: {vp.allied_vp.destruction_vp:.1f})"
            )
            lines.append(
                f"  Axis:   {vp.axis_vp.total_vp:.1f} VP "
                f"(objectives: {vp.axis_vp.objective_vp}, "
                f"destruction: {vp.axis_vp.destruction_vp:.1f})"
            )
            margin = abs(vp.margin)
            if vp.leading_side:
                leading = "Allied" if vp.leading_side == "allied" else "Axis"
                lines.append(f"  Margin: {leading} leads by {margin:.1f} VP")
            else:
                lines.append("  Margin: TIED")
            # Show objective control
            held_allied = vp.allied_vp.objectives_held
            held_axis = vp.axis_vp.objectives_held
            if held_allied:
                lines.append(f"  Allied holds: {', '.join(held_allied)}")
            if held_axis:
                lines.append(f"  Axis holds: {', '.join(held_axis)}")
        except Exception:
            pass  # VP calculation failure shouldn't break the game

    return "\n".join(lines)
