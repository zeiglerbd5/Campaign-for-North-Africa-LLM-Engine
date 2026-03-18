"""
CNA Engine — Infrastructure Effects Tests
Railroad movement, water pipeline, SGSU airfield check, fort shifts, scenario infrastructure.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.scenario import load_operation_compass
from cna_engine.engine.movement import compute_hex_entry_cost, validate_move, _get_hex_state
from cna_engine.engine.supply import draw_water_from_terrain
from cna_engine.models.game_state import GameState, Unit, HexState, TOEStrength, UnitSupply
from cna_engine.models.enums import (
    Side, UnitClass, UnitSize, UnitStatus, MotorizationType,
    TerrainType, RoadType,
)
from cna_engine.data.reference_data import ReferenceData


def _make_test_unit(uid="test_inf", side=Side.ALLIED, motorized=False, unit_class=UnitClass.INFANTRY,
                    hex_id="D1022", base_cpa=6):
    mot = MotorizationType.MOTORIZED if motorized else MotorizationType.NON_MOTORIZED
    return Unit(
        id=uid, name=f"Test {uid}", side=side, nationality="british",
        unit_class=unit_class, unit_size=UnitSize.BATTALION,
        motorization=mot, hex_id=hex_id, base_cpa=base_cpa,
        stacking_points=2,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
        supply=UnitSupply(fuel=10, water=10, ammo=10, stores=5,
                          fuel_capacity=20, water_capacity=20,
                          ammo_capacity=20, stores_capacity=10),
    )


def test_railroad_cost_non_motorized():
    print("=" * 60)
    print("TEST: Railroad cost for non-motorized (0.25 CP)")
    print("=" * 60)

    state = GameState()
    ref = ReferenceData()
    # Create two adjacent hexes, destination has operational railroad
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.CLEAR, road=RoadType.RAILROAD,
                                     railroad_operational=True)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR, road=RoadType.NONE)

    unit = _make_test_unit(motorized=False, hex_id="D0922")
    state.units[unit.id] = unit

    cost = compute_hex_entry_cost(state, ref, unit, "D0922", "D1022")
    assert cost.effective_cost == 0.25, f"Expected 0.25, got {cost.effective_cost}"
    assert cost.used_road is True
    print(f"  Non-motorized railroad cost: {cost.effective_cost} CP — PASSED")


def test_railroad_ignored_for_motorized():
    print("=" * 60)
    print("TEST: Railroad ignored for motorized (normal terrain)")
    print("=" * 60)

    state = GameState()
    ref = ReferenceData()
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.CLEAR, road=RoadType.RAILROAD,
                                     railroad_operational=True)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR, road=RoadType.NONE)

    unit = _make_test_unit(motorized=True, hex_id="D0922")
    state.units[unit.id] = unit

    cost = compute_hex_entry_cost(state, ref, unit, "D0922", "D1022")
    # Motorized should get normal terrain cost, not railroad
    assert cost.effective_cost != 0.25, f"Motorized should not get railroad cost, got {cost.effective_cost}"
    assert cost.used_road is False
    print(f"  Motorized cost on railroad hex: {cost.effective_cost} CP (not 0.25) — PASSED")


def test_water_pipeline_allows_draw():
    print("=" * 60)
    print("TEST: Water pipeline allows draw_water")
    print("=" * 60)

    state, _ = load_operation_compass()
    # D1822 has water pipeline but is COASTAL terrain (not oasis/bir)
    hs = state.hexes["D1822"]
    assert hs.has_water_pipeline is True

    # Place a unit there
    unit = _make_test_unit(hex_id="D1822")
    state.units[unit.id] = unit
    hs.allied_unit_ids.append(unit.id)

    result = draw_water_from_terrain(state, unit.id, 5.0)
    assert result.success, f"Expected success, got: {result.blocked_reason}"
    print(f"  Water pipeline draw: {result.description} — PASSED")


def test_sgsu_blocked_from_non_airfield():
    print("=" * 60)
    print("TEST: SGSU blocked from non-airfield hex")
    print("=" * 60)

    state = GameState()
    ref = ReferenceData()
    # Origin has airfield, destination does not
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.CLEAR, road=RoadType.ROAD,
                                     has_airfield=True)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR, road=RoadType.ROAD)

    unit = _make_test_unit(uid="test_sgsu", unit_class=UnitClass.SGSU, hex_id="D1022",
                           motorized=True, base_cpa=10)
    state.units[unit.id] = unit

    validation = validate_move(state, ref, unit.id, ["D1022", "D0922"])
    assert not validation.is_valid
    assert "airfield" in validation.blocked_reason.lower()
    print(f"  SGSU blocked: {validation.blocked_reason} — PASSED")


def test_fort_shifts_in_scenario():
    print("=" * 60)
    print("TEST: Fort shifts applied in scenario hexes")
    print("=" * 60)

    state, _ = load_operation_compass()

    # Check fort levels set correctly
    assert state.hexes["C1714"].fort_level == 1, "Sollum should be fort level 1"
    assert state.hexes["C1215"].fort_level == 2, "Bardia should be fort level 2"
    assert state.hexes["C0512"].fort_level == 3, "Tobruk should be fort level 3"
    print(f"  Sollum fort={state.hexes['C1714'].fort_level}")
    print(f"  Bardia fort={state.hexes['C1215'].fort_level}")
    print(f"  Tobruk fort={state.hexes['C0512'].fort_level}")
    print("  PASSED")


def test_scenario_infrastructure():
    print("=" * 60)
    print("TEST: Scenario hexes have infrastructure set")
    print("=" * 60)

    state, _ = load_operation_compass()

    # Alexandria
    alex = state.hexes["E1326"]
    assert alex.railroad_operational is True
    assert alex.has_water_pipeline is True
    assert alex.has_airfield is True
    print(f"  Alexandria: railroad={alex.railroad_operational}, pipeline={alex.has_water_pipeline}, airfield={alex.has_airfield}")

    # Mersa Matruh
    matruh = state.hexes["D1822"]
    assert matruh.has_airfield is True
    assert matruh.has_water_pipeline is True
    print(f"  Mersa Matruh: airfield={matruh.has_airfield}, pipeline={matruh.has_water_pipeline}")

    # Sidi Barrani
    barrani = state.hexes["D0821"]
    assert barrani.has_air_landing_strip is True
    print(f"  Sidi Barrani: landing_strip={barrani.has_air_landing_strip}")

    # Pipeline hex (Mersa Matruh)
    pipeline = state.hexes["D1822"]
    assert pipeline.has_water_pipeline is True
    print(f"  D1822 pipeline: {pipeline.has_water_pipeline}")

    # Tobruk airfield
    tobruk = state.hexes["C0512"]
    assert tobruk.has_airfield is True
    print(f"  Tobruk: airfield={tobruk.has_airfield}, fort={tobruk.fort_level}")

    # Benghazi airfield
    assert state.hexes["B1403"].has_airfield is True
    print(f"  Benghazi: airfield={state.hexes['B1403'].has_airfield}")

    print("  PASSED")


def test_engineer_and_truck_units_in_oob():
    print("=" * 60)
    print("TEST: Engineer and truck units in OOB")
    print("=" * 60)

    state, _ = load_operation_compass()

    # Engineers
    assert "cw_8fd_engr" in state.units
    engr = state.units["cw_8fd_engr"]
    assert engr.unit_class == UnitClass.ENGINEER
    assert engr.side == Side.ALLIED
    print(f"  Allied engineer: {engr.name} @ {engr.hex_id}")

    assert "it_eng_co" in state.units
    it_engr = state.units["it_eng_co"]
    assert it_engr.unit_class == UnitClass.ENGINEER
    assert it_engr.side == Side.AXIS
    print(f"  Axis engineer: {it_engr.name} @ {it_engr.hex_id}")

    # Trucks
    assert "cw_rasc_trucks" in state.units
    trucks = state.units["cw_rasc_trucks"]
    assert trucks.unit_class == UnitClass.TRUCK
    assert trucks.side == Side.ALLIED
    print(f"  Allied trucks: {trucks.name} @ {trucks.hex_id}")

    assert "it_autogrp" in state.units
    it_trucks = state.units["it_autogrp"]
    assert it_trucks.unit_class == UnitClass.TRUCK
    assert it_trucks.side == Side.AXIS
    print(f"  Axis trucks: {it_trucks.name} @ {it_trucks.hex_id}")

    print("  PASSED")


def main():
    test_railroad_cost_non_motorized()
    test_railroad_ignored_for_motorized()
    test_water_pipeline_allows_draw()
    test_sgsu_blocked_from_non_airfield()
    test_fort_shifts_in_scenario()
    test_scenario_infrastructure()
    test_engineer_and_truck_units_in_oob()
    print("\n" + "=" * 60)
    print("ALL INFRASTRUCTURE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
