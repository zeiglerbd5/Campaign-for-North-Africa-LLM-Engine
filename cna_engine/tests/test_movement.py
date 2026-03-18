"""
CNA Engine — Movement System Tests
Tests hex geometry, ZOC, movement costs, validation, execution,
contact/engaged management, reaction movement, and a mini scenario.
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.movement import (
    parse_hex_id, HexCoord, get_neighbors, are_adjacent,
    get_ezoc_hexes, is_hex_in_ezoc, _unit_exerts_zoc,
    compute_hex_entry_cost, compute_stacking_in_hex, check_stacking,
    break_contact, break_engaged, auto_clear_contact,
    validate_move, execute_move,
    check_reaction_eligibility, attempt_reaction_move,
    BREAK_CONTACT_COST, BREAK_ENGAGED_COST,
)
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TOEStrength, SupplyDump,
)
from cna_engine.models.enums import (
    Side, UnitStatus, RoadType, TerrainType, MotorizationType,
)
from cna_engine.data.reference_data import ReferenceData, TerrainInfo


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

def _make_ref() -> ReferenceData:
    """Create a minimal ReferenceData for testing."""
    ref = ReferenceData()
    ref.terrain = {
        "clear": TerrainInfo(
            terrain_type="Clear", motorized_cp=2, non_motorized_cp=2,
            track_effect="Halve", barrage_shift="—", anti_armor_shift="—",
            close_assault_shift="—", stacking_limit="10 SP",
        ),
        "rough": TerrainInfo(
            terrain_type="Rough", motorized_cp=4, non_motorized_cp=3,
            track_effect="Halve", barrage_shift="1L", anti_armor_shift="1L",
            close_assault_shift="1L", stacking_limit="5 SP",
        ),
        "sand_sea": TerrainInfo(
            terrain_type="Sand Sea", motorized_cp=6, non_motorized_cp=0,
            track_effect="N/A", barrage_shift="—", anti_armor_shift="—",
            close_assault_shift="—", stacking_limit="Unlimited",
        ),
        "mountain": TerrainInfo(
            terrain_type="Mountain", motorized_cp=0, non_motorized_cp=4,
            track_effect="N/A", barrage_shift="2L", anti_armor_shift="2L",
            close_assault_shift="2L", stacking_limit="3 SP",
        ),
    }
    return ref


def _make_unit(
    uid: str = "test_unit",
    name: str = "Test Unit",
    side: str = Side.ALLIED,
    hex_id: str = "D0821",
    status: str = UnitStatus.ACTIVE,
    motorization: str = MotorizationType.NON_MOTORIZED,
    base_cpa: int = 8,
    current_cpa_spent: int = 0,
    cohesion: int = 0,
    strength_total: int = 10,
    stacking_points: int = 2,
    is_in_contact: bool = False,
) -> Unit:
    """Create a test unit with sensible defaults."""
    return Unit(
        id=uid, name=name, side=side,
        nationality="british", unit_class="infantry",
        unit_size="battalion", motorization=motorization,
        hex_id=hex_id, base_cpa=base_cpa,
        current_cpa_spent=current_cpa_spent,
        cohesion=cohesion,
        stacking_points=stacking_points,
        current_strength=TOEStrength(infantry=strength_total),
        toe_strength=TOEStrength(infantry=strength_total),
        status=status,
        is_in_contact=is_in_contact,
    )


def _make_state_with_hexes(*hex_ids, terrain="clear", road=RoadType.NONE):
    """Create a GameState with given hexes."""
    state = GameState()
    for hid in hex_ids:
        state.hexes[hid] = HexState(hex_id=hid, terrain=terrain, road=road)
    return state


# ════════════════════════════════════════
# TEST 1: HEX PARSING
# ════════════════════════════════════════

def test_hex_parsing():
    print("=" * 60)
    print("TEST 1: Hex Parsing")
    print("=" * 60)

    # Valid parse
    c = parse_hex_id("D1822")
    assert c.section == "D"
    assert c.col == 18
    assert c.row == 22
    assert c.hex_id == "D1822"
    print("  ok parse D1822")

    # Round-trip
    c2 = parse_hex_id("A0100")
    assert c2.hex_id == "A0100"
    print("  ok round-trip A0100")

    # Edge cases
    c3 = parse_hex_id("C9999")
    assert c3.col == 99 and c3.row == 99
    print("  ok edge C9999")

    # Lowercase now accepted (normalized to uppercase)
    c4 = parse_hex_id("b2415")
    assert c4.section == "B" and c4.col == 24 and c4.row == 15
    print("  ok lowercase b2415 -> B2415")

    # Invalid format
    for bad in ["", "2415", "BB2415", "B241", "B24155", "B2x15"]:
        try:
            parse_hex_id(bad)
            assert False, f"Should have raised ValueError for '{bad}'"
        except ValueError:
            pass
    print("  ok invalid formats rejected")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 2: ADJACENCY
# ════════════════════════════════════════

def test_adjacency():
    print("\n" + "=" * 60)
    print("TEST 2: Adjacency")
    print("=" * 60)

    # Interior hex should have 6 neighbors
    neighbors = get_neighbors("D0821")
    assert len(neighbors) == 6, f"Expected 6 neighbors, got {len(neighbors)}: {neighbors}"
    print(f"  D0821 neighbors: {sorted(neighbors)}")

    # All neighbors should consider D0821 adjacent
    for n in neighbors:
        assert are_adjacent("D0821", n), f"{n} should be adjacent to D0821"
        assert are_adjacent(n, "D0821"), f"D0821 should be adjacent to {n}"
    print("  ok all neighbors are mutually adjacent")

    # Non-adjacent hex
    assert not are_adjacent("D0821", "D1122"), "D0821 and D1122 should not be adjacent"
    print("  ok non-adjacent correctly identified")

    # Different sections are never adjacent (current implementation)
    assert not are_adjacent("A1010", "D0821"), "Cross-section adjacency not supported"
    print("  ok cross-section not adjacent")

    # Edge hex (col=0) should have fewer than 6 neighbors
    edge_neighbors = get_neighbors("D0122")
    for n in edge_neighbors:
        c = parse_hex_id(n)
        assert c.col >= 0 and c.row >= 0, f"Neighbor {n} has invalid coords"
    print(f"  D0122 (edge) neighbors: {sorted(edge_neighbors)}")

    # Verify neighbor consistency: if A is neighbor of B, B is neighbor of A
    test_hexes = ["D0821", "D0922", "D1021", "D0522", "D1022"]
    for h in test_hexes:
        for n in get_neighbors(h):
            assert h in get_neighbors(n), \
                f"Adjacency not symmetric: {h} → {n} but {n} does not → {h}"
    print("  ok adjacency is symmetric for test hexes")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 3: ZOC
# ════════════════════════════════════════

def test_zoc():
    print("\n" + "=" * 60)
    print("TEST 3: ZOC Computation")
    print("=" * 60)

    state = GameState()

    # Strong enemy unit exerts ZOC
    enemy = _make_unit(
        uid="axis_inf", name="Axis Infantry", side=Side.AXIS,
        hex_id="D0821", strength_total=12,
    )
    state.units["axis_inf"] = enemy

    ezoc = get_ezoc_hexes(state, Side.ALLIED)
    neighbors = set(get_neighbors("D0821"))
    assert ezoc == neighbors, f"Expected EZOC = neighbors of D0821, got {ezoc}"
    print(f"  Strong unit (12 SP): ZOC covers {len(ezoc)} hexes")

    # Check specific hex
    for n in neighbors:
        assert is_hex_in_ezoc(state, n, Side.ALLIED)
    assert not is_hex_in_ezoc(state, "D0821", Side.ALLIED), "Unit's own hex not in own EZOC"
    print("  ok EZOC hex checks correct")

    # Weak unit (1 SP) does NOT exert ZOC
    state2 = GameState()
    weak = _make_unit(
        uid="axis_weak", name="Axis Remnant", side=Side.AXIS,
        hex_id="D0821", strength_total=1,
    )
    state2.units["axis_weak"] = weak
    ezoc2 = get_ezoc_hexes(state2, Side.ALLIED)
    assert len(ezoc2) == 0, f"Weak unit (1 SP) should not exert ZOC, got {ezoc2}"
    print("  Weak unit (1 SP): no ZOC")

    # Unit with 9 CA points (total=9) does NOT exert ZOC (need >9)
    state3 = GameState()
    borderline = _make_unit(
        uid="axis_border", name="Axis Borderline", side=Side.AXIS,
        hex_id="D0821", strength_total=9,
    )
    state3.units["axis_border"] = borderline
    ezoc3 = get_ezoc_hexes(state3, Side.ALLIED)
    assert len(ezoc3) == 0, f"9 CA unit should not exert ZOC (need >9)"
    print("  Borderline unit (9 CA): no ZOC")

    # Unit with 10 CA points DOES exert ZOC
    state4 = GameState()
    strong_enough = _make_unit(
        uid="axis_10", name="Axis 10 SP", side=Side.AXIS,
        hex_id="D0821", strength_total=10,
    )
    state4.units["axis_10"] = strong_enough
    ezoc4 = get_ezoc_hexes(state4, Side.ALLIED)
    assert len(ezoc4) == 6
    print("  10 SP unit: exerts ZOC")

    # Broken unit doesn't exert ZOC
    state5 = GameState()
    broken = _make_unit(
        uid="axis_broken", name="Axis Broken", side=Side.AXIS,
        hex_id="D0821", strength_total=12,
        status=UnitStatus.BROKEN_DOWN,
    )
    state5.units["axis_broken"] = broken
    ezoc5 = get_ezoc_hexes(state5, Side.ALLIED)
    assert len(ezoc5) == 0
    print("  Broken unit: no ZOC")

    # ENGAGED unit still exerts ZOC
    state6 = GameState()
    engaged = _make_unit(
        uid="axis_eng", name="Axis Engaged", side=Side.AXIS,
        hex_id="D0821", strength_total=12,
        status=UnitStatus.ENGAGED,
    )
    state6.units["axis_eng"] = engaged
    ezoc6 = get_ezoc_hexes(state6, Side.ALLIED)
    assert len(ezoc6) == 6
    print("  Engaged unit: still exerts ZOC")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 4: MOVEMENT COST
# ════════════════════════════════════════

def test_movement_cost():
    print("\n" + "=" * 60)
    print("TEST 4: Movement Cost")
    print("=" * 60)

    ref = _make_ref()

    # Clear terrain, no road
    state = _make_state_with_hexes("D0821", "D0922")
    unit = _make_unit()
    cost = compute_hex_entry_cost(state, ref, unit, "D0821", "D0922")
    assert cost.effective_cost == 2, f"Clear terrain should cost 2 CP, got {cost.effective_cost}"
    assert not cost.used_road
    print(f"  Clear, non-motorized: {cost.description}")

    # Road
    state2 = _make_state_with_hexes("D0821")
    state2.hexes["D0922"] = HexState(hex_id="D0922", terrain="clear", road=RoadType.ROAD)
    cost2 = compute_hex_entry_cost(state2, ref, unit, "D0821", "D0922")
    assert cost2.effective_cost == 0.5, f"Road should cost 0.5 CP, got {cost2.effective_cost}"
    assert cost2.used_road
    print(f"  Road: {cost2.description}")

    # Track + rough terrain (halved)
    state3 = _make_state_with_hexes("D0821")
    state3.hexes["D0922"] = HexState(hex_id="D0922", terrain="rough", road=RoadType.TRACK)
    cost3 = compute_hex_entry_cost(state3, ref, unit, "D0821", "D0922")
    # Non-motorized rough = 3 CP, with track halved = 1.5
    assert cost3.effective_cost == 1.5, f"Track+rough should be 1.5, got {cost3.effective_cost}"
    print(f"  Track+rough, non-motorized: {cost3.description}")

    # Motorized on clear
    motor_unit = _make_unit(motorization=MotorizationType.MOTORIZED)
    cost4 = compute_hex_entry_cost(state, ref, motor_unit, "D0821", "D0922")
    assert cost4.effective_cost == 2
    print(f"  Clear, motorized: {cost4.description}")

    # Sand sea — non-motorized gets 0 (impassable represented as 0)
    state5 = _make_state_with_hexes("D0821")
    state5.hexes["D0922"] = HexState(hex_id="D0922", terrain="sand_sea", road=RoadType.NONE)
    cost5 = compute_hex_entry_cost(state5, ref, unit, "D0821", "D0922")
    print(f"  Sand sea, non-motorized: {cost5.description}")

    # Missing hex → defaults to clear
    empty_state = GameState()
    cost6 = compute_hex_entry_cost(empty_state, ref, unit, "D0821", "D9999")
    assert cost6.effective_cost == 2, "Missing hex should default to clear (2 CP)"
    print(f"  Missing hex (default clear): {cost6.description}")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 5: STACKING
# ════════════════════════════════════════

def test_stacking():
    print("\n" + "=" * 60)
    print("TEST 5: Stacking")
    print("=" * 60)

    ref = _make_ref()
    state = _make_state_with_hexes("D0821")

    # Place 3 units (2 SP each = 6 total) in clear (limit 10)
    for i in range(3):
        u = _make_unit(uid=f"u{i}", hex_id="D0821", stacking_points=2)
        state.units[f"u{i}"] = u

    current = compute_stacking_in_hex(state, "D0821", Side.ALLIED)
    assert current == 6
    print(f"  3 units (2 SP each) = {current} SP")

    ok, curr, limit = check_stacking(state, ref, "D0821", Side.ALLIED, additional_sp=2)
    assert ok, "8 SP should be under 10 limit"
    print(f"  Adding 2 more: ok={ok}, {curr}+2 <= {limit}")

    ok2, curr2, limit2 = check_stacking(state, ref, "D0821", Side.ALLIED, additional_sp=5)
    assert not ok2, "11 SP should exceed 10 limit"
    print(f"  Adding 5 more: ok={ok2}, {curr2}+5 > {limit2}")

    # Unlimited stacking (sand sea)
    state2 = GameState()
    state2.hexes["D1022"] = HexState(hex_id="D1022", terrain="sand_sea")
    ok3, _, lim3 = check_stacking(state2, ref, "D1022", Side.ALLIED, additional_sp=999)
    assert ok3 and lim3 is None
    print(f"  Sand sea: unlimited stacking")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 6: VALIDATION
# ════════════════════════════════════════

def test_validation():
    print("\n" + "=" * 60)
    print("TEST 6: Move Validation")
    print("=" * 60)

    ref = _make_ref()

    # Build a simple 3-hex path: D0821 → neighbor → neighbor
    origin = "D0821"
    neighbors = get_neighbors(origin)
    mid = neighbors[0]
    mid_neighbors = get_neighbors(mid)
    # Find a neighbor of mid that isn't origin
    dest = None
    for n in mid_neighbors:
        if n != origin:
            dest = n
            break
    assert dest is not None, "Could not find a 3-hex path"

    state = _make_state_with_hexes(origin, mid, dest)
    unit = _make_unit(uid="mover", hex_id=origin, base_cpa=8)
    state.units["mover"] = unit

    # Valid 3-hex move (2 CP + 2 CP = 4 CP, budget = 12 with 150% cap)
    path = [origin, mid, dest]
    v = validate_move(state, ref, "mover", path)
    assert v.is_valid, f"Should be valid: {v.blocked_reason}"
    assert v.total_cp_cost == 4.0
    print(f"  Valid path {path}: cost={v.total_cp_cost}, avail={v.cp_available}")

    # Insufficient CPA
    unit.current_cpa_spent = 10  # Only 2 CP left (budget = 12)
    v2 = validate_move(state, ref, "mover", path)
    assert not v2.is_valid
    assert "Insufficient CPA" in v2.blocked_reason
    print(f"  Insufficient CPA: {v2.blocked_reason}")
    unit.current_cpa_spent = 0  # Reset

    # Non-adjacent path
    bad_path = [origin, "D1822"]
    v3 = validate_move(state, ref, "mover", bad_path)
    assert not v3.is_valid
    assert "not adjacent" in v3.blocked_reason
    print(f"  Non-adjacent: {v3.blocked_reason}")

    # Wrong start hex
    bad_start = ["D9999", mid]
    v4 = validate_move(state, ref, "mover", bad_start)
    assert not v4.is_valid
    assert "starts at" in v4.blocked_reason
    print(f"  Wrong start: {v4.blocked_reason}")

    # Unit not found
    v5 = validate_move(state, ref, "nonexistent", path)
    assert not v5.is_valid
    print(f"  Not found: {v5.blocked_reason}")

    # 150% cap check: non-motorized with base_cpa=8 → max 12
    unit.base_cpa = 8
    assert unit.max_cpa_this_stage == 12
    print(f"  150% cap: base_cpa=8, max={unit.max_cpa_this_stage}")

    # Motorized cap: base_cpa + cohesion
    motor = _make_unit(uid="motor", hex_id=origin, base_cpa=10, cohesion=2,
                       motorization=MotorizationType.MOTORIZED)
    assert motor.max_cpa_this_stage == 12
    print(f"  Motorized cap: base=10, cohesion=2, max={motor.max_cpa_this_stage}")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 7: EZOC STOP RULE
# ════════════════════════════════════════

def test_ezoc_stop():
    print("\n" + "=" * 60)
    print("TEST 7: EZOC Stop Rule")
    print("=" * 60)

    ref = _make_ref()

    # Enemy at D1122. Its EZOC = 6 adjacent hexes.
    # Mover at D1120 (not in EZOC). D1120 is adjacent to D1121 (EZOC hex).
    # D1121 is adjacent to D1220 (not in EZOC).
    state = GameState()
    enemy = _make_unit(uid="axis_blk", name="Axis Blocker", side=Side.AXIS,
                       hex_id="D1122", strength_total=12)
    state.units["axis_blk"] = enemy

    mover = _make_unit(uid="mover", hex_id="D1120", base_cpa=20)
    state.units["mover"] = mover

    for h in ["D1120", "D1121", "D1220", "D1122"]:
        state.hexes[h] = HexState(hex_id=h, terrain="clear")

    # Verify setup
    assert are_adjacent("D1120", "D1121"), "D1120 and D1121 must be adjacent"
    assert are_adjacent("D1121", "D1220"), "D1121 and D1220 must be adjacent"
    assert is_hex_in_ezoc(state, "D1121", Side.ALLIED), "D1121 should be in EZOC"
    assert not is_hex_in_ezoc(state, "D1120", Side.ALLIED), "D1120 should NOT be in EZOC"
    assert not is_hex_in_ezoc(state, "D1220", Side.ALLIED), "D1220 should NOT be in EZOC"

    # Valid: enter EZOC as last hex
    path_ok = ["D1120", "D1121"]
    v = validate_move(state, ref, "mover", path_ok)
    assert v.is_valid, f"Entering EZOC as last hex should be valid: {v.blocked_reason}"
    assert v.enters_ezoc
    print(f"  EZOC as last hex: valid, enters EZOC at {v.ezoc_hex}")

    # Invalid: EZOC hex not last in path (D1120 → D1121 → D1220)
    path_bad = ["D1120", "D1121", "D1220"]
    v2 = validate_move(state, ref, "mover", path_bad)
    assert not v2.is_valid
    assert "EZOC entry" in v2.blocked_reason
    print(f"  EZOC not last hex: blocked — {v2.blocked_reason}")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 8: EXECUTION
# ════════════════════════════════════════

def test_execution():
    print("\n" + "=" * 60)
    print("TEST 8: Move Execution")
    print("=" * 60)

    ref = _make_ref()
    origin = "D0821"
    neighbors = get_neighbors(origin)
    dest = neighbors[0]

    state = _make_state_with_hexes(origin, dest)
    unit = _make_unit(uid="mover", hex_id=origin, base_cpa=8)
    state.units["mover"] = unit
    state.hexes[origin].allied_unit_ids.append("mover")

    # Execute move
    result = execute_move(state, ref, "mover", [origin, dest])
    assert result.success, f"Move should succeed: {result.description}"
    assert unit.hex_id == dest
    assert "mover" not in state.hexes[origin].allied_unit_ids
    assert "mover" in state.hexes[dest].allied_unit_ids
    assert unit.current_cpa_spent == 2  # Clear terrain
    print(f"  {result.description}")
    print(f"  Unit hex: {unit.hex_id}, CPA spent: {unit.current_cpa_spent}")

    # Check event log
    assert len(state.event_log) == 1
    assert state.event_log[0]["type"] == "movement"
    print(f"  Event logged: {state.event_log[0]['description']}")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 9: CONTACT / ENGAGED
# ════════════════════════════════════════

def test_contact_engaged():
    print("\n" + "=" * 60)
    print("TEST 9: Contact / Engaged Management")
    print("=" * 60)

    # Break contact
    state = GameState()
    unit = _make_unit(uid="u1", base_cpa=8, is_in_contact=True)
    state.units["u1"] = unit

    # Place an enemy in ZOC range so is_in_contact makes sense
    enemy = _make_unit(uid="axis1", side=Side.AXIS, hex_id="D0922", strength_total=12)
    state.units["axis1"] = enemy

    ok, desc = break_contact(state, "u1")
    assert ok
    assert not unit.is_in_contact
    assert unit.current_cpa_spent == BREAK_CONTACT_COST
    print(f"  Break contact: {desc}")

    # Break contact when not in contact
    ok2, desc2 = break_contact(state, "u1")
    assert not ok2
    print(f"  Already clear: {desc2}")

    # Break engaged
    state2 = GameState()
    engaged_unit = _make_unit(uid="u2", base_cpa=10, status=UnitStatus.ENGAGED)
    state2.units["u2"] = engaged_unit

    ok3, desc3 = break_engaged(state2, "u2")
    assert ok3
    assert engaged_unit.status == UnitStatus.ACTIVE
    assert engaged_unit.current_cpa_spent == BREAK_ENGAGED_COST
    print(f"  Break engaged: {desc3}")

    # Break engaged with insufficient CPA
    state3 = GameState()
    low_unit = _make_unit(uid="u3", base_cpa=2, status=UnitStatus.ENGAGED)
    state3.units["u3"] = low_unit
    ok4, desc4 = break_engaged(state3, "u3")
    assert not ok4
    print(f"  Insufficient CPA: {desc4}")

    # Auto-clear contact when no longer in EZOC
    state4 = GameState()
    contact_unit = _make_unit(uid="u4", hex_id="D1022", is_in_contact=True)
    state4.units["u4"] = contact_unit
    # No enemies → should auto-clear
    cleared = auto_clear_contact(state4, "u4")
    assert cleared
    assert not contact_unit.is_in_contact
    print("  Auto-clear: contact removed (no enemy nearby)")

    # Auto-clear fails when still in EZOC
    state5 = GameState()
    still_contact = _make_unit(uid="u5", hex_id="D0821", is_in_contact=True)
    state5.units["u5"] = still_contact
    blocking_enemy = _make_unit(uid="axis2", side=Side.AXIS, hex_id="D0922", strength_total=12)
    state5.units["axis2"] = blocking_enemy
    not_cleared = auto_clear_contact(state5, "u5")
    # Only fails if D0821 is actually in EZOC of axis2
    if is_hex_in_ezoc(state5, "D0821", Side.ALLIED):
        assert not not_cleared
        print("  Auto-clear: stays in contact (enemy nearby)")
    else:
        print("  Auto-clear: cleared (hex not in EZOC)")

    print("  PASSED")


# ════════════════════════════════════════
# TEST 10: REACTION MOVEMENT
# ════════════════════════════════════════

def test_reaction_movement():
    print("\n" + "=" * 60)
    print("TEST 10: Reaction Movement")
    print("=" * 60)

    ref = _make_ref()
    origin = "D0821"
    dest = get_neighbors(origin)[0]

    # Motorized unit eligible for reaction
    state = _make_state_with_hexes(origin, dest)
    unit = _make_unit(uid="reacter", hex_id=origin, base_cpa=10,
                      motorization=MotorizationType.MOTORIZED)
    state.units["reacter"] = unit
    state.hexes[origin].allied_unit_ids.append("reacter")

    elig, reason = check_reaction_eligibility(state, "reacter")
    assert elig, f"Should be eligible: {reason}"
    print(f"  Eligibility: {reason}")

    # Non-motorized not eligible
    state2 = GameState()
    foot = _make_unit(uid="foot", hex_id=origin, motorization=MotorizationType.NON_MOTORIZED)
    state2.units["foot"] = foot
    elig2, reason2 = check_reaction_eligibility(state2, "foot")
    assert not elig2
    print(f"  Non-motorized: {reason2}")

    # In-contact not eligible
    state3 = GameState()
    contact = _make_unit(uid="contact", hex_id=origin, is_in_contact=True,
                         motorization=MotorizationType.MOTORIZED)
    state3.units["contact"] = contact
    elig3, reason3 = check_reaction_eligibility(state3, "contact")
    assert not elig3
    print(f"  In contact: {reason3}")

    # Successful reaction (prevention roll = 5, not prevented)
    result = attempt_reaction_move(state, ref, "reacter", [origin, dest],
                                   prevention_roll=5)
    assert result.success
    assert not result.prevented
    assert result.prevention_roll == 5
    print(f"  Success (roll 5): {result.description}")

    # Reset state for prevention test
    state4 = _make_state_with_hexes(origin, dest)
    unit4 = _make_unit(uid="reacter2", hex_id=origin, base_cpa=10,
                       motorization=MotorizationType.MOTORIZED)
    state4.units["reacter2"] = unit4
    state4.hexes[origin].allied_unit_ids.append("reacter2")

    # Prevented reaction (roll = 1)
    result2 = attempt_reaction_move(state4, ref, "reacter2", [origin, dest],
                                    prevention_roll=1)
    assert not result2.success
    assert result2.prevented
    print(f"  Prevented (roll 1): {result2.description}")

    # Roll = 2 also prevented
    state5 = _make_state_with_hexes(origin, dest)
    unit5 = _make_unit(uid="reacter3", hex_id=origin, base_cpa=10,
                       motorization=MotorizationType.MOTORIZED)
    state5.units["reacter3"] = unit5
    state5.hexes[origin].allied_unit_ids.append("reacter3")

    result3 = attempt_reaction_move(state5, ref, "reacter3", [origin, dest],
                                    prevention_roll=2)
    assert not result3.success
    assert result3.prevented
    print(f"  Prevented (roll 2): {result3.description}")

    # Roll = 3 not prevented
    state6 = _make_state_with_hexes(origin, dest)
    unit6 = _make_unit(uid="reacter4", hex_id=origin, base_cpa=10,
                       motorization=MotorizationType.MOTORIZED)
    state6.units["reacter4"] = unit6
    state6.hexes[origin].allied_unit_ids.append("reacter4")

    result4 = attempt_reaction_move(state6, ref, "reacter4", [origin, dest],
                                    prevention_roll=3)
    assert result4.success
    assert not result4.prevented
    print(f"  Not prevented (roll 3): {result4.description}")

    print("  PASSED")


# ════════════════════════════════════════
# DUMP CAPTURE ON MOVEMENT
# ════════════════════════════════════════

def test_dump_capture():
    """Moving into an undefended hex with enemy dumps flips ownership."""
    print("\n── test_dump_capture ──")
    ref = _make_ref()
    state = GameState()

    for hid in ["D0821", "D0922"]:
        state.hexes[hid] = HexState(hex_id=hid, terrain="clear")

    # Allied unit at D0821
    cw = Unit(
        id="cw_inf", name="CW Infantry",
        side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED,
        hex_id="D0821", base_cpa=10, cohesion=1,
        stacking_points=1,
        toe_strength=TOEStrength(infantry=6),
        current_strength=TOEStrength(infantry=6),
    )
    state.units["cw_inf"] = cw
    state.hexes["D0821"].allied_unit_ids.append("cw_inf")

    # Enemy dump at D0922 (no defenders)
    axis_dump = SupplyDump(id="axis_dump_1", side=Side.AXIS, fuel=10.0, water=5.0)
    state.hexes["D0922"].supply_dumps.append(axis_dump)

    result = execute_move(state, ref, "cw_inf", ["D0821", "D0922"])
    assert result.success, "Move should succeed"
    assert axis_dump.side == Side.ALLIED, f"Dump should flip to ALLIED, got {axis_dump.side}"

    # Check capture event was logged
    capture_events = [e for e in state.event_log if e["type"] == "dump_captured"]
    assert len(capture_events) == 1, f"Expected 1 capture event, got {len(capture_events)}"
    print(f"  PASS: dump flipped from AXIS → ALLIED")
    print(f"  Event: {capture_events[0]['description']}")


def test_dump_not_captured_when_defended():
    """Dumps should NOT flip if enemy units still occupy the hex."""
    print("\n── test_dump_not_captured_when_defended ──")
    ref = _make_ref()
    state = GameState()

    for hid in ["D0821", "D0922"]:
        state.hexes[hid] = HexState(hex_id=hid, terrain="clear")

    # Allied unit at D0821
    cw = Unit(
        id="cw_inf", name="CW Infantry",
        side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED,
        hex_id="D0821", base_cpa=10, cohesion=1,
        stacking_points=1,
        toe_strength=TOEStrength(infantry=6),
        current_strength=TOEStrength(infantry=6),
    )
    state.units["cw_inf"] = cw
    state.hexes["D0821"].allied_unit_ids.append("cw_inf")

    # Axis defender at D0922
    it_def = Unit(
        id="it_def", name="Italian Defender",
        side=Side.AXIS, nationality="italian",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED,
        hex_id="D0922", base_cpa=10, cohesion=1,
        stacking_points=1,
        toe_strength=TOEStrength(infantry=4),
        current_strength=TOEStrength(infantry=4),
    )
    state.units["it_def"] = it_def
    state.hexes["D0922"].axis_unit_ids.append("it_def")

    # Axis dump at D0922 (WITH defenders)
    axis_dump = SupplyDump(id="axis_dump_2", side=Side.AXIS, fuel=10.0, water=5.0)
    state.hexes["D0922"].supply_dumps.append(axis_dump)

    result = execute_move(state, ref, "cw_inf", ["D0821", "D0922"])
    # Move into enemy-occupied hex is now blocked — must use close_assault
    assert not result.success, "Move into enemy hex should be blocked"
    assert "enemy units" in result.description.lower(), \
        f"Should mention enemy units in reason, got: {result.description}"
    assert axis_dump.side == Side.AXIS, f"Dump should remain AXIS when move is blocked, got {axis_dump.side}"

    capture_events = [e for e in state.event_log if e["type"] == "dump_captured"]
    assert len(capture_events) == 0, f"Expected 0 capture events, got {len(capture_events)}"
    print(f"  PASS: move into enemy hex blocked, dump stays AXIS")


# ════════════════════════════════════════
# MINI SCENARIO
# ════════════════════════════════════════

def run_mini_scenario():
    """
    Mini Scenario: CW armor advances toward Italian position, enters EZOC, Axis reacts.
    """
    print("\n" + "=" * 60)
    print("MINI SCENARIO: CW Armor Advance + Axis Reaction")
    print("=" * 60)
    random.seed(1941)

    ref = _make_ref()
    state = GameState()

    # Set up map: a line of hexes D0821 (CW start) → D0922 (clear) → D1021 (Italian position)
    for hid in ["D0821", "D0922", "D1021", "D1122", "D1121"]:
        state.hexes[hid] = HexState(hex_id=hid, terrain="clear")
    # Road on D0922
    state.hexes["D0922"].road = RoadType.ROAD

    # CW 2 RTR — armor battalion, motorized
    cw_armor = Unit(
        id="cw_2rtr", name="2nd Royal Tank Regiment",
        side=Side.ALLIED, nationality="british",
        unit_class="armor", unit_size="battalion",
        motorization=MotorizationType.MECHANIZED,
        hex_id="D0821", base_cpa=10, cohesion=1,
        stacking_points=3,
        toe_strength=TOEStrength(armor=12),
        current_strength=TOEStrength(armor=12),
    )
    state.units["cw_2rtr"] = cw_armor
    state.hexes["D0821"].allied_unit_ids.append("cw_2rtr")

    # Italian infantry — at D1021
    it_inf = Unit(
        id="it_lib_bn", name="1st Libyan Infantry Bn",
        side=Side.AXIS, nationality="italian",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED,
        hex_id="D1021", base_cpa=6, cohesion=0,
        stacking_points=2,
        toe_strength=TOEStrength(infantry=10),
        current_strength=TOEStrength(infantry=10),
    )
    state.units["it_lib_bn"] = it_inf
    state.hexes["D1021"].axis_unit_ids.append("it_lib_bn")

    # Axis motorized reserve at D1122 (adjacent to D1021)
    axis_reserve = Unit(
        id="axis_m13", name="M13/40 Tank Platoon",
        side=Side.AXIS, nationality="italian",
        unit_class="armor", unit_size="company",
        motorization=MotorizationType.MECHANIZED,
        hex_id="D1122", base_cpa=8, cohesion=0,
        stacking_points=1,
        toe_strength=TOEStrength(armor=4),
        current_strength=TOEStrength(armor=4),
    )
    state.units["axis_m13"] = axis_reserve
    state.hexes["D1122"].axis_unit_ids.append("axis_m13")

    print("""
    SITUATION: CW 2 RTR at D0821 advances toward Italian position at D1021.
    Road through D0922. Italian infantry holds D1021 with M13/40 reserve at D1122.
    """)

    # Check EZOC around Italian infantry
    ezoc = get_ezoc_hexes(state, Side.ALLIED)
    print(f"  Italian EZOC hexes: {sorted(ezoc)}")

    # CW moves: D0821 → D0922 (road, 0.5 CP)
    # Check if D0922 is in EZOC
    d0922_in_ezoc = is_hex_in_ezoc(state, "D0922", Side.ALLIED)
    print(f"  D0922 in Italian EZOC? {d0922_in_ezoc}")

    if d0922_in_ezoc:
        # Must stop at D0922 (EZOC entry)
        path = ["D0821", "D0922"]
        print(f"\n  2 RTR advances to EZOC at D0922...")
    else:
        # Can continue further — find an EZOC hex
        # Try D0922 → neighbor that's in EZOC
        ezoc_step = None
        for n in get_neighbors("D0922"):
            if n in ezoc and n != "D0821":
                # Make sure hex exists
                if n not in state.hexes:
                    state.hexes[n] = HexState(hex_id=n, terrain="clear")
                ezoc_step = n
                break
        if ezoc_step:
            path = ["D0821", "D0922", ezoc_step]
            print(f"\n  2 RTR advances through D0922 into EZOC at {ezoc_step}...")
        else:
            path = ["D0821", "D0922"]
            print(f"\n  2 RTR advances to D0922...")

    result = execute_move(state, ref, "cw_2rtr", path)
    print(f"  Result: {result.description}")
    print(f"  Contact gained: {result.contact_gained}")
    print(f"  CPA spent: {cw_armor.current_cpa_spent}/{cw_armor.max_cpa_this_stage}")

    # Axis reaction: M13/40 reacts from D1122
    print(f"\n  Axis M13/40 attempts reaction move...")

    # Find a path for M13/40 to move toward the action
    m13_neighbors = get_neighbors("D1122")
    react_dest = None
    for n in m13_neighbors:
        if n != "D1021":  # Don't stack on the infantry
            if n not in state.hexes:
                state.hexes[n] = HexState(hex_id=n, terrain="clear")
            react_dest = n
            break

    if react_dest:
        react_result = attempt_reaction_move(
            state, ref, "axis_m13", ["D1122", react_dest],
            prevention_roll=4,  # Not prevented
        )
        print(f"  Result: {react_result.description}")
        print(f"  M13/40 now at: {axis_reserve.hex_id}")
    else:
        print("  (No suitable reaction destination found)")

    # Summary
    print(f"\n  ── SITUATION AFTER MOVES ──")
    for uid, u in state.units.items():
        contact = " [IN CONTACT]" if u.is_in_contact else ""
        print(f"  {u.name:30s} at {u.hex_id} "
              f"(CPA: {u.current_cpa_spent}/{u.max_cpa_this_stage}){contact}")

    print(f"\n  Events: {len(state.event_log)}")
    for ev in state.event_log:
        print(f"    {ev['type']}: {ev['description']}")

    print()


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    test_hex_parsing()
    test_adjacency()
    test_zoc()
    test_movement_cost()
    test_stacking()
    test_validation()
    test_ezoc_stop()
    test_execution()
    test_contact_engaged()
    test_reaction_movement()
    test_dump_capture()
    test_dump_not_captured_when_defended()
    run_mini_scenario()

    print("=" * 60)
    print("ALL MOVEMENT TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
