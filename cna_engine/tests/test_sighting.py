"""
CNA Engine — Sighting & Fog of War Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.sighting import (
    get_sighting_range, check_sighting_for_unit, execute_sighting_check,
    BASE_SIGHTING_RANGE, RECON_SIGHTING_BONUS, MIN_SIGHTING_RANGE,
)
from cna_engine.engine.movement import get_neighbors
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, Weather, MotorizationType,
)


def _make_unit(uid, side=Side.ALLIED, hex_id="D0821", unit_class="infantry",
               strength=10, motorization=MotorizationType.MOTORIZED):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class=unit_class, unit_size="battalion",
        motorization=motorization, hex_id=hex_id,
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=strength),
    )


def _make_state_with_hexes(hex_ids, terrain=TerrainType.CLEAR):
    state = GameState()
    state.turn = TurnState(current_weather=Weather.CLEAR)
    for hid in hex_ids:
        state.hexes[hid] = HexState(hex_id=hid, terrain=terrain)
    return state


def test_sighting_range():
    print("=" * 60)
    print("TEST 1: Sighting Range Calculation")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(current_weather=Weather.CLEAR)

    # Clear terrain = 3 hex range
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    unit = _make_unit("u1", hex_id="D0821")
    state.units["u1"] = unit
    assert get_sighting_range(state, unit) == 3
    print(f"  Clear: {get_sighting_range(state, unit)}")

    # Escarpment = 4 hex range
    state.hexes["D1022"] = HexState(hex_id="D1022", terrain=TerrainType.ESCARPMENT)
    unit2 = _make_unit("u2", hex_id="D1022")
    state.units["u2"] = unit2
    assert get_sighting_range(state, unit2) == 4
    print(f"  Escarpment: {get_sighting_range(state, unit2)}")

    # Recon unit gets bonus
    recon = _make_unit("r1", hex_id="D0821", unit_class="recon")
    state.units["r1"] = recon
    expected = BASE_SIGHTING_RANGE[TerrainType.CLEAR] + RECON_SIGHTING_BONUS
    assert get_sighting_range(state, recon) == expected
    print(f"  Recon (clear): {get_sighting_range(state, recon)}")

    # Sandstorm reduces range
    state.turn.current_weather = Weather.SANDSTORM
    range_sandstorm = get_sighting_range(state, unit)
    assert range_sandstorm == max(MIN_SIGHTING_RANGE, 3 - 2)
    print(f"  Sandstorm: {range_sandstorm}")

    # Off-map unit
    off_map = _make_unit("om", hex_id=None)
    assert get_sighting_range(state, off_map) == 0
    print(f"  Off-map: 0")

    print("  PASSED\n")


def test_unit_sighting():
    print("=" * 60)
    print("TEST 2: Unit Sighting Check")
    print("=" * 60)

    # Build a small hex cluster
    center = "D0821"
    neighbors = get_neighbors(center)
    all_hexes = [center] + neighbors

    state = _make_state_with_hexes(all_hexes)

    # Allied unit at center
    unit = _make_unit("u1", side=Side.ALLIED, hex_id=center)
    state.units["u1"] = unit
    state.hexes[center].allied_unit_ids.append("u1")

    # Enemy at an adjacent hex
    enemy_hex = neighbors[0]
    enemy = _make_unit("e1", side=Side.AXIS, hex_id=enemy_hex)
    state.units["e1"] = enemy
    state.hexes[enemy_hex].axis_unit_ids.append("e1")

    r = check_sighting_for_unit(state, "u1")
    assert len(r.hexes_sighted) > 0
    assert center in r.hexes_sighted
    # Adjacent hexes should be sighted
    for n in neighbors:
        if n in state.hexes:
            assert n in r.hexes_sighted
    # Enemy should be spotted
    assert "e1" in r.enemies_spotted
    # Hex should be marked as sighted
    assert state.hexes[enemy_hex].allied_sighted
    print(f"  Sighting: {r.description}")
    print(f"  Hexes sighted: {len(r.hexes_sighted)}")
    print(f"  Enemies spotted: {r.enemies_spotted}")

    print("  PASSED\n")


def test_sighting_not_found():
    print("=" * 60)
    print("TEST 3: Sighting Edge Cases")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(current_weather=Weather.CLEAR)

    # Unit not found
    r = check_sighting_for_unit(state, "missing")
    assert len(r.hexes_sighted) == 0
    print(f"  Not found: {r.description}")

    # Destroyed unit
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    dead = _make_unit("dead", hex_id="D0821")
    dead.status = UnitStatus.DESTROYED
    state.units["dead"] = dead
    r2 = check_sighting_for_unit(state, "dead")
    assert len(r2.hexes_sighted) == 0
    print(f"  Destroyed: {r2.description}")

    # Off-map unit
    offmap = _make_unit("offmap", hex_id=None)
    state.units["offmap"] = offmap
    r3 = check_sighting_for_unit(state, "offmap")
    assert len(r3.hexes_sighted) == 0
    print(f"  Off-map: {r3.description}")

    print("  PASSED\n")


def test_bulk_sighting():
    print("=" * 60)
    print("TEST 4: Bulk Sighting Phase")
    print("=" * 60)

    center = "D0821"
    neighbors = get_neighbors(center)
    all_hexes = [center] + neighbors

    # Add second-ring hexes for broader sighting
    for n in neighbors:
        for nn in get_neighbors(n):
            if nn not in all_hexes:
                all_hexes.append(nn)

    state = _make_state_with_hexes(all_hexes)

    # Two allied units
    u1 = _make_unit("u1", side=Side.ALLIED, hex_id=center)
    u2 = _make_unit("u2", side=Side.ALLIED, hex_id=neighbors[0])
    state.units["u1"] = u1
    state.units["u2"] = u2
    state.hexes[center].allied_unit_ids.append("u1")
    state.hexes[neighbors[0]].allied_unit_ids.append("u2")

    # Axis unit further away
    far_hex = neighbors[-1]
    enemy = _make_unit("e1", side=Side.AXIS, hex_id=far_hex)
    state.units["e1"] = enemy
    state.hexes[far_hex].axis_unit_ids.append("e1")

    # Also add a destroyed unit (should be skipped)
    dead = _make_unit("dead_ally", side=Side.ALLIED, hex_id=center)
    dead.status = UnitStatus.DESTROYED
    state.units["dead_ally"] = dead

    r = execute_sighting_check(state, Side.ALLIED)
    assert r.units_checked == 2  # u1 + u2 (dead skipped)
    assert r.total_hexes_sighted > 0
    print(f"  Bulk: {r.description}")

    print("  PASSED\n")


def main():
    test_sighting_range()
    test_unit_sighting()
    test_sighting_not_found()
    test_bulk_sighting()
    print("=" * 60)
    print("ALL SIGHTING TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
