"""
CNA Engine — Supply & Logistics Tests
Tests fuel consumption, ammo expenditure, supply status, evaporation,
truck operations, dump operations, water drawing, stores expenditure,
and a "Tobruk Supply Run" mini scenario.
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.supply import (
    consume_fuel, expend_ammo, check_supply_status,
    apply_evaporation_to_unit, apply_evaporation_to_dump,
    execute_truck_attach, execute_truck_detach,
    execute_truck_load, execute_truck_unload,
    create_supply_dump, draw_from_dump, draw_water_from_terrain,
    execute_stores_expenditure,
    AMMO_COST, BASELINE_WATER_PER_GT, BASELINE_STORES_PER_GT,
    ATTACH_TRUCK_COST, DETACH_TRUCK_COST, LOAD_UNLOAD_COST,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TOEStrength, UnitSupply, SupplyDump,
)
from cna_engine.models.enums import (
    Side, UnitStatus, RoadType, TerrainType, MotorizationType,
)
from cna_engine.data.reference_data import ReferenceData, TerrainInfo


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

def _make_ref() -> ReferenceData:
    """Minimal ReferenceData for supply tests."""
    from cna_engine.data.reference_data import EvaporationRate
    ref = ReferenceData()
    ref.terrain = {
        "clear": TerrainInfo(
            terrain_type="Clear", motorized_cp=2, non_motorized_cp=2,
            track_effect="Halve", barrage_shift="—", anti_armor_shift="—",
            close_assault_shift="—", stacking_limit="10 SP",
        ),
    }
    ref.ammo_consumption = dict(AMMO_COST)
    ref.evaporation_rates = [
        EvaporationRate(period="Pre-jerrycan", gt_threshold=1,
                        allied_rate=0.09, axis_rate=0.03),
        EvaporationRate(period="Post-jerrycan", gt_threshold=47,
                        allied_rate=0.06, axis_rate=0.03),
    ]
    return ref


def _make_unit(
    uid: str = "test_unit",
    name: str = "Test Unit",
    side: str = Side.ALLIED,
    hex_id: str = "D0821",
    base_cpa: int = 10,
    motorization: str = MotorizationType.MOTORIZED,
    fuel: float = 10.0,
    water: float = 10.0,
    ammo: float = 10.0,
    stores: float = 10.0,
    fuel_cap: float = 20.0,
    water_cap: float = 20.0,
    ammo_cap: float = 20.0,
    stores_cap: float = 20.0,
    truck_points: int = 0,
    truck_fuel: float = 0.0,
    truck_water: float = 0.0,
    truck_ammo: float = 0.0,
    truck_stores: float = 0.0,
) -> Unit:
    return Unit(
        id=uid, name=name, side=side,
        nationality="british", unit_class="infantry",
        unit_size="battalion", motorization=motorization,
        hex_id=hex_id, base_cpa=base_cpa,
        stacking_points=2,
        current_strength=TOEStrength(infantry=10),
        toe_strength=TOEStrength(infantry=10),
        supply=UnitSupply(
            fuel=fuel, water=water, ammo=ammo, stores=stores,
            fuel_capacity=fuel_cap, water_capacity=water_cap,
            ammo_capacity=ammo_cap, stores_capacity=stores_cap,
        ),
        attached_truck_points=truck_points,
        truck_cargo_fuel=truck_fuel,
        truck_cargo_water=truck_water,
        truck_cargo_ammo=truck_ammo,
        truck_cargo_stores=truck_stores,
    )


def _make_state(*hex_ids):
    """Create a GameState with given hexes (all clear)."""
    state = GameState()
    for hid in hex_ids:
        state.hexes[hid] = HexState(hex_id=hid, terrain=TerrainType.CLEAR)
    return state


# ════════════════════════════════════════
# TEST 1: FUEL CONSUMPTION
# ════════════════════════════════════════

def test_fuel_consumption():
    print("=" * 60)
    print("TEST 1: Fuel Consumption")
    print("=" * 60)

    ref = _make_ref()

    # Basic formula: 4 CPs * rate 1 * 0.2 = 0.8 fuel
    state = _make_state("D0821")
    unit = _make_unit(uid="u1", fuel=10.0)
    state.units["u1"] = unit

    result = consume_fuel(state, ref, "u1", cps_expended=4, fuel_rate=1)
    assert result.fuel_consumed == 0.8, f"Expected 0.8, got {result.fuel_consumed}"
    assert result.from_internal == 0.8
    assert result.from_truck_cargo == 0.0
    assert result.deficit == 0.0
    assert unit.supply.fuel == 10.0 - 0.8
    print(f"  Basic: {result.description}")

    # Higher fuel rate: 6 CPs * rate 3 * 0.2 = 3.6
    state2 = _make_state("D0821")
    unit2 = _make_unit(uid="u2", fuel=5.0, truck_fuel=5.0, truck_points=1)
    state2.units["u2"] = unit2

    result2 = consume_fuel(state2, ref, "u2", cps_expended=6, fuel_rate=3)
    assert result2.fuel_consumed == 3.6
    assert result2.from_internal == 3.6  # 5.0 available, only need 3.6
    assert result2.from_truck_cargo == 0.0
    print(f"  Higher rate: {result2.description}")

    # Truck spillover: internal has 1.0, need 2.0, truck has 5.0
    state3 = _make_state("D0821")
    unit3 = _make_unit(uid="u3", fuel=1.0, truck_fuel=5.0, truck_points=1)
    state3.units["u3"] = unit3

    result3 = consume_fuel(state3, ref, "u3", cps_expended=10, fuel_rate=1)
    assert result3.fuel_consumed == 2.0  # 10*1*0.2
    assert result3.from_internal == 1.0
    assert result3.from_truck_cargo == 1.0
    assert result3.deficit == 0.0
    assert unit3.supply.fuel == 0.0
    assert unit3.truck_cargo_fuel == 4.0
    print(f"  Spillover: {result3.description}")

    # Zero fuel remaining — runs dry
    state4 = _make_state("D0821")
    unit4 = _make_unit(uid="u4", fuel=0.5, truck_fuel=0.0)
    state4.units["u4"] = unit4

    result4 = consume_fuel(state4, ref, "u4", cps_expended=10, fuel_rate=1)
    assert result4.fuel_consumed == 2.0
    assert result4.from_internal == 0.5
    assert result4.deficit == 1.5
    assert "deficit" in result4.description.lower() or "WARNING" in result4.description
    print(f"  Dry: {result4.description}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 2: AMMO EXPENDITURE
# ════════════════════════════════════════

def test_ammo_expenditure():
    print("=" * 60)
    print("TEST 2: Ammo Expenditure")
    print("=" * 60)

    # Barrage: cost=4 per action
    state = _make_state("D0821")
    unit = _make_unit(uid="u1", ammo=10.0)
    state.units["u1"] = unit

    result = expend_ammo(state, "u1", "barrage", count=1)
    assert result.ammo_cost == 4
    assert result.from_internal == 4.0
    assert not result.insufficient
    assert unit.supply.ammo == 6.0
    print(f"  Barrage: {result.description}")

    # Anti-armor: cost=3
    result2 = expend_ammo(state, "u1", "anti_armor", count=1)
    assert result2.ammo_cost == 3
    assert unit.supply.ammo == 3.0
    print(f"  Anti-armor: {result2.description}")

    # Close assault: cost=2
    result3 = expend_ammo(state, "u1", "close_assault", count=1)
    assert result3.ammo_cost == 2
    assert unit.supply.ammo == 1.0
    print(f"  Close assault: {result3.description}")

    # Truck spillover: 1.0 internal + truck
    state2 = _make_state("D0821")
    unit2 = _make_unit(uid="u2", ammo=1.0, truck_ammo=10.0, truck_points=1)
    state2.units["u2"] = unit2

    result4 = expend_ammo(state2, "u2", "barrage", count=1)
    assert result4.ammo_cost == 4
    assert result4.from_internal == 1.0
    assert result4.from_truck_cargo == 3.0
    assert not result4.insufficient
    print(f"  Truck spillover: {result4.description}")

    # Insufficient ammo
    state3 = _make_state("D0821")
    unit3 = _make_unit(uid="u3", ammo=1.0, truck_ammo=0.0)
    state3.units["u3"] = unit3

    result5 = expend_ammo(state3, "u3", "barrage", count=1)
    assert result5.insufficient
    assert result5.ammo_cost == 4
    print(f"  Insufficient: {result5.description}")

    # Count > 1: barrage x2 = 8 ammo
    state4 = _make_state("D0821")
    unit4 = _make_unit(uid="u4", ammo=10.0)
    state4.units["u4"] = unit4

    result6 = expend_ammo(state4, "u4", "barrage", count=2)
    assert result6.ammo_cost == 8
    assert unit4.supply.ammo == 2.0
    print(f"  Barrage x2: {result6.description}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 3: SUPPLY STATUS
# ════════════════════════════════════════

def test_supply_status():
    print("=" * 60)
    print("TEST 3: Supply Status")
    print("=" * 60)

    # Full unit: 10/20 = 50% for each
    state = _make_state("D0821")
    unit = _make_unit(uid="u1", fuel=10.0, water=10.0, ammo=10.0, stores=10.0)
    state.units["u1"] = unit

    status = check_supply_status(state, "u1")
    assert status.fuel_pct == 50.0
    assert status.water_pct == 50.0
    assert status.ammo_pct == 50.0
    assert status.stores_pct == 50.0
    assert not status.is_fuel_critical
    assert not status.is_water_critical
    assert not status.is_ammo_critical
    print(f"  50% full: {status.description}")

    # Critical unit: 4/20 = 20% (< 25%)
    state2 = _make_state("D0821")
    unit2 = _make_unit(uid="u2", fuel=4.0, water=4.0, ammo=4.0, stores=4.0)
    state2.units["u2"] = unit2

    status2 = check_supply_status(state2, "u2")
    assert status2.fuel_pct == 20.0
    assert status2.is_fuel_critical
    assert status2.is_water_critical
    assert status2.is_ammo_critical
    print(f"  20% (critical): {status2.description}")

    # Edge: exactly 25% — NOT critical (< 25)
    state3 = _make_state("D0821")
    unit3 = _make_unit(uid="u3", fuel=5.0, fuel_cap=20.0)
    state3.units["u3"] = unit3

    status3 = check_supply_status(state3, "u3")
    assert status3.fuel_pct == 25.0
    assert not status3.is_fuel_critical
    print(f"  Exactly 25%: not critical")

    # Truck cargo included in status
    state4 = _make_state("D0821")
    unit4 = _make_unit(uid="u4", truck_fuel=5.0, truck_water=3.0, truck_points=2)
    state4.units["u4"] = unit4

    status4 = check_supply_status(state4, "u4")
    assert status4.truck_cargo_fuel == 5.0
    assert status4.truck_cargo_water == 3.0
    assert status4.attached_truck_points == 2
    print(f"  Truck cargo reported: fuel={status4.truck_cargo_fuel}, tp={status4.attached_truck_points}")

    # Zero capacity → 100% (not applicable)
    state5 = _make_state("D0821")
    unit5 = _make_unit(uid="u5", fuel=0.0, fuel_cap=0.0)
    state5.units["u5"] = unit5

    status5 = check_supply_status(state5, "u5")
    assert status5.fuel_pct == 100.0
    print(f"  Zero capacity: treated as 100%")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 4: EVAPORATION (UNIT)
# ════════════════════════════════════════

def test_evaporation_unit():
    print("=" * 60)
    print("TEST 4: Evaporation (Unit)")
    print("=" * 60)

    # NJH rule: internal fuel EXEMPT from evaporation
    unit = _make_unit(
        uid="u1", fuel=100.0, water=100.0, ammo=100.0, stores=100.0,
        truck_fuel=50.0, truck_water=50.0, truck_ammo=50.0, truck_stores=50.0,
        truck_points=2,
    )
    rate = 0.09  # 9% Allied

    result = apply_evaporation_to_unit(unit, rate, njh_fuel_in_tanks_no_evap=True)

    # Fuel: only truck cargo evaporates → 50 * 0.09 = 4.5
    assert result.fuel_lost == 4.5, f"Expected 4.5 fuel lost, got {result.fuel_lost}"
    assert unit.supply.fuel == 100.0, "Internal fuel should be unchanged (NJH)"
    assert abs(unit.truck_cargo_fuel - 45.5) < 0.01
    print(f"  NJH fuel exempt: internal={unit.supply.fuel}, truck={unit.truck_cargo_fuel}")

    # Water: both evaporate → (100*0.09) + (50*0.09) = 9 + 4.5 = 13.5
    assert abs(result.water_lost - 13.5) < 0.01
    assert abs(unit.supply.water - 91.0) < 0.01
    assert abs(unit.truck_cargo_water - 45.5) < 0.01
    print(f"  Water: lost={result.water_lost}, internal={unit.supply.water}, truck={unit.truck_cargo_water}")

    # Ammo: both evaporate → 13.5
    assert abs(result.ammo_lost - 13.5) < 0.01
    print(f"  Ammo: lost={result.ammo_lost}")

    # Stores: both evaporate → 13.5
    assert abs(result.stores_lost - 13.5) < 0.01
    print(f"  Stores: lost={result.stores_lost}")

    # Without NJH rule: internal fuel also evaporates
    unit2 = _make_unit(
        uid="u2", fuel=100.0, water=100.0, ammo=100.0, stores=100.0,
        truck_fuel=50.0, truck_points=1,
    )

    result2 = apply_evaporation_to_unit(unit2, rate, njh_fuel_in_tanks_no_evap=False)

    # Fuel: (100*0.09) + (50*0.09) = 9 + 4.5 = 13.5
    assert abs(result2.fuel_lost - 13.5) < 0.01
    assert abs(unit2.supply.fuel - 91.0) < 0.01
    assert abs(unit2.truck_cargo_fuel - 45.5) < 0.01
    print(f"  No NJH: fuel_lost={result2.fuel_lost}, internal={unit2.supply.fuel}")

    # Axis rate: 3%
    unit3 = _make_unit(uid="u3", side=Side.AXIS, fuel=100.0, truck_fuel=100.0, truck_points=1)
    result3 = apply_evaporation_to_unit(unit3, 0.03, njh_fuel_in_tanks_no_evap=True)
    assert result3.fuel_lost == 3.0  # Only truck: 100 * 0.03
    print(f"  Axis (3%): fuel_lost={result3.fuel_lost}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 5: EVAPORATION (DUMP)
# ════════════════════════════════════════

def test_evaporation_dump():
    print("=" * 60)
    print("TEST 5: Evaporation (Dump)")
    print("=" * 60)

    dump = SupplyDump(
        id="dump1", side=Side.ALLIED, is_real=True,
        fuel=100.0, water=80.0, ammo=60.0, stores=40.0,
    )

    result = apply_evaporation_to_dump(dump, 0.09)

    assert result.fuel_lost == 9.0
    assert result.water_lost == 7.2
    assert abs(result.ammo_lost - 5.4) < 0.01
    assert abs(result.stores_lost - 3.6) < 0.01

    assert dump.fuel == 91.0
    assert abs(dump.water - 72.8) < 0.01
    assert abs(dump.ammo - 54.6) < 0.01
    assert abs(dump.stores - 36.4) < 0.01

    print(f"  {result.description}")
    print(f"  After: fuel={dump.fuel} water={dump.water} ammo={dump.ammo} stores={dump.stores}")

    # Axis dump at 3%
    dump2 = SupplyDump(id="dump2", side=Side.AXIS, is_real=True, fuel=200.0)
    result2 = apply_evaporation_to_dump(dump2, 0.03)
    assert result2.fuel_lost == 6.0
    assert dump2.fuel == 194.0
    print(f"  Axis (3%): {result2.description}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 6: TRUCK OPERATIONS
# ════════════════════════════════════════

def test_truck_operations():
    print("=" * 60)
    print("TEST 6: Truck Operations")
    print("=" * 60)

    # Attach truck points
    state = _make_state("D0821")
    unit = _make_unit(uid="u1", base_cpa=10)
    state.units["u1"] = unit

    result = execute_truck_attach(state, "u1", truck_points=2)
    assert result.success
    assert unit.attached_truck_points == 2
    assert unit.current_cpa_spent == ATTACH_TRUCK_COST
    print(f"  Attach: {result.description}")

    # Detach 1 truck point
    result2 = execute_truck_detach(state, "u1", truck_points=1)
    assert result2.success
    assert unit.attached_truck_points == 1
    assert unit.current_cpa_spent == ATTACH_TRUCK_COST + DETACH_TRUCK_COST
    print(f"  Detach: {result2.description}")

    # Detach too many
    result3 = execute_truck_detach(state, "u1", truck_points=5)
    assert not result3.success
    assert "cannot detach" in result3.blocked_reason.lower()
    print(f"  Detach too many: {result3.description}")

    # Load supplies onto truck
    state2 = _make_state("D0821")
    unit2 = _make_unit(uid="u2", fuel=10.0, water=8.0, truck_points=2)
    state2.units["u2"] = unit2

    result4 = execute_truck_load(state2, "u2", fuel=5.0, water=3.0)
    assert result4.success
    assert result4.supplies_transferred.get("fuel") == 5.0
    assert result4.supplies_transferred.get("water") == 3.0
    assert unit2.supply.fuel == 5.0
    assert unit2.truck_cargo_fuel == 5.0
    assert unit2.supply.water == 5.0
    assert unit2.truck_cargo_water == 3.0
    print(f"  Load: {result4.description}")

    # Load more than available (capped)
    result5 = execute_truck_load(state2, "u2", fuel=999.0)
    assert result5.success
    assert result5.supplies_transferred.get("fuel") == 5.0  # Only 5.0 left
    assert unit2.supply.fuel == 0.0
    assert unit2.truck_cargo_fuel == 10.0
    print(f"  Load capped: {result5.description}")

    # Unload from truck to internal (capped by capacity)
    state3 = _make_state("D0821")
    unit3 = _make_unit(uid="u3", fuel=15.0, fuel_cap=20.0,
                       truck_fuel=10.0, truck_points=1)
    state3.units["u3"] = unit3

    result6 = execute_truck_unload(state3, "u3", fuel=10.0)
    assert result6.success
    # Internal capacity: 20 - 15 = 5 space, so only 5 transferred
    assert result6.supplies_transferred.get("fuel") == 5.0
    assert unit3.supply.fuel == 20.0
    assert unit3.truck_cargo_fuel == 5.0
    print(f"  Unload (capped by capacity): {result6.description}")

    # No truck points → load fails
    state4 = _make_state("D0821")
    unit4 = _make_unit(uid="u4", truck_points=0)
    state4.units["u4"] = unit4

    result7 = execute_truck_load(state4, "u4", fuel=1.0)
    assert not result7.success
    assert "no attached truck points" in result7.blocked_reason.lower()
    print(f"  No trucks: {result7.description}")

    # CPA budget exceeded
    state5 = _make_state("D0821")
    unit5 = _make_unit(uid="u5", base_cpa=1, truck_points=1)
    unit5.current_cpa_spent = 1  # Budget used up (motorized: max = 1+0=1)
    state5.units["u5"] = unit5

    result8 = execute_truck_attach(state5, "u5")
    assert not result8.success
    assert "needs" in result8.blocked_reason.lower() or "remaining" in result8.blocked_reason.lower()
    print(f"  CPA exceeded: {result8.description}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 7: DUMP OPERATIONS
# ════════════════════════════════════════

def test_dump_operations():
    print("=" * 60)
    print("TEST 7: Dump Operations")
    print("=" * 60)

    # Create a dump from truck cargo
    state = _make_state("D0821")
    unit = _make_unit(uid="u1", hex_id="D0821", base_cpa=10,
                      truck_fuel=20.0, truck_water=15.0, truck_points=2)
    state.units["u1"] = unit

    result = create_supply_dump(state, "u1", "fwd_dump1",
                                fuel=10.0, water=8.0)
    assert result.success
    assert result.supplies_transferred.get("fuel") == 10.0
    assert result.supplies_transferred.get("water") == 8.0
    assert unit.truck_cargo_fuel == 10.0
    assert unit.truck_cargo_water == 7.0
    # Verify dump was created
    dumps = state.hexes["D0821"].supply_dumps
    assert len(dumps) == 1
    assert dumps[0].id == "fwd_dump1"
    assert dumps[0].fuel == 10.0
    assert dumps[0].water == 8.0
    assert dumps[0].side == Side.ALLIED
    print(f"  Create: {result.description}")

    # Draw from dump into unit
    state2 = _make_state("D0821")
    dump = SupplyDump(id="depot1", side=Side.ALLIED, fuel=50.0, water=30.0)
    state2.hexes["D0821"].supply_dumps.append(dump)

    unit2 = _make_unit(uid="u2", hex_id="D0821", base_cpa=10,
                       fuel=5.0, fuel_cap=20.0, water=5.0, water_cap=20.0,
                       truck_points=1)
    state2.units["u2"] = unit2

    result2 = draw_from_dump(state2, "u2", "depot1", fuel=25.0, water=10.0)
    assert result2.success
    # Fuel: cap=20, current=5, space=15. Draw 25 from dump but cap at 15 internal + 10 truck
    assert result2.supplies_transferred.get("fuel") == 25.0
    assert unit2.supply.fuel == 20.0   # Filled to capacity
    assert unit2.truck_cargo_fuel == 10.0  # Overflow
    assert dump.fuel == 25.0  # 50 - 25
    # Water: cap=20, current=5, space=15. Draw 10 → all internal
    assert unit2.supply.water == 15.0
    assert unit2.truck_cargo_water == 0.0
    print(f"  Draw: {result2.description}")

    # Draw from non-existent dump
    state3 = _make_state("D0821")
    unit3 = _make_unit(uid="u3", hex_id="D0821", base_cpa=10)
    state3.units["u3"] = unit3

    result3 = draw_from_dump(state3, "u3", "nonexistent", fuel=5.0)
    assert not result3.success
    assert "not found" in result3.blocked_reason.lower()
    print(f"  Not found: {result3.description}")

    # Draw from enemy dump (wrong side)
    state4 = _make_state("D0821")
    enemy_dump = SupplyDump(id="axis_dump", side=Side.AXIS, fuel=50.0)
    state4.hexes["D0821"].supply_dumps.append(enemy_dump)
    unit4 = _make_unit(uid="u4", hex_id="D0821", side=Side.ALLIED, base_cpa=10)
    state4.units["u4"] = unit4

    result4 = draw_from_dump(state4, "u4", "axis_dump", fuel=5.0)
    assert not result4.success
    assert "belongs to" in result4.blocked_reason.lower()
    print(f"  Wrong side: {result4.description}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 8: WATER DRAWING
# ════════════════════════════════════════

def test_water_drawing():
    print("=" * 60)
    print("TEST 8: Water Drawing from Terrain")
    print("=" * 60)

    # Oasis: unlimited water, no depletion
    state = GameState()
    state.hexes["D1122"] = HexState(hex_id="D1122", terrain=TerrainType.OASIS)
    unit = _make_unit(uid="u1", hex_id="D1122", water=5.0, water_cap=20.0, base_cpa=10)
    state.units["u1"] = unit

    result = draw_water_from_terrain(state, "u1", amount=10.0)
    assert result.success
    assert unit.supply.water == 15.0
    assert state.hexes["D1122"].terrain == TerrainType.OASIS  # Not depleted
    print(f"  Oasis: {result.description}")

    # Bir: roll 3 → holds
    state2 = GameState()
    state2.hexes["D1322"] = HexState(hex_id="D1322", terrain=TerrainType.BIR)
    unit2 = _make_unit(uid="u2", hex_id="D1322", water=5.0, water_cap=20.0, base_cpa=10)
    state2.units["u2"] = unit2

    result2 = draw_water_from_terrain(state2, "u2", amount=3.0, depletion_roll=3)
    assert result2.success
    assert unit2.supply.water == 8.0
    assert state2.hexes["D1322"].terrain == TerrainType.BIR  # Still bir
    print(f"  Bir holds (roll 3): {result2.description}")

    # Bir: roll 1 → depletes to clear
    state3 = GameState()
    state3.hexes["D1522"] = HexState(hex_id="D1522", terrain=TerrainType.BIR)
    unit3 = _make_unit(uid="u3", hex_id="D1522", water=5.0, water_cap=20.0, base_cpa=10)
    state3.units["u3"] = unit3

    result3 = draw_water_from_terrain(state3, "u3", amount=2.0, depletion_roll=1)
    assert result3.success
    assert unit3.supply.water == 7.0
    assert state3.hexes["D1522"].terrain == TerrainType.CLEAR  # Depleted!
    print(f"  Bir depletes (roll 1): {result3.description}")

    # Bir: roll 2 → also depletes
    state4 = GameState()
    state4.hexes["D1822"] = HexState(hex_id="D1822", terrain=TerrainType.BIR)
    unit4 = _make_unit(uid="u4", hex_id="D1822", water=5.0, water_cap=20.0, base_cpa=10)
    state4.units["u4"] = unit4

    result4 = draw_water_from_terrain(state4, "u4", amount=1.0, depletion_roll=2)
    assert result4.success
    assert state4.hexes["D1822"].terrain == TerrainType.CLEAR
    print(f"  Bir depletes (roll 2): {result4.description}")

    # Wrong terrain
    state5 = GameState()
    state5.hexes["D1922"] = HexState(hex_id="D1922", terrain=TerrainType.CLEAR)
    unit5 = _make_unit(uid="u5", hex_id="D1922", base_cpa=10)
    state5.units["u5"] = unit5

    result5 = draw_water_from_terrain(state5, "u5")
    assert not result5.success
    assert "not oasis" in result5.blocked_reason.lower()
    print(f"  Wrong terrain: {result5.description}")

    # Overflow to truck cargo when internal full
    state6 = GameState()
    state6.hexes["D0722"] = HexState(hex_id="D0722", terrain=TerrainType.OASIS)
    unit6 = _make_unit(uid="u6", hex_id="D0722", water=18.0, water_cap=20.0,
                       truck_points=1, base_cpa=10)
    state6.units["u6"] = unit6

    result6 = draw_water_from_terrain(state6, "u6", amount=5.0)
    assert result6.success
    assert unit6.supply.water == 20.0  # Capped at capacity
    assert unit6.truck_cargo_water == 3.0  # Overflow
    print(f"  Overflow to truck: water={unit6.supply.water}, truck={unit6.truck_cargo_water}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 9: STORES EXPENDITURE PHASE
# ════════════════════════════════════════

def test_stores_expenditure():
    print("=" * 60)
    print("TEST 9: Stores Expenditure Phase")
    print("=" * 60)

    ref = _make_ref()
    state = GameState()
    state.turn.game_turn = 10  # Pre-jerrycan: Allied 9%, Axis 3%

    # Allied unit with supply
    allied = _make_unit(
        uid="cw_inf", name="CW Infantry", side=Side.ALLIED,
        hex_id="D0821", fuel=100.0, water=20.0, ammo=50.0, stores=10.0,
        fuel_cap=100.0, water_cap=100.0, ammo_cap=100.0, stores_cap=100.0,
        truck_fuel=30.0, truck_water=10.0, truck_points=2,
    )
    state.units["cw_inf"] = allied
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)

    # Axis unit
    axis = _make_unit(
        uid="it_inf", name="Italian Infantry", side=Side.AXIS,
        hex_id="D1122", fuel=80.0, water=15.0, ammo=40.0, stores=8.0,
        fuel_cap=100.0, water_cap=100.0, ammo_cap=100.0, stores_cap=100.0,
        truck_fuel=20.0, truck_points=1,
    )
    state.units["it_inf"] = axis
    state.hexes["D1122"] = HexState(hex_id="D1122", terrain=TerrainType.CLEAR)

    # Destroyed unit (should be skipped)
    destroyed = _make_unit(uid="dead", name="Dead Unit", fuel=999.0)
    destroyed.status = UnitStatus.DESTROYED
    state.units["dead"] = destroyed

    # Allied supply dump
    dump = SupplyDump(id="depot_a", side=Side.ALLIED, is_real=True,
                      fuel=200.0, water=100.0, ammo=50.0, stores=30.0)
    state.hexes["D0821"].supply_dumps.append(dump)

    # Record pre-evaporation values
    allied_fuel_before = allied.supply.fuel
    allied_truck_fuel_before = allied.truck_cargo_fuel
    axis_fuel_before = axis.supply.fuel

    result = execute_stores_expenditure(state, ref)

    # Verify results
    assert result.total_units_processed == 2  # Not counting destroyed
    assert result.total_dumps_processed == 1
    assert result.game_turn == 10
    print(f"  {result.description}")

    # Allied evaporation (9%):
    # NJH: internal fuel exempt. Truck fuel: 30 * 0.09 = 2.7
    assert allied.supply.fuel == allied_fuel_before  # NJH: tank fuel exempt
    assert abs(allied.truck_cargo_fuel - (allied_truck_fuel_before - 2.7)) < 0.01
    print(f"  Allied fuel: internal={allied.supply.fuel} (unchanged), "
          f"truck={allied.truck_cargo_fuel}")

    # Allied water evaporation: (20*0.09) + (10*0.09) = 1.8+0.9 = 2.7
    # Then baseline water: 1.0 subtracted from (post-evap) internal first
    print(f"  Allied water: internal={allied.supply.water}, truck={allied.truck_cargo_water}")

    # Axis evaporation (3%):
    # NJH: internal fuel exempt. Truck fuel: 20 * 0.03 = 0.6
    assert axis.supply.fuel == axis_fuel_before  # NJH: tank fuel exempt
    print(f"  Axis fuel: internal={axis.supply.fuel} (unchanged), truck={axis.truck_cargo_fuel}")

    # Destroyed unit untouched
    assert destroyed.supply.fuel == 999.0
    print(f"  Destroyed unit: fuel={destroyed.supply.fuel} (untouched)")

    # Dump evaporation: 200*0.09 = 18 fuel lost
    assert abs(dump.fuel - (200 - 18)) < 0.01
    print(f"  Dump fuel: {dump.fuel} (was 200, lost 18)")

    # Baseline consumption happened
    assert result.water_consumed == 2.0  # 2 active units * 1.0
    assert result.stores_consumed == 1.0  # 2 active units * 0.5
    print(f"  Baseline: water={result.water_consumed}, stores={result.stores_consumed}")

    # Post-jerrycan rates (GT 47+)
    state2 = GameState()
    state2.turn.game_turn = 50
    allied2 = _make_unit(uid="cw2", side=Side.ALLIED, truck_fuel=100.0, truck_points=1)
    state2.units["cw2"] = allied2
    state2.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)

    result2 = execute_stores_expenditure(state2, ref)
    # Allied rate should be 6% post-jerrycan
    evap = result2.evaporation_results[0]
    assert evap.evap_rate == 0.06, f"Expected 0.06, got {evap.evap_rate}"
    print(f"  Post-jerrycan (GT50): Allied rate={evap.evap_rate*100:.0f}%")

    print("  PASSED\n")


# ════════════════════════════════════════
# MINI SCENARIO: TOBRUK SUPPLY RUN
# ════════════════════════════════════════

def run_mini_scenario():
    """
    Mini Scenario: "Tobruk Supply Run"
    Trucks deliver fuel and ammo to a forward dump, combat units draw
    supplies, move and burn fuel, fire barrage and burn ammo.
    """
    print("=" * 60)
    print("MINI SCENARIO: Tobruk Supply Run")
    print("=" * 60)
    random.seed(1942)

    ref = _make_ref()
    state = GameState()
    state.turn.game_turn = 15

    # Map: supply depot at D0821, forward area at D0922 (adjacent)
    from cna_engine.engine.movement import get_neighbors
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR)

    # Truck column at supply depot
    truck_col = Unit(
        id="cw_trucks", name="7th Armoured Div Supply Column",
        side=Side.ALLIED, nationality="british",
        unit_class="truck", unit_size="company",
        motorization=MotorizationType.MOTORIZED,
        hex_id="D0821", base_cpa=12, cohesion=0,
        stacking_points=1,
        toe_strength=TOEStrength(infantry=2),
        current_strength=TOEStrength(infantry=2),
        supply=UnitSupply(
            fuel=5.0, water=5.0, ammo=0.0, stores=5.0,
            fuel_capacity=10.0, water_capacity=10.0,
            ammo_capacity=10.0, stores_capacity=10.0,
        ),
        attached_truck_points=4,
        truck_cargo_fuel=30.0,
        truck_cargo_water=20.0,
        truck_cargo_ammo=25.0,
        truck_cargo_stores=15.0,
    )
    state.units["cw_trucks"] = truck_col
    state.hexes["D0821"].allied_unit_ids.append("cw_trucks")

    # Combat unit at forward area (low on supplies)
    combat_unit = Unit(
        id="cw_25pdr", name="25-Pounder Battery, RHA",
        side=Side.ALLIED, nationality="british",
        unit_class="gun", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED,
        hex_id="D0922", base_cpa=8, cohesion=1,
        stacking_points=2,
        toe_strength=TOEStrength(gun=6),
        current_strength=TOEStrength(gun=6),
        supply=UnitSupply(
            fuel=2.0, water=3.0, ammo=4.0, stores=2.0,
            fuel_capacity=15.0, water_capacity=15.0,
            ammo_capacity=20.0, stores_capacity=10.0,
        ),
        attached_truck_points=0,
        truck_cargo_fuel=0.0,
    )
    state.units["cw_25pdr"] = combat_unit
    state.hexes["D0922"].allied_unit_ids.append("cw_25pdr")

    print("""
    SITUATION: 25-Pounder battery at D0922 is low on ammo and fuel.
    Supply column at D0821 loaded with supplies needs to deliver forward.
    """)

    # Step 1: Stores Expenditure phase (start of GT)
    print("  --- STORES EXPENDITURE PHASE ---")
    se_result = execute_stores_expenditure(state, ref)
    print(f"  {se_result.description}")
    print(f"  Events logged: {len(state.event_log)}")

    # Step 2: Supply column creates a forward dump at D0821
    print("\n  --- SUPPLY COLUMN CREATES DUMP ---")
    dump_result = create_supply_dump(
        state, "cw_trucks", "fwd_dump_alpha",
        fuel=15.0, water=10.0, ammo=20.0, stores=5.0,
    )
    assert dump_result.success
    print(f"  {dump_result.description}")
    print(f"  Column remaining truck cargo: "
          f"fuel={truck_col.truck_cargo_fuel:.1f}, ammo={truck_col.truck_cargo_ammo:.1f}")

    # Step 3: Move truck column to forward area (D0821 → D0922)
    print("\n  --- TRUCK COLUMN MOVES FORWARD ---")
    from cna_engine.engine.movement import execute_move
    move_result = execute_move(state, ref, "cw_trucks", ["D0821", "D0922"])
    assert move_result.success
    print(f"  {move_result.description}")

    # Step 4: Burn fuel for the move
    fuel_result = consume_fuel(state, ref, "cw_trucks", cps_expended=2, fuel_rate=1)
    print(f"  {fuel_result.description}")

    # Step 5: Attach truck to combat unit and unload ammo
    print("\n  --- RESUPPLY COMBAT UNIT ---")
    # Combat unit at D0922 draws from truck column's remaining cargo
    # First, attach a truck point to the gun battery
    attach_result = execute_truck_attach(state, "cw_25pdr", truck_points=1)
    # This would normally come from the truck column's pool, but for simplicity
    # we just give it a truck point
    print(f"  {attach_result.description}")

    # Now the truck column unloads some ammo to the combat unit
    # (In game terms, the column delivers directly)
    # For this scenario, the gun battery's truck cargo now has ammo from the column
    combat_unit.truck_cargo_ammo = 10.0  # Direct transfer from column
    truck_col.truck_cargo_ammo -= 5.0  # Column offloads

    # Unload from truck cargo to internal
    unload_result = execute_truck_unload(state, "cw_25pdr", ammo=10.0)
    assert unload_result.success
    print(f"  {unload_result.description}")
    print(f"  Battery ammo: internal={combat_unit.supply.ammo:.1f}, "
          f"truck={combat_unit.truck_cargo_ammo:.1f}")

    # Step 6: Battery fires barrage
    print("\n  --- FIRE BARRAGE ---")
    barrage_result = expend_ammo(state, "cw_25pdr", "barrage", count=2)
    print(f"  {barrage_result.description}")
    print(f"  Battery ammo after: internal={combat_unit.supply.ammo:.1f}")

    # Step 7: Check supply status
    print("\n  --- SUPPLY STATUS ---")
    for uid in ["cw_trucks", "cw_25pdr"]:
        status = check_supply_status(state, uid)
        print(f"  {status.description}")
        if status.is_fuel_critical or status.is_water_critical or status.is_ammo_critical:
            criticals = []
            if status.is_fuel_critical:
                criticals.append("FUEL")
            if status.is_water_critical:
                criticals.append("WATER")
            if status.is_ammo_critical:
                criticals.append("AMMO")
            print(f"    ^ CRITICAL: {', '.join(criticals)}")

    # Summary
    print(f"\n  --- SUMMARY ---")
    print(f"  Total events logged: {len(state.event_log)}")
    for ev in state.event_log:
        print(f"    [{ev['type']}] {ev['description'][:80]}")

    # Verify the forward dump exists
    dump_at_depot = state.hexes["D0821"].supply_dumps
    assert len(dump_at_depot) == 1
    fwd = dump_at_depot[0]
    print(f"\n  Forward dump '{fwd.id}': fuel={fwd.fuel:.1f} water={fwd.water:.1f} "
          f"ammo={fwd.ammo:.1f} stores={fwd.stores:.1f}")

    print()


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    test_fuel_consumption()
    test_ammo_expenditure()
    test_supply_status()
    test_evaporation_unit()
    test_evaporation_dump()
    test_truck_operations()
    test_dump_operations()
    test_water_drawing()
    test_stores_expenditure()
    run_mini_scenario()

    print("=" * 60)
    print("ALL SUPPLY TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
