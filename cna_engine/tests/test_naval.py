"""
CNA Engine — Naval & Convoy Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.naval import (
    plan_convoy, execute_fleet_sortie, attempt_interception,
    unload_at_port, execute_convoy_phase,
    reset_fleet_for_turn, reset_convoy_for_turn,
    INTERCEPTION_BASE_THRESHOLD, CONVOY_LOSS_TABLE,
    SUPPLY_DISTRIBUTION_RATIOS, MAX_FLEET_SORTIES_PER_TURN,
)
from cna_engine.models.game_state import (
    GameState, HexState, FleetState, ConvoyState,
)
from cna_engine.models.enums import TerrainType


def _make_state_with_port(port_name="Tripoli", hex_id="A3511"):
    state = GameState()
    state.hexes[hex_id] = HexState(
        hex_id=hex_id, terrain=TerrainType.COASTAL,
        is_port=True, port_name=port_name,
    )
    state.cw_fleet = FleetState(is_available=True, sorties_remaining=2)
    state.axis_convoy = ConvoyState()
    state.axis_supply_in_tripoli_boxes = {
        "fuel": 0.0, "water": 0.0, "ammo": 0.0, "stores": 0.0,
    }
    return state


def test_plan_convoy():
    print("=" * 60)
    print("TEST 1: Convoy Planning")
    print("=" * 60)

    state = _make_state_with_port("Tripoli", "A3511")

    # Successful plan
    r = plan_convoy(state, "Tripoli", 500.0)
    assert r.success
    assert r.planned_tonnage == 500.0
    assert state.axis_convoy.planned_tonnage["Tripoli"] == 500.0
    print(f"  Plan: {r.description}")

    # Port not found
    r2 = plan_convoy(state, "Nonexistent", 200.0)
    assert not r2.success
    assert "not found" in r2.blocked_reason.lower()
    print(f"  Not found: {r2.description}")

    print("  PASSED\n")


def test_fleet_sortie():
    print("=" * 60)
    print("TEST 2: Fleet Sortie")
    print("=" * 60)

    state = _make_state_with_port()
    state.cw_fleet = FleetState(is_available=True, sorties_remaining=2)

    # Successful sortie
    r = execute_fleet_sortie(state, "central_med", ships=2)
    assert r.success
    assert state.cw_fleet.sorties_remaining == 1
    assert state.cw_fleet.ships_committed == 2
    print(f"  Sortie: {r.description}")

    # Second sortie
    r2 = execute_fleet_sortie(state, "eastern_med", ships=1)
    assert r2.success
    assert state.cw_fleet.sorties_remaining == 0
    print(f"  Second: {r2.description}")

    # No sorties remaining
    r3 = execute_fleet_sortie(state, "western_med", ships=1)
    assert not r3.success
    assert "no sorties" in r3.blocked_reason.lower()
    print(f"  No sorties: {r3.description}")

    # Fleet unavailable
    state2 = _make_state_with_port()
    state2.cw_fleet.is_available = False
    r4 = execute_fleet_sortie(state2, "central_med", ships=1)
    assert not r4.success
    assert "not available" in r4.blocked_reason.lower()
    print(f"  Unavailable: {r4.description}")

    # Fleet under repair
    state3 = _make_state_with_port()
    state3.cw_fleet.repair_turns_remaining = 2
    r5 = execute_fleet_sortie(state3, "central_med", ships=1)
    assert not r5.success
    assert "repair" in r5.blocked_reason.lower()
    print(f"  Repairing: {r5.description}")

    print("  PASSED\n")


def test_interception():
    print("=" * 60)
    print("TEST 3: Convoy Interception")
    print("=" * 60)

    state = _make_state_with_port()

    # Intercepted (roll ≤ threshold 3)
    r = attempt_interception(state, "Tripoli", 1000.0,
                             interception_roll=2, loss_roll=3)
    assert r.convoy_intercepted
    assert r.tonnage_lost > 0
    expected_pct = CONVOY_LOSS_TABLE[3]  # 0.20
    assert r.loss_percentage == expected_pct
    assert r.tonnage_delivered == 1000.0 - r.tonnage_lost
    print(f"  Intercepted: {r.description}")

    # Not intercepted (roll > threshold)
    r2 = attempt_interception(state, "Tripoli", 500.0,
                              interception_roll=6)
    assert not r2.convoy_intercepted
    assert r2.tonnage_lost == 0
    assert r2.tonnage_delivered == 500.0
    print(f"  Safe: {r2.description}")

    # Recon bonus (threshold +1 → 4)
    r3 = attempt_interception(state, "Tripoli", 500.0,
                              is_reconned=True,
                              interception_roll=4, loss_roll=1)
    assert r3.convoy_intercepted  # 4 ≤ 4
    assert r3.threshold == INTERCEPTION_BASE_THRESHOLD + 1
    print(f"  Reconned: {r3.description}")

    # CAP penalty (threshold -1 → 2)
    r4 = attempt_interception(state, "Tripoli", 500.0,
                              has_cap=True,
                              interception_roll=3)
    assert not r4.convoy_intercepted  # 3 > 2
    assert r4.threshold == INTERCEPTION_BASE_THRESHOLD - 1
    print(f"  CAP: {r4.description}")

    print("  PASSED\n")


def test_port_unload():
    print("=" * 60)
    print("TEST 4: Port Unloading")
    print("=" * 60)

    state = _make_state_with_port("Tripoli", "A3511")

    # Normal unload
    r = unload_at_port(state, "Tripoli", 80.0)
    assert r.success
    assert r.tonnage_unloaded == 80.0
    assert r.overflow_tonnage == 0
    # Check distribution
    for supply_type, ratio in SUPPLY_DISTRIBUTION_RATIOS.items():
        expected = round(80.0 * ratio, 1)
        assert r.supplies_distributed[supply_type] == expected
    print(f"  Normal: {r.description}")

    # Overflow (exceeds port efficiency)
    r2 = unload_at_port(state, "Tripoli", 150.0, max_efficiency=100.0)
    assert r2.success
    assert r2.tonnage_unloaded == 100.0
    assert r2.overflow_tonnage == 50.0
    print(f"  Overflow: {r2.description}")

    # Port not found
    r3 = unload_at_port(state, "Nonexistent", 50.0)
    assert not r3.success
    print(f"  Not found: {r3.description}")

    # Check supplies accumulated in Tripoli boxes
    total_unloaded = 80.0 + 100.0
    for supply_type, ratio in SUPPLY_DISTRIBUTION_RATIOS.items():
        expected = round(80.0 * ratio, 1) + round(100.0 * ratio, 1)
        assert abs(state.axis_supply_in_tripoli_boxes[supply_type] - expected) < 0.2
    print(f"  Supply pool updated correctly")

    print("  PASSED\n")


def test_convoy_phase():
    print("=" * 60)
    print("TEST 5: Convoy Phase Execution")
    print("=" * 60)

    state = _make_state_with_port("Tripoli", "A3511")
    # Add a second port
    state.hexes["C0512"] = HexState(
        hex_id="C0512", terrain=TerrainType.COASTAL,
        is_port=True, port_name="Tobruk",
    )
    state.turn.game_turn = 5
    state.turn.op_stage = 1

    # Plan two convoys
    state.axis_convoy.planned_tonnage = {
        "Tripoli": 600.0,
        "Tobruk": 400.0,
    }

    # Fleet sortied (will attempt interception)
    state.cw_fleet.is_available = True
    state.cw_fleet.ships_committed = 2

    r = execute_convoy_phase(
        state,
        interception_rolls={"Tripoli": 2, "Tobruk": 6},  # Tripoli intercepted, Tobruk safe
        loss_rolls={"Tripoli": 4},  # 25% loss
    )
    assert r.convoys_planned == 2
    assert r.tonnage_shipped == 1000.0
    assert r.tonnage_lost > 0  # Tripoli was intercepted
    assert r.tonnage_delivered < 1000.0
    assert len(r.interceptions) == 2
    assert len(r.port_unloads) >= 1  # At least one port got supplies
    print(f"  Phase: {r.description}")
    for ir in r.interceptions:
        print(f"    {ir.description}")

    print("  PASSED\n")


def test_convoy_phase_no_fleet():
    print("=" * 60)
    print("TEST 6: Convoy Phase — No Fleet Active")
    print("=" * 60)

    state = _make_state_with_port("Tripoli", "A3511")
    state.turn.game_turn = 3
    state.turn.op_stage = 1

    state.axis_convoy.planned_tonnage = {"Tripoli": 500.0}
    state.cw_fleet.is_available = True
    state.cw_fleet.ships_committed = 0  # Fleet didn't sortie

    r = execute_convoy_phase(state)
    assert r.convoys_planned == 1
    assert r.tonnage_shipped == 500.0
    assert r.tonnage_lost == 0  # No interception
    assert r.tonnage_delivered == 500.0
    assert len(r.interceptions) == 0
    print(f"  No fleet: {r.description}")

    print("  PASSED\n")


def test_fleet_reset():
    print("=" * 60)
    print("TEST 7: Fleet and Convoy Reset")
    print("=" * 60)

    state = _make_state_with_port()

    # Fleet under repair
    state.cw_fleet.is_available = False
    state.cw_fleet.repair_turns_remaining = 2
    state.cw_fleet.sorties_remaining = 0
    state.cw_fleet.ships_committed = 3

    reset_fleet_for_turn(state)
    assert state.cw_fleet.repair_turns_remaining == 1
    assert not state.cw_fleet.is_available  # Still repairing
    assert state.cw_fleet.sorties_remaining == MAX_FLEET_SORTIES_PER_TURN
    assert state.cw_fleet.ships_committed == 0
    print(f"  Fleet mid-repair: repair={state.cw_fleet.repair_turns_remaining}")

    # One more turn of repair
    reset_fleet_for_turn(state)
    assert state.cw_fleet.repair_turns_remaining == 0
    assert state.cw_fleet.is_available  # Repaired!
    print(f"  Fleet repaired: available={state.cw_fleet.is_available}")

    # Convoy reset
    state.axis_convoy.planned_tonnage = {"Tripoli": 500}
    state.axis_convoy.losses_this_turn = 100
    reset_convoy_for_turn(state)
    assert len(state.axis_convoy.planned_tonnage) == 0
    assert state.axis_convoy.losses_this_turn == 0
    print(f"  Convoy reset")

    print("  PASSED\n")


def main():
    test_plan_convoy()
    test_fleet_sortie()
    test_interception()
    test_port_unload()
    test_convoy_phase()
    test_convoy_phase_no_fleet()
    test_fleet_reset()
    print("=" * 60)
    print("ALL NAVAL/CONVOY TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
