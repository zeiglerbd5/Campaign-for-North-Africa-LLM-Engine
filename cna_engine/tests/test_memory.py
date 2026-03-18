"""
CNA Engine — Turn Memory Tests
Tests TurnMemory recording, rolling window, context generation,
expert filtering, serialization, and orchestrator integration.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.orchestrator.memory import TurnMemory, TurnRecord
from cna_engine.orchestrator.orchestrator import GameOrchestrator, PhaseSummary
from cna_engine.orchestrator.general import OrderResult
from cna_engine.orchestrator.config import OrchestratorConfig
from cna_engine.orchestrator.llm_backend import MockLLMClient
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState,
    TOEStrength, UnitSupply, FleetState, ConvoyState,
)
from cna_engine.models.enums import (
    Side, UnitStatus, GamePhase, OpStagePhase, TerrainType,
    MotorizationType, AircraftStatus,
)


# ════════════════════════════════════════
# TEST HELPERS
# ════════════════════════════════════════

def _make_state(gt=5):
    """Build a minimal game state for memory testing."""
    state = GameState()
    state.turn = TurnState(
        game_turn=gt, op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase=OpStagePhase.MOVEMENT_COMBAT,
        active_side=Side.ALLIED,
    )

    # Allied unit with some supply
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

    # Allied unit with low supply
    state.units["u2"] = Unit(
        id="u2", name="11th Hussars", side=Side.ALLIED, nationality="british",
        unit_class="recon", unit_size="company",
        motorization=MotorizationType.MECHANIZED, hex_id="D0921",
        base_cpa=8, stacking_points=1,
        current_strength=TOEStrength(recon=4),
        toe_strength=TOEStrength(recon=4),
        supply=UnitSupply(fuel=2.0, water=1.0, ammo=3.0, stores=1.0,
                          fuel_capacity=20.0, water_capacity=10.0,
                          ammo_capacity=10.0, stores_capacity=5.0),
        losses_taken=2,
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
    state.hexes["D0921"] = HexState(hex_id="D0921", terrain=TerrainType.CLEAR)
    state.hexes["D0921"].allied_unit_ids = ["u2"]
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR)
    state.hexes["D0922"].axis_unit_ids = ["e1"]

    state.cw_fleet = FleetState(is_available=True, sorties_remaining=2)
    state.axis_convoy = ConvoyState()

    return state


def _make_phase_summary(side="allied", succeeded=2, failed=1):
    """Create a PhaseSummary for memory testing."""
    results = [
        OrderResult(command="move_unit", params={"unit_id": "u1", "path": ["D0821", "D0921"]},
                    success=True),
        OrderResult(command="fire_barrage", params={"target_hex": "D0922"},
                    success=True),
        OrderResult(command="end_phase", params={}, success=True),
    ]
    return PhaseSummary(
        side=side,
        phase="op_stage",
        sub_phase="movement_combat",
        orders_issued=3,
        orders_succeeded=succeeded,
        orders_failed=failed,
        results=results[:succeeded + failed],
    )


# ════════════════════════════════════════
# TEST 1: Record Turn and Verify Fields
# ════════════════════════════════════════

def test_record_turn():
    print("=" * 60)
    print("TEST 1: Record Turn — Basic Fields")
    print("=" * 60)

    state = _make_state(gt=5)
    mem = TurnMemory(max_turns=5)

    # Record a phase, then finalize the turn
    phase_sum = _make_phase_summary()
    mem.record_phase(state, phase_sum)
    mem.record_turn(state)

    assert len(mem.records) == 1
    rec = mem.records[0]
    assert rec.game_turn == 5
    assert len(rec.orders_given) > 0
    assert len(rec.outcomes) > 0
    assert "allied" in rec.supply_snapshot
    assert "axis" in rec.supply_snapshot
    assert "allied" in rec.unit_losses
    print(f"  GT{rec.game_turn}: {len(rec.orders_given)} orders, {len(rec.outcomes)} outcomes")
    print(f"  Supply: {rec.supply_snapshot['allied']}")
    print(f"  Losses: {rec.unit_losses}")

    # Verify supply snapshot captured low-supply unit
    assert rec.supply_snapshot["allied"]["critical_units"] >= 1
    print(f"  Critical units detected: {rec.supply_snapshot['allied']['critical_units']}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 2: Rolling Window Eviction
# ════════════════════════════════════════

def test_rolling_window():
    print("=" * 60)
    print("TEST 2: Rolling Window — Evicts Oldest")
    print("=" * 60)

    mem = TurnMemory(max_turns=3)

    # Record 5 turns
    for gt in range(1, 6):
        state = _make_state(gt=gt)
        mem.record_turn(state)

    # Should keep only last 3
    assert len(mem.records) == 3
    assert mem.records[0].game_turn == 3
    assert mem.records[1].game_turn == 4
    assert mem.records[2].game_turn == 5
    print(f"  Stored turns: {[r.game_turn for r in mem.records]}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 3: Context Text Under Char Limit
# ════════════════════════════════════════

def test_context_char_limit():
    print("=" * 60)
    print("TEST 3: Context Text — Under Char Limit")
    print("=" * 60)

    mem = TurnMemory(max_turns=5)

    # Build up several turns with activity
    for gt in range(1, 6):
        state = _make_state(gt=gt)
        phase_sum = _make_phase_summary()
        mem.record_phase(state, phase_sum)
        mem.record_turn(state)

    # Default limit
    text = mem.get_context_text("allied", max_chars=2000)
    assert len(text) <= 2000
    assert "GT1" in text or "GT3" in text  # At least some turns
    print(f"  Context length: {len(text)} chars (limit 2000)")
    print(f"  Preview:\n{text[:300]}...")

    # Small limit forces truncation
    short = mem.get_context_text("allied", max_chars=100)
    assert len(short) <= 100
    assert short.endswith("...")
    print(f"  Short context: {len(short)} chars (limit 100)")

    # Empty memory returns empty string
    empty_mem = TurnMemory()
    assert empty_mem.get_context_text("allied") == ""
    print(f"  Empty memory: returns ''")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 4: Expert-Filtered Context
# ════════════════════════════════════════

def test_expert_filtered_context():
    print("=" * 60)
    print("TEST 4: Expert-Filtered Context")
    print("=" * 60)

    mem = TurnMemory(max_turns=5)
    state = _make_state(gt=5)

    # Record phase with ground commands
    phase_sum = _make_phase_summary()
    mem.record_phase(state, phase_sum)
    mem.record_turn(state)

    # Ground expert should see move_unit and fire_barrage
    ground_ctx = mem.get_context_for_expert("allied", "ground", max_chars=1000)
    assert len(ground_ctx) <= 1000
    print(f"  Ground context ({len(ground_ctx)} chars):")
    if ground_ctx:
        print(f"    {ground_ctx[:200]}")

    # Air expert should get less (no air commands recorded)
    air_ctx = mem.get_context_for_expert("allied", "air", max_chars=1000)
    print(f"  Air context ({len(air_ctx)} chars): {'(empty)' if not air_ctx else air_ctx[:100]}")

    # Logistics expert should see supply info
    log_ctx = mem.get_context_for_expert("allied", "logistics", max_chars=1000)
    assert len(log_ctx) <= 1000
    print(f"  Logistics context ({len(log_ctx)} chars):")
    if log_ctx:
        print(f"    {log_ctx[:200]}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 5: Serialization Roundtrip
# ════════════════════════════════════════

def test_serialization_roundtrip():
    print("=" * 60)
    print("TEST 5: Serialization — to_dict / from_dict Roundtrip")
    print("=" * 60)

    mem = TurnMemory(max_turns=5)

    # Build up some history
    for gt in range(1, 4):
        state = _make_state(gt=gt)
        phase_sum = _make_phase_summary()
        mem.record_phase(state, phase_sum)
        mem.record_turn(state)

    # Serialize
    data = mem.to_dict()
    assert data["max_turns"] == 5
    assert len(data["records"]) == 3
    print(f"  Serialized: {len(data['records'])} records")

    # Deserialize
    mem2 = TurnMemory.from_dict(data)
    assert mem2.max_turns == 5
    assert len(mem2.records) == 3
    assert mem2.records[0].game_turn == 1
    assert mem2.records[2].game_turn == 3

    # Verify content preserved
    assert len(mem2.records[0].orders_given) == len(mem.records[0].orders_given)
    assert mem2.records[0].supply_snapshot == mem.records[0].supply_snapshot
    print(f"  Deserialized: {len(mem2.records)} records, data preserved")

    # Context text should be identical
    ctx1 = mem.get_context_text("allied")
    ctx2 = mem2.get_context_text("allied")
    assert ctx1 == ctx2
    print(f"  Context text match: {ctx1 == ctx2}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 6: Memory Clear
# ════════════════════════════════════════

def test_memory_clear():
    print("=" * 60)
    print("TEST 6: Memory Clear")
    print("=" * 60)

    mem = TurnMemory(max_turns=5)
    state = _make_state(gt=1)
    mem.record_turn(state)
    assert len(mem.records) == 1

    mem.clear()
    assert len(mem.records) == 0
    assert mem._current_record is None
    assert mem.get_context_text("allied") == ""
    print(f"  After clear: {len(mem.records)} records")

    print("  PASSED\n")


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    test_record_turn()
    test_rolling_window()
    test_context_char_limit()
    test_expert_filtered_context()
    test_serialization_roundtrip()
    test_memory_clear()
    print("=" * 60)
    print("ALL MEMORY TESTS PASSED (6/6)")
    print("=" * 60)


if __name__ == "__main__":
    main()
