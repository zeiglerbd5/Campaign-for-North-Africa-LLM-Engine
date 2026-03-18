"""
CNA Engine — Victory Conditions Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.victory import (
    get_hex_controller, get_objective_statuses, calculate_vp,
    assess_victory, OBJECTIVE_HEXES,
    DECISIVE_VICTORY_MARGIN, MARGINAL_VICTORY_MARGIN,
    VP_PER_STRENGTH_POINT_DESTROYED,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import Side, UnitStatus, TerrainType, MotorizationType


def _make_unit(uid, side=Side.ALLIED, hex_id="E1326", strength=10):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=strength),
    )


def _make_state_with_objectives():
    state = GameState()
    state.turn = TurnState(game_turn=10)
    for hex_id in OBJECTIVE_HEXES:
        state.hexes[hex_id] = HexState(hex_id=hex_id, terrain=TerrainType.CLEAR)
    return state


def test_hex_control():
    print("=" * 60)
    print("TEST 1: Hex Control")
    print("=" * 60)

    state = _make_state_with_objectives()

    # Empty hex — no controller
    assert get_hex_controller(state, "E1326") is None
    print(f"  Empty: None")

    # Allied only
    state.hexes["E1326"].allied_unit_ids = ["u1"]
    state.units["u1"] = _make_unit("u1", side=Side.ALLIED, hex_id="E1326")
    assert get_hex_controller(state, "E1326") == Side.ALLIED
    print(f"  Allied only: {Side.ALLIED}")

    # Axis only
    state.hexes["C0512"].axis_unit_ids = ["e1"]
    state.units["e1"] = _make_unit("e1", side=Side.AXIS, hex_id="C0512")
    assert get_hex_controller(state, "C0512") == Side.AXIS
    print(f"  Axis only: {Side.AXIS}")

    # Contested
    state.hexes["D0821"].allied_unit_ids = ["u2"]
    state.hexes["D0821"].axis_unit_ids = ["e2"]
    assert get_hex_controller(state, "D0821") is None
    print(f"  Contested: None")

    # Missing hex
    assert get_hex_controller(state, "B9999") is None
    print(f"  Missing: None")

    print("  PASSED\n")


def test_objective_statuses():
    print("=" * 60)
    print("TEST 2: Objective Statuses")
    print("=" * 60)

    state = _make_state_with_objectives()

    # Place allied at Alexandria
    state.hexes["E1326"].allied_unit_ids = ["u1"]
    state.units["u1"] = _make_unit("u1", hex_id="E1326")

    # Place axis at Tobruk
    state.hexes["C0512"].axis_unit_ids = ["e1"]
    state.units["e1"] = _make_unit("e1", side=Side.AXIS, hex_id="C0512")

    statuses = get_objective_statuses(state)
    assert len(statuses) == len(OBJECTIVE_HEXES)

    alex = next(s for s in statuses if s.hex_id == "E1326")
    assert alex.controller == Side.ALLIED
    assert alex.name == "Alexandria"
    assert alex.vp_value == 5
    print(f"  Alexandria: {alex.controller} (VP={alex.vp_value})")

    tobruk = next(s for s in statuses if s.hex_id == "C0512")
    assert tobruk.controller == Side.AXIS
    assert tobruk.name == "Tobruk"
    print(f"  Tobruk: {tobruk.controller} (VP={tobruk.vp_value})")

    # Uncontrolled objectives
    bardia = next(s for s in statuses if s.hex_id == "C1215")
    assert bardia.controller is None
    print(f"  Bardia: {bardia.controller}")

    print("  PASSED\n")


def test_vp_calculation():
    print("=" * 60)
    print("TEST 3: VP Calculation")
    print("=" * 60)

    state = _make_state_with_objectives()

    # Allied holds Alexandria (5) and Mersa Matruh (3) = 8 objective VP
    state.hexes["E1326"].allied_unit_ids = ["u1"]
    state.hexes["D1822"].allied_unit_ids = ["u2"]
    state.units["u1"] = _make_unit("u1", hex_id="E1326")
    state.units["u2"] = _make_unit("u2", hex_id="D1822")

    allied_vp = calculate_vp(state, Side.ALLIED)
    assert allied_vp.objective_vp == 8
    assert "Alexandria" in allied_vp.objectives_held
    assert "Mersa Matruh" in allied_vp.objectives_held
    print(f"  Allied objectives: {allied_vp.objective_vp} VP ({allied_vp.objectives_held})")

    # Axis holds Tobruk (5) and Benghazi (5) = 10 objective VP
    state.hexes["C0512"].axis_unit_ids = ["e1"]
    state.hexes["B1403"].axis_unit_ids = ["e2"]
    state.units["e1"] = _make_unit("e1", side=Side.AXIS, hex_id="C0512")
    state.units["e2"] = _make_unit("e2", side=Side.AXIS, hex_id="B1403")

    axis_vp = calculate_vp(state, Side.AXIS)
    assert axis_vp.objective_vp == 10
    print(f"  Axis objectives: {axis_vp.objective_vp} VP ({axis_vp.objectives_held})")

    # Destruction VP
    # Give axis units losses
    state.units["e1"].losses_taken = 6
    state.units["e2"].losses_taken = 4
    allied_vp2 = calculate_vp(state, Side.ALLIED)
    expected_destruction = 10 * VP_PER_STRENGTH_POINT_DESTROYED
    assert allied_vp2.destruction_vp == expected_destruction
    print(f"  Allied destruction: {allied_vp2.destruction_vp} VP (10 enemy SP lost)")

    print("  PASSED\n")


def test_victory_assessment():
    print("=" * 60)
    print("TEST 4: Victory Assessment")
    print("=" * 60)

    state = _make_state_with_objectives()
    state.turn.game_turn = 50

    # Allied dominance
    for hex_id, (vp, name) in OBJECTIVE_HEXES.items():
        uid = f"au_{hex_id}"
        state.hexes[hex_id].allied_unit_ids = [uid]
        state.units[uid] = _make_unit(uid, hex_id=hex_id)

    v = assess_victory(state)
    assert v.allied_vp.objective_vp > 0
    assert v.axis_vp.objective_vp == 0
    assert v.leading_side == Side.ALLIED
    assert not v.is_campaign_over
    print(f"  Assessment: {v.description}")
    print(f"  Result: {v.result}")

    # Draw scenario — nobody holds anything
    state2 = _make_state_with_objectives()
    state2.turn.game_turn = 50
    v2 = assess_victory(state2)
    assert v2.result == "draw"
    print(f"  Draw: {v2.description}")

    print("  PASSED\n")


def test_campaign_end():
    print("=" * 60)
    print("TEST 5: Campaign End")
    print("=" * 60)

    state = _make_state_with_objectives()
    state.turn.game_turn = 112  # Past end

    v = assess_victory(state)
    assert v.is_campaign_over
    assert "CAMPAIGN OVER" in v.description
    print(f"  End: {v.description}")

    print("  PASSED\n")


def main():
    test_hex_control()
    test_objective_statuses()
    test_vp_calculation()
    test_victory_assessment()
    test_campaign_end()
    print("=" * 60)
    print("ALL VICTORY TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
