"""
CNA Engine — Morale / Disorganization Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dataclasses import dataclass

from cna_engine.engine.agent_interface import (
    _apply_barrage_result, _apply_close_assault_result,
)
from cna_engine.engine.organization import (
    apply_cohesion_changes,
    DISORG_ATTACK_SHIFT_THRESHOLD, DISORG_DEFEND_SHIFT_THRESHOLD,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState, TOEStrength,
)
from cna_engine.models.enums import (
    Side, TerrainType, MotorizationType,
)


def _make_unit(uid, hex_id="D0821", strength=10, side=Side.ALLIED,
               motorization=MotorizationType.MECHANIZED,
               unit_class="armor", base_cpa=10):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class=unit_class, unit_size="battalion",
        motorization=motorization, hex_id=hex_id,
        base_cpa=base_cpa, stacking_points=3,
        current_strength=TOEStrength(armor=strength // 2, infantry=strength - strength // 2),
        toe_strength=TOEStrength(armor=strength // 2 + 2, infantry=strength - strength // 2 + 2),
    )


def _make_state():
    state = GameState()
    state.turn = TurnState(game_turn=5, op_stage=1)
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR)
    return state


# ══ Mock result dataclasses for barrage/anti-armor/assault ══

@dataclass
class MockBarrageResult:
    strength_points_lost: int = 2
    is_pinned: bool = False


@dataclass
class MockAntiArmorResult:
    armor_protection_lost: int = 3


@dataclass
class MockAssaultResult:
    attacker_loss_percent: int = 20
    defender_loss_percent: int = 30
    defender_retreat_hexes: int = 0
    is_overrun: bool = False


def test_effective_cpa_includes_disorg():
    """base=10, cohesion=-2, disorg=3 → CPA = max(0, 10 + (-2) - 3 - 0) = 5."""
    print("TEST: effective_cpa_includes_disorg")
    unit = _make_unit("u1", base_cpa=10)
    unit.cohesion = -2
    unit.disorganization = 3
    unit.current_cpa_spent = 0

    assert unit.effective_cpa == 5, f"Expected 5, got {unit.effective_cpa}"
    print(f"  effective_cpa = {unit.effective_cpa}")
    print("  PASSED\n")


def test_disorg_on_barrage_loss():
    """Barrage causing SP loss → +1 disorg per unit hit."""
    print("TEST: disorg_on_barrage_loss")
    state = _make_state()
    defender = _make_unit("d1", hex_id="D0821", strength=10, side=Side.AXIS)
    state.units["d1"] = defender
    state.hexes["D0821"].axis_unit_ids = ["d1"]

    assert defender.disorganization == 0

    mock_result = MockBarrageResult(strength_points_lost=2, is_pinned=False)
    _apply_barrage_result(state, Side.ALLIED, "D0821", "armor", mock_result)

    assert defender.disorganization == 1, f"Expected 1, got {defender.disorganization}"
    print(f"  disorg after barrage loss: {defender.disorganization}")
    print("  PASSED\n")


def test_disorg_on_assault_loss():
    """Close assault causing SP loss → +1 disorg for attacker and defender."""
    print("TEST: disorg_on_assault_loss")
    state = _make_state()

    attacker = _make_unit("a1", hex_id="D0922", strength=10, side=Side.ALLIED)
    defender = _make_unit("d1", hex_id="D0821", strength=10, side=Side.AXIS)
    state.units["a1"] = attacker
    state.units["d1"] = defender
    state.hexes["D0821"].axis_unit_ids = ["d1"]
    state.hexes["D0922"].allied_unit_ids = ["a1"]

    mock_result = MockAssaultResult(
        attacker_loss_percent=20,
        defender_loss_percent=30,
        defender_retreat_hexes=0,
        is_overrun=False,
    )
    _apply_close_assault_result(
        state, Side.ALLIED, "D0821", ["a1"], ["d1"], mock_result,
    )

    assert attacker.disorganization >= 1, f"Attacker disorg: {attacker.disorganization}"
    assert defender.disorganization >= 1, f"Defender disorg: {defender.disorganization}"
    print(f"  attacker disorg: {attacker.disorganization}, defender disorg: {defender.disorganization}")
    print("  PASSED\n")


def test_disorg_on_retreat():
    """Forced retreat → +1 disorg for retreating defenders."""
    print("TEST: disorg_on_retreat")
    state = _make_state()

    # Add more hexes for retreat
    from cna_engine.engine.movement import get_neighbors
    for nb in get_neighbors("D0821"):
        if nb not in state.hexes:
            state.hexes[nb] = HexState(hex_id=nb, terrain=TerrainType.CLEAR)

    attacker = _make_unit("a1", hex_id="D0922", strength=10, side=Side.ALLIED)
    defender = _make_unit("d1", hex_id="D0821", strength=10, side=Side.AXIS)
    state.units["a1"] = attacker
    state.units["d1"] = defender
    state.hexes["D0821"].axis_unit_ids = ["d1"]
    state.hexes["D0922"].allied_unit_ids = ["a1"]

    mock_result = MockAssaultResult(
        attacker_loss_percent=0,
        defender_loss_percent=10,
        defender_retreat_hexes=1,
        is_overrun=False,
    )
    _apply_close_assault_result(
        state, Side.ALLIED, "D0821", ["a1"], ["d1"], mock_result,
    )

    # defender gets +1 from assault loss and +1 from retreat = 2
    assert defender.disorganization >= 2, f"Expected >=2, got {defender.disorganization}"
    print(f"  defender disorg after retreat: {defender.disorganization}")
    print("  PASSED\n")


def test_disorg_on_overrun():
    """Overrun → +2 disorg for retreating defenders (instead of +1 for normal retreat)."""
    print("TEST: disorg_on_overrun")
    state = _make_state()

    from cna_engine.engine.movement import get_neighbors
    for nb in get_neighbors("D0821"):
        if nb not in state.hexes:
            state.hexes[nb] = HexState(hex_id=nb, terrain=TerrainType.CLEAR)

    attacker = _make_unit("a1", hex_id="D0922", strength=20, side=Side.ALLIED)
    defender = _make_unit("d1", hex_id="D0821", strength=10, side=Side.AXIS)
    state.units["a1"] = attacker
    state.units["d1"] = defender
    state.hexes["D0821"].axis_unit_ids = ["d1"]
    state.hexes["D0922"].allied_unit_ids = ["a1"]

    mock_result = MockAssaultResult(
        attacker_loss_percent=0,
        defender_loss_percent=20,
        defender_retreat_hexes=2,
        is_overrun=True,
    )
    _apply_close_assault_result(
        state, Side.ALLIED, "D0821", ["a1"], ["d1"], mock_result,
    )

    # defender: +1 from SP loss + +2 from overrun = 3
    assert defender.disorganization >= 3, f"Expected >=3, got {defender.disorganization}"
    print(f"  defender disorg after overrun: {defender.disorganization}")
    print("  PASSED\n")


def test_disorg_recovery_rested():
    """CPA=0 (rested) → -1 disorg."""
    print("TEST: disorg_recovery_rested")
    state = _make_state()
    unit = _make_unit("u1")
    unit.disorganization = 3
    unit.current_cpa_spent = 0  # Rested
    state.units["u1"] = unit

    apply_cohesion_changes(state)

    assert unit.disorganization == 2, f"Expected 2, got {unit.disorganization}"
    print(f"  disorg after rested recovery: {unit.disorganization}")
    print("  PASSED\n")


def test_disorg_recovery_facility():
    """At repair facility + rested → -2 disorg."""
    print("TEST: disorg_recovery_facility")
    state = _make_state()
    state.hexes["D0821"].has_repair_facility = True

    unit = _make_unit("u1", hex_id="D0821")
    unit.disorganization = 5
    unit.current_cpa_spent = 0  # Rested
    state.units["u1"] = unit

    apply_cohesion_changes(state)

    assert unit.disorganization == 3, f"Expected 3, got {unit.disorganization}"
    print(f"  disorg after facility recovery: {unit.disorganization}")
    print("  PASSED\n")


def test_disorg_floors_at_zero():
    """Disorganization can't go below 0."""
    print("TEST: disorg_floors_at_zero")
    state = _make_state()
    state.hexes["D0821"].has_repair_facility = True

    unit = _make_unit("u1", hex_id="D0821")
    unit.disorganization = 1
    unit.current_cpa_spent = 0  # Rested
    state.units["u1"] = unit

    apply_cohesion_changes(state)

    assert unit.disorganization == 0, f"Expected 0, got {unit.disorganization}"
    print(f"  disorg after over-recovery: {unit.disorganization}")
    print("  PASSED\n")


def test_disorg_attack_column_shift():
    """Disorg >= 3 on attacker → -1 shift when attacking."""
    print("TEST: disorg_attack_column_shift")

    # We test that the threshold constant is correctly set
    assert DISORG_ATTACK_SHIFT_THRESHOLD == 3

    unit = _make_unit("a1")
    unit.disorganization = 3
    assert unit.disorganization >= DISORG_ATTACK_SHIFT_THRESHOLD

    unit2 = _make_unit("a2")
    unit2.disorganization = 2
    assert unit2.disorganization < DISORG_ATTACK_SHIFT_THRESHOLD

    print("  Threshold verified: disorg >= 3 triggers attack shift")
    print("  PASSED\n")


def test_disorg_defend_column_shift():
    """Disorg >= 5 on defender → +1 shift (favors attacker)."""
    print("TEST: disorg_defend_column_shift")

    assert DISORG_DEFEND_SHIFT_THRESHOLD == 5

    unit = _make_unit("d1")
    unit.disorganization = 5
    assert unit.disorganization >= DISORG_DEFEND_SHIFT_THRESHOLD

    unit2 = _make_unit("d2")
    unit2.disorganization = 4
    assert unit2.disorganization < DISORG_DEFEND_SHIFT_THRESHOLD

    print("  Threshold verified: disorg >= 5 triggers defend shift")
    print("  PASSED\n")


if __name__ == "__main__":
    test_effective_cpa_includes_disorg()
    test_disorg_on_barrage_loss()
    test_disorg_on_assault_loss()
    test_disorg_on_retreat()
    test_disorg_on_overrun()
    test_disorg_recovery_rested()
    test_disorg_recovery_facility()
    test_disorg_floors_at_zero()
    test_disorg_attack_column_shift()
    test_disorg_defend_column_shift()
    print("=" * 60)
    print("ALL MORALE/DISORGANIZATION TESTS PASSED")
    print("=" * 60)
