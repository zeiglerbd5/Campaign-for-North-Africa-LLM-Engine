"""
CNA Engine — Situation-Action Engine Tests
Tests signal extraction, situation classification, playbook registry,
state filters, and the full two-stage pipeline with mock LLM.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.orchestrator.config import OrchestratorConfig
from cna_engine.orchestrator.llm_backend import MockLLMClient, LLMResponse

from cna_engine.orchestrator.situations import (
    StateSignals, SituationLabel, extract_signals,
    deterministic_classify, build_classifier_system_prompt,
    build_classifier_user_prompt, parse_classifier_response,
    SITUATION_TAXONOMY, SITUATION_TO_CATEGORY,
)
from cna_engine.orchestrator.playbooks import (
    Playbook, PlaybookRegistry,
)
from cna_engine.orchestrator.situation_engine import SituationEngine
from cna_engine.orchestrator.general import DecisionResult

from cna_engine.engine.turn_runner import PausePoint
from cna_engine.models.game_state import (
    GameState, Unit, HexState, TurnState,
    TOEStrength, UnitSupply, SupplyDump,
    FleetState, ConvoyState, Aircraft, SGSU,
)
from cna_engine.models.enums import (
    Side, UnitStatus, GamePhase, OpStagePhase, TerrainType,
    MotorizationType, AircraftStatus,
)


# ════════════════════════════════════════
# TEST HELPERS
# ════════════════════════════════════════

def _make_state():
    """Build a minimal game state for situation engine testing."""
    state = GameState()
    state.turn = TurnState(
        game_turn=5, op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase=OpStagePhase.MOVEMENT_COMBAT,
        active_side=Side.ALLIED,
    )

    # Allied unit — armor near front
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

    # Allied infantry
    state.units["u2"] = Unit(
        id="u2", name="1st Rifle Bn", side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D0821",
        base_cpa=6, stacking_points=2,
        current_strength=TOEStrength(infantry=8, gun=4),
        toe_strength=TOEStrength(infantry=8, gun=4),
        supply=UnitSupply(fuel=0.0, water=6.0, ammo=8.0, stores=4.0,
                          fuel_capacity=0.0, water_capacity=12.0,
                          ammo_capacity=16.0, stores_capacity=8.0),
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

    # Hexes — u1 and u2 are adjacent to e1
    state.hexes["D0821"] = HexState(hex_id="D0821", terrain=TerrainType.CLEAR)
    state.hexes["D0821"].allied_unit_ids = ["u1", "u2"]
    state.hexes["D0922"] = HexState(hex_id="D0922", terrain=TerrainType.CLEAR)
    state.hexes["D0922"].axis_unit_ids = ["e1"]
    state.hexes["D0922"].allied_sighted = True

    # Supply dump (with water too)
    state.hexes["D0821"].supply_dumps.append(SupplyDump(
        id="depot1", side=Side.ALLIED, is_real=True, fuel=100.0, water=50.0,
    ))

    # Oasis and bir hexes (water sources)
    state.hexes["D1122"] = HexState(hex_id="D1122", terrain=TerrainType.OASIS)
    state.hexes["D1222"] = HexState(hex_id="D1222", terrain=TerrainType.BIR)
    state.hexes["D1322"] = HexState(hex_id="D1322", terrain=TerrainType.BIR)

    # Port hexes
    state.hexes["D1822"] = HexState(
        hex_id="D1822", terrain=TerrainType.COASTAL, is_port=True, port_name="Mersa Matruh",
    )
    state.hexes["D0922"].is_port = True
    state.hexes["D0922"].port_name = "Sidi Barrani"

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
    return PausePoint(
        game_turn=5,
        op_stage=2,
        phase=GamePhase.OP_STAGE,
        sub_phase=sub_phase,
        active_side=side,
        awaiting=f"{side.value}_input",
        description=f"Awaiting {sub_phase} input from {side.value}",
    )


class SituationMockLLM:
    """Mock LLM that returns canned responses for situation engine tests."""

    def __init__(self, classifier_response=None, playbook_response=None):
        self.classifier_response = classifier_response or {
            "role": "front_line",
            "situation": "ATTACK_PREPARED",
            "confidence": 0.85,
            "reasoning": "Adjacent to enemy with favorable ratio",
            "secondary_situation": "",
        }
        self.playbook_response = playbook_response or {
            "orders": [
                {"command": "fire_barrage", "params": {"target_hex": "D0922", "target_class": "infantry"}},
                {"command": "close_assault", "params": {"target_hex": "D0922"}},
                {"command": "end_phase", "params": {}},
            ],
            "reasoning": "Barrage then assault the weakened position",
        }
        self.call_log = []
        self._call_count = 0

    def chat(self, messages, json_mode=True, think=None):
        self._call_count += 1
        self.call_log.append({"messages": messages, "call_num": self._call_count})

        # First call = classifier, subsequent = playbook
        if self._call_count == 1:
            parsed = self.classifier_response
        else:
            parsed = self.playbook_response

        return LLMResponse(
            content=str(parsed),
            parsed=parsed,
            model="mock",
            prompt_tokens=100,
            completion_tokens=50,
            duration_ms=100,
        )


# ════════════════════════════════════════
# TEST 1: StateSignals extraction
# ════════════════════════════════════════

def test_extract_signals():
    print("=" * 60)
    print("TEST 1: StateSignals Extraction")
    print("=" * 60)

    state = _make_state()
    signals = extract_signals(state, Side.ALLIED, "movement_combat")

    assert signals.side == Side.ALLIED
    assert signals.game_turn == 5
    assert signals.active_units == 2, f"Expected 2 active units, got {signals.active_units}"
    assert signals.total_strength > 0, "Expected positive strength"

    # Enemy sighted (D0922 is sighted)
    assert signals.enemy_units_sighted >= 1, f"Expected >=1 sighted enemy, got {signals.enemy_units_sighted}"

    # Supply percentages
    assert 0 <= signals.avg_fuel_pct <= 100
    assert 0 <= signals.avg_water_pct <= 100

    print(f"  Active units: {signals.active_units}")
    print(f"  Total strength: {signals.total_strength}")
    print(f"  Enemy sighted: {signals.enemy_units_sighted}")
    print(f"  Contact points: {signals.units_in_contact}")
    print(f"  Best ratio: {signals.best_assault_ratio:.1f}:1")
    print(f"  Fuel: {signals.avg_fuel_pct:.0f}%  Water: {signals.avg_water_pct:.0f}%")
    print(f"  Fuel critical: {signals.fuel_critical_count}  Water critical: {signals.water_critical_count}")
    print(f"  Motorized: {signals.motorized_count}")
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 2: Deterministic classification
# ════════════════════════════════════════

def test_deterministic_classify():
    print("=" * 60)
    print("TEST 2: Deterministic Classification")
    print("=" * 60)

    # Zero water → SUPPLY_CRITICAL_WATER
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=5, any_zero_water=True,
    )
    label = deterministic_classify(signals)
    assert label is not None
    assert label.situation == "SUPPLY_CRITICAL_WATER"
    assert label.confidence == 1.0
    assert label.deterministic is True
    print(f"  Zero water → {label.situation} (conf={label.confidence})")

    # Zero fuel + motorized → SUPPLY_CRITICAL_FUEL
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=5, any_zero_fuel=True, motorized_fuel_critical=2,
    )
    label = deterministic_classify(signals)
    assert label is not None
    assert label.situation == "SUPPLY_CRITICAL_FUEL"
    print(f"  Zero fuel motorized → {label.situation}")

    # No active units → VP_CRISIS
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=0,
    )
    label = deterministic_classify(signals)
    assert label is not None
    assert label.situation == "VP_CRISIS"
    print(f"  No units → {label.situation}")

    # Overextended + low fuel → OVEREXTENDED_HALT
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=5, overextended=True, avg_fuel_pct=20.0,
        furthest_unit_from_port=40,
    )
    label = deterministic_classify(signals)
    assert label is not None
    assert label.situation == "OVEREXTENDED_HALT"
    print(f"  Overextended + low fuel → {label.situation}")

    # No enemy → ADVANCE_OPPORTUNITY
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=5, enemy_units_sighted=0, units_in_contact=0,
        avg_fuel_pct=80.0, avg_water_pct=70.0,
    )
    label = deterministic_classify(signals)
    assert label is not None
    assert label.situation == "ADVANCE_OPPORTUNITY"
    print(f"  No enemy contact → {label.situation}")

    # Normal combat state → None (needs LLM)
    signals = StateSignals(
        side="allied", phase="movement_combat",
        active_units=5, enemy_units_sighted=3, units_in_contact=2,
        avg_fuel_pct=60.0, avg_water_pct=50.0,
    )
    label = deterministic_classify(signals)
    assert label is None
    print("  Normal combat → None (needs LLM)")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 3: Classifier prompts
# ════════════════════════════════════════

def test_classifier_prompts():
    print("=" * 60)
    print("TEST 3: Classifier Prompts")
    print("=" * 60)

    # System prompt
    system = build_classifier_system_prompt("allied", "movement_combat", role_hint="front_line")
    assert "Allied" in system
    assert "ATTACK_PREPARED" in system
    assert "DEFENSIVE_HOLD" in system
    # Should not include logistics situations when role_hint is front_line
    assert "CONVOY_PLANNING" not in system
    print(f"  System prompt length: {len(system)} chars")

    # User prompt
    signals = StateSignals(
        side="allied", phase="movement_combat", game_turn=5,
        active_units=4, total_strength=50, enemy_units_sighted=2,
        units_in_contact=1, best_assault_ratio=2.5,
        avg_fuel_pct=60.0, avg_water_pct=45.0,
    )
    user = build_classifier_user_prompt(signals)
    assert "active_units=4" in user
    assert "best_ratio=2.5:1" in user
    print(f"  User prompt length: {len(user)} chars")

    # Total ~550 tokens (well under old ~3000)
    print(f"  Combined: ~{(len(system) + len(user)) // 4} tokens (estimated)")

    # Parse response
    parsed = {
        "role": "front_line",
        "situation": "ATTACK_PREPARED",
        "confidence": 0.85,
        "reasoning": "Good ratio for assault",
    }
    label = parse_classifier_response(parsed)
    assert label.situation == "ATTACK_PREPARED"
    assert label.role == "front_line"
    print(f"  Parsed: {label.situation} ({label.confidence})")

    # Unknown situation → fallback
    parsed = {"situation": "NONEXISTENT_SITUATION", "role": "front_line"}
    label = parse_classifier_response(parsed)
    assert label.situation == "DEFENSIVE_HOLD"
    print(f"  Unknown situation fallback: {label.situation}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 4: PlaybookRegistry
# ════════════════════════════════════════

def test_playbook_registry():
    print("=" * 60)
    print("TEST 4: PlaybookRegistry")
    print("=" * 60)

    registry = PlaybookRegistry(side="allied")

    # Priority playbooks exist
    pb = registry.get("front_line", "ATTACK_PREPARED")
    assert pb is not None
    assert "ASSAULT" in pb.system_prompt.upper() or "fire_barrage" in pb.system_prompt
    assert "fire_barrage" in pb.applicable_commands
    assert "close_assault" in pb.applicable_commands
    # Should NOT have truck commands
    assert "truck_load" not in pb.applicable_commands
    print(f"  ATTACK_PREPARED: {len(pb.applicable_commands)} commands, max_orders={pb.max_orders}")

    pb = registry.get("front_line", "FIGHTING_RETREAT")
    assert pb is not None
    assert "break_contact" in pb.applicable_commands
    print(f"  FIGHTING_RETREAT: {len(pb.applicable_commands)} commands")

    pb = registry.get("logistics", "SUPPLY_CRITICAL_FUEL")
    assert pb is not None
    assert "truck_attach" in pb.applicable_commands
    assert "close_assault" not in pb.applicable_commands
    print(f"  SUPPLY_CRITICAL_FUEL: {len(pb.applicable_commands)} commands")

    # Fallback for unknown situation
    pb = registry.get_or_fallback("front_line", "NONEXISTENT")
    assert pb is not None
    assert pb.situation == "DEFENSIVE_HOLD"
    print(f"  Unknown front_line fallback: {pb.situation}")

    pb = registry.get_or_fallback("logistics", "NONEXISTENT")
    assert pb is not None
    assert pb.situation == "SUPPLY_FLOWING"
    print(f"  Unknown logistics fallback: {pb.situation}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 5: State filter functions
# ════════════════════════════════════════

def test_state_filters():
    print("=" * 60)
    print("TEST 5: State Filter Functions")
    print("=" * 60)

    state = _make_state()
    signals = extract_signals(state, Side.ALLIED, "movement_combat")
    registry = PlaybookRegistry(side="allied")

    # Combat filter
    pb = registry.get("front_line", "ATTACK_PREPARED")
    filtered = pb.state_filter(state, Side.ALLIED, signals)
    assert len(filtered) > 0
    # Should be much shorter than full state
    print(f"  Combat filter: {len(filtered)} chars (~{len(filtered)//4} tokens)")

    # Supply filter
    pb = registry.get("logistics", "SUPPLY_CRITICAL_FUEL")
    filtered = pb.state_filter(state, Side.ALLIED, signals)
    assert len(filtered) > 0
    assert "TRUCK" in filtered.upper() or "SUPPLY" in filtered.upper()
    print(f"  Supply filter: {len(filtered)} chars (~{len(filtered)//4} tokens)")

    # Consolidate filter
    pb = registry.get("cinc", "CAMPAIGN_CONSOLIDATE")
    filtered = pb.state_filter(state, Side.ALLIED, signals)
    assert "VP" in filtered.upper()
    print(f"  Consolidate filter: {len(filtered)} chars (~{len(filtered)//4} tokens)")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 6: SituationEngine full pipeline
# ════════════════════════════════════════

def test_situation_engine_pipeline():
    print("=" * 60)
    print("TEST 6: SituationEngine Full Pipeline")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig(use_situation_engine=True)
    mock_llm = SituationMockLLM()

    engine = SituationEngine(
        side=Side.ALLIED,
        llm_client=mock_llm,
        config=config,
    )

    pause = _make_pause_point()
    result = engine.decide(state, pause)

    assert isinstance(result, DecisionResult)
    assert len(result.orders) > 0
    assert result.orders[-1]["command"] == "end_phase"

    # Should have expert_recommendations with situation info
    assert len(result.expert_recommendations) > 0
    assert "situation" in result.expert_recommendations[0]

    print(f"  Orders: {len(result.orders)}")
    for o in result.orders:
        print(f"    {o['command']} {o.get('params', {})}")
    print(f"  Expert recs: {result.expert_recommendations}")
    print(f"  Timing: classify={result.experts_ms}ms exec={result.synthesis_ms}ms")
    print(f"  LLM calls: {len(mock_llm.call_log)}")
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 7: Deterministic pipeline (no LLM)
# ════════════════════════════════════════

def test_deterministic_pipeline():
    print("=" * 60)
    print("TEST 7: Deterministic Pipeline (no LLM for classification)")
    print("=" * 60)

    state = _make_state()
    # Set water to zero to trigger deterministic override
    state.units["u1"].supply.water = 0.0

    config = OrchestratorConfig(use_situation_engine=True)

    # Playbook response for supply
    mock_llm = SituationMockLLM(
        playbook_response={
            "orders": [
                {"command": "truck_attach", "params": {"unit_id": "u1"}},
                {"command": "truck_load", "params": {"unit_id": "u1", "supplies": {"water": 5.0}}},
                {"command": "end_phase", "params": {}},
            ],
            "reasoning": "Emergency water resupply",
        }
    )

    engine = SituationEngine(
        side=Side.ALLIED,
        llm_client=mock_llm,
        config=config,
    )

    pause = _make_pause_point()
    result = engine.decide(state, pause)

    assert isinstance(result, DecisionResult)
    assert len(result.orders) > 0

    # For movement_combat, supply critical is detected first, then front-line
    # So we should see supply orders + front-line orders
    print(f"  Orders: {len(result.orders)}")
    for o in result.orders:
        print(f"    {o['command']} {o.get('params', {})}")

    # Check that situation classification was logged
    recs = result.expert_recommendations
    print(f"  Situations detected: {[r.get('situation') for r in recs]}")

    # With zero water, should detect SUPPLY_CRITICAL_WATER
    has_supply_critical = any(
        r.get("situation") == "SUPPLY_CRITICAL_WATER" for r in recs
    )
    assert has_supply_critical, f"Expected SUPPLY_CRITICAL_WATER, got {recs}"
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 8: Command whitelist enforcement
# ════════════════════════════════════════

def test_whitelist_enforcement():
    print("=" * 60)
    print("TEST 8: Command Whitelist Enforcement")
    print("=" * 60)

    state = _make_state()
    config = OrchestratorConfig(use_situation_engine=True)

    # Mock LLM returns combat + supply commands mixed together
    mock_llm = SituationMockLLM(
        classifier_response={
            "role": "front_line",
            "situation": "ATTACK_PREPARED",
            "confidence": 0.9,
            "reasoning": "test",
        },
        playbook_response={
            "orders": [
                {"command": "fire_barrage", "params": {"target_hex": "D0922", "target_class": "infantry"}},
                {"command": "truck_load", "params": {"unit_id": "u1", "supplies": {"fuel": 5.0}}},
                {"command": "close_assault", "params": {"target_hex": "D0922"}},
                {"command": "plan_convoy", "params": {"port": "Tobruk", "tonnage": 100}},
                {"command": "end_phase", "params": {}},
            ],
            "reasoning": "Mixed orders — whitelist should filter",
        }
    )

    engine = SituationEngine(
        side=Side.ALLIED,
        llm_client=mock_llm,
        config=config,
    )

    # Use a non-movement_combat phase to test single-role path
    pause = _make_pause_point(sub_phase="patrol")
    result = engine.decide(state, pause)

    # The ATTACK_PREPARED playbook should only allow combat commands
    # truck_load and plan_convoy should be dropped
    commands = [o["command"] for o in result.orders]
    assert "truck_load" not in commands, f"truck_load should be filtered out, got {commands}"
    assert "plan_convoy" not in commands, f"plan_convoy should be filtered out, got {commands}"
    print(f"  Orders after whitelist: {commands}")
    print("  truck_load and plan_convoy correctly filtered out")
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 9: Taxonomy completeness
# ════════════════════════════════════════

def test_taxonomy_completeness():
    print("=" * 60)
    print("TEST 9: Taxonomy Completeness")
    print("=" * 60)

    # All situations should have a category
    for sit_name in SITUATION_TAXONOMY:
        assert sit_name in SITUATION_TO_CATEGORY, f"{sit_name} missing from SITUATION_TO_CATEGORY"

    print(f"  Total situations: {len(SITUATION_TAXONOMY)}")
    print(f"  All mapped to categories: {len(SITUATION_TO_CATEGORY)}")

    # Check category counts
    from collections import Counter
    cats = Counter(SITUATION_TO_CATEGORY.values())
    for cat, count in sorted(cats.items()):
        print(f"    {cat}: {count}")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 10: Config toggle
# ════════════════════════════════════════

def test_config_toggle():
    print("=" * 60)
    print("TEST 10: Config Toggle")
    print("=" * 60)

    config_off = OrchestratorConfig()
    assert config_off.use_situation_engine is False
    print("  Default: situation engine OFF")

    config_on = OrchestratorConfig(use_situation_engine=True)
    assert config_on.use_situation_engine is True
    print("  Explicit: situation engine ON")

    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 11: Oasis/bir draw_water in emergency supply
# ════════════════════════════════════════

def test_oasis_draw_water_orders():
    print("=" * 60)
    print("TEST 11: Oasis/Bir draw_water in Emergency Supply")
    print("=" * 60)

    state = _make_state()
    # Place a unit at the oasis with zero water
    state.units["u3"] = Unit(
        id="u3", name="Supply HQ", side=Side.ALLIED, nationality="british",
        unit_class="hq", unit_size="battalion",
        motorization=MotorizationType.MOTORIZED, hex_id="D1122",
        base_cpa=10, stacking_points=1,
        current_strength=TOEStrength(infantry=2),
        toe_strength=TOEStrength(infantry=2),
        supply=UnitSupply(fuel=5.0, water=0.0, ammo=4.0, stores=3.0,
                          fuel_capacity=10.0, water_capacity=16.0,
                          ammo_capacity=8.0, stores_capacity=6.0),
    )
    state.hexes["D1122"].allied_unit_ids = ["u3"]

    config = OrchestratorConfig(use_situation_engine=True)
    mock_llm = SituationMockLLM(
        playbook_response={
            "orders": [{"command": "end_phase", "params": {}}],
            "reasoning": "No combat needed",
        }
    )

    engine = SituationEngine(
        side=Side.ALLIED, llm_client=mock_llm, config=config,
    )

    from cna_engine.orchestrator.situations import extract_signals
    signals = extract_signals(state, Side.ALLIED, "movement_combat")

    orders = engine._compute_emergency_supply_orders(state, signals, "water", max_orders=8)

    # Should emit a draw_water command for u3 at the oasis
    draw_water_orders = [o for o in orders if o["command"] == "draw_water"]
    assert len(draw_water_orders) >= 1, f"Expected draw_water order, got {[o['command'] for o in orders]}"
    assert draw_water_orders[0]["params"]["unit_id"] == "u3"
    print(f"  draw_water orders: {draw_water_orders}")
    print(f"  Total orders: {len(orders)}")
    for o in orders:
        print(f"    {o['command']} {o.get('params', {})}")
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 12: draw_from_supply_pool at port
# ════════════════════════════════════════

def test_supply_pool_draw_at_port():
    print("=" * 60)
    print("TEST 12: draw_from_supply_pool at Port")
    print("=" * 60)

    state = _make_state()
    # Place a unit at port Mersa Matruh with zero water
    state.units["u4"] = Unit(
        id="u4", name="Port Garrison", side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D1822",
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=6),
        toe_strength=TOEStrength(infantry=6),
        supply=UnitSupply(fuel=0.0, water=0.0, ammo=8.0, stores=4.0,
                          fuel_capacity=0.0, water_capacity=12.0,
                          ammo_capacity=16.0, stores_capacity=8.0),
    )
    state.hexes["D1822"].allied_unit_ids = ["u4"]

    config = OrchestratorConfig(use_situation_engine=True)
    mock_llm = SituationMockLLM(
        playbook_response={
            "orders": [{"command": "end_phase", "params": {}}],
            "reasoning": "No combat needed",
        }
    )

    engine = SituationEngine(
        side=Side.ALLIED, llm_client=mock_llm, config=config,
    )

    from cna_engine.orchestrator.situations import extract_signals
    signals = extract_signals(state, Side.ALLIED, "movement_combat")

    orders = engine._compute_emergency_supply_orders(state, signals, "water", max_orders=8)

    # Should emit a draw_from_supply_pool for u4 at the port
    pool_orders = [o for o in orders if o["command"] == "draw_from_supply_pool"]
    assert len(pool_orders) >= 1, f"Expected draw_from_supply_pool order, got {[o['command'] for o in orders]}"
    assert pool_orders[0]["params"]["unit_id"] == "u4"
    assert "water" in pool_orders[0]["params"]["supplies"]
    print(f"  draw_from_supply_pool orders: {pool_orders}")
    print(f"  Total orders: {len(orders)}")
    for o in orders:
        print(f"    {o['command']} {o.get('params', {})}")
    print("  PASSED\n")


# ════════════════════════════════════════
# TEST 13: draw_from_supply_pool engine command
# ════════════════════════════════════════

def test_draw_from_supply_pool_engine():
    print("=" * 60)
    print("TEST 13: draw_from_supply_pool Engine Command")
    print("=" * 60)

    state = _make_state()
    # Place a unit at port
    state.units["u5"] = Unit(
        id="u5", name="Port Unit", side=Side.ALLIED, nationality="british",
        unit_class="infantry", unit_size="battalion",
        motorization=MotorizationType.NON_MOTORIZED, hex_id="D1822",
        base_cpa=8, stacking_points=2,
        current_strength=TOEStrength(infantry=6),
        toe_strength=TOEStrength(infantry=6),
        supply=UnitSupply(fuel=0.0, water=2.0, ammo=8.0, stores=4.0,
                          fuel_capacity=0.0, water_capacity=12.0,
                          ammo_capacity=16.0, stores_capacity=8.0),
    )
    state.hexes["D1822"].allied_unit_ids = ["u5"]

    from cna_engine.engine.supply import draw_from_supply_pool

    # Draw water from supply pool
    pool_before = state.allied_supply_in_egypt["water"]
    result = draw_from_supply_pool(state, "u5", water=10.0)

    assert result.success, f"Expected success, got: {result.blocked_reason}"
    assert result.supplies_transferred.get("water", 0) == 10.0
    assert state.units["u5"].supply.water == 12.0  # 2.0 existing + 10.0 drawn (capped at capacity)
    assert state.allied_supply_in_egypt["water"] == pool_before - 10.0
    assert state.units["u5"].current_cpa_spent == 1.0
    print(f"  Result: {result.description}")
    print(f"  Water after: {state.units['u5'].supply.water}")
    print(f"  Pool after: {state.allied_supply_in_egypt['water']}")

    # Fail: not at port
    state.units["u1"].hex_id = "D0821"  # clear hex, not a port
    result2 = draw_from_supply_pool(state, "u1", water=10.0)
    assert not result2.success, "Should fail for non-port hex"
    assert "port" in result2.blocked_reason.lower()
    print(f"  Non-port correctly rejected: {result2.blocked_reason}")

    print("  PASSED\n")


# ════════════════════════════════════════
# RUN ALL TESTS
# ════════════════════════════════════════

def run_all():
    tests = [
        test_extract_signals,
        test_deterministic_classify,
        test_classifier_prompts,
        test_playbook_registry,
        test_state_filters,
        test_situation_engine_pipeline,
        test_deterministic_pipeline,
        test_whitelist_enforcement,
        test_taxonomy_completeness,
        test_config_toggle,
        test_oasis_draw_water_orders,
        test_supply_pool_draw_at_port,
        test_draw_from_supply_pool_engine,
    ]

    passed = 0
    failed = 0
    errors = []

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            import traceback
            print(f"  FAILED: {e}")
            traceback.print_exc()
            print()

    print("=" * 60)
    print(f"RESULTS: {passed}/{passed + failed} passed")
    if errors:
        print("FAILURES:")
        for name, err in errors:
            print(f"  {name}: {err}")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
