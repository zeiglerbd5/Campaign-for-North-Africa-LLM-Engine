"""
CNA Engine — Convoy Movement Phase Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.turn_runner import TurnRunner, PausePoint, PhaseExecutionResult
from cna_engine.engine.agent_interface import execute_command, ROLE_LOGISTICS
from cna_engine.engine.scenario import load_operation_compass
from cna_engine.models.game_state import GameState, Unit, HexState, TurnState, TOEStrength, UnitSupply
from cna_engine.models.enums import (
    Side, UnitClass, UnitSize, UnitStatus, MotorizationType,
    TerrainType, RoadType, GamePhase, OpStagePhase,
)


def _make_convoy_state(has_trucks=True, active_side=Side.ALLIED):
    """Build a minimal state positioned at the CONVOY_MOVEMENT sub-phase."""
    state = GameState()
    state.turn = TurnState(
        game_turn=1, op_stage=1,
        phase=GamePhase.OP_STAGE,
        sub_phase=OpStagePhase.CONVOY_MOVEMENT,
        active_side=active_side,
    )

    # Map hexes
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.CLEAR, road=RoadType.ROAD)

    if has_trucks:
        truck = Unit(
            id="truck_1", name="Test Truck Column", side=active_side, nationality="british",
            unit_class=UnitClass.TRUCK, unit_size=UnitSize.COMPANY,
            motorization=MotorizationType.MOTORIZED, hex_id="D0821",
            base_cpa=8, stacking_points=1,
            toe_strength=TOEStrength(infantry=2),
            current_strength=TOEStrength(infantry=2),
            supply=UnitSupply(fuel=15, water=5, ammo=2, stores=3,
                              fuel_capacity=30, water_capacity=10,
                              ammo_capacity=4, stores_capacity=6),
        )
        state.units["truck_1"] = truck
        state.hexes["D0821"].allied_unit_ids.append("truck_1")

        hq = Unit(
            id="hq_1", name="Test HQ", side=active_side, nationality="british",
            unit_class=UnitClass.HQ, unit_size=UnitSize.BRIGADE,
            motorization=MotorizationType.MOTORIZED, hex_id="D0821",
            base_cpa=4, stacking_points=1,
            toe_strength=TOEStrength(infantry=2),
            current_strength=TOEStrength(infantry=2),
            supply=UnitSupply(fuel=5, water=5, ammo=2, stores=3,
                              fuel_capacity=10, water_capacity=10,
                              ammo_capacity=4, stores_capacity=6),
        )
        state.units["hq_1"] = hq
        state.hexes["D0821"].allied_unit_ids.append("hq_1")

    return state


def test_phase_pauses_with_eligible_units():
    print("=" * 60)
    print("TEST: Convoy phase pauses when eligible units exist")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=True)
    runner = TurnRunner(state)
    result = runner.execute_current_phase()
    assert isinstance(result, PausePoint), f"Expected PausePoint, got {type(result).__name__}"
    print(f"  Phase paused: {result.description} — PASSED")


def test_phase_auto_skips_without_units():
    print("=" * 60)
    print("TEST: Convoy phase auto-skips when no eligible units")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=False)
    runner = TurnRunner(state)
    result = runner.execute_current_phase()
    assert isinstance(result, PhaseExecutionResult), f"Expected PhaseExecutionResult, got {type(result).__name__}"
    assert result.auto_executed is True
    print(f"  Phase auto-executed: {result.description} — PASSED")


def test_truck_moves_via_convoy_move():
    print("=" * 60)
    print("TEST: Truck moves via convoy_move")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=True)
    result = execute_command(
        state, ROLE_LOGISTICS, Side.ALLIED, "convoy_move",
        unit_id="truck_1", destination="D0922",
    )
    assert result.success, f"Failed: {result.error}"
    assert state.units["truck_1"].hex_id == "D0922"
    print(f"  Truck moved to D0922 — PASSED")


def test_hq_moves_via_convoy_move():
    print("=" * 60)
    print("TEST: HQ moves via convoy_move")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=True)
    result = execute_command(
        state, ROLE_LOGISTICS, Side.ALLIED, "convoy_move",
        unit_id="hq_1", destination="D0922",
    )
    assert result.success, f"Failed: {result.error}"
    assert state.units["hq_1"].hex_id == "D0922"
    print(f"  HQ moved to D0922 — PASSED")


def test_combat_unit_rejected():
    print("=" * 60)
    print("TEST: Combat unit (infantry/armor) rejected for convoy_move")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=True)
    # Add an infantry unit
    inf = Unit(
        id="inf_1", name="Test Infantry", side=Side.ALLIED, nationality="british",
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D0821",
        base_cpa=6, stacking_points=2,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
    )
    state.units["inf_1"] = inf

    result = execute_command(
        state, ROLE_LOGISTICS, Side.ALLIED, "convoy_move",
        unit_id="inf_1", destination="D0922",
    )
    assert not result.success
    assert "not eligible" in result.error.lower() or "TRUCK" in result.error
    print(f"  Infantry rejected: {result.error} — PASSED")


def test_wrong_phase_rejected():
    print("=" * 60)
    print("TEST: convoy_move rejected in wrong phase")
    print("=" * 60)

    state = _make_convoy_state(has_trucks=True)
    state.turn.sub_phase = OpStagePhase.MOVEMENT_COMBAT  # Wrong phase

    result = execute_command(
        state, ROLE_LOGISTICS, Side.ALLIED, "convoy_move",
        unit_id="truck_1", destination="D0922",
    )
    assert not result.success
    assert "convoy_movement" in result.error.lower() or "CONVOY" in result.error
    print(f"  Wrong phase rejected: {result.error} — PASSED")


def test_full_scenario_has_convoy_eligible():
    print("=" * 60)
    print("TEST: Full scenario has convoy-eligible units")
    print("=" * 60)

    state, _ = load_operation_compass()
    # Check that truck and HQ units exist
    truck_classes = {UnitClass.TRUCK, UnitClass.HQ}
    eligible = [u for u in state.units.values()
                if u.unit_class in truck_classes and u.status == UnitStatus.ACTIVE and u.hex_id]
    assert len(eligible) > 0, "Should have convoy-eligible units"
    print(f"  Found {len(eligible)} convoy-eligible units:")
    for u in eligible:
        print(f"    {u.id}: {u.name} ({u.unit_class}) @ {u.hex_id}")
    print("  PASSED")


def main():
    test_phase_pauses_with_eligible_units()
    test_phase_auto_skips_without_units()
    test_truck_moves_via_convoy_move()
    test_hq_moves_via_convoy_move()
    test_combat_unit_rejected()
    test_wrong_phase_rejected()
    test_full_scenario_has_convoy_eligible()
    print("\n" + "=" * 60)
    print("ALL CONVOY MOVEMENT TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
