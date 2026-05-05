"""
CNA Engine — Tool Schemas for Agentic Tool-Calling Mode

Three tools the LLM can call iteratively during playbook execution:
  - inspect_unit: Read a unit's status (position, CPA, strength, supply)
  - check_hex: Read a hex's info (terrain, units, enemies, dumps)
  - issue_order: Validate and accumulate an order against the engine

Used by SituationEngine._execute_playbook_tools() when tool_calling=True.
"""
from __future__ import annotations
import json
import logging
from typing import Callable

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Side, UnitStatus
from cna_engine.engine.agent_interface import (
    validate_command, _compute_suggested_moves,
)
from cna_engine.engine.movement import get_neighbors

from .situations import StateSignals

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# Tool definitions (OpenAI function-calling format)
# ─────────────────────────────────────

_INSPECT_UNIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_unit",
        "description": (
            "Look up a friendly unit's current status: position, CPA remaining, "
            "strength points, supply levels, and a suggested move destination."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "unit_id": {
                    "type": "string",
                    "description": "The unit ID to inspect (e.g. 'allied_7arm_div').",
                },
            },
            "required": ["unit_id"],
        },
    },
}

_CHECK_HEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_hex",
        "description": (
            "Look up a hex's terrain, which units are present, adjacent enemy "
            "units with their strength, and any supply dumps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hex_id": {
                    "type": "string",
                    "description": "The hex ID to check (e.g. 'B2415').",
                },
            },
            "required": ["hex_id"],
        },
    },
}

_ISSUE_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "issue_order",
        "description": (
            "Submit an order. The order is validated against engine rules "
            "and the playbook's command whitelist. If valid, it is queued. "
            "Returns success/failure with details."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The command name (e.g. 'move_unit', 'fire_barrage', "
                        "'close_assault', 'end_phase')."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Command parameters as key-value pairs "
                        "(e.g. {\"unit_id\": \"allied_7arm_div\", "
                        "\"destination\": \"B2815\"})."
                    ),
                },
            },
            "required": ["command", "params"],
        },
    },
}


def build_tool_list() -> list[dict]:
    """Return the list of 3 tool schemas for chat_with_tools()."""
    return [_INSPECT_UNIT_SCHEMA, _CHECK_HEX_SCHEMA, _ISSUE_ORDER_SCHEMA]


# ─────────────────────────────────────
# Tool handler factory
# ─────────────────────────────────────

def create_tool_handler(
    state: GameState,
    side: str,
    playbook,  # Playbook instance
    accumulated_orders: list[dict],
    signals: StateSignals,
) -> Callable[[str, dict], str]:
    """
    Create a closure that dispatches tool calls to the appropriate handler.

    Args:
        state: Current game state (read-only during tool loop).
        side: The side issuing orders ("allied" or "axis").
        playbook: Active Playbook (for command whitelist).
        accumulated_orders: Mutable list where valid orders are appended.
        signals: Pre-computed state signals for suggested moves.

    Returns:
        Callable(tool_name, tool_args) -> JSON string result.
    """
    # Pre-compute suggested moves once for inspect_unit
    _suggestions_cache: dict[str, str] = {}
    _suggestions_computed = [False]

    def _get_suggestions() -> dict[str, str]:
        if not _suggestions_computed[0]:
            friendly = []
            for uid, u in state.units.items():
                if (u.side == side
                        and u.status == UnitStatus.ACTIVE
                        and u.effective_cpa > 0):
                    friendly.append({"id": uid, "cpa_remaining": u.effective_cpa})
            _suggestions_cache.update(
                _compute_suggested_moves(state, side, friendly)
            )
            _suggestions_computed[0] = True
        return _suggestions_cache

    whitelist = set(playbook.applicable_commands) if playbook else None

    # Map role from command
    from .general import _COMMAND_TO_ROLE

    def handler(tool_name: str, tool_args: dict) -> str:
        if tool_name == "inspect_unit":
            return _handle_inspect_unit(tool_args)
        elif tool_name == "check_hex":
            return _handle_check_hex(tool_args)
        elif tool_name == "issue_order":
            return _handle_issue_order(tool_args)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _handle_inspect_unit(args: dict) -> str:
        uid = args.get("unit_id", "")
        unit = state.units.get(uid)
        if not unit:
            return json.dumps({"error": f"Unit '{uid}' not found."})
        if unit.side != side:
            return json.dumps({"error": f"Unit '{uid}' belongs to the enemy."})

        suggestions = _get_suggestions()
        result = {
            "unit_id": uid,
            "name": unit.name,
            "hex": unit.hex_id,
            "status": unit.status if isinstance(unit.status, str) else unit.status.value,
            "cpa_remaining": unit.effective_cpa,
            "strength": unit.current_strength.total,
            "supply": {
                "fuel": round(unit.supply.fuel, 1),
                "water": round(unit.supply.water, 1),
                "ammo": round(unit.supply.ammo, 1),
            },
            "motorized": unit.is_motorized,
            "in_contact": unit.is_in_contact,
            "pinned": unit.is_pinned,
        }
        if uid in suggestions:
            result["suggested_move"] = suggestions[uid]
        return json.dumps(result)

    def _handle_check_hex(args: dict) -> str:
        hex_id = args.get("hex_id", "")
        hs = state.hexes.get(hex_id)
        if not hs:
            return json.dumps({"error": f"Hex '{hex_id}' not found on map."})

        # Friendly and enemy unit IDs
        if side == Side.ALLIED:
            friendly_ids = hs.allied_unit_ids
            enemy_ids = hs.axis_unit_ids
        else:
            friendly_ids = hs.axis_unit_ids
            enemy_ids = hs.allied_unit_ids

        # Adjacent enemies with strength
        adjacent_enemies = []
        for neighbor_id in get_neighbors(hex_id):
            nhs = state.hexes.get(neighbor_id)
            if not nhs:
                continue
            enemy_at_neighbor = (
                nhs.axis_unit_ids if side == Side.ALLIED else nhs.allied_unit_ids
            )
            for eid in enemy_at_neighbor:
                eu = state.units.get(eid)
                if eu and eu.status == UnitStatus.ACTIVE:
                    adjacent_enemies.append({
                        "unit_id": eid,
                        "hex": neighbor_id,
                        "strength": eu.current_strength.total,
                    })

        # Supply dumps at this hex (friendly only)
        dumps = []
        for dump in hs.supply_dumps:
            if dump.side == side:
                dumps.append({
                    "dump_id": dump.id,
                    "fuel": round(dump.fuel, 1),
                    "water": round(dump.water, 1),
                    "ammo": round(dump.ammo, 1),
                })

        terrain = hs.terrain if isinstance(hs.terrain, str) else hs.terrain.value

        result = {
            "hex_id": hex_id,
            "terrain": terrain,
            "friendly_units": friendly_ids,
            "enemy_units": enemy_ids,
            "adjacent_enemies": adjacent_enemies[:6],  # cap for token budget
            "supply_dumps": dumps,
            "is_port": hs.is_port,
            "fort_level": hs.fort_level,
        }
        return json.dumps(result)

    def _handle_issue_order(args: dict) -> str:
        command = args.get("command", "")
        params = args.get("params", {})

        if not isinstance(params, dict):
            return json.dumps({"error": "params must be a JSON object."})

        # end_phase is always valid
        if command == "end_phase":
            accumulated_orders.append({"command": "end_phase", "params": {}})
            return json.dumps({
                "status": "accepted",
                "command": "end_phase",
                "orders_queued": len(accumulated_orders),
            })

        # Whitelist check
        if whitelist and command not in whitelist:
            return json.dumps({
                "error": f"Command '{command}' not allowed in this playbook. "
                         f"Allowed: {', '.join(sorted(whitelist))}",
            })

        # Engine validation
        role = _COMMAND_TO_ROLE.get(command)
        if not role:
            return json.dumps({"error": f"Unknown command: '{command}'"})

        result = validate_command(state, role, side, command, **params)
        if result.success:
            accumulated_orders.append({"command": command, "params": params})
            return json.dumps({
                "status": "accepted",
                "command": command,
                "orders_queued": len(accumulated_orders),
            })
        else:
            return json.dumps({
                "status": "rejected",
                "command": command,
                "error": result.error or "Validation failed",
            })

    return handler
