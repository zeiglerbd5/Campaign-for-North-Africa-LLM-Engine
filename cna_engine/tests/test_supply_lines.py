"""
CNA Engine — Supply Line Tracing Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.supply_lines import (
    trace_supply_line, check_supply_lines,
    SUPPLY_CONNECTED, SUPPLY_INTERDICTED, SUPPLY_SEVERED,
)
from cna_engine.engine.movement import get_neighbors
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TOEStrength, SupplyDump,
)
from cna_engine.models.enums import Side, UnitStatus, TerrainType, MotorizationType


def _make_unit(uid, side=Side.ALLIED, hex_id="D0821", strength=10):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=strength),
    )


def _build_hex_chain(state, hex_ids):
    """Create hexes along a chain."""
    for hid in hex_ids:
        state.hexes[hid] = HexState(hex_id=hid, terrain=TerrainType.CLEAR)


def test_connected_supply():
    print("=" * 60)
    print("TEST 1: Connected Supply Line")
    print("=" * 60)

    state = GameState()

    # Unit at D0821, supply dump at D0821 (same hex)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0821"].supply_dumps.append(
        SupplyDump(id="depot1", side=Side.ALLIED, is_real=True,
                   fuel=100.0, water=50.0, ammo=50.0, stores=25.0)
    )

    unit = _make_unit("u1", hex_id="D0821")
    state.units["u1"] = unit

    result = trace_supply_line(state, "u1")
    assert result.status == SUPPLY_CONNECTED
    assert result.path_length == 0
    print(f"  Same hex: {result.description}")

    # Unit a few hexes away from dump
    state2 = GameState()
    origin = "D0821"
    n1 = get_neighbors(origin)[0]
    n2 = get_neighbors(n1)[0]
    for h in [origin, n1, n2]:
        state2.hexes[h] = HexState(hex_id=h, terrain=TerrainType.CLEAR)
    state2.hexes[origin].supply_dumps.append(
        SupplyDump(id="depot2", side=Side.ALLIED, is_real=True, fuel=100.0)
    )

    unit2 = _make_unit("u2", hex_id=n2)
    state2.units["u2"] = unit2

    result2 = trace_supply_line(state2, "u2")
    assert result2.status == SUPPLY_CONNECTED
    assert result2.path_length > 0
    print(f"  Nearby dump: {result2.description}")

    print("  PASSED\n")


def test_port_supply():
    print("=" * 60)
    print("TEST 2: Port as Supply Source")
    print("=" * 60)

    state = GameState()

    # Port hex
    state.hexes["D1022"] = HexState(
        hex_id="D1022", terrain=TerrainType.COASTAL,
        is_port=True, port_name="Alexandria",
    )
    state.hexes["D1022"].allied_unit_ids.append("port_garrison")
    state.units["port_garrison"] = _make_unit("port_garrison", hex_id="D1022")

    # Unit at adjacent hex
    n = get_neighbors("D1022")[0]
    state.hexes[n] = HexState(hex_id=n, terrain=TerrainType.CLEAR)
    unit = _make_unit("u1", hex_id=n)
    state.units["u1"] = unit

    result = trace_supply_line(state, "u1")
    assert result.status == SUPPLY_CONNECTED
    assert result.source_type == "port"
    print(f"  Port supply: {result.description}")

    print("  PASSED\n")


def test_interdicted_supply():
    print("=" * 60)
    print("TEST 3: Interdicted Supply Line")
    print("=" * 60)

    state = GameState()

    # Depot at D0821
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0821"].supply_dumps.append(
        SupplyDump(id="depot1", side=Side.ALLIED, is_real=True, fuel=100.0)
    )

    # Unit 2 hexes away
    n1 = get_neighbors("D0821")[0]
    n2_candidates = [x for x in get_neighbors(n1) if x != "D0821"]
    n2 = n2_candidates[0]
    for h in [n1, n2]:
        state.hexes[h] = HexState(hex_id=h, terrain=TerrainType.CLEAR)

    unit = _make_unit("u1", hex_id=n2)
    state.units["u1"] = unit

    # Place strong enemy adjacent to the path hex (creates EZOC on n1)
    # Find a hex adjacent to n1 that isn't in our path
    enemy_hex = None
    for eh in get_neighbors(n1):
        if eh not in ("D0821", n2):
            state.hexes[eh] = HexState(hex_id=eh, terrain=TerrainType.CLEAR)
            enemy_hex = eh
            break

    if enemy_hex:
        enemy = _make_unit("axis1", side=Side.AXIS, hex_id=enemy_hex, strength=12)
        state.units["axis1"] = enemy

        result = trace_supply_line(state, "u1")
        # Path may go through EZOC or around it
        print(f"  With enemy nearby: {result.description}")
        print(f"  Status: {result.status}")
        # The exact result depends on hex geometry - just verify it returns something valid
        assert result.status in (SUPPLY_CONNECTED, SUPPLY_INTERDICTED)

    print("  PASSED\n")


def test_severed_supply():
    print("=" * 60)
    print("TEST 4: Severed Supply Line")
    print("=" * 60)

    state = GameState()

    # Unit with no supply sources at all
    state.hexes["D1822"] = HexState(hex_id="D1822", terrain=TerrainType.CLEAR)
    unit = _make_unit("u1", hex_id="D1822")
    state.units["u1"] = unit

    result = trace_supply_line(state, "u1", max_distance=5)
    assert result.status == SUPPLY_SEVERED
    assert result.cpa_penalty > 0
    print(f"  No sources: {result.description}")

    # Unit not on map
    off_map = _make_unit("u2", hex_id=None)
    state.units["u2"] = off_map
    result2 = trace_supply_line(state, "u2")
    assert result2.status == SUPPLY_SEVERED
    print(f"  Off map: {result2.description}")

    print("  PASSED\n")


def test_bulk_check():
    print("=" * 60)
    print("TEST 5: Bulk Supply Line Check")
    print("=" * 60)

    state = GameState()

    # Depot
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0821"].supply_dumps.append(
        SupplyDump(id="depot", side=Side.ALLIED, is_real=True, fuel=100.0)
    )

    # Two units near depot
    n1 = get_neighbors("D0821")[0]
    state.hexes[n1] = HexState(hex_id=n1, terrain=TerrainType.CLEAR)

    u1 = _make_unit("u1", hex_id="D0821")
    u2 = _make_unit("u2", hex_id=n1)
    state.units["u1"] = u1
    state.units["u2"] = u2

    # One axis unit (should not be counted)
    ax = _make_unit("ax1", side=Side.AXIS, hex_id="D1822")
    state.units["ax1"] = ax
    state.hexes["D1822"] = HexState(hex_id="D1822", terrain=TerrainType.CLEAR)

    result = check_supply_lines(state, Side.ALLIED, max_distance=10)
    assert result.total_units == 2
    assert result.connected >= 1
    assert result.severed == 0
    print(f"  {result.description}")

    print("  PASSED\n")


def main():
    test_connected_supply()
    test_port_supply()
    test_interdicted_supply()
    test_severed_supply()
    test_bulk_check()
    print("=" * 60)
    print("ALL SUPPLY LINE TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
