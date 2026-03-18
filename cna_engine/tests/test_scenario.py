"""
CNA Engine — Scenario & OOB Loader Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.scenario import (
    load_scenario, load_operation_compass, process_reinforcements,
    process_air_reinforcements, get_air_reinforcement_schedule,
    Reinforcement, AirReinforcement, ScenarioLoadResult,
)
from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType, GamePhase, Weather, Season,
    AircraftStatus,
)


def test_load_operation_compass():
    print("=" * 60)
    print("TEST 1: Load Operation Compass Scenario")
    print("=" * 60)

    state, reinforcements = load_operation_compass()

    # Turn state
    assert state.turn.game_turn == 1
    assert state.turn.op_stage == 1
    assert state.turn.phase == GamePhase.STORES_EXPENDITURE
    assert state.turn.current_weather == Weather.CLEAR
    assert state.turn.current_season == Season.AUTUMN
    print(f"  Turn: GT{state.turn.game_turn}, Phase={state.turn.phase}")

    # Allied units (10 active + 2 reinforcement = 12)
    allied_units = {uid: u for uid, u in state.units.items() if u.side == Side.ALLIED}
    allied_active = {uid: u for uid, u in allied_units.items() if u.status != UnitStatus.NOT_YET_ARRIVED}
    allied_pending = {uid: u for uid, u in allied_units.items() if u.status == UnitStatus.NOT_YET_ARRIVED}
    assert len(allied_active) == 12  # 10 original + engineer + truck
    assert len(allied_pending) == 2
    print(f"  Allied units: {len(allied_active)} active, {len(allied_pending)} pending")
    for uid, u in allied_active.items():
        assert u.hex_id is not None
        assert u.hex_id in state.hexes
        print(f"    {uid}: {u.name} @ {u.hex_id}")
    for uid, u in allied_pending.items():
        assert u.hex_id is None
        print(f"    {uid}: {u.name} (arrives GT{u.arrival_gt})")

    # Axis units (10 active + 5 reinforcement = 15)
    axis_units = {uid: u for uid, u in state.units.items() if u.side == Side.AXIS}
    axis_active = {uid: u for uid, u in axis_units.items() if u.status != UnitStatus.NOT_YET_ARRIVED}
    axis_pending = {uid: u for uid, u in axis_units.items() if u.status == UnitStatus.NOT_YET_ARRIVED}
    assert len(axis_active) == 12  # 10 original + engineer + truck
    assert len(axis_pending) == 5
    print(f"  Axis units: {len(axis_active)} active, {len(axis_pending)} pending")
    for uid, u in axis_active.items():
        assert u.hex_id is not None
        assert u.hex_id in state.hexes
        print(f"    {uid}: {u.name} @ {u.hex_id}")
    for uid, u in axis_pending.items():
        assert u.hex_id is None
        print(f"    {uid}: {u.name} (arrives GT{u.arrival_gt})")

    print("  PASSED\n")


def test_formations():
    print("=" * 60)
    print("TEST 2: Formation Structure")
    print("=" * 60)

    state, _ = load_operation_compass()

    # Allied formations
    allied_fms = {fid: f for fid, f in state.formations.items() if f.side == Side.ALLIED}
    assert len(allied_fms) >= 4  # WDF, 7th Arm, 4th Ind, brigades
    print(f"  Allied formations: {len(allied_fms)}")

    # Check hierarchy: WDF → 7th Armoured → 7th Arm Bde
    wdf = state.formations["cw_wdf"]
    assert "cw_7arm_div" in wdf.sub_formation_ids
    assert "cw_4ind_div" in wdf.sub_formation_ids
    print(f"  WDF subs: {wdf.sub_formation_ids}")

    arm7 = state.formations["cw_7arm_div"]
    assert arm7.parent_formation_id == "cw_wdf"
    assert "cw_7arm_bde" in arm7.sub_formation_ids
    assert "cw_4arm_bde" in arm7.sub_formation_ids
    assert arm7.hq_unit_id == "cw_7arm_hq"
    print(f"  7th Arm Div: parent={arm7.parent_formation_id}, HQ={arm7.hq_unit_id}")

    # Axis formations
    axis_fms = {fid: f for fid, f in state.formations.items() if f.side == Side.AXIS}
    assert len(axis_fms) >= 4
    print(f"  Axis formations: {len(axis_fms)}")

    # Maletti Group
    maletti = state.formations["it_maletti_grp"]
    assert maletti.parent_formation_id == "it_10army"
    assert "it_m11_plt" in maletti.unit_ids
    print(f"  Maletti: {maletti.unit_ids}")

    print("  PASSED\n")


def test_map_hexes():
    print("=" * 60)
    print("TEST 3: Map Hexes")
    print("=" * 60)

    state, _ = load_operation_compass()

    assert len(state.hexes) >= 25
    print(f"  Total hexes: {len(state.hexes)}")

    # Check ports
    ports = {hid: hs for hid, hs in state.hexes.items() if hs.is_port}
    assert len(ports) >= 5
    port_names = [hs.port_name for hs in ports.values()]
    assert "Alexandria" in port_names
    assert "Tobruk" in port_names
    assert "Sidi Barrani" in port_names
    print(f"  Ports: {port_names}")

    # Check terrain variety (from hex_database: CLEAR, SAND_SEA, DESERT, etc.)
    terrains = {hs.terrain for hs in state.hexes.values()}
    assert TerrainType.CLEAR in terrains
    assert TerrainType.SAND_SEA in terrains
    assert TerrainType.DESERT in terrains
    assert TerrainType.ROUGH in terrains
    print(f"  Terrain types: {terrains}")

    # Units placed on hex lists
    for uid, unit in state.units.items():
        if unit.hex_id and unit.hex_id in state.hexes:
            hs = state.hexes[unit.hex_id]
            if unit.side == Side.ALLIED:
                assert uid in hs.allied_unit_ids, f"{uid} not in {unit.hex_id} allied list"
            else:
                assert uid in hs.axis_unit_ids, f"{uid} not in {unit.hex_id} axis list"
    print(f"  All units placed on hex maps correctly")

    print("  PASSED\n")


def test_supply_setup():
    print("=" * 60)
    print("TEST 4: Initial Supply Setup")
    print("=" * 60)

    state, _ = load_operation_compass()

    # Supply dumps
    matruh = state.hexes["D1822"]
    assert len(matruh.supply_dumps) >= 1
    allied_dump = matruh.supply_dumps[0]
    assert allied_dump.side == Side.ALLIED
    assert allied_dump.fuel > 0
    print(f"  Allied dump at Matruh: fuel={allied_dump.fuel}, water={allied_dump.water}")

    barrani = state.hexes["D0821"]
    assert len(barrani.supply_dumps) >= 1
    axis_dump = barrani.supply_dumps[0]
    assert axis_dump.side == Side.AXIS
    assert axis_dump.fuel > 0
    print(f"  Axis dump at Barrani: fuel={axis_dump.fuel}, ammo={axis_dump.ammo}")

    # Supply pools
    assert state.allied_supply_in_egypt["fuel"] > 0
    assert state.axis_supply_in_tripoli_boxes["fuel"] > 0
    print(f"  Allied Egypt pool: {state.allied_supply_in_egypt}")
    print(f"  Axis Tripoli pool: {state.axis_supply_in_tripoli_boxes}")

    # Fleet
    assert state.cw_fleet.is_available
    assert state.cw_fleet.sorties_remaining == 2
    print(f"  Fleet: available={state.cw_fleet.is_available}, sorties={state.cw_fleet.sorties_remaining}")

    # Unit supply levels
    for uid, unit in state.units.items():
        if unit.supply:
            assert unit.supply.water >= 0
    print(f"  All units have supply initialized")

    print("  PASSED\n")


def test_reinforcements():
    print("=" * 60)
    print("TEST 5: Reinforcement Processing")
    print("=" * 60)

    state, reinforcements = load_operation_compass()

    assert len(reinforcements) >= 7
    print(f"  Scheduled: {len(reinforcements)} reinforcements")

    # Check GT8 Australian arrivals
    gt8_reinf = [r for r in reinforcements if r.arrival_gt == 8]
    assert len(gt8_reinf) >= 2
    print(f"  GT8 arrivals: {[r.unit_id for r in gt8_reinf]}")

    # Check DAK arrivals
    dak_reinf = [r for r in reinforcements if r.arrival_gt >= 25]
    assert len(dak_reinf) >= 5
    print(f"  DAK arrivals: {[(r.unit_id, r.arrival_gt) for r in dak_reinf]}")

    # Units should already exist with NOT_YET_ARRIVED status
    for r in reinforcements:
        assert r.unit_id in state.units, f"{r.unit_id} missing from units"
        assert state.units[r.unit_id].status == UnitStatus.NOT_YET_ARRIVED
    print(f"  All reinforcement units pre-created with NOT_YET_ARRIVED status")

    # Process reinforcements at GT1 (nothing should arrive)
    arrived = process_reinforcements(state, reinforcements)
    assert len(arrived) == 0
    print(f"  GT1 arrivals: {arrived} (expected none)")

    # Simulate GT8: Australian units should arrive on-map at Alexandria
    state.turn.game_turn = 8
    arrived_gt8 = process_reinforcements(state, reinforcements)
    assert len(arrived_gt8) == 2
    for uid in arrived_gt8:
        assert state.units[uid].status == UnitStatus.ACTIVE
        assert state.units[uid].hex_id == "E1326", f"{uid} should deploy to Alexandria hex"
        assert state.units[uid].off_map_location is None
        # Should arrive fully supplied
        assert state.units[uid].supply.water == state.units[uid].supply.water_capacity
    print(f"  GT8 arrivals: {arrived_gt8}")

    # Simulate GT25: DAK recon should arrive on-map at Benghazi
    state.turn.game_turn = 25
    arrived_gt25 = process_reinforcements(state, reinforcements)
    assert len(arrived_gt25) == 1
    assert state.units["dak_recon_bn"].status == UnitStatus.ACTIVE
    assert state.units["dak_recon_bn"].hex_id == "A3511", "DAK recon should deploy to Tripoli hex"
    print(f"  GT25 arrivals: {arrived_gt25}")

    # Simulate GT27: 5th Light Div
    state.turn.game_turn = 27
    arrived_gt27 = process_reinforcements(state, reinforcements)
    assert len(arrived_gt27) == 2
    print(f"  GT27 arrivals: {arrived_gt27}")

    # Simulate GT33: 15th Panzer Div
    state.turn.game_turn = 33
    arrived_gt33 = process_reinforcements(state, reinforcements)
    assert len(arrived_gt33) == 2
    print(f"  GT33 arrivals: {arrived_gt33}")

    print("  PASSED\n")


def test_air_oob():
    print("=" * 60)
    print("TEST 6: Air OOB")
    print("=" * 60)

    state, _ = load_operation_compass()

    # SGSUs: 2 RAF + 2 RA + 1 LW (Derna, empty until GT25)
    assert len(state.sgsus) == 6  # 2 RAF + 2 RA + 1 RA Tripoli + 1 LW Derna
    assert "sgsu_raf_alex" in state.sgsus
    assert "sgsu_raf_matruh" in state.sgsus
    assert "sgsu_ra_tobruk" in state.sgsus
    assert "sgsu_ra_benghazi" in state.sgsus
    assert "sgsu_lw_derna" in state.sgsus
    print(f"  SGSUs: {len(state.sgsus)}")

    # Aircraft: 8 RAF + 12 RA = 20 ready, + 7 LW NOT_YET_ARRIVED = 27 total
    assert len(state.aircraft) == 27
    ready = [a for a in state.aircraft.values() if a.status == AircraftStatus.READY]
    pending = [a for a in state.aircraft.values() if a.status == AircraftStatus.NOT_YET_ARRIVED]
    assert len(ready) == 20, f"Expected 20 ready, got {len(ready)}"
    assert len(pending) == 7, f"Expected 7 pending, got {len(pending)}"
    print(f"  Aircraft: {len(ready)} ready, {len(pending)} pending")

    # RAF aircraft at correct SGSUs
    allied_ac = [a for a in state.aircraft.values() if a.side == Side.ALLIED]
    assert len(allied_ac) == 8
    assert state.aircraft["raf_274sqn_hurr"].aircraft_type_id == "hurricane_i"
    assert state.aircraft["raf_33sqn_glad"].aircraft_type_id == "gladiator"
    assert state.aircraft["raf_45sqn_blen"].aircraft_type_id == "blenheim_iv"
    print(f"  RAF: {len(allied_ac)} aircraft")

    # Italian aircraft
    axis_ready = [a for a in state.aircraft.values() if a.side == Side.AXIS and a.status == AircraftStatus.READY]
    assert len(axis_ready) == 12
    print(f"  Regia Aeronautica: {len(axis_ready)} aircraft")

    # SGSU aircraft_ids populated
    assert len(state.sgsus["sgsu_raf_matruh"].aircraft_ids) == 5  # 3 glad + 2 blen
    assert len(state.sgsus["sgsu_raf_alex"].aircraft_ids) == 3    # 1 hurr + 2 blen
    assert len(state.sgsus["sgsu_ra_tobruk"].aircraft_ids) == 6   # 2 cr42 + 4 sm79
    assert len(state.sgsus["sgsu_ra_benghazi"].aircraft_ids) == 6 # 2 cr32 + 4 sm79
    print(f"  SGSU aircraft lists verified")

    # Aircraft have correct bombload/tacair from reference data
    hurr = state.aircraft["raf_274sqn_hurr"]
    assert hurr.bombload_remaining == 2
    assert hurr.tacair_remaining == 4
    glad = state.aircraft["raf_33sqn_glad"]
    assert glad.bombload_remaining == 0  # Gladiator has "—" bombload
    assert glad.tacair_remaining == 2
    print(f"  Aircraft stats verified (Hurricane: bomb={hurr.bombload_remaining}, tacair={hurr.tacair_remaining})")

    print("  PASSED\n")


def test_air_reinforcements():
    print("=" * 60)
    print("TEST 7: Air Reinforcement Processing")
    print("=" * 60)

    state, _ = load_operation_compass()
    air_reinf = get_air_reinforcement_schedule()
    assert len(air_reinf) == 7
    print(f"  Scheduled: {len(air_reinf)} air reinforcements")

    # GT1 — nothing arrives
    arrived = process_air_reinforcements(state, air_reinf)
    assert len(arrived) == 0
    print(f"  GT1 arrivals: {arrived} (expected none)")

    # GT25 — 2x Bf109E + 2x Ju87B
    state.turn.game_turn = 25
    arrived_gt25 = process_air_reinforcements(state, air_reinf)
    assert len(arrived_gt25) == 4
    for ac_id in arrived_gt25:
        ac = state.aircraft[ac_id]
        assert ac.status == AircraftStatus.READY
        assert ac.sgsu_id == "sgsu_lw_derna"
    print(f"  GT25 arrivals: {arrived_gt25}")

    # GT27 — 1x Bf110 + 2x Ju88A
    state.turn.game_turn = 27
    arrived_gt27 = process_air_reinforcements(state, air_reinf)
    assert len(arrived_gt27) == 3
    print(f"  GT27 arrivals: {arrived_gt27}")

    # All 7 should now be READY
    lw_ac = [a for a in state.aircraft.values()
             if a.id.startswith("lw_") and a.status == AircraftStatus.READY]
    assert len(lw_ac) == 7
    print(f"  All Luftwaffe aircraft now READY: {len(lw_ac)}")

    print("  PASSED\n")


def test_load_scenario_entry():
    print("=" * 60)
    print("TEST 8: Scenario Loader Entry Point")
    print("=" * 60)

    state, reinf = load_scenario("operation_compass")
    assert state.turn.game_turn == 1
    assert len(state.units) == 31  # 24 active + 7 reinforcements
    assert len(reinf) == 7
    print(f"  Loaded: {len(state.units)} units, {len(reinf)} reinforcements")

    # Unknown scenario
    try:
        load_scenario("nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"  Unknown scenario: {e}")

    print("  PASSED\n")


def main():
    test_load_operation_compass()
    test_formations()
    test_map_hexes()
    test_supply_setup()
    test_reinforcements()
    test_air_oob()
    test_air_reinforcements()
    test_load_scenario_entry()
    print("=" * 60)
    print("ALL SCENARIO TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
