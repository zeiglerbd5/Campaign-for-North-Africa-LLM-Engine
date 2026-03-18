"""
CNA Engine — Patrol Phase Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.patrol import (
    can_patrol, execute_patrol, execute_patrol_phase,
    PATROL_RANGE, PATROL_ELIGIBLE_CLASSES, MIN_PATROL_STRENGTH,
)
from cna_engine.engine.movement import get_neighbors
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, MotorizationType,
)


def _make_recon(uid, hex_id="D0821", side=Side.ALLIED, strength=4):
    return Unit(
        id=uid, name=f"Recon {uid}", side=side, nationality="british",
        unit_class="recon", unit_size="company",
        motorization=MotorizationType.MECHANIZED, hex_id=hex_id,
        base_cpa=10, stacking_points=1,
        current_strength=TOEStrength(recon=strength),
        toe_strength=TOEStrength(recon=strength),
    )


def _make_infantry(uid, hex_id="D0821", side=Side.ALLIED, strength=10):
    return Unit(
        id=uid, name=f"Infantry {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id=hex_id,
        base_cpa=6, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=strength),
    )


def _build_hex_area(state, center, depth=4):
    """Build hexes around center to depth rings."""
    visited = {center}
    state.hexes[center] = HexState(hex_id=center, terrain=TerrainType.CLEAR)
    queue = [(center, 0)]
    while queue:
        current, d = queue.pop(0)
        if d >= depth:
            continue
        for n in get_neighbors(current):
            if n not in visited:
                visited.add(n)
                state.hexes[n] = HexState(hex_id=n, terrain=TerrainType.CLEAR)
                queue.append((n, d + 1))


def test_patrol_eligibility():
    print("=" * 60)
    print("TEST 1: Patrol Eligibility")
    print("=" * 60)

    state = GameState()
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)

    # Recon unit — eligible
    recon = _make_recon("r1")
    state.units["r1"] = recon
    ok, reason = can_patrol(state, "r1")
    assert ok
    print(f"  Recon: {ok} ({reason})")

    # Infantry — not eligible
    inf = _make_infantry("i1")
    state.units["i1"] = inf
    ok2, reason2 = can_patrol(state, "i1")
    assert not ok2
    print(f"  Infantry: {ok2} ({reason2})")

    # Recon in contact — not eligible
    recon2 = _make_recon("r2")
    recon2.is_in_contact = True
    state.units["r2"] = recon2
    ok3, reason3 = can_patrol(state, "r2")
    assert not ok3
    print(f"  In contact: {ok3} ({reason3})")

    # Too weak
    weak = _make_recon("r3", strength=1)
    state.units["r3"] = weak
    ok4, reason4 = can_patrol(state, "r3")
    assert not ok4
    print(f"  Weak: {ok4} ({reason4})")

    # Not found
    ok5, reason5 = can_patrol(state, "missing")
    assert not ok5
    print(f"  Missing: {ok5} ({reason5})")

    print("  PASSED\n")


def test_patrol_execution():
    print("=" * 60)
    print("TEST 2: Patrol Execution")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState()
    _build_hex_area(state, "D0821", depth=4)

    recon = _make_recon("r1", hex_id="D0821")
    state.units["r1"] = recon
    state.hexes["D0821"].allied_unit_ids.append("r1")

    # Patrol to adjacent hex
    target = get_neighbors("D0821")[0]
    r = execute_patrol(state, "r1", target)
    assert r.success
    assert len(r.path) >= 2
    assert r.path[0] == "D0821"
    assert r.path[-1] == target
    assert len(r.hexes_sighted) > 0
    # Unit should still be at original hex (round-trip)
    assert recon.hex_id == "D0821"
    print(f"  Adjacent: {r.description}")

    # Patrol to 2-hex range
    n2_candidates = [x for x in get_neighbors(target) if x != "D0821"]
    if n2_candidates:
        target2 = n2_candidates[0]
        r2 = execute_patrol(state, "r1", target2)
        assert r2.success
        assert len(r2.path) == 3
        print(f"  2 hexes: {r2.description}")

    print("  PASSED\n")


def test_patrol_out_of_range():
    print("=" * 60)
    print("TEST 3: Patrol Out of Range")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState()
    _build_hex_area(state, "D0821", depth=5)

    recon = _make_recon("r1", hex_id="D0821")
    state.units["r1"] = recon

    # Try to patrol beyond range (need to find a hex > PATROL_RANGE away)
    # BFS to find a hex at distance PATROL_RANGE+1
    visited = {"D0821"}
    queue = [("D0821", 0)]
    far_hex = None
    while queue:
        current, d = queue.pop(0)
        if d == PATROL_RANGE + 1:
            far_hex = current
            break
        for n in get_neighbors(current):
            if n not in visited and n in state.hexes:
                visited.add(n)
                queue.append((n, d + 1))

    if far_hex:
        r = execute_patrol(state, "r1", far_hex)
        assert not r.success
        assert "range" in r.blocked_reason.lower() or "reachable" in r.blocked_reason.lower()
        print(f"  Out of range: {r.description}")

    # Same hex as start
    r2 = execute_patrol(state, "r1", "D0821")
    assert not r2.success
    print(f"  Same hex: {r2.description}")

    print("  PASSED\n")


def test_patrol_encounter():
    print("=" * 60)
    print("TEST 4: Patrol Encounter")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState()
    _build_hex_area(state, "D0821", depth=3)

    recon = _make_recon("r1", hex_id="D0821", side=Side.ALLIED)
    state.units["r1"] = recon
    state.hexes["D0821"].allied_unit_ids.append("r1")

    # Place enemy on patrol path
    target = get_neighbors("D0821")[0]
    enemy = _make_recon("e1", hex_id=target, side=Side.AXIS)
    state.units["e1"] = enemy
    state.hexes[target].axis_unit_ids.append("e1")

    # Force encounter (roll ≤ 3)
    r = execute_patrol(state, "r1", target, encounter_roll=2)
    assert r.success
    assert "e1" in r.encounters
    print(f"  Encounter: {r.description}")

    # Force no encounter (roll > 3)
    r2 = execute_patrol(state, "r1", target, encounter_roll=5)
    assert r2.success
    assert len(r2.encounters) == 0
    print(f"  No encounter: {r2.description}")

    print("  PASSED\n")


def test_patrol_phase():
    print("=" * 60)
    print("TEST 5: Patrol Phase Execution")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    _build_hex_area(state, "D0821", depth=3)

    r1 = _make_recon("r1", hex_id="D0821")
    state.units["r1"] = r1
    state.hexes["D0821"].allied_unit_ids.append("r1")

    target1 = get_neighbors("D0821")[0]
    target2 = get_neighbors("D0821")[1]

    orders = {"r1": target1}
    r = execute_patrol_phase(state, Side.ALLIED, patrol_orders=orders)
    assert r.patrols_sent == 1
    assert r.hexes_sighted > 0
    print(f"  Phase: {r.description}")

    # No orders
    r2 = execute_patrol_phase(state, Side.ALLIED, patrol_orders={})
    assert r2.patrols_sent == 0
    print(f"  Empty phase: {r2.description}")

    print("  PASSED\n")


def main():
    test_patrol_eligibility()
    test_patrol_execution()
    test_patrol_out_of_range()
    test_patrol_encounter()
    test_patrol_phase()
    print("=" * 60)
    print("ALL PATROL TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
