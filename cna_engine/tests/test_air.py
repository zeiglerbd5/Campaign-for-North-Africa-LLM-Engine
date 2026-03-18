"""
CNA Engine — Air Game Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.air import (
    assign_mission, resolve_air_combat, resolve_flak,
    resolve_bombardment, resolve_strafing, resolve_recon,
    fly_sortie, execute_air_phase,
    get_aircraft_stats, _load_aircraft_chars, _parse_int_or_zero,
    AIR_COMBAT_BASE_KILL_CHANCE, MAINTENANCE_CHANCE,
)
from cna_engine.models.game_state import (
    GameState, Aircraft, SGSU, HexState, Unit, TOEStrength, Pilot,
)
from cna_engine.models.enums import (
    Side, AircraftStatus, AircraftMission, TerrainType, MotorizationType,
)


def _make_aircraft(ac_id, side=Side.ALLIED, status=AircraftStatus.READY,
                   sgsu_id="sgsu1", bombload=2, tacair=2):
    return Aircraft(
        id=ac_id, aircraft_type_id="hurricane_i", side=side,
        sgsu_id=sgsu_id, status=status, mission=AircraftMission.NONE,
        bombload_remaining=bombload, tacair_remaining=tacair,
    )


def _make_sgsu(sgsu_id, side=Side.ALLIED, hex_id="D1022", operational=True):
    return SGSU(
        id=sgsu_id, side=side, hex_id=hex_id,
        capacity=10, is_operational=operational,
    )


def _make_unit(uid, side=Side.ALLIED, hex_id="D0821", strength=10):
    return Unit(
        id=uid, name=f"Unit {uid}", side=side, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id=hex_id,
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=strength),
        toe_strength=TOEStrength(infantry=strength),
    )


def test_assign_mission():
    print("=" * 60)
    print("TEST 1: Mission Assignment")
    print("=" * 60)

    state = GameState()
    state.sgsus["sgsu1"] = _make_sgsu("sgsu1")
    state.aircraft["ac1"] = _make_aircraft("ac1")

    # Successful assignment
    r = assign_mission(state, "ac1", AircraftMission.BOMBING, "D1222")
    assert r.success
    assert state.aircraft["ac1"].status == AircraftStatus.FLEW_THIS_STAGE
    assert state.aircraft["ac1"].mission == AircraftMission.BOMBING
    print(f"  Assign bombing: {r.description}")

    # Not ready (already flew)
    r2 = assign_mission(state, "ac1", AircraftMission.RECON, "D1222")
    assert not r2.success
    assert "READY" in r2.blocked_reason or "ready" in r2.blocked_reason.lower()
    print(f"  Not ready: {r2.description}")

    # Aircraft not found
    r3 = assign_mission(state, "nonexistent", AircraftMission.RECON, "D1222")
    assert not r3.success
    print(f"  Not found: {r3.description}")

    # Non-operational SGSU
    state.aircraft["ac2"] = _make_aircraft("ac2", sgsu_id="sgsu_bad")
    state.sgsus["sgsu_bad"] = _make_sgsu("sgsu_bad", operational=False)
    r4 = assign_mission(state, "ac2", AircraftMission.BOMBING, "D1222")
    assert not r4.success
    assert "operational" in r4.blocked_reason.lower()
    print(f"  Bad SGSU: {r4.description}")

    print("  PASSED\n")


def test_air_combat():
    print("=" * 60)
    print("TEST 2: Air-to-Air Combat")
    print("=" * 60)

    state = GameState()
    state.aircraft["ac_a"] = _make_aircraft("ac_a", side=Side.ALLIED)
    state.aircraft["ac_x"] = _make_aircraft("ac_x", side=Side.AXIS)

    # Deterministic roll: attacker rolls low (should kill with base chance 3)
    r = resolve_air_combat(state, "ac_a", "ac_x", dice_roll=1)
    assert r.attacker_kills  # Roll 1 ≤ 3 → kill
    print(f"  Low roll: {r.description}")

    # Reset aircraft for next test
    state.aircraft["ac_a"] = _make_aircraft("ac_a", side=Side.ALLIED)
    state.aircraft["ac_x"] = _make_aircraft("ac_x", side=Side.AXIS)

    # Attacker rolls high (miss)
    r2 = resolve_air_combat(state, "ac_a", "ac_x", dice_roll=6)
    assert not r2.attacker_kills  # Roll 6 > 3 → miss
    print(f"  High roll: {r2.description}")

    # Missing aircraft
    r3 = resolve_air_combat(state, "missing", "ac_x", dice_roll=1)
    assert not r3.attacker_kills
    print(f"  Missing: {r3.description}")

    print("  PASSED\n")


def test_flak():
    print("=" * 60)
    print("TEST 3: Flak Resolution")
    print("=" * 60)

    state = GameState()
    state.aircraft["ac1"] = _make_aircraft("ac1")

    # Zero flak — always miss
    r = resolve_flak(state, "ac1", flak_points=0, dice_roll=1)
    assert not r.is_hit
    print(f"  Zero flak: {r.description}")

    # High flak, low roll — hit
    state.aircraft["ac2"] = _make_aircraft("ac2")
    r2 = resolve_flak(state, "ac2", flak_points=6, dice_roll=1)
    assert r2.is_hit
    print(f"  Hit (flak=6, roll=1): {r2.description}")

    # High flak, high roll — miss
    state.aircraft["ac3"] = _make_aircraft("ac3")
    r3 = resolve_flak(state, "ac3", flak_points=3, dice_roll=6)
    assert not r3.is_hit
    print(f"  Miss (flak=3, roll=6): {r3.description}")

    # Aircraft not found
    r4 = resolve_flak(state, "missing", flak_points=3, dice_roll=1)
    assert not r4.is_hit
    print(f"  Missing: {r4.description}")

    print("  PASSED\n")


def test_bombardment():
    print("=" * 60)
    print("TEST 4: Air Bombardment")
    print("=" * 60)

    state = GameState()
    ac = _make_aircraft("ac1", bombload=3)
    state.aircraft["ac1"] = ac

    r = resolve_bombardment(state, "ac1", "D1222", target_class="infantry")
    assert r.bombload_used == 3
    assert r.barrage_equivalent == 6  # 3 * 2
    assert ac.bombload_remaining == 0
    assert r.effect != ""
    print(f"  Bombardment: {r.description}")

    # No bombload left
    r2 = resolve_bombardment(state, "ac1", "D1222")
    assert r2.effect == "no_effect"
    print(f"  No bombs: {r2.description}")

    # Missing aircraft
    r3 = resolve_bombardment(state, "missing", "D1222")
    assert r3.effect == "no_effect"
    print(f"  Missing: {r3.description}")

    print("  PASSED\n")


def test_strafing():
    print("=" * 60)
    print("TEST 5: Strafing")
    print("=" * 60)

    state = GameState()
    ac = _make_aircraft("ac1", tacair=4)
    state.aircraft["ac1"] = ac

    r = resolve_strafing(state, "ac1", "D1222", target_class="truck")
    assert r.tacair_used == 4
    assert r.barrage_equivalent == 4  # 4 * 1
    assert ac.tacair_remaining == 0
    print(f"  Strafing: {r.description}")

    # No tacair left
    r2 = resolve_strafing(state, "ac1", "D1222")
    assert r2.effect == "no_effect"
    print(f"  No tacair: {r2.description}")

    print("  PASSED\n")


def test_recon():
    print("=" * 60)
    print("TEST 6: Reconnaissance")
    print("=" * 60)

    state = GameState()
    ac = _make_aircraft("ac1", side=Side.ALLIED)
    state.aircraft["ac1"] = ac

    # Set up target hex with enemy
    state.hexes["D1222"] = HexState(hex_id="D1222", terrain=TerrainType.CLEAR)
    enemy = _make_unit("e1", side=Side.AXIS, hex_id="D1222")
    state.units["e1"] = enemy
    state.hexes["D1222"].axis_unit_ids.append("e1")

    r = resolve_recon(state, "ac1", "D1222", sighting_radius=1)
    assert "D1222" in r.hexes_sighted
    assert len(r.hexes_sighted) >= 1
    # Target hex should be sighted
    assert state.hexes["D1222"].allied_sighted
    print(f"  Recon: {r.description}")
    print(f"  Hexes sighted: {len(r.hexes_sighted)}")
    print(f"  Units spotted: {r.units_spotted}")

    # Missing aircraft
    r2 = resolve_recon(state, "missing", "D1222")
    assert len(r2.hexes_sighted) == 0
    print(f"  Missing: {r2.description}")

    print("  PASSED\n")


def test_fly_sortie():
    print("=" * 60)
    print("TEST 7: Complete Sortie")
    print("=" * 60)

    state = GameState()
    state.sgsus["sgsu1"] = _make_sgsu("sgsu1")

    # Successful bombing sortie, no flak, no intercept, no maintenance
    ac = _make_aircraft("ac1", bombload=3)
    state.aircraft["ac1"] = ac

    r = fly_sortie(state, "ac1", AircraftMission.BOMBING, "D1222",
                   dice_overrides={"maintenance": 6})  # No maintenance
    assert r.success
    assert not r.intercepted
    assert not r.flak_encountered
    assert not r.needs_maintenance
    assert r.mission_result is not None
    print(f"  Clean sortie: {r.description}")

    # Sortie with flak (high flak, low roll = hit)
    state.aircraft["ac2"] = _make_aircraft("ac2", bombload=2)
    r2 = fly_sortie(state, "ac2", AircraftMission.BOMBING, "D1222",
                    flak_points=6, dice_overrides={"flak": 1, "maintenance": 6})
    # Flak hit at roll=1 with 6 flak points
    assert r2.flak_encountered
    assert r2.flak.is_hit
    print(f"  Flak hit: {r2.description}")

    # Sortie with maintenance needed
    state.aircraft["ac3"] = _make_aircraft("ac3", bombload=2)
    r3 = fly_sortie(state, "ac3", AircraftMission.BOMBING, "D1222",
                    dice_overrides={"maintenance": 1})  # Roll 1 ≤ 2 = maintenance
    assert r3.needs_maintenance
    print(f"  Maintenance: {r3.description}")

    # Failed sortie (aircraft not ready)
    state.aircraft["ac4"] = _make_aircraft("ac4", status=AircraftStatus.MAINTENANCE)
    r4 = fly_sortie(state, "ac4", AircraftMission.BOMBING, "D1222")
    assert not r4.success
    print(f"  Not ready: {r4.description}")

    print("  PASSED\n")


def test_air_phase():
    print("=" * 60)
    print("TEST 8: Air Phase Execution")
    print("=" * 60)

    state = GameState()
    state.turn.game_turn = 3
    state.turn.op_stage = 1

    # Some aircraft that flew last stage should reset
    ac1 = _make_aircraft("ac1", status=AircraftStatus.FLEW_THIS_STAGE)
    ac2 = _make_aircraft("ac2", status=AircraftStatus.MAINTENANCE)
    ac3 = _make_aircraft("ac3", side=Side.AXIS, status=AircraftStatus.FLEW_THIS_STAGE)
    state.aircraft["ac1"] = ac1
    state.aircraft["ac2"] = ac2
    state.aircraft["ac3"] = ac3

    r = execute_air_phase(state)
    assert r.game_turn == 3
    assert r.op_stage == 1
    # ac1 should be reset to READY
    assert ac1.status == AircraftStatus.READY
    assert ac1.mission == AircraftMission.NONE
    # ac2 stays in maintenance
    assert ac2.status == AircraftStatus.MAINTENANCE
    # ac3 should be reset to READY
    assert ac3.status == AircraftStatus.READY
    print(f"  Phase: {r.description}")

    print("  PASSED\n")


def test_aircraft_characteristics():
    print("=" * 60)
    print("TEST 9: Aircraft Characteristics Lookup")
    print("=" * 60)

    # Hurricane I: maneuver=34, tacair=4, bombload=2, range=60
    man, tac, bomb, rng = get_aircraft_stats("hurricane_i")
    assert man == 34, f"Hurricane I maneuver: expected 34, got {man}"
    assert tac == 4, f"Hurricane I tacair: expected 4, got {tac}"
    assert bomb == 2, f"Hurricane I bombload: expected 2, got {bomb}"
    assert rng == 60, f"Hurricane I range: expected 60, got {rng}"
    print(f"  Hurricane I: man={man}, tac={tac}, bomb={bomb}, range={rng}")

    # CR.42: maneuver=26, tacair=2, bombload=0 (dash), range=50
    man, tac, bomb, rng = get_aircraft_stats("cr42")
    assert man == 26, f"CR.42 maneuver: expected 26, got {man}"
    assert tac == 2
    assert bomb == 0, f"CR.42 bombload: expected 0, got {bomb}"
    assert rng == 50
    print(f"  CR.42: man={man}, tac={tac}, bomb={bomb}, range={rng}")

    # Bf.110: maneuver=30 (30/32 → takes first), range=70
    man, tac, bomb, rng = get_aircraft_stats("bf110")
    assert man == 30, f"Bf.110 maneuver: expected 30, got {man}"
    assert bomb == 4
    print(f"  Bf.110: man={man}, tac={tac}, bomb={bomb}, range={rng}")

    # Gladiator: bombload is "—" → 0
    man, tac, bomb, rng = get_aircraft_stats("gladiator")
    assert man == 28
    assert bomb == 0
    print(f"  Gladiator: man={man}, tac={tac}, bomb={bomb}, range={rng}")

    # Unknown type → defaults
    man, tac, bomb, rng = get_aircraft_stats("nonexistent_type")
    assert man == 30  # default
    assert tac == 0
    assert bomb == 0
    print(f"  Unknown: man={man}, tac={tac}, bomb={bomb}, range={rng}")

    # _parse_int_or_zero edge cases
    assert _parse_int_or_zero("—") == 0
    assert _parse_int_or_zero("-") == 0
    assert _parse_int_or_zero("4") == 4
    assert _parse_int_or_zero(6) == 6
    print(f"  _parse_int_or_zero edge cases verified")

    print("  PASSED\n")


def test_air_combat_with_real_stats():
    print("=" * 60)
    print("TEST 10: Air Combat with Real Aircraft Stats")
    print("=" * 60)

    state = GameState()

    # Hurricane I (maneuver=34) vs CR.42 (maneuver=26) → +8 differential
    state.aircraft["hurr"] = Aircraft(
        id="hurr", aircraft_type_id="hurricane_i", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=2, tacair_remaining=4,
    )
    state.aircraft["cr42"] = Aircraft(
        id="cr42", aircraft_type_id="cr42", side=Side.AXIS,
        sgsu_id="sgsu2", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=0, tacair_remaining=2,
    )

    r = resolve_air_combat(state, "hurr", "cr42", dice_roll=3)
    assert r.attacker_maneuver == 34, f"Expected 34, got {r.attacker_maneuver}"
    assert r.defender_maneuver == 26, f"Expected 26, got {r.defender_maneuver}"
    assert r.differential == 8, f"Expected differential +8, got {r.differential}"
    print(f"  Hurricane vs CR.42: man={r.attacker_maneuver} vs {r.defender_maneuver}, diff={r.differential}")

    # Gladiator (28) vs Bf109E (38) → -10 differential
    state.aircraft["glad"] = Aircraft(
        id="glad", aircraft_type_id="gladiator", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=0, tacair_remaining=2,
    )
    state.aircraft["bf109"] = Aircraft(
        id="bf109", aircraft_type_id="bf109e", side=Side.AXIS,
        sgsu_id="sgsu2", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=2, tacair_remaining=4,
    )

    r2 = resolve_air_combat(state, "glad", "bf109", dice_roll=3)
    assert r2.attacker_maneuver == 28
    assert r2.defender_maneuver == 38
    assert r2.differential == -10
    print(f"  Gladiator vs Bf109E: man={r2.attacker_maneuver} vs {r2.defender_maneuver}, diff={r2.differential}")

    print("  PASSED\n")


def test_pilot_experience_bonus():
    print("=" * 60)
    print("TEST 11: Pilot Experience Bonus in Air Combat")
    print("=" * 60)

    state = GameState()
    state.aircraft["hurr"] = Aircraft(
        id="hurr", aircraft_type_id="hurricane_i", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        pilot_id="ace_pilot", bombload_remaining=2, tacair_remaining=4,
    )
    state.aircraft["cr42"] = Aircraft(
        id="cr42", aircraft_type_id="cr42", side=Side.AXIS,
        sgsu_id="sgsu2", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=0, tacair_remaining=2,
    )
    state.pilots["ace_pilot"] = Pilot(
        id="ace_pilot", name="Ace", side=Side.ALLIED,
        nationality="british", experience=3,  # ace = +6
    )

    r = resolve_air_combat(state, "hurr", "cr42", dice_roll=3)
    # Hurricane (34) + ace bonus (3*2=6) = 40 vs CR.42 (26) → diff = +14
    assert r.attacker_maneuver == 40, f"Expected 40, got {r.attacker_maneuver}"
    assert r.differential == 14, f"Expected 14, got {r.differential}"
    print(f"  Ace pilot: man={r.attacker_maneuver} vs {r.defender_maneuver}, diff={r.differential}")

    print("  PASSED\n")


def test_range_validation():
    print("=" * 60)
    print("TEST 12: Range Validation")
    print("=" * 60)

    state = GameState()
    state.sgsus["sgsu1"] = _make_sgsu("sgsu1", hex_id="D0821")  # Sidi Barrani area

    # Gladiator: range=50, effective=25 hexes round trip
    # D0821 to D3421 = 26 hexes — should be out of range
    ac = Aircraft(
        id="glad1", aircraft_type_id="gladiator", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=0, tacair_remaining=2,
    )
    state.aircraft["glad1"] = ac

    # Need hexes for distance calc
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D3421"] = HexState(hex_id="D3421", terrain=TerrainType.CLEAR)

    r = fly_sortie(state, "glad1", AircraftMission.RECON, "D3421",
                   dice_overrides={"maintenance": 6})
    assert not r.success, "Should fail — D3421 out of range for Gladiator from D0821"
    assert "out of range" in r.description.lower()
    print(f"  Out of range: {r.description}")

    # Hurricane I: range=60, effective=30. D0821 to D2821 = 20 hexes — in range
    ac2 = Aircraft(
        id="hurr1", aircraft_type_id="hurricane_i", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.READY, mission=AircraftMission.NONE,
        bombload_remaining=2, tacair_remaining=4,
    )
    state.aircraft["hurr1"] = ac2
    state.hexes["D2821"] = HexState(hex_id="D2821", terrain=TerrainType.CLEAR)

    r2 = fly_sortie(state, "hurr1", AircraftMission.BOMBING, "D2821",
                    dice_overrides={"maintenance": 6})
    assert r2.success, f"Should succeed — D2821 in range for Hurricane. Got: {r2.description}"
    print(f"  In range: {r2.description}")

    print("  PASSED\n")


def test_rearm_after_phase():
    print("=" * 60)
    print("TEST 13: Rearm After Air Phase")
    print("=" * 60)

    state = GameState()
    state.turn.game_turn = 3
    state.turn.op_stage = 1

    # Hurricane I that flew and used all ordnance
    ac = Aircraft(
        id="hurr1", aircraft_type_id="hurricane_i", side=Side.ALLIED,
        sgsu_id="sgsu1", status=AircraftStatus.FLEW_THIS_STAGE,
        mission=AircraftMission.BOMBING,
        bombload_remaining=0, tacair_remaining=0,
    )
    state.aircraft["hurr1"] = ac

    # Execute air phase — should reset and rearm
    execute_air_phase(state)

    assert ac.status == AircraftStatus.READY
    assert ac.mission == AircraftMission.NONE
    # Hurricane I: tacair=4, bombload=2
    assert ac.tacair_remaining == 4, f"Expected tacair=4 after rearm, got {ac.tacair_remaining}"
    assert ac.bombload_remaining == 2, f"Expected bombload=2 after rearm, got {ac.bombload_remaining}"
    print(f"  Rearmed: tacair={ac.tacair_remaining}, bombload={ac.bombload_remaining}")

    print("  PASSED\n")


def test_scenario_air_oob():
    print("=" * 60)
    print("TEST 14: Scenario Loads Full Air OOB")
    print("=" * 60)

    from cna_engine.engine.scenario import load_operation_compass

    state, _ = load_operation_compass()

    # 20 ready + 7 NOT_YET_ARRIVED = 27 total
    assert len(state.aircraft) == 27, f"Expected 27 aircraft, got {len(state.aircraft)}"
    ready = sum(1 for a in state.aircraft.values() if a.status == AircraftStatus.READY)
    assert ready == 20, f"Expected 20 ready, got {ready}"
    print(f"  Total aircraft: {len(state.aircraft)} ({ready} ready)")

    # 6 SGSUs (2 RAF + 2 RA + 1 RA Tripoli + 1 LW Derna)
    assert len(state.sgsus) == 6, f"Expected 6 SGSUs, got {len(state.sgsus)}"
    print(f"  SGSUs: {len(state.sgsus)}")

    print("  PASSED\n")


def main():
    test_assign_mission()
    test_air_combat()
    test_flak()
    test_bombardment()
    test_strafing()
    test_recon()
    test_fly_sortie()
    test_air_phase()
    test_aircraft_characteristics()
    test_air_combat_with_real_stats()
    test_pilot_experience_bonus()
    test_range_validation()
    test_rearm_after_phase()
    test_scenario_air_oob()
    print("=" * 60)
    print("ALL AIR GAME TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
