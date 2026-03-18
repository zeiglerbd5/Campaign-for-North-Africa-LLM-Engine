"""Tests for CNA action notation module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cna_engine.tools.notation import (
    abbrev_aircraft,
    abbrev_unit,
    format_game_header,
    format_turn_transcript,
    notate_event,
)


# ════════════════════════════════════════════════════════════════════
# ABBREVIATION TESTS
# ════════════════════════════════════════════════════════════════════

class TestAbbreviations:
    def test_known_unit(self):
        assert abbrev_unit("cw_2rtr") == "2RTR"
        assert abbrev_unit("it_1lib_hq") == "1LibHQ"
        assert abbrev_unit("dak_recon_bn") == "DAKRec"

    def test_known_aircraft(self):
        assert abbrev_aircraft("ra_sm79_1sqn") == "SM79/1"
        assert abbrev_aircraft("raf_274sqn_hurr") == "274Hurr"
        assert abbrev_aircraft("lw_ju87b_1sqn") == "Ju87/1"

    def test_fallback_abbreviation(self):
        """Unknown IDs should get a reasonable fallback."""
        result = abbrev_unit("cw_unknown_regiment")
        assert len(result) <= 10
        assert result  # not empty

    def test_aircraft_falls_back_to_unit(self):
        result = abbrev_aircraft("unknown_plane_123")
        assert result  # should not crash


# ════════════════════════════════════════════════════════════════════
# PER-EVENT NOTATION TESTS
# ════════════════════════════════════════════════════════════════════

class TestMovementNotation:
    def test_basic_movement(self):
        event = {
            "type": "movement",
            "unit_id": "cw_2rtr",
            "from_hex": "D0821",
            "to_hex": "D0921",
            "description": "2nd Royal Tank Regiment moves D0821 → D0921 (2.0 CP)",
        }
        side, notation = notate_event(event)
        assert side == "allied"
        assert "2RTR" in notation
        assert "D0821->D0921" in notation
        assert "(2CP)" in notation

    def test_ezoc_movement(self):
        event = {
            "type": "movement",
            "unit_id": "cw_2rtr",
            "from_hex": "A1437",
            "to_hex": "D1022",
            "description": "2RTR moves A1437 → D1022 (8 CP) [enters EZOC — IN CONTACT]",
        }
        side, notation = notate_event(event)
        assert "[EZOC]" in notation

    def test_contact_broken(self):
        event = {
            "type": "movement",
            "unit_id": "it_1lib_1bn",
            "from_hex": "D0922",
            "to_hex": "D1021",
            "description": "1st Libyan Bn moves D0922 → D1021 (3 CP) [broke contact]",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "[~C]" in notation


class TestCloseAssaultNotation:
    def test_basic_assault(self):
        event = {
            "type": "close_assault",
            "target_hex": "D0922",
            "atk_ids": ["cw_2rtr", "cw_7hus"],
            "def_ids": ["it_1lib_hq"],
            "differential": 5,
            "dice_roll": 43,
            "atk_loss_pct": 10,
            "def_loss_pct": 25,
            "retreat_hexes": 1,
            "is_overrun": False,
        }
        side, notation = notate_event(event)
        assert side == "allied"
        assert "2RTR,7Hus" in notation
        assert "1LibHQ" in notation
        assert "@D0922" in notation
        assert "[+5]" in notation
        assert "d43" in notation
        assert "A-10%" in notation
        assert "D-25%" in notation
        assert "RET1" in notation

    def test_overrun(self):
        event = {
            "type": "close_assault",
            "target_hex": "D0922",
            "atk_ids": ["cw_2rtr"],
            "def_ids": ["it_1lib_hq"],
            "differential": 8,
            "dice_roll": 11,
            "atk_loss_pct": 0,
            "def_loss_pct": 50,
            "retreat_hexes": 0,
            "is_overrun": True,
        }
        _, notation = notate_event(event)
        assert "OVERRUN" in notation

    def test_negative_differential(self):
        event = {
            "type": "close_assault",
            "target_hex": "D0922",
            "atk_ids": ["it_1lib_1bn"],
            "def_ids": ["cw_2rtr"],
            "differential": -3,
            "dice_roll": 55,
            "atk_loss_pct": 25,
            "def_loss_pct": 5,
            "retreat_hexes": 0,
            "is_overrun": False,
        }
        _, notation = notate_event(event)
        assert "[-3]" in notation


class TestBarrageNotation:
    def test_barrage_pin(self):
        event = {
            "type": "barrage_result",
            "target_hex": "D1021",
            "bp": 8,
            "dice_roll": 34,
            "sp_lost": 0,
            "is_pinned": True,
        }
        side, notation = notate_event(event)
        assert side is None  # can't infer side
        assert "~D1021" in notation
        assert "8BP" in notation
        assert "PIN" in notation

    def test_barrage_with_sp_loss(self):
        event = {
            "type": "barrage_result",
            "target_hex": "D1021",
            "bp": 12,
            "dice_roll": 22,
            "sp_lost": 3,
            "is_pinned": True,
        }
        _, notation = notate_event(event)
        assert "-3SP" in notation
        assert "PIN" in notation

    def test_barrage_no_effect(self):
        event = {
            "type": "barrage_result",
            "target_hex": "D1021",
            "bp": 4,
            "dice_roll": 66,
            "sp_lost": 0,
            "is_pinned": False,
        }
        _, notation = notate_event(event)
        assert "NE" in notation


class TestAntiArmorNotation:
    def test_basic_anti_armor(self):
        event = {
            "type": "anti_armor_result",
            "target_hex": "D1021",
            "aa_points": 6,
            "dice_roll": 33,
            "ap_destroyed": 8,
            "overflow_sp": 0,
        }
        side, notation = notate_event(event)
        assert side is None
        assert ">>D1021" in notation
        assert "6AA" in notation
        assert "-8AP" in notation

    def test_anti_armor_overflow(self):
        event = {
            "type": "anti_armor_result",
            "target_hex": "D1021",
            "aa_points": 10,
            "dice_roll": 11,
            "ap_destroyed": 6,
            "overflow_sp": 2,
        }
        _, notation = notate_event(event)
        assert "-6AP" in notation
        assert "-2SP" in notation


class TestBombardmentNotation:
    def test_bombardment_pinned(self):
        event = {
            "type": "bombardment",
            "aircraft_id": "ra_sm79_1sqn",
            "target_hex": "D1122",
            "description": "Bombardment by ra_sm79_1sqn on D1122: 6 bombs = 12 BP → PINNED",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "SM79/1" in notation
        assert "*D1122" in notation
        assert "12BP" in notation
        assert "PIN" in notation

    def test_bombardment_no_effect(self):
        event = {
            "type": "bombardment",
            "aircraft_id": "raf_45sqn_blen",
            "target_hex": "D0922",
            "description": "Bombardment by raf_45sqn_blen on D0922: 4 bombs = 8 BP → NO EFFECT",
        }
        side, notation = notate_event(event)
        assert side == "allied"
        assert "45Blen" in notation
        assert "NE" in notation


class TestReconNotation:
    def test_recon(self):
        event = {
            "type": "recon",
            "aircraft_id": "ra_cr42_1sqn",
            "target_hex": "D1822",
            "units_spotted": ["cw_7arm_hq", "cw_1rha", "cw_1_6raj"],
            "description": "Recon by ra_cr42_1sqn over D1822: 37 hexes, 3 enemy units",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "CR42/1" in notation
        assert "?D1822" in notation
        assert "3units" in notation


class TestMiscNotation:
    def test_cohesion_change(self):
        event = {
            "type": "cohesion_change",
            "unit_id": "it_maletti_inf",
            "old": 0, "new": -1,
            "description": "Maletti Inf cohesion 0 → -1",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "MalettiI" in notation
        assert "C0->-1" in notation

    def test_disorganization(self):
        event = {
            "type": "disorganization",
            "unit_id": "it_1lib_2bn",
            "description": "2nd Libyan Infantry Bn disorg 0 → 1 (assault loss)",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "1Lib/2" in notation
        assert "D0->1" in notation
        assert "(assault loss)" in notation

    def test_disorg_recovery(self):
        event = {
            "type": "disorg_recovery",
            "unit_id": "it_1lib_hq",
            "description": "1st Libyan Div HQ disorg 2 → 1",
        }
        side, notation = notate_event(event)
        assert side == "axis"
        assert "1LibHQ" in notation
        assert "D+2->1" in notation

    def test_initiative(self):
        event = {
            "type": "initiative",
            "allied_roll": 6, "axis_roll": 3,
            "winner": "axis",
            "description": "Initiative...",
        }
        side, notation = notate_event(event)
        assert side is None
        assert "I: A6 X3->AXIS" in notation

    def test_weather(self):
        event = {
            "type": "weather",
            "season": "autumn", "roll": 2, "weather": "clear",
            "description": "Weather...",
        }
        side, notation = notate_event(event)
        assert side is None
        assert "W: Autumn r2->CLEAR" in notation

    def test_stores_expenditure(self):
        event = {
            "type": "stores_expenditure",
            "description": "GT1 Stores Expenditure: 24 units. Water consumed=24.0, Stores consumed=12.5.",
        }
        side, notation = notate_event(event)
        assert side is None
        assert "$: W=24.0 S=12.5" in notation

    def test_draw_from_dump(self):
        event = {
            "type": "draw_from_dump",
            "unit_id": "cw_4ind_hq",
            "dump_id": "allied_matruh_depot",
            "description": "4th Indian draws from dump...",
        }
        side, notation = notate_event(event)
        assert side == "allied"
        assert "4IndHQ" in notation
        assert "<Matruh Depot" in notation


class TestSkippedEvents:
    def test_scenario_load_skipped(self):
        event = {"type": "scenario_load", "description": "Loaded scenario"}
        side, notation = notate_event(event)
        assert side is None
        assert notation is None

    def test_phase_advance_skipped(self):
        event = {"type": "phase_advance", "description": "Phase advanced"}
        side, notation = notate_event(event)
        assert side is None
        assert notation is None

    def test_unknown_event_skipped(self):
        event = {"type": "totally_unknown_type_xyz", "description": "?"}
        side, notation = notate_event(event)
        assert side is None
        assert notation is None


# ════════════════════════════════════════════════════════════════════
# TRANSCRIPT FORMATTING TESTS
# ════════════════════════════════════════════════════════════════════

class TestFormatTurnTranscript:
    def test_basic_transcript(self):
        events = [
            {
                "gt": 1, "op_stage": 1, "phase": "op_stage",
                "type": "weather",
                "season": "autumn", "roll": 2, "weather": "clear",
                "description": "Weather...",
            },
            {
                "gt": 1, "op_stage": 1, "phase": "op_stage",
                "type": "initiative",
                "allied_roll": 6, "axis_roll": 3, "winner": "axis",
                "description": "Initiative...",
            },
            {
                "gt": 1, "op_stage": 1, "phase": "op_stage",
                "type": "movement",
                "unit_id": "it_1lib_hq",
                "from_hex": "D0922", "to_hex": "D0921",
                "description": "1st Libyan Div HQ moves D0922 → D0921 (2 CP)",
            },
            {
                "gt": 1, "op_stage": 1, "phase": "op_stage",
                "type": "movement",
                "unit_id": "cw_2rtr",
                "from_hex": "D0821", "to_hex": "D0922",
                "description": "2RTR moves D0821 → D0922 (2 CP)",
            },
        ]
        turn_info = {"gt": 1, "date": "Sep 1940 Wk1"}
        text = format_turn_transcript(events, turn_info)

        assert "=== GT1 (Sep 1940 Wk1) ===" in text
        assert "--- OS1 ---" in text
        assert "W: Autumn r2->CLEAR" in text
        assert "I: A6 X3->AXIS" in text
        assert "AXIS:" in text
        assert "ALLIED:" in text
        assert "1LibHQ" in text
        assert "2RTR" in text

    def test_empty_events(self):
        text = format_turn_transcript([], {"gt": 5, "date": "Oct 1940 Wk1"})
        assert "=== GT5" in text

    def test_multi_stage(self):
        events = [
            {
                "gt": 1, "op_stage": 1, "phase": "op_stage",
                "type": "weather", "season": "autumn", "roll": 2,
                "weather": "clear", "description": "w",
            },
            {
                "gt": 1, "op_stage": 2, "phase": "op_stage",
                "type": "movement", "unit_id": "cw_2rtr",
                "from_hex": "D0821", "to_hex": "D0921",
                "description": "2RTR moves D0821 → D0921 (3 CP)",
            },
        ]
        text = format_turn_transcript(events, {"gt": 1, "date": "Sep 1940 Wk1"})
        assert "--- OS1 ---" in text
        assert "--- OS2 ---" in text


class TestFormatGameHeader:
    def test_default_header(self):
        header = format_game_header(turns="GT1-GT11")
        assert '[Scenario "Operation Compass"]' in header
        assert '[Turns "GT1-GT11"]' in header
        assert '[Allied "AI"]' in header
        assert '[Axis "AI"]' in header


# ════════════════════════════════════════════════════════════════════
# INTEGRATION: REAL SAVE FILE (if available)
# ════════════════════════════════════════════════════════════════════

class TestWithRealSave:
    """Integration tests that run against actual save files (skipped if not found)."""

    SAVE_PATH = Path(__file__).parent.parent.parent / "saves" / "gt11.json"

    @pytest.fixture
    def save_data(self):
        if not self.SAVE_PATH.exists():
            pytest.skip("No save file at saves/gt11.json")
        with open(self.SAVE_PATH) as f:
            return json.load(f)

    def test_all_events_notate_without_error(self, save_data):
        """Every event in the save file should either notate or be skipped, never crash."""
        events = save_data.get("event_log", [])
        for e in events:
            side, notation = notate_event(e)
            # Should return (None, None) for skipped, or (side|None, str) for notated
            if notation is not None:
                assert isinstance(notation, str)
                assert len(notation) > 0

    def test_transcript_renders(self, save_data):
        """Full turn transcript should render without error."""
        from cna_engine.tools.export_logsheets import extract_events, extract_turn_info
        turn_info = extract_turn_info(save_data)
        events = extract_events(save_data, turn_info["gt"])
        text = format_turn_transcript(events, turn_info)
        assert "===" in text
        assert len(text) > 50

