"""
CNA Engine — Sequence of Play State Machine
Implements the NJH-restructured Sequence of Play.
Advances the game turn cursor through each phase and sub-phase.
"""
from __future__ import annotations
from ..models.enums import (
    Side, GamePhase, OpStagePhase, CombatSegment, Season,
    OP_STAGES_PER_TURN, GAME_TURNS_CAMPAIGN, UnitStatus,
)
from ..models.game_state import GameState, TurnState


# ════════════════════════════════════════
# PHASE SEQUENCE DEFINITION
# ════════════════════════════════════════

# Top-level GT phases in order (NJH restructured)
GT_PHASE_ORDER = [
    GamePhase.STORES_EXPENDITURE,
    GamePhase.STRATEGIC_STAGE,
    GamePhase.OP_STAGE,         # ×3
    GamePhase.END_OF_TURN,
]

# Shared portion of each OpStage
SHARED_PHASES = [
    OpStagePhase.WEATHER,
    OpStagePhase.ORGANIZATION,
    OpStagePhase.CONVOY_ARRIVAL,
    OpStagePhase.FLEET,
    OpStagePhase.AIR,
    OpStagePhase.INITIATIVE,
]

# Per-side phases (A side then B side)
SIDE_PHASES = [
    OpStagePhase.RESERVE,
    OpStagePhase.MOVEMENT_COMBAT,
    OpStagePhase.VEHICLE_REPAIR,
    OpStagePhase.CONVOY_MOVEMENT,
    OpStagePhase.PATROL,
]

# Movement & Combat sub-segments (repeatable)
COMBAT_SEGMENTS = [
    CombatSegment.RECON,
    CombatSegment.MOVEMENT,
    CombatSegment.BREAKDOWN,
    CombatSegment.BARRAGE,
    CombatSegment.RETREAT_BEFORE_ASSAULT,
    CombatSegment.ANTI_ARMOR,
    CombatSegment.CLOSE_ASSAULT,
    CombatSegment.RESERVE_RELEASE,
]


# ════════════════════════════════════════
# SEASON DETERMINATION
# ════════════════════════════════════════

def get_season(game_turn: int) -> str:
    """
    Determine season from game turn.
    ERRATA CORRECTED: Original chart had seasons backwards.
    GT1 = Sept 1940 Wk1. Seasons defined per [29.1] errata.
    """
    # Calculate approximate week-of-year from GT
    # GT1 = Sept Wk1 = ~week 36 of year
    week_of_year = ((game_turn - 1) + 35) % 52 + 1  # Rough mapping

    # Spring: Wk4 March (wk13) – Wk3 June (wk25)
    if 13 <= week_of_year <= 25:
        return Season.SPRING
    # Summer: Wk4 June (wk26) – Wk3 Sept (wk38)
    elif 26 <= week_of_year <= 38:
        return Season.SUMMER
    # Autumn: Wk4 Sept (wk39) – Wk3 Dec (wk51)
    elif 39 <= week_of_year <= 51:
        return Season.AUTUMN
    # Winter: Wk4 Dec (wk52) – Wk3 March (wk12)
    else:
        return Season.WINTER


# ════════════════════════════════════════
# INITIATIVE
# ════════════════════════════════════════

def get_initiative_ratings(game_turn: int) -> tuple[int, int]:
    """Get (allied_rating, axis_rating) for a game turn per [7.2]."""
    if game_turn <= 42:
        return 3, 5
    elif game_turn <= 90:
        return 4, 5
    else:
        return 5, 4


def resolve_initiative(state: GameState, allied_roll: int, axis_roll: int) -> str:
    """
    Resolve initiative per [7.0].
    Roll d6: succeed if roll ≤ rating. Higher successful roll wins. Ties re-roll.
    Returns the side with initiative.
    """
    a_rating, x_rating = get_initiative_ratings(state.turn.game_turn)
    allied_success = allied_roll <= a_rating
    axis_success = axis_roll <= x_rating

    if allied_success and not axis_success:
        return Side.ALLIED
    elif axis_success and not allied_success:
        return Side.AXIS
    elif allied_success and axis_success:
        # Both succeed: higher roll wins
        if allied_roll > axis_roll:
            return Side.ALLIED
        elif axis_roll > allied_roll:
            return Side.AXIS
        else:
            return None  # Tie — must re-roll
    else:
        # Neither succeeds — Axis gets initiative by default
        return Side.AXIS


# ════════════════════════════════════════
# STATE MACHINE CORE
# ════════════════════════════════════════

class SequenceOfPlay:
    """
    Manages progression through the Sequence of Play.
    Call advance() to move to the next phase.
    The engine processes each phase's logic, then calls advance().
    """

    def __init__(self, state: GameState):
        self.state = state

    @property
    def turn(self) -> TurnState:
        return self.state.turn

    def get_current_phase_description(self) -> str:
        """Human-readable description of current phase."""
        t = self.turn
        base = f"GT{t.game_turn} ({t.date_string})"

        if t.phase == GamePhase.STORES_EXPENDITURE:
            return f"{base} — Stores Expenditure Stage"
        elif t.phase == GamePhase.STRATEGIC_STAGE:
            return f"{base} — Strategic Stage"
        elif t.phase == GamePhase.OP_STAGE:
            side_label = ""
            if t.active_side:
                side_label = f" [{t.active_side.upper()} portion]"
            sub = f" > {t.sub_phase}" if t.sub_phase else ""
            return f"{base} — OpStage {t.op_stage}{side_label}{sub}"
        elif t.phase == GamePhase.END_OF_TURN:
            return f"{base} — End of Turn"
        return base

    def advance(self) -> dict:
        """
        Advance to the next phase in the sequence.
        Returns a dict describing what just transitioned and what's next.
        """
        t = self.turn
        previous = self.get_current_phase_description()

        if t.phase == GamePhase.STORES_EXPENDITURE:
            self._advance_from_stores()
        elif t.phase == GamePhase.STRATEGIC_STAGE:
            self._advance_from_strategic()
        elif t.phase == GamePhase.OP_STAGE:
            self._advance_within_opstage()
        elif t.phase == GamePhase.END_OF_TURN:
            self._advance_to_next_turn()

        current = self.get_current_phase_description()
        result = {
            "previous": previous,
            "current": current,
            "game_turn": t.game_turn,
            "op_stage": t.op_stage,
            "phase": t.phase,
            "sub_phase": t.sub_phase,
            "active_side": t.active_side,
        }
        self.state.log_event("phase_advance", f"{previous} → {current}")
        return result

    def _advance_from_stores(self):
        self.turn.phase = GamePhase.STRATEGIC_STAGE
        self.turn.sub_phase = None
        self.turn.active_side = None

    def _advance_from_strategic(self):
        self.turn.phase = GamePhase.OP_STAGE
        self.turn.op_stage = 1
        self.turn.sub_phase = OpStagePhase.WEATHER
        self.turn.active_side = None

    def _advance_within_opstage(self):
        t = self.turn
        sub = t.sub_phase

        # ── Shared portion progression ──
        if sub in SHARED_PHASES:
            idx = SHARED_PHASES.index(sub)
            if idx < len(SHARED_PHASES) - 1:
                t.sub_phase = SHARED_PHASES[idx + 1]
                t.active_side = None
            else:
                # Move to A side's first phase
                a_side = t.initiative_side or Side.AXIS
                t.active_side = a_side
                t.sub_phase = SIDE_PHASES[0]
            return

        # ── Per-side progression ──
        if sub in SIDE_PHASES:
            idx = SIDE_PHASES.index(sub)
            if idx < len(SIDE_PHASES) - 1:
                t.sub_phase = SIDE_PHASES[idx + 1]
            else:
                # Finished this side's portion
                a_side = t.initiative_side or Side.AXIS
                b_side = Side.ALLIED if a_side == Side.AXIS else Side.AXIS

                if t.active_side == a_side:
                    # Switch to B side
                    t.active_side = b_side
                    t.sub_phase = SIDE_PHASES[0]
                else:
                    # B side done — advance to next OpStage or End of Turn
                    self._advance_to_next_opstage()
            return

        # Fallback
        self._advance_to_next_opstage()

    def _advance_to_next_opstage(self):
        t = self.turn
        if t.op_stage < OP_STAGES_PER_TURN:
            t.op_stage += 1
            t.sub_phase = OpStagePhase.WEATHER
            t.active_side = None
            t.initiative_side = None
            t.movement_combat_iteration = 0
            # Reset per-OpStage unit flags
            self._reset_opstage_flags()
        else:
            t.phase = GamePhase.END_OF_TURN
            t.sub_phase = None
            t.active_side = None

    def _advance_to_next_turn(self):
        t = self.turn
        t.game_turn += 1
        t.op_stage = 1
        t.phase = GamePhase.STORES_EXPENDITURE
        t.sub_phase = None
        t.active_side = None
        t.initiative_side = None
        t.movement_combat_iteration = 0
        t.current_season = get_season(t.game_turn)

        # Update initiative ratings for the new turn
        a_rat, x_rat = get_initiative_ratings(t.game_turn)
        t.allied_initiative_rating = a_rat
        t.axis_initiative_rating = x_rat

        self._reset_turn_flags()

    def _reset_opstage_flags(self):
        """Reset per-OpStage tracking on units.

        NOTE: current_cpa_spent is NOT reset here. It is read by
        apply_cohesion_changes() and then cleared in
        execute_organization_phase() after cohesion processing.
        """
        for unit in self.state.units.values():
            unit.has_acted_this_stage = False
            unit.is_pinned = False
            unit.terrains_traversed_this_stage = []
        # Clear sighting
        for hex_state in self.state.hexes.values():
            hex_state.allied_sighted = False
            hex_state.axis_sighted = False

    def _reset_turn_flags(self):
        """Reset per-turn tracking."""
        self._reset_opstage_flags()
        for aircraft in self.state.aircraft.values():
            if aircraft.status == "flew_this_stage":
                aircraft.status = "ready"

    def is_game_over(self) -> bool:
        if self.turn.game_turn > GAME_TURNS_CAMPAIGN:
            return True
        # Early termination: a side has no active units left
        allied_active = any(
            u.status == UnitStatus.ACTIVE and u.side == "allied"
            for u in self.state.units.values()
        )
        axis_active = any(
            u.status == UnitStatus.ACTIVE and u.side == "axis"
            for u in self.state.units.values()
        )
        return not allied_active or not axis_active
