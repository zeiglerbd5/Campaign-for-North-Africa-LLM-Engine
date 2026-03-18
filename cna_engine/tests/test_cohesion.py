"""
CNA Engine — Morale / Cohesion Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.organization import (
    apply_cohesion_changes, check_surrenders, execute_organization_phase,
    COHESION_DEGRADE_THRESHOLD, COHESION_RECOVERY_AMOUNT,
)
from cna_engine.engine.movement import validate_move, get_neighbors
from cna_engine.models.game_state import GameState, Unit, HexState, TurnState, TOEStrength, UnitSupply
from cna_engine.models.enums import (
    Side, UnitClass, UnitSize, UnitStatus, MotorizationType,
    TerrainType, RoadType, GamePhase,
)
from cna_engine.data.reference_data import ReferenceData


def _make_state_with_unit(uid="test_u", side=Side.ALLIED, hex_id="D0821",
                          base_cpa=6, cohesion=0, cpa_spent=0,
                          water=10.0, ammo=10.0, status=UnitStatus.ACTIVE,
                          is_in_contact=False):
    state = GameState()
    state.turn = TurnState(game_turn=1, op_stage=1, phase=GamePhase.OP_STAGE)
    state.hexes[hex_id] = HexState(hex_id=hex_id, terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    unit = Unit(
        id=uid, name=f"Test Unit {uid}", side=side, nationality="british",
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.NON_MOTORIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=2, cohesion=cohesion,
        current_cpa_spent=cpa_spent, status=status,
        is_in_contact=is_in_contact,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
        supply=UnitSupply(fuel=10, water=water, ammo=ammo, stores=5,
                          fuel_capacity=20, water_capacity=20,
                          ammo_capacity=20, stores_capacity=10),
    )
    state.units[uid] = unit
    if hex_id in state.hexes:
        if side == Side.ALLIED:
            state.hexes[hex_id].allied_unit_ids.append(uid)
        else:
            state.hexes[hex_id].axis_unit_ids.append(uid)
    return state, unit


def test_degradation_proportional_to_cpa():
    print("=" * 60)
    print("TEST: Cohesion degradation proportional to CPA spent")
    print("=" * 60)

    # Threshold is 6: 12 CPA // 6 = -2 cohesion
    state, unit = _make_state_with_unit(cpa_spent=12, cohesion=0)
    results = apply_cohesion_changes(state)
    assert len(results) == 1
    assert unit.cohesion == -2, f"Expected -2, got {unit.cohesion}"
    print(f"  12 CPA spent → cohesion {results[0].old_cohesion} → {unit.cohesion} — PASSED")

    # Reset and test with 5 CPA (below threshold of 6)
    unit.cohesion = 0
    unit.current_cpa_spent = 5
    results = apply_cohesion_changes(state)
    assert unit.cohesion == 0, f"Expected 0 (5 < threshold 6), got {unit.cohesion}"
    print(f"  5 CPA spent → cohesion stays 0 — PASSED")


def test_recovery_when_rested():
    print("=" * 60)
    print("TEST: Recovery when rested (caps at 0)")
    print("=" * 60)

    state, unit = _make_state_with_unit(cpa_spent=0, cohesion=-3)
    results = apply_cohesion_changes(state)
    assert unit.cohesion == -2, f"Expected -2 (recovery +1), got {unit.cohesion}"
    print(f"  Rested with cohesion -3 → {unit.cohesion} — PASSED")

    # Already at 0 — should not go positive
    unit.cohesion = 0
    unit.current_cpa_spent = 0
    results = apply_cohesion_changes(state)
    assert unit.cohesion == 0, f"Expected 0 (cap), got {unit.cohesion}"
    print(f"  Rested at 0 → stays 0 — PASSED")


def test_supply_stress_penalty():
    print("=" * 60)
    print("TEST: Supply stress penalty (<10% water/ammo, capped -1/OpStage)")
    print("=" * 60)

    # Water below 10% (< 2.0 of 20.0 capacity) AND ammo below 10%
    # Combined penalty capped at -1 per OpStage
    state, unit = _make_state_with_unit(cpa_spent=0, cohesion=0, water=1.0, ammo=1.0)
    results = apply_cohesion_changes(state)
    assert unit.cohesion == -1, f"Expected -1 (supply penalty capped at 1/OpStage), got {unit.cohesion}"
    print(f"  Low water + low ammo → cohesion {unit.cohesion} (capped at -1) — PASSED")

    # At exactly 10% (2.0 / 20.0) — no penalty (must be strictly below)
    state2, unit2 = _make_state_with_unit(cpa_spent=0, cohesion=0, water=2.0, ammo=2.0)
    results2 = apply_cohesion_changes(state2)
    assert unit2.cohesion == 0, f"Expected 0 (at 10% threshold, no penalty), got {unit2.cohesion}"
    print(f"  Water/ammo at 10% → cohesion stays 0 — PASSED")


def test_surrender_at_minus_17_in_contact():
    print("=" * 60)
    print("TEST: Surrender at -17 in contact")
    print("=" * 60)

    state, unit = _make_state_with_unit(cohesion=-17, is_in_contact=True)
    surrendered = check_surrenders(state)
    assert unit.id in surrendered, f"Unit should have surrendered"
    assert unit.status == UnitStatus.SURRENDERED
    print(f"  Cohesion -17 + in_contact → SURRENDERED — PASSED")


def test_surrender_at_minus_26_with_ezoc():
    print("=" * 60)
    print("TEST: Surrender at -26 with EZOC")
    print("=" * 60)

    state, unit = _make_state_with_unit(side=Side.ALLIED, hex_id="D0821", cohesion=-26)

    # Place an enemy unit adjacent to create EZOC
    enemy_hex = get_neighbors("D0821")[0]
    state.hexes[enemy_hex] = HexState(hex_id=enemy_hex, terrain=TerrainType.CLEAR)
    enemy = Unit(
        id="enemy_1", name="Enemy Unit", side=Side.AXIS, nationality="italian",
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.NON_MOTORIZED, hex_id=enemy_hex,
        base_cpa=6, stacking_points=2,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
    )
    state.units["enemy_1"] = enemy
    state.hexes[enemy_hex].axis_unit_ids.append("enemy_1")

    surrendered = check_surrenders(state)
    assert unit.id in surrendered, f"Unit should have surrendered at -26 in EZOC"
    assert unit.status == UnitStatus.SURRENDERED
    print(f"  Cohesion -26 + in EZOC → SURRENDERED — PASSED")


def test_no_surrender_at_minus_17_without_contact():
    print("=" * 60)
    print("TEST: No surrender at -17 without contact")
    print("=" * 60)

    state, unit = _make_state_with_unit(cohesion=-17, is_in_contact=False)
    surrendered = check_surrenders(state)
    assert unit.id not in surrendered, f"Unit should NOT surrender at -17 without contact"
    assert unit.status == UnitStatus.ACTIVE
    print(f"  Cohesion -17, no contact → stays ACTIVE — PASSED")


def test_movement_blocked_at_minus_26():
    print("=" * 60)
    print("TEST: Movement blocked at -26")
    print("=" * 60)

    state = GameState()
    ref = ReferenceData()
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    state.hexes["D0921"] = HexState(hex_id="D0921", terrain=TerrainType.CLEAR, road=RoadType.ROAD)

    unit = Unit(
        id="test_blocked", name="Demoralized Unit", side=Side.ALLIED, nationality="british",
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D0821",
        base_cpa=6, stacking_points=2, cohesion=-26,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
    )
    state.units["test_blocked"] = unit

    validation = validate_move(state, ref, "test_blocked", ["D0821", "D0921"])
    assert not validation.is_valid
    assert "cohesion" in validation.blocked_reason.lower()
    print(f"  Cohesion -26 → move blocked: {validation.blocked_reason} — PASSED")


def main():
    test_degradation_proportional_to_cpa()
    test_recovery_when_rested()
    test_supply_stress_penalty()
    test_surrender_at_minus_17_in_contact()
    test_surrender_at_minus_26_with_ezoc()
    test_no_surrender_at_minus_17_without_contact()
    test_movement_blocked_at_minus_26()
    print("\n" + "=" * 60)
    print("ALL COHESION TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
