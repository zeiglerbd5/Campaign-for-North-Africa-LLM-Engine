"""
CNA Engine — Integration Test & Demo
Loads reference data, creates a minimal game state, runs through the SoP,
and saves/loads the state to verify serialization.
"""
import sys
import os
import json
import tempfile

# Add project root to path so `cna_engine` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.models.enums import *
from cna_engine.models.game_state import *
from cna_engine.models.serialization import *
from cna_engine.data.reference_data import *
from cna_engine.engine.sequence_of_play import *


def create_test_units() -> dict[str, Unit]:
    """Create a handful of test units for both sides."""
    units = {}

    # ── Commonwealth ──
    units["cw_2rtr"] = Unit(
        id="cw_2rtr", name="2nd Royal Tank Regiment",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        unit_class=UnitClass.ARMOR, unit_size=UnitSize.REGIMENT,
        motorization=MotorizationType.MECHANIZED,
        hex_id="D0821", base_cpa=35,
        parent_formation_id="cw_7arm_bde",
        toe_strength=TOEStrength(armor=12),
        current_strength=TOEStrength(armor=12),
        bar=3, armor_protection=2,
        supply=UnitSupply(fuel=10, water=5, ammo=8, stores=4,
                         fuel_capacity=15, water_capacity=8, ammo_capacity=12, stores_capacity=6),
        arrival_gt=1,
    )

    units["cw_7rtr"] = Unit(
        id="cw_7rtr", name="7th Royal Tank Regiment",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        unit_class=UnitClass.ARMOR, unit_size=UnitSize.REGIMENT,
        motorization=MotorizationType.MECHANIZED,
        hex_id="D0821", base_cpa=35,
        parent_formation_id="cw_7arm_bde",
        toe_strength=TOEStrength(armor=12),
        current_strength=TOEStrength(armor=12),
        bar=2, armor_protection=1,
        supply=UnitSupply(fuel=10, water=5, ammo=8, stores=4),
        arrival_gt=1,
    )

    units["cw_1rha"] = Unit(
        id="cw_1rha", name="1st Royal Horse Artillery",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        unit_class=UnitClass.GUN, unit_size=UnitSize.REGIMENT,
        motorization=MotorizationType.MOTORIZED,
        hex_id="D0921", base_cpa=25,
        parent_formation_id="cw_7arm_div",
        toe_strength=TOEStrength(gun=6),
        current_strength=TOEStrength(gun=6),
        supply=UnitSupply(fuel=5, water=3, ammo=12, stores=3),
        arrival_gt=1,
    )

    # ── Axis ──
    units["ax_ariete_tanks"] = Unit(
        id="ax_ariete_tanks", name="Ariete Division Tank Regiment",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        unit_class=UnitClass.ARMOR, unit_size=UnitSize.REGIMENT,
        motorization=MotorizationType.MECHANIZED,
        hex_id="A1437", base_cpa=25,
        parent_formation_id="ax_ariete_div",
        toe_strength=TOEStrength(armor=10),
        current_strength=TOEStrength(armor=10),
        bar=1, armor_protection=1,
        supply=UnitSupply(fuel=8, water=4, ammo=6, stores=3),
        arrival_gt=20,
    )

    units["ax_5pz_regt"] = Unit(
        id="ax_5pz_regt", name="5th Panzer Regiment",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        unit_class=UnitClass.ARMOR, unit_size=UnitSize.REGIMENT,
        motorization=MotorizationType.MECHANIZED,
        hex_id="A2438", base_cpa=40,
        parent_formation_id="ax_5light_div",
        toe_strength=TOEStrength(armor=15),
        current_strength=TOEStrength(armor=15),
        bar=3, armor_protection=3,
        supply=UnitSupply(fuel=12, water=5, ammo=10, stores=5),
        arrival_gt=20,
    )

    return units


def create_test_formations() -> dict[str, Formation]:
    """Create test formations."""
    formations = {}

    formations["cw_7arm_bde"] = Formation(
        id="cw_7arm_bde", name="7th Armoured Brigade",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.BRIGADE,
        parent_formation_id="cw_7arm_div",
        unit_ids=["cw_2rtr", "cw_7rtr"],
    )

    formations["cw_7arm_div"] = Formation(
        id="cw_7arm_div", name="7th Armoured Division",
        side=Side.ALLIED, nationality=Nationality.BRITISH,
        formation_size=UnitSize.DIVISION,
        sub_formation_ids=["cw_7arm_bde"],
        unit_ids=["cw_1rha"],
    )

    formations["ax_ariete_div"] = Formation(
        id="ax_ariete_div", name="Ariete Armoured Division",
        side=Side.AXIS, nationality=Nationality.ITALIAN,
        formation_size=UnitSize.DIVISION,
        unit_ids=["ax_ariete_tanks"],
    )

    formations["ax_5light_div"] = Formation(
        id="ax_5light_div", name="5th Light Division",
        side=Side.AXIS, nationality=Nationality.GERMAN,
        formation_size=UnitSize.DIVISION,
        unit_ids=["ax_5pz_regt"],
    )

    return formations


def main():
    print("=" * 70)
    print("CNA ENGINE — Integration Test")
    print("=" * 70)

    # ── 1. Load Reference Data ──
    print("\n[1] Loading reference data from CNA_DATA_TABLES.xlsx...")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    xlsx_path = os.path.join(project_root, "CNA_DATA_TABLES.xlsx")
    if os.path.exists(xlsx_path):
        ref = load_reference_data_from_xlsx(xlsx_path)
        print(f"    Terrain types: {len(ref.terrain)}")
        print(f"    CPA costs: {len(ref.cpa_costs)}")
        print(f"    Ports: {len(ref.ports)}")
        print(f"    Aircraft: {len(ref.aircraft)}")
        print(f"    Initiative periods: {len(ref.initiative_ratings)}")
        print(f"    Key dates: {len(ref.key_dates)}")
        print(f"    Fuel chart entries: {len(ref.fuel_consumption_chart)}")

        # Test some lookups
        clear_mot = ref.get_terrain_movement_cost("clear", is_motorized=True)
        clear_inf = ref.get_terrain_movement_cost("clear", is_motorized=False)
        print(f"\n    Movement cost (Clear): Motorized={clear_mot} CP, Infantry={clear_inf} CP")

        rough_track = ref.get_terrain_movement_cost("rough", is_motorized=True, has_track=True)
        print(f"    Movement cost (Rough + Track): Motorized={rough_track} CP")

        a_init, x_init = ref.get_initiative_rating(1)
        print(f"    GT1 Initiative: Allied={a_init}, Axis={x_init}")

        a_evap, x_evap = ref.get_evaporation_rates(1)
        print(f"    GT1 Evaporation: Allied={a_evap:.0%}, Axis={x_evap:.0%}")

        fuel = ref.lookup_fuel_consumption(cps_expended=5, fuel_rate=2)
        print(f"    Fuel consumed (5 CPs, rate 2): {fuel} points")

        # Save reference data as JSON cache
        ref_json_path = os.path.join(project_root, "cna_engine", "data", "reference_data.json")
        save_reference_data_json(ref, ref_json_path)
        print(f"\n    Reference data cached to {ref_json_path}")
    else:
        print(f"    WARNING: {xlsx_path} not found, skipping reference data load")

    # ── 2. Create Game State ──
    print("\n[2] Creating initial game state...")
    state = GameState()
    state.units = create_test_units()
    state.formations = create_test_formations()

    # Set initial turn state
    state.turn.game_turn = 1
    state.turn.op_stage = 1
    state.turn.current_season = Season.AUTUMN
    state.turn.allied_initiative_rating = 3
    state.turn.axis_initiative_rating = 5

    print(f"    Units: {len(state.units)}")
    print(f"    Formations: {len(state.formations)}")
    print(f"    Turn: GT{state.turn.game_turn} ({state.turn.date_string})")

    # ── 3. Test Serialization ──
    print("\n[3] Testing serialization...")
    tmpdir = tempfile.mkdtemp(prefix="cna_test_")
    save_path = os.path.join(tmpdir, "test_state.json")
    save_state(state, save_path)
    print(f"    Saved to {save_path}")

    loaded = load_state(save_path)
    print(f"    Loaded back: {len(loaded.units)} units, GT{loaded.turn.game_turn}")
    assert len(loaded.units) == len(state.units), "Unit count mismatch!"
    assert loaded.turn.game_turn == state.turn.game_turn, "Turn mismatch!"
    print("    ✓ Serialization round-trip OK")

    # ── 4. Test State Summary ──
    print("\n[4] State summary (for LLM context):")
    summary = state_summary(state)
    print(json.dumps(summary, indent=2))

    # ── 5. Test Sequence of Play ──
    print("\n[5] Testing Sequence of Play state machine...")
    sop = SequenceOfPlay(state)
    print(f"    Start: {sop.get_current_phase_description()}")

    # Advance through the first several phases
    for i in range(20):
        # Set initiative for OpStage phases
        if state.turn.sub_phase == OpStagePhase.INITIATIVE:
            state.turn.initiative_side = Side.AXIS  # Axis has initiative GT1

        result = sop.advance()
        print(f"    [{i+1:2d}] → {result['current']}")

        if state.turn.phase == GamePhase.END_OF_TURN:
            print(f"\n    Reached End of Turn for GT{state.turn.game_turn}")
            break

    # Continue to next turn
    if state.turn.phase == GamePhase.END_OF_TURN:
        result = sop.advance()
        print(f"    → New turn: {result['current']}")

    # ── 6. Test Unit Queries ──
    print("\n[6] Unit queries:")
    hex_units = state.get_units_in_hex("D0821")
    print(f"    Units in D0821: {[u.name for u in hex_units]}")

    form_units = state.get_units_in_formation("cw_7arm_bde")
    print(f"    7th Armoured Bde: {[u.name for u in form_units]}")

    # ── 7. Save final state ──
    final_path = os.path.join(tmpdir, "test_state_final.json")
    save_state(state, final_path)
    print(f"\n[7] Final state saved to {final_path}")

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED ✓")
    print("=" * 70)

    # Print the state JSON size
    state_dict = state_to_dict(state)
    json_str = json.dumps(state_dict)
    print(f"\nState JSON size: {len(json_str):,} bytes ({len(json_str)/1024:.1f} KB)")
    print(f"Event log entries: {len(state.event_log)}")


if __name__ == "__main__":
    main()
