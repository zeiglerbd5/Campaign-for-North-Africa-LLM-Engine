"""
CNA Engine — Replacements Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.replacements import (
    absorb_replacements, add_production, execute_replacement_phase,
    MAX_REPLACEMENTS_PER_UNIT, DEFAULT_ALLIED_PRODUCTION, DEFAULT_AXIS_PRODUCTION,
)
from cna_engine.models.game_state import (
    GameState, Unit, TurnState, TOEStrength,
)
from cna_engine.models.enums import Side, UnitStatus, MotorizationType


def _make_unit(uid, side=Side.ALLIED, strength=8, toe=12, stype="armor",
               status=UnitStatus.ACTIVE, in_contact=False):
    kwargs = {stype: strength}
    toe_kwargs = {stype: toe}
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class=stype, unit_size="battalion",
        motorization=MotorizationType.MECHANIZED, hex_id="D0821",
        base_cpa=10, stacking_points=2,
        current_strength=TOEStrength(**kwargs),
        toe_strength=TOEStrength(**toe_kwargs),
        status=status,
        is_in_contact=in_contact,
    )


def test_absorb_replacements():
    print("=" * 60)
    print("TEST 1: Absorb Replacements")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5)
    state.allied_replacement_pool = {"infantry": 5, "armor": 3, "gun": 2}

    # Armor unit absorbs 2 points
    unit = _make_unit("u1", strength=8, toe=12, stype="armor")
    state.units["u1"] = unit

    r = absorb_replacements(state, "u1", points=2)
    assert r.success
    assert r.points_absorbed == 2
    assert r.strength_before == 8
    assert r.strength_after == 10
    assert unit.current_strength.armor == 10
    assert state.allied_replacement_pool["armor"] == 1  # 3 - 2
    print(f"  Armor: {r.description}")

    # Infantry unit
    inf = _make_unit("u2", strength=6, toe=10, stype="infantry")
    state.units["u2"] = inf
    r2 = absorb_replacements(state, "u2", points=2)
    assert r2.success
    assert r2.points_absorbed == 2
    assert inf.current_strength.infantry == 8
    print(f"  Infantry: {r2.description}")

    print("  PASSED\n")


def test_replacement_limits():
    print("=" * 60)
    print("TEST 2: Replacement Limits")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5)
    state.allied_replacement_pool = {"armor": 10}

    # Can't absorb more than MAX_REPLACEMENTS_PER_UNIT
    unit = _make_unit("u1", strength=5, toe=12)
    state.units["u1"] = unit
    r = absorb_replacements(state, "u1", points=5)
    assert r.success
    assert r.points_absorbed == MAX_REPLACEMENTS_PER_UNIT
    print(f"  Max cap: absorbed {r.points_absorbed} (requested 5)")

    # Can't absorb more than deficit
    unit2 = _make_unit("u2", strength=11, toe=12)
    state.units["u2"] = unit2
    r2 = absorb_replacements(state, "u2", points=2)
    assert r2.success
    assert r2.points_absorbed == 1  # Only 1 deficit
    print(f"  Deficit cap: absorbed {r2.points_absorbed} (deficit=1)")

    # Full strength — fails
    full = _make_unit("u3", strength=12, toe=12)
    state.units["u3"] = full
    r3 = absorb_replacements(state, "u3")
    assert not r3.success
    print(f"  Full: {r3.description}")

    print("  PASSED\n")


def test_replacement_validation():
    print("=" * 60)
    print("TEST 3: Replacement Validation")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5)
    state.allied_replacement_pool = {"armor": 5}

    # Destroyed unit
    dead = _make_unit("dead", status=UnitStatus.DESTROYED)
    state.units["dead"] = dead
    r = absorb_replacements(state, "dead")
    assert not r.success
    assert "status" in r.blocked_reason.lower()
    print(f"  Destroyed: {r.description}")

    # In contact
    contact = _make_unit("contact", in_contact=True)
    state.units["contact"] = contact
    r2 = absorb_replacements(state, "contact")
    assert not r2.success
    assert "contact" in r2.blocked_reason.lower()
    print(f"  In contact: {r2.description}")

    # Empty pool
    state.allied_replacement_pool = {"armor": 0}
    unit = _make_unit("u1")
    state.units["u1"] = unit
    r3 = absorb_replacements(state, "u1")
    assert not r3.success
    assert "pool" in r3.blocked_reason.lower()
    print(f"  Empty pool: {r3.description}")

    # Not found
    r4 = absorb_replacements(state, "missing")
    assert not r4.success
    print(f"  Not found: {r4.description}")

    print("  PASSED\n")


def test_production():
    print("=" * 60)
    print("TEST 4: Replacement Production")
    print("=" * 60)

    state = GameState()
    state.allied_replacement_pool = {"infantry": 2, "armor": 1, "gun": 0}

    add_production(state, Side.ALLIED)
    expected_inf = 2 + DEFAULT_ALLIED_PRODUCTION["infantry"]
    expected_arm = 1 + DEFAULT_ALLIED_PRODUCTION["armor"]
    expected_gun = 0 + DEFAULT_ALLIED_PRODUCTION["gun"]
    assert state.allied_replacement_pool["infantry"] == expected_inf
    assert state.allied_replacement_pool["armor"] == expected_arm
    assert state.allied_replacement_pool["gun"] == expected_gun
    print(f"  Allied production: {state.allied_replacement_pool}")

    state.axis_replacement_pool = {}
    add_production(state, Side.AXIS)
    for k, v in DEFAULT_AXIS_PRODUCTION.items():
        assert state.axis_replacement_pool[k] == v
    print(f"  Axis production: {state.axis_replacement_pool}")

    print("  PASSED\n")


def test_replacement_phase():
    print("=" * 60)
    print("TEST 5: Replacement Phase Execution")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5)
    state.allied_replacement_pool = {"infantry": 0, "armor": 0, "gun": 0}

    # Two depleted units
    u1 = _make_unit("u1", strength=6, toe=12, stype="armor")
    u2 = _make_unit("u2", strength=5, toe=10, stype="infantry")
    # Full strength unit (should be skipped)
    u3 = _make_unit("u3", strength=12, toe=12, stype="armor")
    state.units.update({"u1": u1, "u2": u2, "u3": u3})

    r = execute_replacement_phase(state, Side.ALLIED)
    # Production should have been added first
    assert r.production_added == DEFAULT_ALLIED_PRODUCTION
    # Then auto-allocated to depleted units
    assert r.units_reinforced >= 1
    assert r.total_points_absorbed >= 1
    print(f"  Phase: {r.description}")
    for rr in r.results:
        print(f"    {rr.description}")
    print(f"  Pool after: {r.pool_after}")

    print("  PASSED\n")


def test_replacement_phase_explicit():
    print("=" * 60)
    print("TEST 6: Explicit Replacement Allocation")
    print("=" * 60)

    state = GameState()
    state.turn = TurnState(game_turn=5)
    state.allied_replacement_pool = {"armor": 0}

    u1 = _make_unit("u1", strength=8, toe=12, stype="armor")
    state.units["u1"] = u1

    # Explicit allocation
    r = execute_replacement_phase(state, Side.ALLIED, allocation={"u1": 1})
    assert r.units_reinforced == 1
    assert u1.current_strength.armor == 9
    print(f"  Explicit: {r.description}")

    print("  PASSED\n")


def main():
    test_absorb_replacements()
    test_replacement_limits()
    test_replacement_validation()
    test_production()
    test_replacement_phase()
    test_replacement_phase_explicit()
    print("=" * 60)
    print("ALL REPLACEMENT TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
