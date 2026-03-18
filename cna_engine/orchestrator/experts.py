"""
CNA Engine — Expert Agent
Domain expert agents that assess the game state and produce
structured recommendations for the General.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.engine.agent_interface import (
    get_state_view, generate_agent_prompt, validate_command,
    ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL,
    ROLE_COMMANDS,
)
from cna_engine.models.game_state import GameState
from cna_engine.engine.turn_runner import PausePoint

from .config import OrchestratorConfig
from .prompts import (
    build_expert_system_prompt,
    build_expert_user_message,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# DATACLASSES
# ════════════════════════════════════════

@dataclass
class Recommendation:
    """A single recommended action from an expert."""
    action: str
    params: dict = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class ExpertRecommendation:
    """Full recommendation from a domain expert."""
    role: str
    assessment: str = ""
    priority: str = "medium"  # high | medium | low
    recommendations: list[Recommendation] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for passing to the General's prompt."""
        return {
            "role": self.role,
            "assessment": self.assessment,
            "priority": self.priority,
            "recommendations": [
                {"action": r.action, "params": r.params, "reasoning": r.reasoning}
                for r in self.recommendations
            ],
            "concerns": self.concerns,
        }


# ════════════════════════════════════════
# PHASE → EXPERT MAPPING
# ════════════════════════════════════════

# Which experts are consulted for each interactive phase type.
# The phase_type strings match what TurnRunner._pause_for_input() uses.
PHASE_EXPERTS = {
    "fleet":            [ROLE_NAVAL],
    "air":              [ROLE_AIR],
    "reserve":          [ROLE_GROUND],
    "movement_combat":  [ROLE_GROUND, ROLE_LOGISTICS],
    "patrol":           [ROLE_GROUND],
}

# Map expert role string to agent_interface role constant
ROLE_MAP = {
    "ground":    ROLE_GROUND,
    "logistics": ROLE_LOGISTICS,
    "air":       ROLE_AIR,
    "naval":     ROLE_NAVAL,
}


def get_experts_for_phase(pause_point: PausePoint) -> list[str]:
    """Return the list of expert roles to consult for a given pause point."""
    # Extract phase type from sub_phase or description
    phase_type = _extract_phase_type(pause_point)
    return PHASE_EXPERTS.get(phase_type, [ROLE_GROUND])


def _extract_phase_type(pause_point: PausePoint) -> str:
    """Extract the phase type string from a PausePoint."""
    # The sub_phase field holds the OpStagePhase value
    sub = pause_point.sub_phase
    if sub:
        # OpStagePhase values: "fleet", "air", "reserve", "movement_combat", "patrol"
        sub_str = sub if isinstance(sub, str) else sub.value
        if sub_str in PHASE_EXPERTS:
            return sub_str
    # Fallback: parse from description
    desc = pause_point.description.lower()
    for phase_type in PHASE_EXPERTS:
        if phase_type.replace("_", " ") in desc or phase_type in desc:
            return phase_type
    return "movement_combat"


# ════════════════════════════════════════
# EXPERT AGENT
# ════════════════════════════════════════

def _extract_state_signals(state: GameState, side: str, role: str) -> dict:
    """
    Extract high-level state signals for RAG query construction.
    Scans game state for key situational indicators.
    """
    from cna_engine.models.enums import UnitStatus, Side

    signals = {}

    units = [u for u in state.units.values()
             if u.side == side and u.status not in
             (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN, "not_yet_arrived")]

    # Check for units in contact
    signals["units_in_contact"] = any(
        getattr(u, "is_in_contact", False) for u in units
    )

    # Check supply criticality
    fuel_critical = any(
        u.supply.fuel_capacity > 0 and u.supply.fuel / u.supply.fuel_capacity < 0.25
        for u in units if hasattr(u, "supply")
    )
    water_critical = any(
        u.supply.water_capacity > 0 and u.supply.water / u.supply.water_capacity < 0.25
        for u in units if hasattr(u, "supply")
    )
    signals["supply_critical"] = fuel_critical or water_critical
    signals["low_fuel"] = fuel_critical
    signals["low_water"] = water_critical

    # Determine if advancing or retreating (Allied = attacking, Axis = defending)
    if side == Side.ALLIED or side == "allied":
        signals["advancing"] = True
    else:
        signals["retreating"] = False

    return signals


class ExpertAgent:
    """
    A domain expert agent that queries the LLM for a role-specific
    assessment and returns structured recommendations.
    """

    def __init__(self, role: str, side: str, llm_client, config: OrchestratorConfig,
                 doctrine_context: str = "", rag=None):
        """
        Args:
            role: Agent interface role (e.g., ROLE_GROUND).
            side: Side this expert advises for ("allied" or "axis").
            llm_client: OllamaClient or MockLLMClient.
            config: Orchestrator config.
            doctrine_context: Cross-game doctrine text for prompt injection.
            rag: Optional StrategyRAG instance for retrieval-augmented context.
        """
        self.role = role
        self.side = side
        self.llm_client = llm_client
        self.config = config
        self.doctrine_context = doctrine_context
        self.rag = rag

        # Get available commands for this role
        self.available_commands = ROLE_COMMANDS.get(role, [])

    def assess(
        self,
        state: GameState,
        pause_point: PausePoint,
        memory_context: str = "",
    ) -> ExpertRecommendation:
        """
        Query this expert for their domain assessment.

        1. Get role-filtered state view via generate_agent_prompt
        2. Build messages with system prompt + state context
        3. Call LLM with JSON mode
        4. Parse into ExpertRecommendation
        5. Validate recommended commands are legal
        """
        assess_start = time.monotonic()

        # Build state context using the existing agent prompt generator
        state_text = generate_agent_prompt(state, self.role, self.side)
        phase_desc = pause_point.description or f"Phase: {pause_point.sub_phase}"

        # Determine phase type for phase-specific guidance
        phase_type = _extract_phase_type(pause_point)

        # Query RAG for situationally relevant context
        rag_context = ""
        if self.rag and self.rag.is_available:
            try:
                from .rag import build_expert_query
                state_signals = _extract_state_signals(state, self.side, self.role)
                query = build_expert_query(self.role, phase_type, state_signals)
                results = self.rag.query(query, side=self.side, k=3)
                rag_context = self.rag.format_context(results, max_chars=1500)
            except Exception as e:
                logger.warning("Expert %s RAG query failed: %s", self.role, e)

        # Build LLM messages
        system_prompt = build_expert_system_prompt(
            self.role, self.side, self.available_commands,
            doctrine_context=self.doctrine_context,
            phase=phase_type,
            rag_context=rag_context,
        )
        user_message = build_expert_user_message(state_text, phase_desc, memory_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        logger.info(
            "Expert %s (%s) starting assessment for phase: %s",
            self.role, self.side, phase_desc,
        )

        # Call LLM
        try:
            response = self.llm_client.chat(messages, json_mode=True)
            parsed = response.parsed or {}
        except Exception as e:
            elapsed_ms = int((time.monotonic() - assess_start) * 1000)
            logger.error(
                "Expert %s (%s) LLM call failed after %dms: %s",
                self.role, self.side, elapsed_ms, e,
            )
            return ExpertRecommendation(
                role=self.role,
                assessment=f"Assessment unavailable: {e}",
                priority="low",
            )

        elapsed_ms = int((time.monotonic() - assess_start) * 1000)

        # Log timing and response
        logger.info(
            "Expert %s (%s) assess complete: %dms  priority=%s  recs=%d  concerns=%d",
            self.role, self.side, elapsed_ms,
            parsed.get("priority", "?"),
            len(parsed.get("recommendations", [])),
            len(parsed.get("concerns", [])),
        )
        if self.config.log_llm_calls:
            logger.debug(
                "Expert %s (%s) full response: %s",
                self.role, self.side, parsed,
            )

        # Parse response into dataclass
        recommendation = self._parse_response(parsed)

        # Validate recommended commands
        recommendation.recommendations = self._validate_recommendations(
            state, recommendation.recommendations,
        )

        return recommendation

    def _parse_response(self, parsed: dict) -> ExpertRecommendation:
        """Parse LLM JSON response into an ExpertRecommendation."""
        recs = []
        for r in parsed.get("recommendations", []):
            if isinstance(r, dict):
                recs.append(Recommendation(
                    action=r.get("action", ""),
                    params=r.get("params", {}),
                    reasoning=r.get("reasoning", ""),
                ))
            elif isinstance(r, str):
                # LLM returned a plain string instead of a dict — skip it
                logger.warning(
                    "Expert %s: skipping non-dict recommendation: %s",
                    self.role, r[:120],
                )

        # Concerns can also come back as dicts instead of strings
        raw_concerns = parsed.get("concerns", [])
        concerns = []
        for c in raw_concerns:
            if isinstance(c, str):
                concerns.append(c)
            elif isinstance(c, dict):
                concerns.append(str(c.get("description", c)))

        return ExpertRecommendation(
            role=self.role,
            assessment=parsed.get("assessment", ""),
            priority=parsed.get("priority", "medium"),
            recommendations=recs,
            concerns=concerns,
        )

    def _validate_recommendations(
        self,
        state: GameState,
        recommendations: list[Recommendation],
    ) -> list[Recommendation]:
        """Filter out recommendations with invalid commands."""
        valid = []
        for rec in recommendations:
            if rec.action not in self.available_commands:
                logger.warning(
                    "Expert %s recommended invalid command '%s' — skipping",
                    self.role, rec.action,
                )
                continue

            # Check via agent_interface validation
            result = validate_command(
                state, self.role, self.side, rec.action, **rec.params,
            )
            if result.success:
                valid.append(rec)
            else:
                logger.warning(
                    "Expert %s command '%s' failed validation: %s — keeping as advisory",
                    self.role, rec.action, result.error,
                )
                # Keep it as advisory — the General may still find it useful
                valid.append(rec)

        return valid
