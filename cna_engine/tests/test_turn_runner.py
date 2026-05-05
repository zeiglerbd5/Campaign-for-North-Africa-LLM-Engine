"""
CNA Engine — Turn Runner / Game Orchestrator Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.turn_runner import (
    TurnRunner, PhaseExecutionResult, PausePoint,
)
from cna_engine.engine.scenario import load_operation_compass
from cna_engine.models.game_state import GameState, TurnState, HexState, Unit, TOEStrength
from cna_engine.models.enums import (
    Side, GamePhase, OpStagePhase, Weather, Season, MotorizationType,
)


def _make_minimal_state():
    """Minimal state for testing turn runner without full scenario."""
    state = GameState()
    state.turn = TurnState(
        game_turn=1, op_stage=1,
        phase=GamePhase.STORES_EXPENDITURE,
        current_season=Season.AUTUMN,
        current_weather=Weather.CLEAR,
    )
    # Need at least one unit for stores expenditure
    state.units["u1"] = Unit(
        id="u1", name="Test Unit", side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id="D0821",
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=10),
        toe_strength=TOEStrength(infantry=10),
    )
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain="clear")
    state.allied_replacement_pool = {"infantry": 0, "armor": 0, "gun": 0}
    state.axis_replacement_pool = {"infantry": 0, "armor": 0, "gun": 0}
    return state


def test_stores_expenditure():
    print("=" * 60)
    print("TEST 1: Stores Expenditure Phase")
    print("=" * 60)

    state = _make_minimal_state()
    runner = TurnRunner(state)

    result = runner.execute_current_phase()
    assert isinstance(result, PhaseExecutionResult)
    assert result.auto_executed
    assert result.phase == GamePhase.STORES_EXPENDITURE
    print(f"  Stores: {result.description}")

    # Advance to next phase
    runner.advance()
    assert state.turn.phase == GamePhase.STRATEGIC_STAGE
    print(f"  Advanced to: {state.turn.phase}")

    print("  PASSED\n")


def test_strategic_stage():
    print("=" * 60)
    print("TEST 2: Strategic Stage")
    print("=" * 60)

    state = _make_minimal_state()
    runner = TurnRunner(state)

    # Skip stores expenditure
    runner.execute_current_phase()
    runner.advance()

    # Now at Strategic Stage
    assert state.turn.phase == GamePhase.STRATEGIC_STAGE
    result = runner.execute_current_phase()
    assert isinstance(result, PhaseExecutionResult)
    assert result.auto_executed
    print(f"  Strategic: {result.description}")

    runner.advance()
    assert state.turn.phase == GamePhase.OP_STAGE
    assert state.turn.op_stage == 1
    assert state.turn.sub_phase == OpStagePhase.WEATHER
    print(f"  Advanced to: OpStage {state.turn.op_stage} {state.turn.sub_phase}")

    print("  PASSED\n")


def test_weather_phase():
    print("=" * 60)
    print("TEST 3: Weather Phase")
    print("=" * 60)

    state = _make_minimal_state()
    state.turn.phase = GamePhase.OP_STAGE
    state.turn.op_stage = 1
    state.turn.sub_phase = OpStagePhase.WEATHER
    state.turn.current_season = Season.WINTER

    runner = TurnRunner(state)

    # Force mud weather
    result = runner.execute_current_phase(dice_overrides={"weather_roll": 4})
    assert isinstance(result, PhaseExecutionResult)
    assert result.auto_executed
    assert state.turn.current_weather == Weather.MUD
    print(f"  Weather: {result.description}")

    print("  PASSED\n")


def test_initiative_phase():
    print("=" * 60)
    print("TEST 4: Initiative Phase")
    print("=" * 60)

    state = _make_minimal_state()
    state.turn.phase = GamePhase.OP_STAGE
    state.turn.op_stage = 1
    state.turn.sub_phase = OpStagePhase.INITIATIVE

    runner = TurnRunner(state)

    # Force Axis wins initiative (Allied roll=6 fails, Axis roll=3 succeeds with rating 5)
    result = runner.execute_current_phase(
        dice_overrides={"allied_initiative_roll": 6, "axis_initiative_roll": 3})
    assert isinstance(result, PhaseExecutionResult)
    assert result.auto_executed
    assert state.turn.initiative_side == Side.AXIS
    print(f"  Initiative: {result.description}")

    # Force Allied wins
    state.turn.sub_phase = OpStagePhase.INITIATIVE
    state.turn.initiative_side = None
    result2 = runner.execute_current_phase(
        dice_overrides={"allied_initiative_roll": 1, "axis_initiative_roll": 6})
    assert state.turn.initiative_side == Side.ALLIED
    print(f"  Allied wins: {result2.description}")

    print("  PASSED\n")


def test_interactive_phases():
    print("=" * 60)
    print("TEST 5: Interactive Phases (Pause Points)")
    print("=" * 60)

    state = _make_minimal_state()
    state.turn.phase = GamePhase.OP_STAGE
    state.turn.op_stage = 1
    state.turn.active_side = Side.AXIS

    runner = TurnRunner(state)

    # Movement/Combat → pause
    state.turn.sub_phase = OpStagePhase.MOVEMENT_COMBAT
    result = runner.execute_current_phase()
    assert isinstance(result, PausePoint)
    assert result.awaiting == f"{Side.AXIS}_input"
    print(f"  Movement: {result.description}")

    # Air → pause
    state.turn.sub_phase = OpStagePhase.AIR
    state.turn.active_side = None
    result2 = runner.execute_current_phase()
    assert isinstance(result2, PausePoint)
    print(f"  Air: {result2.description}")

    # Reserve → pause
    state.turn.sub_phase = OpStagePhase.RESERVE
    state.turn.active_side = Side.ALLIED
    result3 = runner.execute_current_phase()
    assert isinstance(result3, PausePoint)
    print(f"  Reserve: {result3.description}")

    # Patrol → pause
    state.turn.sub_phase = OpStagePhase.PATROL
    result4 = runner.execute_current_phase()
    assert isinstance(result4, PausePoint)
    print(f"  Patrol: {result4.description}")

    print("  PASSED\n")


def test_vehicle_repair():
    print("=" * 60)
    print("TEST 6: Vehicle Repair Phase")
    print("=" * 60)

    state = _make_minimal_state()
    state.turn.phase = GamePhase.OP_STAGE
    state.turn.op_stage = 1
    state.turn.sub_phase = OpStagePhase.VEHICLE_REPAIR
    state.turn.active_side = Side.ALLIED

    runner = TurnRunner(state)
    result = runner.execute_current_phase()
    assert isinstance(result, PhaseExecutionResult)
    assert result.auto_executed
    print(f"  Repair: {result.description}")

    print("  PASSED\n")


def test_run_until_pause():
    print("=" * 60)
    print("TEST 7: Run Until Pause")
    print("=" * 60)

    state = _make_minimal_state()
    runner = TurnRunner(state)

    results = runner.run_until_pause(
        dice_overrides={"weather_roll": 1, "allied_initiative_roll": 1,
                        "axis_initiative_roll": 6})

    assert len(results) >= 3  # At least stores, strategic, weather...

    # Last result should be a PausePoint (first interactive phase)
    last = results[-1]
    print(f"  Ran {len(results)} phases before pause")
    for r in results:
        if isinstance(r, PhaseExecutionResult):
            print(f"    AUTO: {r.description[:80]}")
        else:
            print(f"    PAUSE: {r.description}")

    assert isinstance(last, PausePoint)
    print(f"  Paused at: {last.description}")

    print("  PASSED\n")


def test_full_turn():
    print("=" * 60)
    print("TEST 8: Full Turn Execution")
    print("=" * 60)

    state = _make_minimal_state()
    runner = TurnRunner(state)

    summary = runner.execute_full_turn(
        dice_overrides={"weather_roll": 1, "allied_initiative_roll": 3,
                        "axis_initiative_roll": 3})

    assert summary.game_turn == 1
    assert summary.phases_executed > 10  # A full turn has many phases
    print(f"  Full turn: {summary.description}")
    print(f"  Phases: {summary.phases_executed}")
    for evt in summary.events[:10]:
        print(f"    {evt[:80]}")
    if len(summary.events) > 10:
        print(f"    ... ({len(summary.events) - 10} more)")

    # State should now be at GT2
    assert state.turn.game_turn == 2
    print(f"  After: GT{state.turn.game_turn}")

    print("  PASSED\n")


def test_scenario_turn():
    print("=" * 60)
    print("TEST 9: Scenario Full Turn")
    print("=" * 60)

    state, _ = load_operation_compass()
    runner = TurnRunner(state)

    assert state.turn.game_turn == 1
    summary = runner.execute_full_turn(
        dice_overrides={"weather_roll": 1, "allied_initiative_roll": 2,
                        "axis_initiative_roll": 4})

    assert summary.phases_executed > 10
    assert state.turn.game_turn == 2
    print(f"  Scenario turn: {summary.description}")
    print(f"  Events: {len(summary.events)}")
    for evt in summary.events[:8]:
        print(f"    {evt[:80]}")
    if len(summary.events) > 8:
        print(f"    ... ({len(summary.events) - 8} more)")

    print("  PASSED\n")


def test_multi_turn():
    print("=" * 60)
    print("TEST 10: Multi-Turn Execution")
    print("=" * 60)

    state = _make_minimal_state()
    runner = TurnRunner(state)

    for gt in range(1, 4):
        assert state.turn.game_turn == gt
        summary = runner.execute_full_turn(
            dice_overrides={"weather_roll": 1})
        print(f"  GT{gt}: {summary.phases_executed} phases, "
              f"now at GT{state.turn.game_turn}")

    assert state.turn.game_turn == 4
    print(f"  Final: GT{state.turn.game_turn}")

    print("  PASSED\n")


def main():
    test_stores_expenditure()
    test_strategic_stage()
    test_weather_phase()
    test_initiative_phase()
    test_interactive_phases()
    test_vehicle_repair()
    test_run_until_pause()
    test_full_turn()
    test_scenario_turn()
    test_multi_turn()
    print("=" * 60)
    print("ALL TURN RUNNER TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
