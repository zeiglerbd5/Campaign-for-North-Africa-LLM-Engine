"""
CNA Engine — Minefield Operations
Laying, clearing, and resolving entry into real and fake minefields.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import random

from cna_engine.models.game_state import GameState, Unit
from cna_engine.models.enums import UnitClass, UnitStatus
from cna_engine.engine.movement import get_neighbors


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

MINEFIELD_ENTRY_COST = 2   # Extra MP cost (handled in movement.py)
CLEAR_CPA_COST = 2
LAY_CPA_COST = 2
LAY_FAKE_CPA_COST = 1


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class MinefieldEntryResult:
    """Result of a unit entering an enemy minefield hex."""
    unit_id: str
    hex_id: str
    was_fake: bool = False
    sp_lost: int = 0
    pinned: bool = False
    no_effect: bool = False
    roll: Optional[int] = None
    description: str = ""


@dataclass
class MinefieldOpResult:
    """Result of laying or clearing a minefield."""
    success: bool
    unit_id: str
    hex_id: str
    operation: str          # "lay", "lay_fake", "clear"
    blocked_reason: Optional[str] = None
    roll: Optional[int] = None
    description: str = ""


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

def _apply_sp_loss(unit: Unit, amount: int):
    """Reduce strength by removing from the largest category first."""
    cs = unit.current_strength
    for _ in range(amount):
        categories = [
            ("infantry", cs.infantry),
            ("armor", cs.armor),
            ("gun", cs.gun),
            ("mg", cs.mg),
            ("recon", cs.recon),
        ]
        categories.sort(key=lambda x: x[1], reverse=True)
        for cat_name, val in categories:
            if val > 0:
                setattr(cs, cat_name, val - 1)
                unit.losses_taken += 1
                break


# ════════════════════════════════════════
# MINEFIELD ENTRY RESOLUTION
# ════════════════════════════════════════

def resolve_minefield_entry(
    state: GameState,
    unit_id: str,
    hex_id: str,
    dice_roll: Optional[int] = None,
) -> MinefieldEntryResult:
    """
    Resolve a unit entering an enemy minefield hex.
    Called after movement into the hex.

    Fake: reveal + remove, no damage.
    Real: d6 → 1-2: 1 SP loss, 3-4: pinned, 5-6: no effect.
    Friendly minefields: no effect (shouldn't be called, but safe).
    """
    unit = state.units.get(unit_id)
    hs = state.hexes.get(hex_id)

    if not unit or not hs:
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     no_effect=True, description="No effect (missing data)")

    # Friendly minefield — no effect
    if hs.minefield_owner == unit.side:
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     no_effect=True, description="Friendly minefield — no effect")

    # Fake minefield — reveal and remove
    if hs.fake_minefield and not hs.real_minefield:
        hs.fake_minefield = False
        hs.minefield_owner = None
        desc = f"Fake minefield at {hex_id} revealed — no damage"
        state.log_event("minefield_fake_revealed", desc, unit_id=unit_id, hex_id=hex_id)
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     was_fake=True, description=desc)

    # Real minefield — roll for effect
    roll = dice_roll if dice_roll is not None else random.randint(1, 6)

    if roll <= 2:
        # SP loss
        _apply_sp_loss(unit, 1)
        desc = f"Minefield at {hex_id}: roll {roll} → 1 SP loss to {unit.name}"
        state.log_event("minefield_damage", desc, unit_id=unit_id, hex_id=hex_id, roll=roll)
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     sp_lost=1, roll=roll, description=desc)
    elif roll <= 4:
        # Pinned
        unit.is_pinned = True
        desc = f"Minefield at {hex_id}: roll {roll} → {unit.name} PINNED"
        state.log_event("minefield_damage", desc, unit_id=unit_id, hex_id=hex_id, roll=roll)
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     pinned=True, roll=roll, description=desc)
    else:
        # No effect
        desc = f"Minefield at {hex_id}: roll {roll} → no effect on {unit.name}"
        state.log_event("minefield_damage", desc, unit_id=unit_id, hex_id=hex_id, roll=roll)
        return MinefieldEntryResult(unit_id=unit_id, hex_id=hex_id,
                                     no_effect=True, roll=roll, description=desc)


# ════════════════════════════════════════
# LAY MINEFIELD
# ════════════════════════════════════════

def lay_minefield(
    state: GameState,
    unit_id: str,
    hex_id: str,
) -> MinefieldOpResult:
    """
    Lay a real minefield. Engineer only, in or adjacent hex, costs 2 CPA.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"Unit {unit_id} not found")

    if unit.unit_class != UnitClass.ENGINEER:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"{unit.name} is not an engineer unit")

    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"{unit.name} status {unit.status} cannot act")

    # Must be in or adjacent to target hex
    if not unit.hex_id:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"{unit.name} is not on the map")
    if unit.hex_id != hex_id and hex_id not in get_neighbors(unit.hex_id):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"{hex_id} not in/adjacent to {unit.hex_id}")

    # CPA check
    remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
    if remaining < LAY_CPA_COST:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay", blocked_reason=f"Need {LAY_CPA_COST} CPA, have {remaining}")

    # Apply
    unit.current_cpa_spent += LAY_CPA_COST
    hs = state.hexes.get(hex_id)
    if hs:
        hs.real_minefield = True
        hs.minefield_owner = unit.side

    desc = f"{unit.name} lays real minefield at {hex_id} (-{LAY_CPA_COST} CP)"
    state.log_event("lay_minefield", desc, unit_id=unit_id, hex_id=hex_id)
    return MinefieldOpResult(success=True, unit_id=unit_id, hex_id=hex_id,
                              operation="lay", description=desc)


def lay_fake_minefield(
    state: GameState,
    unit_id: str,
    hex_id: str,
) -> MinefieldOpResult:
    """
    Lay a fake minefield. Engineer only, in or adjacent hex, costs 1 CPA.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"Unit {unit_id} not found")

    if unit.unit_class != UnitClass.ENGINEER:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"{unit.name} is not an engineer unit")

    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"{unit.name} status {unit.status} cannot act")

    if not unit.hex_id:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"{unit.name} is not on the map")
    if unit.hex_id != hex_id and hex_id not in get_neighbors(unit.hex_id):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"{hex_id} not in/adjacent to {unit.hex_id}")

    remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
    if remaining < LAY_FAKE_CPA_COST:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="lay_fake", blocked_reason=f"Need {LAY_FAKE_CPA_COST} CPA, have {remaining}")

    unit.current_cpa_spent += LAY_FAKE_CPA_COST
    hs = state.hexes.get(hex_id)
    if hs:
        hs.fake_minefield = True
        hs.minefield_owner = unit.side

    desc = f"{unit.name} lays fake minefield at {hex_id} (-{LAY_FAKE_CPA_COST} CP)"
    state.log_event("lay_fake_minefield", desc, unit_id=unit_id, hex_id=hex_id)
    return MinefieldOpResult(success=True, unit_id=unit_id, hex_id=hex_id,
                              operation="lay_fake", description=desc)


# ════════════════════════════════════════
# CLEAR MINEFIELD
# ════════════════════════════════════════

def clear_minefield(
    state: GameState,
    unit_id: str,
    hex_id: str,
    dice_roll: Optional[int] = None,
) -> MinefieldOpResult:
    """
    Clear a minefield. Engineer only, in or adjacent.
    Fake: free reveal.
    Real: 2 CPA + d6 ≤ 4 succeeds.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"Unit {unit_id} not found")

    if unit.unit_class != UnitClass.ENGINEER:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"{unit.name} is not an engineer unit")

    if unit.status not in (UnitStatus.ACTIVE, UnitStatus.ENGAGED):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"{unit.name} status {unit.status} cannot act")

    if not unit.hex_id:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"{unit.name} is not on the map")
    if unit.hex_id != hex_id and hex_id not in get_neighbors(unit.hex_id):
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"{hex_id} not in/adjacent to {unit.hex_id}")

    hs = state.hexes.get(hex_id)
    if not hs:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"No hex state for {hex_id}")

    if not hs.real_minefield and not hs.fake_minefield:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"No minefield at {hex_id}")

    # Fake minefield — free reveal
    if hs.fake_minefield and not hs.real_minefield:
        hs.fake_minefield = False
        hs.minefield_owner = None
        desc = f"{unit.name} clears fake minefield at {hex_id} (free)"
        state.log_event("clear_minefield", desc, unit_id=unit_id, hex_id=hex_id)
        return MinefieldOpResult(success=True, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", description=desc)

    # Real minefield — costs CPA + roll
    remaining = unit.max_cpa_this_stage - unit.current_cpa_spent
    if remaining < CLEAR_CPA_COST:
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", blocked_reason=f"Need {CLEAR_CPA_COST} CPA, have {remaining}")

    unit.current_cpa_spent += CLEAR_CPA_COST
    roll = dice_roll if dice_roll is not None else random.randint(1, 6)

    if roll <= 4:
        # Success
        hs.real_minefield = False
        hs.fake_minefield = False
        hs.minefield_owner = None
        desc = f"{unit.name} clears real minefield at {hex_id} (roll {roll} ≤ 4 — success, -{CLEAR_CPA_COST} CP)"
        state.log_event("clear_minefield", desc, unit_id=unit_id, hex_id=hex_id, roll=roll)
        return MinefieldOpResult(success=True, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", roll=roll, description=desc)
    else:
        # Failure
        desc = f"{unit.name} fails to clear minefield at {hex_id} (roll {roll} > 4, -{CLEAR_CPA_COST} CP)"
        state.log_event("clear_minefield_failed", desc, unit_id=unit_id, hex_id=hex_id, roll=roll)
        return MinefieldOpResult(success=False, unit_id=unit_id, hex_id=hex_id,
                                  operation="clear", roll=roll,
                                  blocked_reason=f"Clearing failed (roll {roll})",
                                  description=desc)
