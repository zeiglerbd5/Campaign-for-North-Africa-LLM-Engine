"""
CNA Engine — Orchestrator Tests
Tests the multi-agent orchestrator with mock LLM responses.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.orchestrator.config import OrchestratorConfig
from cna_engine.orchestrator.llm_backend import MockLLMClient, LLMResponse, LLMError
from cna_engine.orchestrator.prompts import (
    build_expert_system_prompt, build_general_system_prompt,
    build_expert_user_message, build_general_user_message,
)
from cna_engine.orchestrator.experts import (
    ExpertAgent, ExpertRecommendation, Recommendation,
    get_experts_for_phase, PHASE_EXPERTS,
)
from cna_engine.orchestrator.general import GeneralAgent, OrderResult, DecisionResult
from cna_engine.orchestrator.orchestrator import GameOrchestrator, PhaseSummary
from cna_engine.orchestrator.memory import TurnMemory, TurnRecord
from cna_engine.orchestrator.doctrine import Doctrine

from cna_engine.engine.agent_interface import (
    ROLE_COMMANDER, ROLE_GROUND, ROLE_LOGISTICS, ROLE_AIR, ROLE_NAVAL,
    ROLE_COMMANDS,
)
from cna_engine.engine.turn_runner import PausePoint, TurnRunner
from cna_engine.models.game_state import (
    GameState, Unit, Formation, HexState, TurnState,
    TOEStrength, UnitSupply, SupplyDump, Aircraft, SGSU,
    FleetState, ConvoyState,
)
from cna_engine.models.enums import (
    Side, UnitStatus, GamePhase, OpStagePhase, TerrainType,
    MotorizationType, AircraftStatus,
)


# ════════════════════════════════════════
# TEST HELPERS
# ════════════════════════════════════════

def _make_state():
    """Build a minimal game state for orchestrator testing."""
    state = GameState()
    state.turn = TurnState(
        game_turn=5, op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase=OpStagePhase.MOVEMENT_COMBAT,
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
    state.hexes["D0922"].allied_sighted = True

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

    # Fleet & convoy
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


def _make_pause_point(sub_phase=OpStagePhase.MOVEMENT_COMBAT, side=Side.ALLIED):
    """Create a PausePoint for testing."""
    return PausePoint(
        game_turn=5,
        op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase=sub_phase,
        active_side=side,
        awaiting=f"{side.value}_input",
        description=f"Awaiting {sub_phase} input from {side.value}",
    )


# ════════════════════════════════════════
# TEST 1: MockLLMClient
# ════════════════════════════════════════

def test_mock_llm_client():
    print("=" * 60)
    print("TEST 1: MockLLMClient — Canned Responses")
    print("=" * 60)

    client = MockLLMClient()

    # Ground expert system prompt → should detect "ground" role
    messages = [
        {"role": "system", "content": "You are the ground operations officer."},
        {"role": "user", "content": "Assess the situation."},
    ]
    resp = client.chat(messages)
    assert resp.parsed is not None
    assert resp.parsed["role"] == "ground"
    assert "assessment" in resp.parsed
    assert "recommendations" in resp.parsed
    print(f"  Ground response: {resp.parsed}")

    # Commander system prompt → should detect "commander" role
    messages = [
        {"role": "system", "content": "You are the theater commander."},
        {"role": "user", "content": "Issue orders."},
    ]
    resp = client.chat(messages)
    assert "orders" in resp.parsed
    assert "end_phase" in resp.parsed
    print(f"  Commander response: {resp.parsed}")

    # Call log tracks all calls
    assert len(client.call_log) == 2
    print(f"  Call log entries: {len(client.call_log)}")

    # Custom response override
    client.set_response("air", {
        "role": "air",
        "assessment": "Custom air assessment",
        "priority": "high",
        "recommendations": [
            {"action": "fly_sortie", "params": {"aircraft_id": "ac1"}, "reasoning": "test"}
        ],
        "concerns": [],
    })
    messages = [
        {"role": "system", "content": "You are the air operations officer."},
        {"role": "user", "content": "Plan missions."},
    ]
    resp = client.chat(messages)
    assert resp.parsed["assessment"] == "Custom air assessment"
    assert len(resp.parsed["recommendations"]) == 1
    print(f"  Custom air response: {resp.parsed}")

    assert client.is_available() is True

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 2: Expert Assessment
# ════════════════════════════════════════

def test_expert_assessment():
    print("=" * 60)
    print("TEST 2: Expert Assessment — Valid Recommendation Schema")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)
    pause = _make_pause_point()

    # Ground expert
    expert = ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config)
    rec = expert.assess(state, pause)

    assert isinstance(rec, ExpertRecommendation)
    assert rec.role == ROLE_GROUND
    assert rec.assessment != ""
    assert rec.priority in ("high", "medium", "low")
    assert isinstance(rec.recommendations, list)
    assert isinstance(rec.concerns, list)
    print(f"  Ground rec: role={rec.role}, priority={rec.priority}, "
          f"recs={len(rec.recommendations)}")

    # Logistics expert
    expert_log = ExpertAgent(ROLE_LOGISTICS, Side.ALLIED.value, client, config)
    rec_log = expert_log.assess(state, pause)
    assert rec_log.role == ROLE_LOGISTICS
    print(f"  Logistics rec: role={rec_log.role}, priority={rec_log.priority}")

    # to_dict roundtrip
    d = rec.to_dict()
    assert d["role"] == ROLE_GROUND
    assert "assessment" in d
    assert "recommendations" in d
    print(f"  to_dict: {list(d.keys())}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 3: General Synthesis
# ════════════════════════════════════════

def test_general_synthesis():
    print("=" * 60)
    print("TEST 3: General Synthesis — Outputs Valid Commands")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)
    pause = _make_pause_point()

    # Create experts
    experts = {
        ROLE_GROUND: ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config),
        ROLE_LOGISTICS: ExpertAgent(ROLE_LOGISTICS, Side.ALLIED.value, client, config),
    }

    general = GeneralAgent(Side.ALLIED.value, client, config, experts)
    decision = general.decide(state, pause)

    assert isinstance(decision, DecisionResult)
    orders = decision.orders
    assert isinstance(orders, list)
    assert len(orders) >= 1
    # Last order should be end_phase
    assert orders[-1]["command"] == "end_phase"
    assert isinstance(decision.expert_recommendations, list)
    print(f"  Orders: {orders}")

    # Execute orders
    results = general.execute_orders(state, orders)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, OrderResult)
    print(f"  Execution results: {[(r.command, r.success) for r in results]}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 4: Command Validation
# ════════════════════════════════════════

def test_command_validation():
    print("=" * 60)
    print("TEST 4: Command Validation — Invalid Commands Skipped")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)

    # Set up General with custom response containing invalid commands
    client.set_response("commander", {
        "orders": [
            {"command": "bogus_command", "params": {}},
            {"command": "fly_to_moon", "params": {}},
            {"command": "end_phase", "params": {}},
        ],
        "end_phase": True,
        "reasoning": "Testing invalid command filtering",
    })

    experts = {
        ROLE_GROUND: ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config),
    }
    general = GeneralAgent(Side.ALLIED.value, client, config, experts)

    pause = _make_pause_point()
    decision = general.decide(state, pause)
    orders = decision.orders

    # Invalid commands should be filtered out, only end_phase remains
    commands = [o["command"] for o in orders]
    assert "bogus_command" not in commands
    assert "fly_to_moon" not in commands
    assert "end_phase" in commands
    print(f"  Filtered orders: {orders}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 5: Phase Routing
# ════════════════════════════════════════

def test_phase_routing():
    print("=" * 60)
    print("TEST 5: Phase Routing — Correct Experts per Phase")
    print("=" * 60)

    # Movement/Combat → Ground + Logistics
    pause = _make_pause_point(OpStagePhase.MOVEMENT_COMBAT)
    experts = get_experts_for_phase(pause)
    assert ROLE_GROUND in experts
    assert ROLE_LOGISTICS in experts
    print(f"  MOVEMENT_COMBAT experts: {experts}")

    # Air → Air
    pause = _make_pause_point(OpStagePhase.AIR)
    experts = get_experts_for_phase(pause)
    assert ROLE_AIR in experts
    assert ROLE_GROUND not in experts
    print(f"  AIR experts: {experts}")

    # Fleet → Naval
    pause = _make_pause_point(OpStagePhase.FLEET)
    experts = get_experts_for_phase(pause)
    assert ROLE_NAVAL in experts
    print(f"  FLEET experts: {experts}")

    # Reserve → Ground
    pause = _make_pause_point(OpStagePhase.RESERVE)
    experts = get_experts_for_phase(pause)
    assert ROLE_GROUND in experts
    print(f"  RESERVE experts: {experts}")

    # Patrol → Ground
    pause = _make_pause_point(OpStagePhase.PATROL)
    experts = get_experts_for_phase(pause)
    assert ROLE_GROUND in experts
    print(f"  PATROL experts: {experts}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 6: Full Turn Mock
# ════════════════════════════════════════

def test_full_turn_mock():
    print("=" * 60)
    print("TEST 6: Full Turn Mock — Play GT with Mock Responses")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)

    orch = GameOrchestrator(state, config)
    orch.setup(llm_client=client)

    # Verify setup created generals for both sides
    assert "allied" in orch.generals
    assert "axis" in orch.generals
    print(f"  Generals: {list(orch.generals.keys())}")

    # Verify experts were created
    allied_gen = orch.generals["allied"]
    assert len(allied_gen.experts) == len(config.experts)
    print(f"  Allied experts: {list(allied_gen.experts.keys())}")

    # Play a single phase (direct call)
    pause = _make_pause_point()
    phase_summary = orch.play_phase(pause)

    assert isinstance(phase_summary, PhaseSummary)
    assert phase_summary.side == "allied"
    assert phase_summary.orders_issued >= 1
    print(f"  Phase: side={phase_summary.side}, "
          f"orders={phase_summary.orders_issued}, "
          f"succeeded={phase_summary.orders_succeeded}")

    # Verify LLM was called (experts + general)
    assert len(client.call_log) >= 2  # at least 1 expert + 1 general
    print(f"  LLM calls made: {len(client.call_log)}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 7: Error Recovery
# ════════════════════════════════════════

def test_error_recovery():
    print("=" * 60)
    print("TEST 7: Error Recovery — Malformed LLM → Fallback")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()

    # Create a client that returns malformed (but valid JSON) response
    client = MockLLMClient(config)
    client.set_response("commander", {
        # Missing "orders" key entirely
        "thinking": "I'm confused",
    })

    experts = {
        ROLE_GROUND: ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config),
    }
    general = GeneralAgent(Side.ALLIED.value, client, config, experts)

    pause = _make_pause_point()
    decision = general.decide(state, pause)
    orders = decision.orders

    # Should still produce valid orders (at minimum end_phase)
    assert len(orders) >= 1
    assert orders[-1]["command"] == "end_phase"
    print(f"  Fallback orders: {orders}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 8: Config Switching
# ════════════════════════════════════════

def test_config_switching():
    print("=" * 60)
    print("TEST 8: Config Switching — Model Name & Parameters")
    print("=" * 60)

    # Default config
    config = OrchestratorConfig()
    assert config.model == "gpt-oss:20b"
    assert config.temperature == 0.3
    assert config.max_retries == 3
    print(f"  Default: model={config.model}, temp={config.temperature}")

    # Custom config
    config2 = OrchestratorConfig(
        model="qwen3-30b-a3b",
        temperature=0.5,
        max_retries=5,
        experts=["ground", "air"],
    )
    assert config2.model == "qwen3-30b-a3b"
    assert config2.temperature == 0.5
    assert config2.max_retries == 5
    assert len(config2.experts) == 2
    print(f"  Custom: model={config2.model}, temp={config2.temperature}, "
          f"experts={config2.experts}")

    # Orchestrator uses new config
    state = _make_state()
    orch = GameOrchestrator(state, config2)
    client = MockLLMClient(config2)
    orch.setup(llm_client=client)

    # Should only have 2 experts (ground, air)
    allied_gen = orch.generals["allied"]
    assert len(allied_gen.experts) == 2
    assert ROLE_GROUND in allied_gen.experts
    assert ROLE_AIR in allied_gen.experts
    assert ROLE_LOGISTICS not in allied_gen.experts
    print(f"  Experts created: {list(allied_gen.experts.keys())}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 9: Logging
# ════════════════════════════════════════

def test_logging():
    print("=" * 60)
    print("TEST 9: Logging — LLM Calls Tracked")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig(log_llm_calls=True)
    client = MockLLMClient(config)

    expert = ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config)
    pause = _make_pause_point()

    rec = expert.assess(state, pause)

    # Check call log
    assert len(client.call_log) == 1
    call = client.call_log[0]
    assert len(call["messages"]) == 2  # system + user
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert call["json_mode"] is True
    print(f"  Call logged: {len(call['messages'])} messages, json_mode={call['json_mode']}")

    # With logging disabled, LLM is still called (just no extra log output)
    config2 = OrchestratorConfig(log_llm_calls=False)
    client2 = MockLLMClient(config2)
    expert2 = ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client2, config2)
    rec2 = expert2.assess(state, pause)
    assert len(client2.call_log) == 1
    print(f"  Logging disabled: still called, {len(client2.call_log)} logged")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 10: Both Sides
# ════════════════════════════════════════

def test_both_sides():
    print("=" * 60)
    print("TEST 10: Both Sides — Independent Decisions")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)

    orch = GameOrchestrator(state, config)
    orch.setup(llm_client=client)

    # Allied phase
    allied_pause = _make_pause_point(
        OpStagePhase.MOVEMENT_COMBAT, Side.ALLIED,
    )
    allied_summary = orch.play_phase(allied_pause)
    assert allied_summary.side == "allied"
    allied_calls = len(client.call_log)
    print(f"  Allied phase: {allied_summary.orders_issued} orders, "
          f"{allied_calls} LLM calls")

    # Switch active side for Axis phase
    state.turn.active_side = Side.AXIS
    axis_pause = _make_pause_point(
        OpStagePhase.MOVEMENT_COMBAT, Side.AXIS,
    )
    axis_summary = orch.play_phase(axis_pause)
    assert axis_summary.side == "axis"
    axis_calls = len(client.call_log) - allied_calls
    print(f"  Axis phase: {axis_summary.orders_issued} orders, "
          f"{axis_calls} LLM calls")

    # Both sides made independent LLM calls
    assert len(client.call_log) > allied_calls
    print(f"  Total LLM calls: {len(client.call_log)}")

    print("  PASSED\n")


# ════════════════════════════════════════
# BONUS TESTS
# ════════════════════════════════════════

def test_prompt_builders():
    print("=" * 60)
    print("TEST 11: Prompt Builders — System & User Messages")
    print("=" * 60)

    # Expert system prompt
    prompt = build_expert_system_prompt(
        ROLE_GROUND, "allied", ["move_unit", "fire_barrage", "end_phase"],
    )
    assert "ground operations" in prompt.lower()
    assert "move_unit" in prompt
    assert "JSON" in prompt
    print(f"  Ground prompt length: {len(prompt)} chars")

    # General system prompt
    prompt = build_general_system_prompt("allied", "movement_combat")
    assert "theater commander" in prompt.lower()
    assert "movement_combat" in prompt
    print(f"  General prompt length: {len(prompt)} chars")

    # Expert user message
    msg = build_expert_user_message("State: units at D0821", "Movement phase")
    assert "D0821" in msg
    assert "Movement phase" in msg
    print(f"  Expert user msg length: {len(msg)} chars")

    # General user message with recommendations
    recs = [
        {
            "role": "ground",
            "assessment": "Enemy weak at D0922",
            "priority": "high",
            "recommendations": [
                {"action": "move_unit", "params": {"unit_id": "u1"}, "reasoning": "advance"}
            ],
            "concerns": ["Low fuel"],
        }
    ]
    msg = build_general_user_message("Overview text", recs, "Movement phase")
    assert "GROUND" in msg
    assert "Enemy weak" in msg
    assert "Low fuel" in msg
    assert "move_unit" in msg
    print(f"  General user msg length: {len(msg)} chars")

    print("  PASSED\n")


def test_expert_with_custom_recommendations():
    print("=" * 60)
    print("TEST 12: Expert with Actionable Recommendations")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)

    # Set up ground expert to recommend a move
    client.set_response("ground", {
        "role": "ground",
        "assessment": "Enemy is weak. Time to advance.",
        "priority": "high",
        "recommendations": [
            {
                "action": "move_unit",
                "params": {"unit_id": "u1", "destination": "D0922"},
                "reasoning": "Advance toward Sidi Barrani",
            },
        ],
        "concerns": ["Supply lines getting long"],
    })

    expert = ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config)
    pause = _make_pause_point()
    rec = expert.assess(state, pause)

    assert rec.priority == "high"
    assert len(rec.recommendations) == 1
    assert rec.recommendations[0].action == "move_unit"
    assert rec.recommendations[0].params["unit_id"] == "u1"
    assert len(rec.concerns) == 1
    print(f"  Recommendation: {rec.recommendations[0].action} "
          f"({rec.recommendations[0].reasoning})")
    print(f"  Concerns: {rec.concerns}")

    print("  PASSED\n")


def test_general_with_expert_recommendations():
    print("=" * 60)
    print("TEST 13: General Synthesizes Expert Recommendations")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig()
    client = MockLLMClient(config)

    # Set commander to return orders based on expert input
    client.set_response("commander", {
        "orders": [
            {"command": "end_phase", "params": {}},
        ],
        "end_phase": True,
        "reasoning": "Advancing with artillery support",
    })

    experts = {
        ROLE_GROUND: ExpertAgent(ROLE_GROUND, Side.ALLIED.value, client, config),
        ROLE_LOGISTICS: ExpertAgent(ROLE_LOGISTICS, Side.ALLIED.value, client, config),
    }
    general = GeneralAgent(Side.ALLIED.value, client, config, experts)

    pause = _make_pause_point()
    decision = general.decide(state, pause)

    # General should have consulted ground + logistics + made final call
    # That's 3 LLM calls: ground expert, logistics expert, general
    assert len(client.call_log) == 3
    print(f"  LLM calls: {len(client.call_log)} (2 experts + 1 general)")
    print(f"  Final orders: {decision.orders}")
    assert len(decision.expert_recommendations) >= 1

    # Verify the general's messages include expert reports
    general_call = client.call_log[2]
    general_user_msg = general_call["messages"][1]["content"]
    assert "STAFF REPORTS" in general_user_msg
    print(f"  General received staff reports: {'STAFF REPORTS' in general_user_msg}")

    print("  PASSED\n")


def test_recommendation_dataclass():
    print("=" * 60)
    print("TEST 14: ExpertRecommendation Dataclass")
    print("=" * 60)

    rec = ExpertRecommendation(
        role="ground",
        assessment="Situation is favorable",
        priority="high",
        recommendations=[
            Recommendation(action="move_unit", params={"unit_id": "u1"}, reasoning="advance"),
            Recommendation(action="fire_barrage", params={"target_hex": "D0922"}, reasoning="suppress"),
        ],
        concerns=["Low ammo", "Enemy reinforcements possible"],
    )

    d = rec.to_dict()
    assert d["role"] == "ground"
    assert d["priority"] == "high"
    assert len(d["recommendations"]) == 2
    assert d["recommendations"][0]["action"] == "move_unit"
    assert len(d["concerns"]) == 2
    print(f"  to_dict keys: {list(d.keys())}")
    print(f"  Recs: {[r['action'] for r in d['recommendations']]}")
    print(f"  Concerns: {d['concerns']}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 15: Outcome Feedback Generation
# ════════════════════════════════════════

def test_outcome_feedback():
    print("=" * 60)
    print("TEST 15: Outcome Feedback — Tactical Lessons from Turn History")
    print("=" * 60)

    memory = TurnMemory(max_turns=5)

    # Simulate GT1: good supply
    state = _make_state()
    state.turn.game_turn = 1
    # Record a phase with some orders
    ps = PhaseSummary(
        side="allied",
        phase="op_stage",
        sub_phase="movement_combat",
        orders_issued=2,
        orders_succeeded=2,
        orders_failed=0,
        results=[
            OrderResult(command="move_unit", params={"unit_id": "u1"}, success=True),
            OrderResult(command="end_phase", params={}, success=True),
        ],
    )
    memory.record_phase(state, ps)
    memory.record_turn(state)

    # GT1 should have no feedback (no previous turn to compare)
    assert memory.records[-1].feedback == []
    print(f"  GT1 feedback (no prior): {memory.records[-1].feedback}")

    # Simulate GT2: lower supply (simulate by modifying unit supply)
    state.turn.game_turn = 2
    # Drain water significantly
    state.units["u1"].supply.water = 2.0  # was 8.0 / 16.0 = 50%, now 2/16 = 12.5%

    ps2 = PhaseSummary(
        side="allied",
        phase="op_stage",
        sub_phase="movement_combat",
        orders_issued=3,
        orders_succeeded=1,
        orders_failed=2,
        results=[
            OrderResult(command="move_unit", params={"unit_id": "u1"}, success=False, error="no CPA"),
            OrderResult(command="fire_barrage", params={"target": "D0922"}, success=False, error="out of range"),
            OrderResult(command="end_phase", params={}, success=True),
        ],
    )
    memory.record_phase(state, ps2)
    memory.record_turn(state)

    gt2_feedback = memory.records[-1].feedback
    assert len(gt2_feedback) > 0, "GT2 should have feedback"
    print(f"  GT2 feedback ({len(gt2_feedback)} items):")
    for fb in gt2_feedback:
        print(f"    - {fb}")

    # Check that feedback mentions water decline or critical supply
    feedback_text = " ".join(gt2_feedback).lower()
    has_supply_warning = "water" in feedback_text or "critical" in feedback_text
    assert has_supply_warning, f"Expected supply warning in feedback: {gt2_feedback}"

    # Check that feedback mentions command failures
    has_failure_warning = "failed" in feedback_text or "fail" in feedback_text
    assert has_failure_warning, f"Expected failure warning in feedback: {gt2_feedback}"

    # Verify feedback appears in context text
    context = memory.get_context_text("allied")
    assert "Note:" in context, "Context text should contain 'Note:' feedback lines"
    print(f"  Context contains feedback: {'Note:' in context}")

    # Verify serialization roundtrip preserves feedback
    data = memory.to_dict()
    assert data["records"][1]["feedback"] == gt2_feedback
    restored = TurnMemory.from_dict(data)
    assert restored.records[1].feedback == gt2_feedback
    print(f"  Serialization roundtrip: OK")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 16: Doctrine
# ════════════════════════════════════════

def test_doctrine():
    print("=" * 60)
    print("TEST 16: Doctrine — Cross-Game Lessons")
    print("=" * 60)

    import tempfile, os, json

    # Test with empty doctrine (no file)
    with tempfile.TemporaryDirectory() as tmpdir:
        doctrine_path = os.path.join(tmpdir, "doctrine.jsonl")
        doctrine = Doctrine(filepath=doctrine_path)

        # No lessons yet
        assert len(doctrine.lessons) == 0
        assert doctrine.get_doctrine_text("allied") == ""
        print(f"  Empty doctrine: OK")

        # Write a fake game log with some failures
        game_log_path = os.path.join(tmpdir, "game_test.jsonl")
        log_records = [
            {
                "type": "phase", "timestamp": "2025-01-01T00:00:00",
                "game_turn": 1, "side": "allied", "skipped": False,
                "orders": [
                    {"command": "truck_load", "success": False, "error": "truck not attached", "params": {}},
                    {"command": "truck_load", "success": False, "error": "truck not attached", "params": {}},
                    {"command": "move_unit", "success": True, "params": {"unit_id": "u1"}},
                    {"command": "end_phase", "success": True, "params": {}},
                ],
            },
            {
                "type": "phase", "timestamp": "2025-01-01T00:01:00",
                "game_turn": 1, "side": "allied", "skipped": False,
                "orders": [
                    {"command": "move_unit", "success": True, "params": {"unit_id": "u1"}},
                    {"command": "move_unit", "success": True, "params": {"unit_id": "u2"}},
                    {"command": "move_unit", "success": True, "params": {"unit_id": "u3"}},
                    {"command": "end_phase", "success": True, "params": {}},
                ],
            },
            {
                "type": "turn_end", "timestamp": "2025-01-01T00:02:00",
                "game_turn": 1, "elapsed_ms": 5000,
            },
        ]
        with open(game_log_path, "w") as f:
            for rec in log_records:
                f.write(json.dumps(rec) + "\n")

        # Extract lessons
        lessons = doctrine.update_from_game_log(game_log_path)
        assert len(lessons) > 0, "Should extract lessons from game log"
        print(f"  Extracted {len(lessons)} lessons:")
        for l in lessons:
            print(f"    [{l['category']}] {l['lesson']}")

        # Doctrine file should now exist
        assert os.path.exists(doctrine_path)
        print(f"  Doctrine file created: OK")

        # get_doctrine_text should return content
        text = doctrine.get_doctrine_text("allied")
        assert len(text) > 0
        assert "Cross-game doctrine" in text
        print(f"  Doctrine text ({len(text)} chars): OK")

        # Reload doctrine from file
        doctrine2 = Doctrine(filepath=doctrine_path)
        assert len(doctrine2.lessons) == len(lessons)
        print(f"  Reload from file: {len(doctrine2.lessons)} lessons")

    print("  PASSED\n")


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    test_mock_llm_client()
    test_expert_assessment()
    test_general_synthesis()
    test_command_validation()
    test_phase_routing()
    test_full_turn_mock()
    test_error_recovery()
    test_config_switching()
    test_logging()
    test_both_sides()
    test_prompt_builders()
    test_expert_with_custom_recommendations()
    test_general_with_expert_recommendations()
    test_recommendation_dataclass()
    test_outcome_feedback()
    test_doctrine()
    print("=" * 60)
    print("ALL ORCHESTRATOR TESTS PASSED (16/16)")
    print("=" * 60)


if __name__ == "__main__":
    main()
