"""
CNA Engine — Organization Phase Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.organization import (
    attach_unit_to_formation, detach_unit_from_formation,
    place_in_reserve, release_from_reserve,
    execute_organization_phase,
)
from cna_engine.models.game_state import (
    GameState, Unit, Formation, HexState, TOEStrength,
)
from cna_engine.models.enums import Side, UnitStatus, MotorizationType


def _make_unit(uid="u1", name="Test Unit", side=Side.ALLIED, hex_id="D0821",
               base_cpa=8, parent=None, attached=None):
    return Unit(
        id=uid, name=name, side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=2,
        current_strength=TOEStrength(infantry=10),
        toe_strength=TOEStrength(infantry=10),
        parent_formation_id=parent,
        attached_to_id=attached,
    )


def test_attach_detach():
    print("=" * 60)
    print("TEST 1: Unit Attach / Detach")
    print("=" * 60)

    state = GameState()
    fm1 = Formation(id="fm1", name="1st Brigade", side=Side.ALLIED,
                    nationality="british", formation_size="brigade",
                    unit_ids=["u1"])
    fm2 = Formation(id="fm2", name="2nd Brigade", side=Side.ALLIED,
                    nationality="british", formation_size="brigade",
                    unit_ids=[])
    state.formations["fm1"] = fm1
    state.formations["fm2"] = fm2

    unit = _make_unit(uid="u1", parent="fm1", attached="fm1")
    state.units["u1"] = unit

    # Attach to fm2
    r = attach_unit_to_formation(state, "u1", "fm2")
    assert r.success
    assert unit.attached_to_id == "fm2"
    assert "u1" in fm2.unit_ids
    assert "u1" not in fm1.unit_ids
    print(f"  Attach: {r.description}")

    # Detach
    r2 = detach_unit_from_formation(state, "u1")
    assert r2.success
    assert unit.attached_to_id is None
    # Returns to parent formation
    assert "u1" in fm1.unit_ids
    print(f"  Detach: {r2.description}")

    # Detach when not attached
    r3 = detach_unit_from_formation(state, "u1")
    assert not r3.success
    print(f"  Not attached: {r3.description}")

    # Side mismatch
    axis_fm = Formation(id="axfm", name="Axis Brigade", side=Side.AXIS,
                        nationality="italian", formation_size="brigade")
    state.formations["axfm"] = axis_fm
    r4 = attach_unit_to_formation(state, "u1", "axfm")
    assert not r4.success
    assert "mismatch" in r4.blocked_reason.lower()
    print(f"  Side mismatch: {r4.description}")

    print("  PASSED\n")


def test_reserve():
    print("=" * 60)
    print("TEST 2: Reserve Placement / Release")
    print("=" * 60)

    state = GameState()
    unit = _make_unit(uid="u1")
    state.units["u1"] = unit

    # Place in reserve
    r = place_in_reserve(state, "u1")
    assert r.success
    assert unit.status == UnitStatus.IN_RESERVE
    print(f"  Place: {r.description}")

    # Can't place again (not ACTIVE)
    r2 = place_in_reserve(state, "u1")
    assert not r2.success
    print(f"  Already reserved: {r2.description}")

    # Release
    r3 = release_from_reserve(state, "u1")
    assert r3.success
    assert unit.status == UnitStatus.ACTIVE
    print(f"  Release: {r3.description}")

    # Can't release (not in reserve)
    r4 = release_from_reserve(state, "u1")
    assert not r4.success
    print(f"  Not in reserve: {r4.description}")

    # Can't place in reserve if in contact
    unit2 = _make_unit(uid="u2")
    unit2.is_in_contact = True
    state.units["u2"] = unit2
    r5 = place_in_reserve(state, "u2")
    assert not r5.success
    assert "contact" in r5.blocked_reason.lower()
    print(f"  In contact: {r5.description}")

    print("  PASSED\n")


def test_organization_phase():
    print("=" * 60)
    print("TEST 3: Organization Phase Execution")
    print("=" * 60)

    state = GameState()
    state.turn.game_turn = 5
    state.turn.op_stage = 2

    u1 = _make_unit(uid="u1")
    u1.current_cpa_spent = 6
    u1.has_acted_this_stage = True
    u1.is_pinned = True
    state.units["u1"] = u1

    u2 = _make_unit(uid="u2", side=Side.AXIS)
    u2.current_cpa_spent = 4
    state.units["u2"] = u2

    # Destroyed unit should be skipped
    u3 = _make_unit(uid="u3")
    u3.status = UnitStatus.DESTROYED
    u3.current_cpa_spent = 99
    state.units["u3"] = u3

    state.hexes["D0821"] = HexState(hex_id="D0821", terrain="clear")
    state.hexes["D0821"].allied_sighted = True

    result = execute_organization_phase(state)
    assert result.units_reset == 2  # u1 and u2, not u3
    assert result.sighting_cleared == 1
    assert u1.current_cpa_spent == 0
    assert not u1.has_acted_this_stage
    assert not u1.is_pinned
    assert u2.current_cpa_spent == 0
    assert u3.current_cpa_spent == 99  # Untouched
    assert not state.hexes["D0821"].allied_sighted
    print(f"  {result.description}")

    print("  PASSED\n")


def main():
    test_attach_detach()
    test_reserve()
    test_organization_phase()
    print("=" * 60)
    print("ALL ORGANIZATION TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
