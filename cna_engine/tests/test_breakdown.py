"""
CNA Engine — Vehicle Breakdown Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.breakdown import (
    check_breakdown, execute_breakdown_segment,
    BREAKDOWN_TERRAINS, BREAKDOWN_THRESHOLD, BREAKDOWN_SP_LOSS,
    BREAKDOWN_COHESION_PENALTY, BREAKDOWN_MOTORIZATIONS,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, MotorizationType,
)


def _make_mech_unit(uid, hex_id="D0821", armor=6, infantry=4, side=Side.ALLIED):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="armor", unit_size="battalion",
        motorization=MotorizationType.MECHANIZED, hex_id=hex_id,
        base_cpa=10, stacking_points=3,
        current_strength=TOEStrength(armor=armor, infantry=infantry),
        toe_strength=TOEStrength(armor=8, infantry=6),
    )


def _make_infantry(uid, hex_id="D0821", strength=8, side=Side.ALLIED):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id=hex_id,
        base_cpa=6, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=10),
    )


def _make_state():
    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    return state


def test_breakdown_on_bad_terrain():
    """Roll >= 5 with sand_sea → loses 1 SP, -1 cohesion."""
    print("TEST: breakdown_on_bad_terrain")
    state = _make_state()
    unit = _make_mech_unit("u1", armor=6, infantry=4)
    state.units["u1"] = unit

    terrains = ["sand_sea", "clear", "sand_sea"]
    result = check_breakdown(state, "u1", terrains, dice_roll=5)

    assert result.checked
    assert result.broke_down
    assert result.sp_lost == 1
    assert result.cohesion_penalty == BREAKDOWN_COHESION_PENALTY
    assert unit.cohesion == BREAKDOWN_COHESION_PENALTY
    # Should lose armor first
    assert unit.current_strength.armor == 5  # was 6
    print(f"  {result.description}")
    print("  PASSED\n")


def test_no_breakdown_on_clear():
    """Clear terrain only → no roll, no check."""
    print("TEST: no_breakdown_on_clear")
    state = _make_state()
    unit = _make_mech_unit("u1")
    state.units["u1"] = unit

    terrains = ["clear", "clear", "coastal"]
    result = check_breakdown(state, "u1", terrains, dice_roll=6)

    assert not result.checked
    assert not result.broke_down
    assert result.sp_lost == 0
    assert unit.cohesion == 0
    print(f"  {result.description}")
    print("  PASSED\n")


def test_non_motorized_exempt():
    """Infantry (non-motorized) never checked by phase executor."""
    print("TEST: non_motorized_exempt")
    state = _make_state()
    inf = _make_infantry("i1")
    inf.terrains_traversed_this_stage = ["sand_sea", "rough"]
    state.units["i1"] = inf

    phase_result = execute_breakdown_segment(state, Side.ALLIED)
    assert phase_result.units_checked == 0
    print(f"  {phase_result.description}")
    print("  PASSED\n")


def test_breakdown_sp_loss_armor_first():
    """Armor SP removed before infantry SP."""
    print("TEST: breakdown_sp_loss_armor_first")
    state = _make_state()
    unit = _make_mech_unit("u1", armor=1, infantry=5)
    state.units["u1"] = unit

    terrains = ["sand_sea"]
    result = check_breakdown(state, "u1", terrains, dice_roll=6)

    assert result.broke_down
    assert unit.current_strength.armor == 0  # was 1, lost 1
    assert unit.current_strength.infantry == 5  # unchanged
    print(f"  {result.description}")
    print("  PASSED\n")


def test_breakdown_cohesion_penalty():
    """Cohesion decreases by 1 on breakdown."""
    print("TEST: breakdown_cohesion_penalty")
    state = _make_state()
    unit = _make_mech_unit("u1")
    unit.cohesion = -3
    state.units["u1"] = unit

    terrains = ["rough", "wadi"]
    result = check_breakdown(state, "u1", terrains, dice_roll=5)

    assert result.broke_down
    assert result.cohesion_penalty == -1
    assert unit.cohesion == -4  # was -3, now -4
    print(f"  {result.description}")
    print("  PASSED\n")


def test_phase_execution():
    """execute_breakdown_segment processes all eligible motorized units."""
    print("TEST: phase_execution")
    state = _make_state()

    # Three units: 2 motorized with bad terrain, 1 without
    u1 = _make_mech_unit("u1", side=Side.ALLIED)
    u1.terrains_traversed_this_stage = ["sand_sea", "rough"]
    state.units["u1"] = u1

    u2 = _make_mech_unit("u2", side=Side.ALLIED)
    u2.terrains_traversed_this_stage = ["sand_sea"]
    state.units["u2"] = u2

    u3 = _make_mech_unit("u3", side=Side.ALLIED)
    u3.terrains_traversed_this_stage = ["clear"]
    state.units["u3"] = u3

    phase_result = execute_breakdown_segment(state, Side.ALLIED)
    assert phase_result.units_checked == 2  # u1 and u2 (u3 had no bad terrain)
    print(f"  {phase_result.description}")
    print("  PASSED\n")


def test_dice_override():
    """Deterministic testing with dice_roll override."""
    print("TEST: dice_override")
    state = _make_state()
    unit = _make_mech_unit("u1")
    state.units["u1"] = unit

    terrains = ["sand_sea"]

    # Roll 4 → no breakdown (threshold 5)
    r1 = check_breakdown(state, "u1", terrains, dice_roll=4)
    assert not r1.broke_down
    assert r1.dice_roll == 4

    # Roll 5 → breakdown
    r2 = check_breakdown(state, "u1", terrains, dice_roll=5)
    assert r2.broke_down
    assert r2.dice_roll == 5

    print("  PASSED\n")


def test_threshold_adjustment():
    """6+ bad terrain hexes lowers the effective threshold."""
    print("TEST: threshold_adjustment")
    state = _make_state()
    unit = _make_mech_unit("u1")
    state.units["u1"] = unit

    # 1 bad terrain hex → threshold stays at 5
    terrains_1 = ["sand_sea"]
    r1 = check_breakdown(state, "u1", terrains_1, dice_roll=4)
    assert r1.threshold == 5

    # 3 bad terrain hexes → threshold = max(3, 5 - 3//3) = max(3, 4) = 4
    terrains_3 = ["sand_sea", "rough", "wadi"]
    r3 = check_breakdown(state, "u1", terrains_3, dice_roll=3)
    assert r3.threshold == 4

    # 6 bad terrain hexes → threshold = max(3, 5 - 6//3) = max(3, 3) = 3
    terrains_6 = ["sand_sea"] * 6
    r6 = check_breakdown(state, "u1", terrains_6, dice_roll=2)
    assert r6.threshold == 3

    # 9 bad terrain hexes → threshold = max(3, 5 - 9//3) = max(3, 2) = 3
    terrains_9 = ["sand_sea"] * 9
    r9 = check_breakdown(state, "u1", terrains_9, dice_roll=2)
    assert r9.threshold == 3

    print("  PASSED\n")


if __name__ == "__main__":
    test_breakdown_on_bad_terrain()
    test_no_breakdown_on_clear()
    test_non_motorized_exempt()
    test_breakdown_sp_loss_armor_first()
    test_breakdown_cohesion_penalty()
    test_phase_execution()
    test_dice_override()
    test_threshold_adjustment()
    print("=" * 60)
    print("ALL BREAKDOWN TESTS PASSED")
    print("=" * 60)
