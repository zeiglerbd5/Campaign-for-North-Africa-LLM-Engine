"""
CNA Engine — Minefield Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.minefields import (
    lay_minefield, lay_fake_minefield, clear_minefield,
    resolve_minefield_entry,
)
from cna_engine.engine.movement import compute_hex_entry_cost
from cna_engine.models.game_state import GameState, Unit, HexState, TurnState, TOEStrength, UnitSupply
from cna_engine.models.enums import (
    Side, UnitClass, UnitSize, UnitStatus, MotorizationType,
    TerrainType, RoadType, GamePhase,
)
from cna_engine.data.reference_data import ReferenceData


def _make_state():
    state = GameState()
    state.turn = TurnState(game_turn=1, op_stage=1, phase=GamePhase.OP_STAGE)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    state.hexes["D0921"] = HexState(hex_id="D0921", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    state.hexes["D1021"] = HexState(hex_id="D1021", terrain=TerrainType.CLEAR, road=RoadType.ROAD)
    return state


def _make_engineer(state, uid="engr_1", side=Side.ALLIED, hex_id="D0821"):
    unit = Unit(
        id=uid, name=f"Engineer {uid}", side=side, nationality="british",
        unit_class=UnitClass.ENGINEER, unit_size=UnitSize.COMPANY,
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=6, stacking_points=1,
        toe_strength=TOEStrength(infantry=4),
        current_strength=TOEStrength(infantry=4),
        supply=UnitSupply(fuel=5, water=5, ammo=4, stores=3,
                          fuel_capacity=10, water_capacity=10,
                          ammo_capacity=8, stores_capacity=6),
    )
    state.units[uid] = unit
    if hex_id in state.hexes:
        attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
        getattr(state.hexes[hex_id], attr).append(uid)
    return unit


def _make_infantry(state, uid="inf_1", side=Side.ALLIED, hex_id="D0821"):
    unit = Unit(
        id=uid, name=f"Infantry {uid}", side=side, nationality="british",
        unit_class=UnitClass.INFANTRY, unit_size=UnitSize.BATTALION,
        motorization=MotorizationType.NON_MOTORIZED, hex_id=hex_id,
        base_cpa=6, stacking_points=2,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
        supply=UnitSupply(fuel=5, water=10, ammo=10, stores=5,
                          fuel_capacity=10, water_capacity=20,
                          ammo_capacity=20, stores_capacity=10),
    )
    state.units[uid] = unit
    if hex_id in state.hexes:
        attr = "allied_unit_ids" if side == Side.ALLIED else "axis_unit_ids"
        getattr(state.hexes[hex_id], attr).append(uid)
    return unit


def test_lay_real_by_engineer():
    print("=" * 60)
    print("TEST: Lay real minefield by engineer")
    print("=" * 60)

    state = _make_state()
    engr = _make_engineer(state)

    result = lay_minefield(state, engr.id, "D0821")
    assert result.success, f"Failed: {result.blocked_reason}"
    assert state.hexes["D0821"].real_minefield is True
    assert state.hexes["D0821"].minefield_owner == Side.ALLIED
    assert engr.current_cpa_spent == 2
    print(f"  {result.description} — PASSED")


def test_lay_fake_by_engineer():
    print("=" * 60)
    print("TEST: Lay fake minefield by engineer")
    print("=" * 60)

    state = _make_state()
    engr = _make_engineer(state)

    result = lay_fake_minefield(state, engr.id, "D0921")  # adjacent hex
    assert result.success, f"Failed: {result.blocked_reason}"
    assert state.hexes["D0921"].fake_minefield is True
    assert state.hexes["D0921"].minefield_owner == Side.ALLIED
    assert engr.current_cpa_spent == 1
    print(f"  {result.description} — PASSED")


def test_non_engineer_rejected():
    print("=" * 60)
    print("TEST: Non-engineer rejected for minefield ops")
    print("=" * 60)

    state = _make_state()
    inf = _make_infantry(state)

    result = lay_minefield(state, inf.id, "D0821")
    assert not result.success
    assert "engineer" in result.blocked_reason.lower()
    print(f"  Infantry rejected: {result.blocked_reason} — PASSED")


def test_clear_success():
    print("=" * 60)
    print("TEST: Clear real minefield success (roll ≤ 4)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    engr = _make_engineer(state)

    result = clear_minefield(state, engr.id, "D0921", dice_roll=3)
    assert result.success, f"Failed: {result.blocked_reason}"
    assert state.hexes["D0921"].real_minefield is False
    assert state.hexes["D0921"].minefield_owner is None
    print(f"  {result.description} — PASSED")


def test_clear_failure():
    print("=" * 60)
    print("TEST: Clear real minefield failure (roll > 4)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    engr = _make_engineer(state)

    result = clear_minefield(state, engr.id, "D0921", dice_roll=5)
    assert not result.success
    assert state.hexes["D0921"].real_minefield is True  # Still there
    assert engr.current_cpa_spent == 2  # CPA still spent
    print(f"  {result.description} — PASSED")


def test_clear_fake_is_free():
    print("=" * 60)
    print("TEST: Clear fake minefield is free")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].fake_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    engr = _make_engineer(state)

    result = clear_minefield(state, engr.id, "D0921")
    assert result.success
    assert state.hexes["D0921"].fake_minefield is False
    assert engr.current_cpa_spent == 0  # Free!
    print(f"  {result.description} — PASSED")


def test_entry_sp_loss():
    print("=" * 60)
    print("TEST: Minefield entry — SP loss (roll 1-2)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    inf = _make_infantry(state, hex_id="D0921")

    result = resolve_minefield_entry(state, inf.id, "D0921", dice_roll=1)
    assert result.sp_lost == 1
    assert inf.current_strength.infantry == 9
    print(f"  {result.description} — PASSED")


def test_entry_pinned():
    print("=" * 60)
    print("TEST: Minefield entry — pinned (roll 3-4)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    inf = _make_infantry(state, hex_id="D0921")

    result = resolve_minefield_entry(state, inf.id, "D0921", dice_roll=3)
    assert result.pinned is True
    assert inf.is_pinned is True
    print(f"  {result.description} — PASSED")


def test_entry_no_effect():
    print("=" * 60)
    print("TEST: Minefield entry — no effect (roll 5-6)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    inf = _make_infantry(state, hex_id="D0921")

    result = resolve_minefield_entry(state, inf.id, "D0921", dice_roll=6)
    assert result.no_effect is True
    assert inf.current_strength.infantry == 10  # No damage
    print(f"  {result.description} — PASSED")


def test_fake_reveal_on_entry():
    print("=" * 60)
    print("TEST: Fake minefield reveal on entry (no damage)")
    print("=" * 60)

    state = _make_state()
    state.hexes["D0921"].fake_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS
    inf = _make_infantry(state, hex_id="D0921")

    result = resolve_minefield_entry(state, inf.id, "D0921")
    assert result.was_fake is True
    assert state.hexes["D0921"].fake_minefield is False
    assert inf.current_strength.infantry == 10  # No damage
    print(f"  {result.description} — PASSED")


def test_movement_cost_plus_2():
    print("=" * 60)
    print("TEST: Movement cost +2 CP for enemy minefields")
    print("=" * 60)

    state = _make_state()
    ref = ReferenceData()

    # Enemy minefield on road hex
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.AXIS

    inf = _make_infantry(state, hex_id="D0821")
    cost = compute_hex_entry_cost(state, ref, inf, "D0821", "D0921")
    # Road cost (0.5) + minefield (2) = 2.5
    assert cost.effective_cost == 2.5, f"Expected 2.5, got {cost.effective_cost}"
    print(f"  Enemy minefield on road: {cost.effective_cost} CP (0.5 + 2.0) — PASSED")


def test_friendly_minefield_no_cost():
    print("=" * 60)
    print("TEST: Friendly minefields — no cost, no damage")
    print("=" * 60)

    state = _make_state()
    ref = ReferenceData()

    # Friendly minefield
    state.hexes["D0921"].real_minefield = True
    state.hexes["D0921"].minefield_owner = Side.ALLIED

    inf = _make_infantry(state, hex_id="D0821")
    cost = compute_hex_entry_cost(state, ref, inf, "D0821", "D0921")
    # Road cost only (0.5) — no minefield surcharge for friendly
    assert cost.effective_cost == 0.5, f"Expected 0.5, got {cost.effective_cost}"
    print(f"  Friendly minefield on road: {cost.effective_cost} CP (no surcharge) — PASSED")

    # Entry damage check
    inf.hex_id = "D0921"
    result = resolve_minefield_entry(state, inf.id, "D0921")
    assert result.no_effect is True
    print(f"  Friendly minefield entry: no damage — PASSED")


def main():
    test_lay_real_by_engineer()
    test_lay_fake_by_engineer()
    test_non_engineer_rejected()
    test_clear_success()
    test_clear_failure()
    test_clear_fake_is_free()
    test_entry_sp_loss()
    test_entry_pinned()
    test_entry_no_effect()
    test_fake_reveal_on_entry()
    test_movement_cost_plus_2()
    test_friendly_minefield_no_cost()
    print("\n" + "=" * 60)
    print("ALL MINEFIELD TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
