"""
Tests for the VASSAL .vlog replay exporter.
"""
from __future__ import annotations


import pytest

from cna_engine.tools.export_vlog import (
    ACTION_TYPES,
    PieceState,
    VlogStep,
    assemble_vlog,
    build_chat_command,
    build_initial_state,
    build_move_command,
    build_remove_command,
    build_turn_steps,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def piece_mapping():
    return {
        "cw_2rtr": "2RTR_counter",
        "cw_7hus": "7Hus_counter",
        "it_1lib_hq": "1LibHQ_counter",
    }


@pytest.fixture
def piece_templates():
    return {
        "2RTR_counter": "+/1000/piece;;;2RTR_counter.png;\tPieces\\",
        "7Hus_counter": "+/1001/piece;;;7Hus_counter.png;\tPieces\\",
        "1LibHQ_counter": "+/1002/piece;;;1LibHQ_counter.png;\tPieces\\",
    }


@pytest.fixture
def non_piece_cmds():
    return [
        "BoardPicker;Main Map;Map0",
        "TurnTracker;1",
    ]


@pytest.fixture
def sample_save():
    """A minimal save-file dict with 3 units."""
    return {
        "turn": {"game_turn": 32, "op_stage": 1, "phase": "op_stage"},
        "_metadata": {"date": "Jan 1941 Wk4"},
        "units": {
            "cw_2rtr": {
                "id": "cw_2rtr",
                "name": "2nd Royal Tank Regiment",
                "hex_id": "B2912",
                "status": "active",
                "side": "allied",
            },
            "cw_7hus": {
                "id": "cw_7hus",
                "name": "7th Hussars",
                "hex_id": "B2810",
                "status": "active",
                "side": "allied",
            },
            "it_1lib_hq": {
                "id": "it_1lib_hq",
                "name": "1st Libyan Div HQ",
                "hex_id": "B3610",
                "status": "active",
                "side": "axis",
            },
        },
        "event_log": [],
        "formations": {},
        "hexes": {},
    }


@pytest.fixture
def sample_events_gt32():
    """Events for GT32 with a mix of logistics and actions."""
    return [
        {
            "gt": 32, "op_stage": 1, "type": "weather",
            "season": "winter", "roll": 3, "weather": "clear",
        },
        {
            "gt": 32, "op_stage": 1, "type": "initiative",
            "allied_roll": 4, "axis_roll": 2, "winner": "allied",
        },
        {
            "gt": 32, "op_stage": 1, "type": "movement",
            "unit_id": "cw_2rtr", "from_hex": "B2912", "to_hex": "B3010",
            "description": "2nd Royal Tank Regiment moves B2912 → B3010 (2.0 CP)",
        },
        {
            "gt": 32, "op_stage": 1, "type": "bombardment",
            "aircraft_id": "ra_sm79_1sqn", "target_hex": "B2810",
            "description": "Bombardment by SM.79 on B2810: 3 bombs = 2 BP result PINNED",
        },
    ]


# ---------------------------------------------------------------------------
# Command builder tests
# ---------------------------------------------------------------------------

class TestBuildMoveCommand:
    def test_valid_hexes(self):
        cmd = build_move_command(10000, "B2912", "B3010")
        assert cmd is not None
        assert cmd.startswith("M/10000/Main Map/")
        assert "/null/" in cmd
        assert cmd.endswith("/observer")

    def test_parses_coordinates(self):
        cmd = build_move_command(10000, "B2912", "B3010")
        parts = cmd.split("/")
        assert parts[0] == "M"
        assert parts[1] == "10000"
        assert parts[2] == "Main Map"
        # new coords
        new_x, new_y = int(parts[3]), int(parts[4])
        assert new_x > 0 and new_y > 0
        # old coords
        old_x, old_y = int(parts[7]), int(parts[8])
        assert old_x > 0 and old_y > 0
        # They should differ (different hexes)
        assert (new_x, new_y) != (old_x, old_y)

    def test_invalid_hex_returns_none(self):
        assert build_move_command(10000, "Z9999", "B3010") is None
        assert build_move_command(10000, "B2912", "INVALID") is None


class TestBuildRemoveCommand:
    def test_format(self):
        assert build_remove_command(12345) == "-/12345"


class TestBuildChatCommand:
    def test_format(self):
        assert build_chat_command("hello world") == "CHAT* hello world"

    def test_special_chars(self):
        cmd = build_chat_command("A-10% D-25% RET1")
        assert cmd == "CHAT* A-10% D-25% RET1"


# ---------------------------------------------------------------------------
# Initial state tests
# ---------------------------------------------------------------------------

class TestBuildInitialState:
    def test_basic_construction(self, sample_save, piece_templates, piece_mapping, non_piece_cmds):
        state, registry, next_id = build_initial_state(
            sample_save, piece_templates, piece_mapping, non_piece_cmds,
        )

        assert state.startswith("begin_save")
        assert state.endswith("end_save")
        assert "BoardPicker" in state
        assert "TurnTracker" in state

        # All 3 units should be in registry
        assert len(registry) == 3
        assert "cw_2rtr" in registry
        assert "it_1lib_hq" in registry

        # All placed (they all have valid hex_ids and mappings)
        placed = [ps for ps in registry.values() if ps.placed]
        assert len(placed) == 3

        # gpIds assigned sequentially
        gp_ids = sorted(ps.gp_id for ps in placed)
        assert gp_ids == [10000, 10001, 10002]
        assert next_id == 10003

    def test_unmapped_unit_tracked_as_unplaced(self, sample_save, piece_templates, non_piece_cmds):
        # Mapping that's missing cw_7hus
        partial_mapping = {"cw_2rtr": "2RTR_counter", "it_1lib_hq": "1LibHQ_counter"}

        state, registry, _ = build_initial_state(
            sample_save, piece_templates, partial_mapping, non_piece_cmds,
        )

        assert registry["cw_7hus"].placed is False
        assert registry["cw_7hus"].gp_id == -1
        assert registry["cw_7hus"].hex_id == "B2810"

    def test_destroyed_units_skipped(self, sample_save, piece_templates, piece_mapping, non_piece_cmds):
        sample_save["units"]["cw_2rtr"]["status"] = "destroyed"
        state, registry, _ = build_initial_state(
            sample_save, piece_templates, piece_mapping, non_piece_cmds,
        )
        assert "cw_2rtr" not in registry

    def test_off_map_units_skipped(self, sample_save, piece_templates, piece_mapping, non_piece_cmds):
        sample_save["units"]["cw_2rtr"]["hex_id"] = None
        state, registry, _ = build_initial_state(
            sample_save, piece_templates, piece_mapping, non_piece_cmds,
        )
        assert "cw_2rtr" not in registry


# ---------------------------------------------------------------------------
# Turn step tests
# ---------------------------------------------------------------------------

class TestBuildTurnSteps:
    def _make_registry(self):
        return {
            "cw_2rtr": PieceState(
                unit_id="cw_2rtr", gp_id=10000, type_str="dummy",
                hex_id="B2912", status="active", side="allied", placed=True,
            ),
            "cw_7hus": PieceState(
                unit_id="cw_7hus", gp_id=10001, type_str="dummy",
                hex_id="B2810", status="active", side="allied", placed=True,
            ),
            "it_1lib_hq": PieceState(
                unit_id="it_1lib_hq", gp_id=10002, type_str="dummy",
                hex_id="B3610", status="active", side="axis", placed=True,
            ),
        }

    def test_turn_header_is_first_step(self, sample_events_gt32, piece_templates, piece_mapping):
        registry = self._make_registry()
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
            "cw_7hus": {"hex_id": "B2810", "status": "active", "side": "allied"},
            "it_1lib_hq": {"hex_id": "B3610", "status": "active", "side": "axis"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=sample_events_gt32,
            turn_info={"gt": 32, "date": "Jan 1941 Wk4"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        assert len(steps) >= 1
        header = steps[0]
        assert any("GT32" in c for c in header.commands)

    def test_logistics_bundled(self, sample_events_gt32, piece_templates, piece_mapping):
        registry = self._make_registry()
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
            "cw_7hus": {"hex_id": "B2810", "status": "active", "side": "allied"},
            "it_1lib_hq": {"hex_id": "B3610", "status": "active", "side": "axis"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=sample_events_gt32,
            turn_info={"gt": 32, "date": "Jan 1941 Wk4"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        # Step 0 = header, Step 1 = logistics bundle (weather + initiative)
        logistics_step = steps[1]
        assert logistics_step.label.endswith("logistics")
        # Should have 2 CHAT commands (weather + initiative)
        chat_cmds = [c for c in logistics_step.commands if c.startswith("CHAT*")]
        assert len(chat_cmds) == 2

    def test_movement_creates_move_command(self, sample_events_gt32, piece_templates, piece_mapping):
        registry = self._make_registry()
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
            "cw_7hus": {"hex_id": "B2810", "status": "active", "side": "allied"},
            "it_1lib_hq": {"hex_id": "B3610", "status": "active", "side": "axis"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=sample_events_gt32,
            turn_info={"gt": 32, "date": "Jan 1941 Wk4"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        # Find the movement step
        move_steps = [s for s in steps if "movement" in s.label]
        assert len(move_steps) == 1
        move_step = move_steps[0]
        # Should have a MovePiece + CHAT
        move_cmds = [c for c in move_step.commands if c.startswith("M/")]
        chat_cmds = [c for c in move_step.commands if c.startswith("CHAT*")]
        assert len(move_cmds) == 1
        assert len(chat_cmds) == 1
        assert "10000" in move_cmds[0]  # cw_2rtr's gpId

    def test_movement_updates_registry(self, sample_events_gt32, piece_templates, piece_mapping):
        registry = self._make_registry()
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
            "cw_7hus": {"hex_id": "B2810", "status": "active", "side": "allied"},
            "it_1lib_hq": {"hex_id": "B3610", "status": "active", "side": "axis"},
        }
        build_turn_steps(
            gt=32, events=sample_events_gt32,
            turn_info={"gt": 32, "date": "Jan 1941 Wk4"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        assert registry["cw_2rtr"].hex_id == "B3010"

    def test_bombardment_is_chat_only(self, sample_events_gt32, piece_templates, piece_mapping):
        registry = self._make_registry()
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
            "cw_7hus": {"hex_id": "B2810", "status": "active", "side": "allied"},
            "it_1lib_hq": {"hex_id": "B3610", "status": "active", "side": "axis"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=sample_events_gt32,
            turn_info={"gt": 32, "date": "Jan 1941 Wk4"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        bomb_steps = [s for s in steps if "bombardment" in s.label]
        assert len(bomb_steps) == 1
        # No MovePiece commands
        move_cmds = [c for c in bomb_steps[0].commands if c.startswith("M/")]
        assert len(move_cmds) == 0

    def test_reconciliation_detects_position_mismatch(self, piece_templates, piece_mapping):
        """Unit moved by retreat (not logged as movement event) gets corrected."""
        registry = {
            "cw_2rtr": PieceState(
                unit_id="cw_2rtr", gp_id=10000, type_str="dummy",
                hex_id="B3010", status="active", side="allied", placed=True,
            ),
        }
        curr_units = {
            "cw_2rtr": {"hex_id": "B2912", "status": "active", "side": "allied"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=[], turn_info={"gt": 32, "date": "test"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10001,
        )
        recon_steps = [s for s in steps if "reconciliation" in s.label]
        assert len(recon_steps) == 1
        move_cmds = [c for c in recon_steps[0].commands if c.startswith("M/")]
        assert len(move_cmds) == 1

    def test_reconciliation_detects_destruction(self, piece_templates, piece_mapping):
        registry = {
            "it_1lib_hq": PieceState(
                unit_id="it_1lib_hq", gp_id=10002, type_str="dummy",
                hex_id="B3610", status="active", side="axis", placed=True,
            ),
        }
        curr_units = {
            "it_1lib_hq": {"hex_id": "B3610", "status": "destroyed", "side": "axis"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=[], turn_info={"gt": 32, "date": "test"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10003,
        )
        recon_steps = [s for s in steps if "reconciliation" in s.label]
        assert len(recon_steps) == 1
        remove_cmds = [c for c in recon_steps[0].commands if c.startswith("-/")]
        assert len(remove_cmds) == 1
        assert "-/10002" in remove_cmds[0]

    def test_no_reconciliation_when_positions_match(self, piece_templates, piece_mapping):
        registry = {
            "cw_2rtr": PieceState(
                unit_id="cw_2rtr", gp_id=10000, type_str="dummy",
                hex_id="B3010", status="active", side="allied", placed=True,
            ),
        }
        curr_units = {
            "cw_2rtr": {"hex_id": "B3010", "status": "active", "side": "allied"},
        }
        steps, _ = build_turn_steps(
            gt=32, events=[], turn_info={"gt": 32, "date": "test"},
            prev_units={}, curr_units=curr_units,
            piece_registry=registry, piece_templates=piece_templates,
            piece_mapping=piece_mapping, next_gp_id=10001,
        )
        recon_steps = [s for s in steps if "reconciliation" in s.label]
        assert len(recon_steps) == 0


# ---------------------------------------------------------------------------
# Assembly tests
# ---------------------------------------------------------------------------

class TestAssembleVlog:
    def test_basic_assembly(self):
        initial = "begin_save\x1b\x1bend_save"
        steps = [
            VlogStep(commands=["CHAT* hello"], label="test"),
            VlogStep(commands=["M/10000/Main Map/100/200/null/Main Map/50/100/null/observer",
                              "CHAT* moved"], label="move"),
        ]
        result = assemble_vlog(initial, steps)
        assert result.startswith("begin_save")
        assert "LOG\thello" not in result  # No — it's LOG\tCHAT* hello
        assert "LOG\tCHAT* hello" in result
        # Second step has two commands joined by ESC
        assert "LOG\tM/10000/" in result

    def test_empty_steps_skipped(self):
        initial = "begin_save\x1bend_save"
        steps = [
            VlogStep(commands=[], label="empty"),
            VlogStep(commands=["CHAT* real"], label="real"),
        ]
        result = assemble_vlog(initial, steps)
        # Only one LOG entry
        assert result.count("LOG\t") == 1

    def test_log_format(self):
        initial = "STATE"
        steps = [
            VlogStep(commands=["CMD1", "CMD2"], label="multi"),
        ]
        result = assemble_vlog(initial, steps)
        # Structure: STATE ESC LOG\tCMD1 ESC CMD2
        parts = result.split("\x1b")
        assert parts[0] == "STATE"
        assert parts[1].startswith("LOG\t")
        log_content = parts[1][4:]  # strip "LOG\t"
        assert log_content == "CMD1"
        assert parts[2] == "CMD2"


# ---------------------------------------------------------------------------
# Action classification test
# ---------------------------------------------------------------------------

class TestActionTypes:
    def test_expected_action_types(self):
        assert "movement" in ACTION_TYPES
        assert "close_assault" in ACTION_TYPES
        assert "barrage_result" in ACTION_TYPES
        assert "bombardment" in ACTION_TYPES
        assert "recon" in ACTION_TYPES
        assert "patrol" in ACTION_TYPES

    def test_logistics_not_in_action_types(self):
        assert "weather" not in ACTION_TYPES
        assert "initiative" not in ACTION_TYPES
        assert "stores_expenditure" not in ACTION_TYPES
        assert "cohesion_change" not in ACTION_TYPES


# ---------------------------------------------------------------------------
# PieceState dataclass tests
# ---------------------------------------------------------------------------

class TestPieceState:
    def test_creation(self):
        ps = PieceState(
            unit_id="cw_2rtr", gp_id=10000, type_str="test",
            hex_id="B2912", status="active", side="allied", placed=True,
        )
        assert ps.unit_id == "cw_2rtr"
        assert ps.placed is True

    def test_mutation(self):
        ps = PieceState(
            unit_id="cw_2rtr", gp_id=10000, type_str="test",
            hex_id="B2912", status="active", side="allied", placed=True,
        )
        ps.hex_id = "B3010"
        ps.status = "destroyed"
        assert ps.hex_id == "B3010"
        assert ps.status == "destroyed"
