"""
CNA Engine — Vehicle Repair Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.repair import (
    attempt_repair, execute_vehicle_repair_phase,
    FACILITY_REPAIR_THRESHOLD, FIELD_REPAIR_THRESHOLD,
    REPAIR_CPA_COST,
)
from cna_engine.engine.movement import get_neighbors
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, MotorizationType,
)


def _make_mech_unit(uid, hex_id="D0821", strength=8, toe=12, side=Side.ALLIED):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="armor", unit_size="battalion",
        motorization=MotorizationType.MECHANIZED, hex_id=hex_id,
        base_cpa=10, stacking_points=3,
        current_strength=TOEStrength(armor=strength),
        toe_strength=TOEStrength(armor=toe),
    )


def _make_infantry(uid, hex_id="D0821", strength=8, toe=10, side=Side.ALLIED):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id=hex_id,
        base_cpa=6, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=toe),
    )


def test_repair_at_facility():
    print("=" * 60)
    print("TEST 1: Repair at Facility")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)

    # Hex with repair facility
    state.hexes["D0821"] = HexState(
        hex_id="D0821", terrain=TerrainType.CLEAR,
        has_repair_facility=True,
    )

    unit = _make_mech_unit("u1", hex_id="D0821", strength=8, toe=12)
    state.units["u1"] = unit

    # Roll ≤ 4 → repair succeeds
    r = attempt_repair(state, "u1", dice_roll=3)
    assert r.success
    assert r.has_facility
    assert r.threshold == FACILITY_REPAIR_THRESHOLD
    assert r.strength_restored == 1
    assert unit.current_strength.armor == 9  # 8 + 1
    assert unit.current_cpa_spent == REPAIR_CPA_COST
    print(f"  Facility repair: {r.description}")

    # Roll > 4 → repair fails
    unit2 = _make_mech_unit("u2", hex_id="D0821", strength=6, toe=12)
    state.units["u2"] = unit2
    r2 = attempt_repair(state, "u2", dice_roll=5)
    assert not r2.success
    assert unit2.current_strength.armor == 6  # Unchanged
    print(f"  Failed: {r2.description}")

    print("  PASSED\n")


def test_field_repair():
    print("=" * 60)
    print("TEST 2: Field Repair (No Facility)")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.CLEAR)

    unit = _make_mech_unit("u1", hex_id="D1022", strength=8, toe=12)
    state.units["u1"] = unit

    # Field repair needs roll ≤ 2
    r = attempt_repair(state, "u1", dice_roll=2)
    assert r.success
    assert not r.has_facility
    assert r.threshold == FIELD_REPAIR_THRESHOLD
    print(f"  Field repair: {r.description}")

    # Roll 3 → fails
    unit2 = _make_mech_unit("u2", hex_id="D1022", strength=8, toe=12)
    state.units["u2"] = unit2
    r2 = attempt_repair(state, "u2", dice_roll=3)
    assert not r2.success
    print(f"  Failed: {r2.description}")

    print("  PASSED\n")


def test_adjacent_facility():
    print("=" * 60)
    print("TEST 3: Adjacent Repair Facility")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)

    # Unit at D0821, facility at adjacent hex
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    adj = get_neighbors("D0821")[0]
    state.hexes[adj] = HexState(hex_id=adj, terrain=TerrainType.CLEAR,
                                 has_repair_facility=True)

    unit = _make_mech_unit("u1", hex_id="D0821", strength=8, toe=12)
    state.units["u1"] = unit

    r = attempt_repair(state, "u1", dice_roll=3)
    assert r.has_facility  # Should detect adjacent facility
    assert r.threshold == FACILITY_REPAIR_THRESHOLD
    print(f"  Adjacent facility: {r.description}")

    print("  PASSED\n")


def test_repair_validation():
    print("=" * 60)
    print("TEST 4: Repair Validation")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)

    # Non-motorized unit can't repair
    inf = _make_infantry("inf1", hex_id="D0821")
    state.units["inf1"] = inf
    r = attempt_repair(state, "inf1")
    assert not r.success
    assert "not" in r.blocked_reason.lower()
    print(f"  Non-motorized: {r.description}")

    # Full strength unit can't repair
    full = _make_mech_unit("full", hex_id="D0821", strength=12, toe=12)
    state.units["full"] = full
    r2 = attempt_repair(state, "full")
    assert not r2.success
    assert "full strength" in r2.blocked_reason.lower()
    print(f"  Full strength: {r2.description}")

    # Destroyed unit can't repair
    dead = _make_mech_unit("dead", hex_id="D0821", strength=5, toe=12)
    dead.status = UnitStatus.DESTROYED
    state.units["dead"] = dead
    r3 = attempt_repair(state, "dead")
    assert not r3.success
    print(f"  Destroyed: {r3.description}")

    # Unit not found
    r4 = attempt_repair(state, "missing")
    assert not r4.success
    print(f"  Not found: {r4.description}")

    # Insufficient CPA
    tired = _make_mech_unit("tired", hex_id="D0821", strength=8, toe=12)
    tired.current_cpa_spent = tired.max_cpa_this_stage  # All spent
    state.units["tired"] = tired
    r5 = attempt_repair(state, "tired")
    assert not r5.success
    assert "CPA" in r5.blocked_reason.upper()
    print(f"  No CPA: {r5.description}")

    print("  PASSED\n")


def test_repair_phase():
    print("=" * 60)
    print("TEST 5: Vehicle Repair Phase")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR,
                                     has_repair_facility=True)

    # Two damaged mech units
    u1 = _make_mech_unit("u1", hex_id="D0821", strength=8, toe=12)
    u2 = _make_mech_unit("u2", hex_id="D0821", strength=6, toe=12)
    # One full strength (shouldn't be attempted)
    u3 = _make_mech_unit("u3", hex_id="D0821", strength=12, toe=12)
    # One infantry (shouldn't be attempted)
    inf = _make_infantry("inf", hex_id="D0821")

    state.units.update({"u1": u1, "u2": u2, "u3": u3, "inf": inf})

    r = execute_vehicle_repair_phase(state, Side.ALLIED)
    assert r.units_attempted == 2  # u1 and u2 only
    print(f"  Phase: {r.description}")
    for rr in r.results:
        print(f"    {rr.description}")

    print("  PASSED\n")


def main():
    test_repair_at_facility()
    test_field_repair()
    test_adjacent_facility()
    test_repair_validation()
    test_repair_phase()
    print("=" * 60)
    print("ALL REPAIR TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
