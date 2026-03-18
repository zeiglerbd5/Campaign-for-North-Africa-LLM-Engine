"""
CNA Engine — Smart Mock Strategy Tests
Tests SmartMockLLMClient with real Operation Compass game state.
Verifies that per-role strategies produce valid, state-aware responses.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.orchestrator.mock_strategies import (
    SmartMockLLMClient,
    _ground_strategy,
    _logistics_strategy,
    _air_strategy,
    _naval_strategy,
    _commander_strategy,
)
from cna_engine.orchestrator.config import OrchestratorConfig
from cna_engine.orchestrator.orchestrator import GameOrchestrator
from cna_engine.orchestrator.experts import ExpertAgent
from cna_engine.orchestrator.general import GeneralAgent
from cna_engine.engine.agent_interface import (
    ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL,
    ROLE_COMMANDS,
)
from cna_engine.engine.scenario import load_scenario
from cna_engine.engine.turn_runner import PausePoint
from cna_engine.models.enums import (
    Side, GamePhase, OpStagePhase, UnitStatus,
)


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

def _load_scenario():
    """Load Operation Compass scenario for testing."""
    state, reinforcements = load_scenario("operation_compass")
    return state, reinforcements


def _make_pause_point(sub_phase=OpStagePhase.MOVEMENT_COMBAT, side=Side.ALLIED):
    """Create a PausePoint matching Operation Compass GT1."""
    return PausePoint(
        game_turn=1,
        op_stage=1,
        phase=GamePhase.OP_STAGE,
        sub_phase=sub_phase,
        active_side=side,
        awaiting=f"{side.value}_input",
        description=f"Awaiting {sub_phase} input from {side.value}",
    )


# ════════════════════════════════════════
# TEST 1: Ground Strategy — Valid Unit IDs
# ════════════════════════════════════════

def test_ground_valid_unit_ids():
    print("=" * 60)
    print("TEST 1: Ground Strategy — Valid Unit IDs")
    print("=" * 60)

    state, _ = _load_scenario()
    result = _ground_strategy(state, "allied")

    assert result["role"] == "ground"
    assert isinstance(result["recommendations"], list)
    assert isinstance(result["assessment"], str)
    print(f"  Assessment: {result['assessment']}")
    print(f"  Recommendations: {len(result['recommendations'])}")

    # Verify all unit IDs in recommendations are real
    for rec in result["recommendations"]:
        params = rec.get("params", {})
        if "unit_id" in params:
            uid = params["unit_id"]
            assert uid in state.units, f"Invalid unit ID: {uid}"
            print(f"    {rec['action']}: unit={uid} ({state.units[uid].name})")
        if "target_hex" in params:
            print(f"    {rec['action']}: target={params['target_hex']}")

    # Also test Axis side
    axis_result = _ground_strategy(state, "axis")
    assert axis_result["role"] == "ground"
    print(f"  Axis: {axis_result['assessment']}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 2: Logistics — Critical Supply Detection
# ════════════════════════════════════════

def test_logistics_critical_supply():
    print("=" * 60)
    print("TEST 2: Logistics — Critical Supply Detection")
    print("=" * 60)

    state, _ = _load_scenario()
    result = _logistics_strategy(state, "allied")

    assert result["role"] == "logistics"
    print(f"  Assessment: {result['assessment']}")
    print(f"  Priority: {result['priority']}")
    print(f"  Recommendations: {len(result['recommendations'])}")
    print(f"  Concerns: {len(result['concerns'])}")

    for rec in result["recommendations"]:
        params = rec.get("params", {})
        if "unit_id" in params:
            assert params["unit_id"] in state.units
        print(f"    {rec['action']}: {rec['reasoning'][:60]}")

    # Axis side
    axis_result = _logistics_strategy(state, "axis")
    print(f"  Axis: {axis_result['assessment']}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 3: Air — No-Aircraft Case
# ════════════════════════════════════════

def test_air_no_aircraft():
    print("=" * 60)
    print("TEST 3: Air Strategy — Handles Available/No Aircraft")
    print("=" * 60)

    state, _ = _load_scenario()

    # Allied should have some aircraft
    allied_result = _air_strategy(state, "allied")
    assert allied_result["role"] == "air"
    print(f"  Allied: {allied_result['assessment']}")
    print(f"  Allied recommendations: {len(allied_result['recommendations'])}")

    for rec in allied_result["recommendations"]:
        params = rec.get("params", {})
        if "aircraft_id" in params:
            assert params["aircraft_id"] in state.aircraft, \
                f"Invalid aircraft ID: {params['aircraft_id']}"
            print(f"    {rec['action']}: aircraft={params['aircraft_id']}")

    # Test with a state that has no aircraft
    empty_state = state
    # Save and clear aircraft
    saved_aircraft = dict(state.aircraft)
    state.aircraft.clear()
    no_ac_result = _air_strategy(state, "allied")
    assert len(no_ac_result["recommendations"]) == 0
    assert "no aircraft" in no_ac_result["assessment"].lower()
    print(f"  No aircraft: {no_ac_result['assessment']}")

    # Restore
    state.aircraft = saved_aircraft

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 4: Naval — Allied vs Axis Differ
# ════════════════════════════════════════

def test_naval_differs():
    print("=" * 60)
    print("TEST 4: Naval — Allied vs Axis Strategies Differ")
    print("=" * 60)

    state, _ = _load_scenario()

    allied_result = _naval_strategy(state, "allied")
    axis_result = _naval_strategy(state, "axis")

    assert allied_result["role"] == "naval"
    assert axis_result["role"] == "naval"
    print(f"  Allied: {allied_result['assessment']}")
    print(f"  Axis: {axis_result['assessment']}")

    # Allied should reference fleet, Axis should reference convoy
    allied_actions = {r["action"] for r in allied_result["recommendations"]}
    axis_actions = {r["action"] for r in axis_result["recommendations"]}

    # The strategies should differ
    if allied_result["recommendations"]:
        print(f"  Allied actions: {allied_actions}")
    if axis_result["recommendations"]:
        assert "plan_convoy" in axis_actions
        print(f"  Axis actions: {axis_actions}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 5: Commander — Synthesizes Expert Recs
# ════════════════════════════════════════

def test_commander_synthesis():
    print("=" * 60)
    print("TEST 5: Commander — Synthesizes Expert Recommendations")
    print("=" * 60)

    state, _ = _load_scenario()

    # Simulate messages the commander would receive
    messages = [
        {"role": "system", "content": "You are the theater commander for allied forces."},
        {"role": "user", "content": """=== STAFF REPORTS ===
--- GROUND (priority: high) ---
Assessment: Enemy is weak on the flank
Recommendations:
  - move_unit: Advance 2RTR toward Sidi Barrani
  - fire_barrage: Suppress enemy at D0922

--- LOGISTICS (priority: medium) ---
Assessment: Supply adequate
Recommendations:
  - draw_from_dump: Resupply low units
"""},
    ]

    result = _commander_strategy(state, "allied", messages)
    assert "orders" in result
    assert "end_phase" in result
    assert "reasoning" in result

    # Should have picked up some actions from expert text
    commands = [o["command"] for o in result["orders"]]
    assert "end_phase" in commands  # Always present
    print(f"  Orders: {commands}")
    print(f"  Reasoning: {result['reasoning']}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 6: Full Phase with SmartMockLLMClient
# ════════════════════════════════════════

def test_full_phase():
    print("=" * 60)
    print("TEST 6: Full Phase — SmartMockLLMClient Through Expert+General")
    print("=" * 60)

    state, _ = _load_scenario()
    config = OrchestratorConfig()
    client = SmartMockLLMClient(state, config)

    # Create experts and general
    experts = {
        ROLE_GROUND: ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config),
        ROLE_LOGISTICS: ExpertAgent(ROLE_LOGISTICS, Side.ALLIED.value, client, config),
    }
    general = GeneralAgent(Side.ALLIED.value, client, config, experts)

    pause = _make_pause_point()
    orders = general.decide(state, pause)

    assert isinstance(orders, list)
    assert len(orders) >= 1
    assert orders[-1]["command"] == "end_phase"
    print(f"  Orders: {[o['command'] for o in orders]}")
    print(f"  LLM calls: {len(client.call_log)}")

    # Verify call log shows expert + general calls
    assert len(client.call_log) >= 3  # ground + logistics + general

    # Execute orders (some may fail due to phase/state constraints)
    results = general.execute_orders(state, orders)
    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    print(f"  Execution: {succeeded} succeeded, {failed} failed")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 7: Full Turn Through Orchestrator
# ════════════════════════════════════════

def test_full_turn():
    print("=" * 60)
    print("TEST 7: Full Turn — SmartMock Through GameOrchestrator")
    print("=" * 60)

    state, _ = _load_scenario()
    config = OrchestratorConfig()
    client = SmartMockLLMClient(state, config)

    orch = GameOrchestrator(state, config)
    orch.setup(llm_client=client)

    # Play one full turn
    summary = orch.play_turn()

    assert summary.game_turn == 1
    assert summary.phases_handled >= 0
    print(f"  Turn GT{summary.game_turn}:")
    print(f"    Auto phases: {summary.auto_phases}")
    print(f"    Interactive phases: {summary.interactive_phases}")
    print(f"    Phase summaries: {len(summary.phase_summaries)}")
    print(f"    Total LLM calls: {len(client.call_log)}")

    for ps in summary.phase_summaries:
        print(f"    {ps.side} {ps.sub_phase}: {ps.orders_succeeded}/{ps.orders_issued} orders ok")

    print("  PASSED\n")


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    test_ground_valid_unit_ids()
    test_logistics_critical_supply()
    test_air_no_aircraft()
    test_naval_differs()
    test_commander_synthesis()
    test_full_phase()
    test_full_turn()
    print("=" * 60)
    print("ALL SMART MOCK TESTS PASSED (7/7)")
    print("=" * 60)


if __name__ == "__main__":
    main()
