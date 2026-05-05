"""
CNA Engine — Agent Interface Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.agent_interface import (
    get_state_view, validate_command, execute_command,
    generate_agent_prompt,
    ROLE_COMMANDER, ROLE_GROUND, ROLE_LOGISTICS,
    ROLE_AIR, ROLE_NAVAL, ROLE_OBSERVER,
    VALID_ROLES, ROLE_COMMANDS,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState,
    TOEStrength, UnitSupply, SupplyDump, Aircraft, SGSU,
    FleetState, ConvoyState,
)
from cna_engine.models.enums import (
    Side, UnitStatus, GamePhase, TerrainType,
    MotorizationType, AircraftStatus,
)


def _make_state():
    """Build a minimal game state for testing."""
    state = GameState()
    state.turn = TurnState(
        game_turn=5, op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase="movement",
        active_side=Side.ALLIED,
    )

    # Allied unit
    state.units["u1"] = Unit(
        id="u1", name="2nd RTR", side=Side.ALLIED, nationality="british",
        unit_class="armor", unit_size="battalion",
        motorization=MotorizationType.MECHANIZED, hex_id="D0821",
        base_cpa=10, stacking_points=3,
        current_strength=TOEStrength(armor=12),
        toe_strength=TOEStrength(armor=12),
        supply=UnitSupply(fuel=10.0, water=8.0, ammo=12.0, stores=5.0,
                          fuel_capacity=30.0, water_capacity=16.0,
                          ammo_capacity=24.0, stores_capacity=10.0),
    )

    # Axis unit
    state.units["e1"] = Unit(
        id="e1", name="1st Libyan Bn", side=Side.AXIS, nationality="italian",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D0922",
        base_cpa=6, stacking_points=2,
        current_strength=TOEStrength(infantry=10),
        toe_strength=TOEStrength(infantry=10),
    )

    # Hexes
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0821"].allied_unit_ids = ["u1"]
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR)
    state.hexes["D0922"].axis_unit_ids = ["e1"]
    state.hexes["D0922"].allied_sighted = True  # Sighted by Allied

    # Supply dump
    state.hexes["D0821"].supply_dumps.append(SupplyDump(
        id="depot1", side=Side.ALLIED, is_real=True, fuel=100.0,
    ))

    # Aircraft
    state.aircraft["ac1"] = Aircraft(
        id="ac1", aircraft_type_id="hurricane_i", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY,
    )
    state.sgsus["sgsu1"] = SGSU(
        id="sgsu1", side=Side.ALLIED, hex_id="D1022",
        capacity=10, aircraft_ids=["ac1"], is_operational=True,
    )

    # Fleet
    state.cw_fleet = FleetState(is_available=True, sorties_remaining=2)
    state.axis_convoy = ConvoyState()

    # Supply pools
    state.allied_supply_in_egypt = {
        "fuel": 5000.0, "water": 5000.0, "ammo": 3000.0, "stores": 2000.0,
    }
    state.axis_supply_in_tripoli_boxes = {
        "fuel": 2000.0, "water": 1000.0, "ammo": 1500.0, "stores": 800.0,
    }

    return state


def test_state_view_ground():
    print("=" * 60)
    print("TEST 1: State View — Ground Role")
    print("=" * 60)

    state = _make_state()
    view = get_state_view(state, ROLE_GROUND, Side.ALLIED)

    assert view.role == ROLE_GROUND
    assert view.side == Side.ALLIED
    assert view.game_turn == 5
    assert view.phase == GamePhase.OP_STAGE
    assert "move_unit" in view.available_commands
    assert "fire_barrage" in view.available_commands
    print(f"  Commands: {view.available_commands}")

    # Should see friendly unit with CPA info
    friendly = [u for u in view.visible_units if not u.get("enemy")]
    assert len(friendly) >= 1
    assert any(u["id"] == "u1" for u in friendly)
    u1_view = next(u for u in friendly if u["id"] == "u1")
    assert "cpa_remaining" in u1_view
    assert "is_motorized" in u1_view
    print(f"  Friendly unit: {u1_view}")

    # Should see sighted enemy
    enemies = [u for u in view.visible_units if u.get("enemy")]
    assert len(enemies) >= 1
    assert enemies[0]["id"] == "e1"
    print(f"  Sighted enemy: {enemies[0]}")

    print(f"  View: {view.description}")
    print("  PASSED\n")


def test_state_view_logistics():
    print("=" * 60)
    print("TEST 2: State View — Logistics Role")
    print("=" * 60)

    state = _make_state()
    view = get_state_view(state, ROLE_LOGISTICS, Side.ALLIED)

    assert view.supply_summary is not None
    assert view.supply_summary["total_fuel"] > 0
    assert view.supply_summary["units_tracked"] >= 1
    print(f"  Supply summary: {view.supply_summary}")

    # Logistics view should include supply details on units
    u1_view = next(u for u in view.visible_units if u.get("id") == "u1")
    assert "fuel" in u1_view
    assert "truck_points" in u1_view
    assert "truck_cargo" in u1_view
    print(f"  Unit supply view: fuel={u1_view['fuel']}, water={u1_view['water']}")

    assert "consume_fuel" in view.available_commands
    assert "truck_attach" in view.available_commands
    print(f"  Commands: {view.available_commands}")

    print("  PASSED\n")


def test_state_view_observer():
    print("=" * 60)
    print("TEST 3: State View — Observer Role")
    print("=" * 60)

    state = _make_state()
    view = get_state_view(state, ROLE_OBSERVER, Side.ALLIED)

    assert view.overview is not None
    # Observer sees ALL units
    assert len(view.visible_units) == 2  # u1 + e1
    # Observer sees all hexes
    assert len(view.visible_hexes) >= 2
    # Observer has no commands
    assert len(view.available_commands) == 0
    print(f"  Units visible: {len(view.visible_units)}")
    print(f"  Hexes visible: {len(view.visible_hexes)}")
    print(f"  Overview keys: {list(view.overview.keys()) if isinstance(view.overview, dict) else 'summary'}")

    print("  PASSED\n")


def test_state_view_air():
    print("=" * 60)
    print("TEST 4: State View — Air Role")
    print("=" * 60)

    state = _make_state()
    view = get_state_view(state, ROLE_AIR, Side.ALLIED)

    assert view.air_summary is not None
    assert view.air_summary["total_aircraft"] >= 1
    assert "assign_mission" in view.available_commands
    assert "fly_sortie" in view.available_commands
    print(f"  Air summary: {view.air_summary}")

    print("  PASSED\n")


def test_state_view_naval():
    print("=" * 60)
    print("TEST 5: State View — Naval Role")
    print("=" * 60)

    state = _make_state()

    # Allied naval
    view_a = get_state_view(state, ROLE_NAVAL, Side.ALLIED)
    assert view_a.naval_summary is not None
    assert view_a.naval_summary["fleet_available"]
    assert view_a.naval_summary["sorties_remaining"] == 2
    print(f"  Allied naval: {view_a.naval_summary}")

    # Axis naval
    view_x = get_state_view(state, ROLE_NAVAL, Side.AXIS)
    assert view_x.naval_summary is not None
    assert "planned_tonnage" in view_x.naval_summary
    print(f"  Axis naval: {view_x.naval_summary}")

    print("  PASSED\n")


def test_state_view_invalid():
    print("=" * 60)
    print("TEST 6: State View — Invalid Role")
    print("=" * 60)

    state = _make_state()
    view = get_state_view(state, "hacker", Side.ALLIED)
    assert "Invalid" in view.description or "invalid" in view.description.lower()
    print(f"  Invalid role: {view.description}")

    print("  PASSED\n")


def test_validate_command():
    print("=" * 60)
    print("TEST 7: Command Validation")
    print("=" * 60)

    state = _make_state()

    # Valid command
    r = validate_command(state, ROLE_GROUND, Side.ALLIED, "move_unit", unit_id="u1")
    assert r.success
    print(f"  Valid: {r.description}")

    # Invalid role
    r2 = validate_command(state, "hacker", Side.ALLIED, "move_unit")
    assert not r2.success
    assert "Invalid role" in r2.error
    print(f"  Invalid role: {r2.error}")

    # Command not in role
    r3 = validate_command(state, ROLE_GROUND, Side.ALLIED, "plan_convoy")
    assert not r3.success
    assert "not available" in r3.error.lower()
    print(f"  Wrong role: {r3.error}")

    # Wrong side's turn
    r4 = validate_command(state, ROLE_GROUND, Side.AXIS, "move_unit", unit_id="e1")
    assert not r4.success
    assert "turn" in r4.error.lower() or "active" in r4.error.lower()
    print(f"  Wrong side: {r4.error}")

    # Unit not found
    r5 = validate_command(state, ROLE_GROUND, Side.ALLIED, "move_unit", unit_id="missing")
    assert not r5.success
    assert "not found" in r5.error.lower()
    print(f"  Unit missing: {r5.error}")

    # Can't command enemy unit
    r6 = validate_command(state, ROLE_GROUND, Side.ALLIED, "move_unit", unit_id="e1")
    assert not r6.success
    assert "enemy" in r6.error.lower()
    print(f"  Enemy unit: {r6.error}")

    # Observer can't issue commands
    r7 = validate_command(state, ROLE_OBSERVER, Side.ALLIED, "move_unit")
    assert not r7.success
    print(f"  Observer: {r7.error}")

    # end_phase works even when not your turn
    r8 = validate_command(state, ROLE_GROUND, Side.AXIS, "end_phase")
    assert r8.success
    print(f"  end_phase: {r8.description}")

    print("  PASSED\n")


def test_execute_command():
    print("=" * 60)
    print("TEST 8: Command Execution")
    print("=" * 60)

    state = _make_state()

    # Check supply status (should work through command routing)
    r = execute_command(state, ROLE_LOGISTICS, Side.ALLIED, "check_supply",
                        unit_id="u1")
    assert r.success
    assert r.result is not None
    print(f"  check_supply: {r.description}")

    # end_phase
    r2 = execute_command(state, ROLE_GROUND, Side.ALLIED, "end_phase")
    assert r2.success
    assert r2.result["acknowledged"]
    print(f"  end_phase: {r2.description}")

    # Invalid command for role
    r3 = execute_command(state, ROLE_GROUND, Side.ALLIED, "plan_convoy",
                         port="Tripoli", tonnage=500)
    assert not r3.success
    print(f"  Wrong role: {r3.error}")

    # Place in reserve (commander command)
    r4 = execute_command(state, ROLE_COMMANDER, Side.ALLIED, "place_reserve",
                         unit_id="u1")
    assert r4.success
    assert state.units["u1"].status == UnitStatus.IN_RESERVE
    print(f"  reserve: {r4.description}")

    print("  PASSED\n")


def test_generate_prompt():
    print("=" * 60)
    print("TEST 9: Agent Prompt Generation")
    print("=" * 60)

    state = _make_state()

    for role in [ROLE_COMMANDER, ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL]:
        prompt = generate_agent_prompt(state, role, Side.ALLIED)
        assert f"CNA AGENT: {role.upper()}" in prompt
        assert "GT5" in prompt
        assert "Available Commands:" in prompt
        print(f"  {role}: {len(prompt)} chars, starts with:")
        print(f"    {prompt.split(chr(10))[0]}")

    # Observer prompt
    obs_prompt = generate_agent_prompt(state, ROLE_OBSERVER, Side.ALLIED)
    assert "OBSERVER" in obs_prompt
    print(f"  observer: {len(obs_prompt)} chars")

    print("  PASSED\n")


def test_role_command_completeness():
    print("=" * 60)
    print("TEST 10: Role-Command Matrix")
    print("=" * 60)

    # Every role should be in VALID_ROLES
    for role in ROLE_COMMANDS:
        assert role in VALID_ROLES
    print(f"  All roles valid: {sorted(VALID_ROLES)}")

    # Ground should have movement + combat
    ground_cmds = ROLE_COMMANDS[ROLE_GROUND]
    assert "move_unit" in ground_cmds
    assert "fire_barrage" in ground_cmds
    assert "close_assault" in ground_cmds
    print(f"  Ground: {ground_cmds}")

    # Logistics should have supply commands
    log_cmds = ROLE_COMMANDS[ROLE_LOGISTICS]
    assert "consume_fuel" in log_cmds
    assert "truck_attach" in log_cmds
    assert "create_dump" in log_cmds
    print(f"  Logistics: {log_cmds}")

    # Observer has no commands
    assert len(ROLE_COMMANDS[ROLE_OBSERVER]) == 0
    print(f"  Observer: [] (read-only)")

    print("  PASSED\n")


def main():
    test_state_view_ground()
    test_state_view_logistics()
    test_state_view_observer()
    test_state_view_air()
    test_state_view_naval()
    test_state_view_invalid()
    test_validate_command()
    test_execute_command()
    test_generate_prompt()
    test_role_command_completeness()
    print("=" * 60)
    print("ALL AGENT INTERFACE TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
