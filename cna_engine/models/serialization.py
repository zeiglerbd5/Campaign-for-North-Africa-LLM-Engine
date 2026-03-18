"""
CNA Engine — State Serialization
Save and load complete game state to/from JSON.
Handles dataclass → dict → JSON and back.
"""
import json
import os
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from typing import Any

from .game_state import (
    GameState, TurnState, Unit, TOEStrength, UnitSupply, Formation,
    HexState, SupplyDump, Aircraft, Pilot, SGSU, FleetState, ConvoyState,
)


def state_to_dict(state: GameState) -> dict:
    """Convert GameState to a plain dict suitable for JSON serialization."""
    return _dataclass_to_dict(state)


def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses to dicts."""
    if is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = _dataclass_to_dict(value)
        return result
    elif isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)


def dict_to_state(data: dict) -> GameState:
    """Reconstruct a GameState from a plain dict (loaded from JSON)."""
    state = GameState()

    # Turn
    if "turn" in data:
        state.turn = _build_dataclass(TurnState, data["turn"])

    # Units
    if "units" in data:
        state.units = {
            uid: _build_dataclass(Unit, udata)
            for uid, udata in data["units"].items()
        }

    # Formations
    if "formations" in data:
        state.formations = {
            fid: _build_dataclass(Formation, fdata)
            for fid, fdata in data["formations"].items()
        }

    # Hexes
    if "hexes" in data:
        state.hexes = {
            hid: _build_hex(hdata)
            for hid, hdata in data["hexes"].items()
        }

    # Air game
    if "aircraft" in data:
        state.aircraft = {
            aid: _build_dataclass(Aircraft, adata)
            for aid, adata in data["aircraft"].items()
        }
    if "pilots" in data:
        state.pilots = {
            pid: _build_dataclass(Pilot, pdata)
            for pid, pdata in data["pilots"].items()
        }
    if "sgsus" in data:
        state.sgsus = {
            sid: _build_dataclass(SGSU, sdata)
            for sid, sdata in data["sgsus"].items()
        }

    # Naval
    if "cw_fleet" in data:
        state.cw_fleet = _build_dataclass(FleetState, data["cw_fleet"])
    if "axis_convoy" in data:
        state.axis_convoy = _build_dataclass(ConvoyState, data["axis_convoy"])

    # Supply pools
    for key in ["allied_supply_in_egypt", "axis_supply_in_tripoli_boxes",
                "allied_replacement_pool", "axis_replacement_pool",
                "allied_truck_production_queue", "axis_truck_production_queue",
                "event_log", "options"]:
        if key in data:
            setattr(state, key, data[key])

    return state


def _build_dataclass(cls, data: dict):
    """Build a dataclass instance from a dict, handling nested types."""
    if not isinstance(data, dict):
        return data

    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        val = data[f.name]

        # Handle known nested dataclasses
        if f.name == "toe_strength" or f.name == "current_strength":
            kwargs[f.name] = _build_dataclass(TOEStrength, val) if isinstance(val, dict) else val
        elif f.name == "supply" and isinstance(val, dict):
            kwargs[f.name] = _build_dataclass(UnitSupply, val)
        else:
            kwargs[f.name] = val

    return cls(**kwargs)


def _build_hex(data: dict) -> HexState:
    """Build HexState with nested SupplyDump list."""
    dumps = []
    if "supply_dumps" in data:
        for d in data["supply_dumps"]:
            if isinstance(d, dict):
                dumps.append(_build_dataclass(SupplyDump, d))
    data_copy = dict(data)
    data_copy["supply_dumps"] = dumps
    return _build_dataclass(HexState, data_copy)


# ── File I/O ──

def save_state(state: GameState, filepath: str):
    """Save game state to a JSON file."""
    data = state_to_dict(state)
    data["_metadata"] = {
        "engine_version": "0.1.0",
        "saved_at": datetime.utcnow().isoformat(),
        "game_turn": state.turn.game_turn,
        "op_stage": state.turn.op_stage,
        "date": state.turn.date_string,
    }
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def load_state(filepath: str) -> GameState:
    """Load game state from a JSON file."""
    with open(filepath, "r") as f:
        data = json.load(f)
    data.pop("_metadata", None)
    return dict_to_state(data)


def state_summary(state: GameState) -> dict:
    """Generate a compact summary of the game state for LLM context windows."""
    allied_units = [u for u in state.units.values() if u.side == "allied" and u.status == "active"]
    axis_units = [u for u in state.units.values() if u.side == "axis" and u.status == "active"]

    return {
        "turn": state.turn.game_turn,
        "op_stage": state.turn.op_stage,
        "date": state.turn.date_string,
        "phase": state.turn.phase,
        "weather": state.turn.current_weather,
        "initiative": state.turn.initiative_side,
        "allied": {
            "active_units": len(allied_units),
            "total_strength": sum(u.current_strength.total for u in allied_units),
            "formations": len([f for f in state.formations.values() if f.side == "allied" and f.is_active]),
        },
        "axis": {
            "active_units": len(axis_units),
            "total_strength": sum(u.current_strength.total for u in axis_units),
            "formations": len([f for f in state.formations.values() if f.side == "axis" and f.is_active]),
        },
        "aircraft": {
            "allied": len([a for a in state.aircraft.values() if a.side == "allied" and a.status != "destroyed"]),
            "axis": len([a for a in state.aircraft.values() if a.side == "axis" and a.status != "destroyed"]),
        },
    }
