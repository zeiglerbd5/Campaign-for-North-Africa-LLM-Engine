"""
CNA Engine — Supply & Logistics Module
Fuel consumption, ammo expenditure, water/stores baseline,
evaporation, truck operations, supply dump operations,
and Stores Expenditure phase execution.

NJH key rule: When options["njh_fuel_in_tanks_no_evap"] is True,
fuel in unit internal tanks does NOT evaporate — only fuel in
drums/dumps/truck cargo evaporates.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import GameState, Unit, HexState, SupplyDump, UnitSupply
from cna_engine.models.enums import (
    Side, UnitStatus, TerrainType,
)
from cna_engine.data.reference_data import ReferenceData


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# CPA costs for truck/dump/water operations
DETACH_TRUCK_COST = 0.5
ATTACH_TRUCK_COST = 0.5
LOAD_UNLOAD_COST = 1.0
DRAW_WATER_COST = 1.0
CREATE_DUMP_COST = 1.0
DRAW_FROM_DUMP_COST = 1.0
DRAW_FROM_SUPPLY_POOL_COST = 1.0

# Baseline consumption per GT (Stores Expenditure phase)
BASELINE_WATER_PER_GT = 1.0
BASELINE_STORES_PER_GT = 0.5

# Ammo costs per combat action (mirrors ref.ammo_consumption)
AMMO_COST = {
    "barrage": 4,
    "anti_armor": 3,
    "close_assault": 2,
    "anti_air": 2,
    "rearm_tacair": 1,
    "rearm_bombs": 1,
}

# Unit statuses that are exempt from supply processing
_INACTIVE_STATUSES = frozenset({
    UnitStatus.DESTROYED,
    UnitStatus.SURRENDERED,
    UnitStatus.WITHDRAWN,
    UnitStatus.NOT_YET_ARRIVED,
})


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class FuelConsumptionResult:
    """Result of consuming fuel after movement."""
    unit_id: str
    cps_expended: float
    fuel_rate: int
    fuel_consumed: float
    fuel_before: float
    fuel_after: float
    from_internal: float
    from_truck_cargo: float
    deficit: float = 0.0
    description: str = ""


@dataclass
class AmmoExpendResult:
    """Result of expending ammo after combat."""
    unit_id: str
    action: str
    ammo_cost: float
    ammo_before: float
    ammo_after: float
    from_internal: float
    from_truck_cargo: float
    insufficient: bool = False
    description: str = ""


@dataclass
class EvaporationResult:
    """Result of applying evaporation to a single target."""
    target_id: str
    target_type: str        # "unit" or "dump"
    side: str
    evap_rate: float
    fuel_lost: float = 0.0
    water_lost: float = 0.0
    ammo_lost: float = 0.0
    stores_lost: float = 0.0
    description: str = ""


@dataclass
class SupplyStatusResult:
    """Query result for a unit's supply status."""
    unit_id: str
    fuel_pct: float
    water_pct: float
    ammo_pct: float
    stores_pct: float
    is_fuel_critical: bool
    is_water_critical: bool
    is_ammo_critical: bool
    truck_cargo_fuel: float
    truck_cargo_water: float
    truck_cargo_ammo: float
    truck_cargo_stores: float
    attached_truck_points: int
    description: str = ""


@dataclass
class TruckOpResult:
    """Result of a truck operation (attach/detach/load/unload)."""
    success: bool
    unit_id: str
    operation: str
    truck_points_moved: int = 0
    supplies_transferred: dict = field(default_factory=dict)
    cp_cost: float = 0.0
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class DumpOpResult:
    """Result of a supply dump operation (create/draw)."""
    success: bool
    unit_id: str
    dump_id: str
    hex_id: str
    operation: str
    supplies_transferred: dict = field(default_factory=dict)
    cp_cost: float = 0.0
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class StoresExpenditureResult:
    """Aggregated result of the Stores Expenditure phase."""
    game_turn: int
    evaporation_results: list[EvaporationResult] = field(default_factory=list)
    water_consumed: float = 0.0
    stores_consumed: float = 0.0
    fuel_critical_units: list[str] = field(default_factory=list)
    water_critical_units: list[str] = field(default_factory=list)
    ammo_critical_units: list[str] = field(default_factory=list)
    total_units_processed: int = 0
    total_dumps_processed: int = 0
    description: str = ""


# ════════════════════════════════════════
# INTERNAL HELPERS
# ════════════════════════════════════════

def _draw_from_unit(unit: Unit, supply_type: str, amount: float) -> tuple[float, float, float]:
    """
    Draw a supply amount from a unit, internal first then truck cargo.
    Returns (from_internal, from_truck_cargo, deficit).
    """
    internal_attr = supply_type              # "fuel", "water", "ammo", "stores"
    truck_attr = f"truck_cargo_{supply_type}"

    internal_avail = getattr(unit.supply, internal_attr)
    from_internal = min(internal_avail, amount)
    setattr(unit.supply, internal_attr, round(internal_avail - from_internal, 4))
    remaining = amount - from_internal

    from_truck = 0.0
    if remaining > 0:
        truck_avail = getattr(unit, truck_attr)
        from_truck = min(truck_avail, remaining)
        setattr(unit, truck_attr, round(truck_avail - from_truck, 4))
        remaining = remaining - from_truck

    deficit = max(0.0, round(remaining, 4))
    return (round(from_internal, 4), round(from_truck, 4), deficit)


def _normalize_cpa(unit: Unit):
    """Normalize current_cpa_spent to int if it's a whole number."""
    if isinstance(unit.current_cpa_spent, float) and unit.current_cpa_spent == int(unit.current_cpa_spent):
        unit.current_cpa_spent = int(unit.current_cpa_spent)


def _check_cpa_budget(unit: Unit, cost: float) -> Optional[str]:
    """Check if unit has enough CPA. Returns blocked_reason or None."""
    budget = unit.max_cpa_this_stage
    remaining = budget - unit.current_cpa_spent
    if remaining < cost:
        return (f"{unit.name} needs {cost} CP, only {remaining} remaining "
                f"({unit.current_cpa_spent}/{budget} spent)")
    return None


def _is_unit_active(unit: Unit) -> bool:
    """Check if a unit should be processed for supply."""
    return unit.status not in _INACTIVE_STATUSES


# ════════════════════════════════════════
# STEP 2: FUEL CONSUMPTION
# ════════════════════════════════════════

def consume_fuel(
    state: GameState,
    ref: ReferenceData,
    unit_id: str,
    cps_expended: float,
    fuel_rate: int = 1,
) -> FuelConsumptionResult:
    """
    Consume fuel after movement. Called after execute_move.

    Formula: fuel = cps_expended * fuel_rate * 0.2
    Draw order: internal tank first, then truck cargo.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return FuelConsumptionResult(
            unit_id=unit_id, cps_expended=cps_expended, fuel_rate=fuel_rate,
            fuel_consumed=0, fuel_before=0, fuel_after=0,
            from_internal=0, from_truck_cargo=0,
            description=f"Unit {unit_id} not found",
        )

    fuel_needed = round(cps_expended * fuel_rate * 0.2, 1)
    fuel_before = unit.supply.fuel + unit.truck_cargo_fuel

    from_internal, from_truck, deficit = _draw_from_unit(unit, "fuel", fuel_needed)

    fuel_after = unit.supply.fuel + unit.truck_cargo_fuel

    desc = (f"{unit.name} consumes {fuel_needed} fuel "
            f"({cps_expended} CPs x rate {fuel_rate} x 0.2)")
    if from_truck > 0:
        desc += f" [internal={from_internal}, truck={from_truck}]"
    if deficit > 0:
        desc += f" WARNING: {deficit} fuel deficit!"

    state.log_event("fuel_consumption", desc, unit_id=unit_id,
                    fuel_consumed=fuel_needed, deficit=deficit)

    return FuelConsumptionResult(
        unit_id=unit_id,
        cps_expended=cps_expended,
        fuel_rate=fuel_rate,
        fuel_consumed=fuel_needed,
        fuel_before=fuel_before,
        fuel_after=fuel_after,
        from_internal=from_internal,
        from_truck_cargo=from_truck,
        deficit=deficit,
        description=desc,
    )


# ════════════════════════════════════════
# STEP 3: AMMO EXPENDITURE
# ════════════════════════════════════════

def expend_ammo(
    state: GameState,
    unit_id: str,
    action: str,
    count: int = 1,
) -> AmmoExpendResult:
    """
    Expend ammo after combat. Looks up cost from AMMO_COST[action] * count.
    Draw order: internal first, truck cargo second.
    Sets insufficient flag if not enough ammo.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return AmmoExpendResult(
            unit_id=unit_id, action=action, ammo_cost=0,
            ammo_before=0, ammo_after=0, from_internal=0,
            from_truck_cargo=0, insufficient=True,
            description=f"Unit {unit_id} not found",
        )

    base_cost = AMMO_COST.get(action, 0)
    ammo_needed = base_cost * count

    ammo_before = unit.supply.ammo + unit.truck_cargo_ammo
    from_internal, from_truck, deficit = _draw_from_unit(unit, "ammo", ammo_needed)
    ammo_after = unit.supply.ammo + unit.truck_cargo_ammo
    insufficient = deficit > 0

    desc = f"{unit.name} expends {ammo_needed} ammo ({action} x{count})"
    if from_truck > 0:
        desc += f" [internal={from_internal}, truck={from_truck}]"
    if insufficient:
        desc += f" INSUFFICIENT: {deficit} short"

    state.log_event("ammo_expenditure", desc, unit_id=unit_id,
                    action=action, ammo_expended=ammo_needed, deficit=deficit)

    return AmmoExpendResult(
        unit_id=unit_id,
        action=action,
        ammo_cost=ammo_needed,
        ammo_before=ammo_before,
        ammo_after=ammo_after,
        from_internal=from_internal,
        from_truck_cargo=from_truck,
        insufficient=insufficient,
        description=desc,
    )


# ════════════════════════════════════════
# STEP 4: SUPPLY STATUS QUERY
# ════════════════════════════════════════

def check_supply_status(state: GameState, unit_id: str) -> SupplyStatusResult:
    """
    Pure query — no state mutation. Returns percentage fill for each supply type.
    Critical flag at <25% capacity.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return SupplyStatusResult(
            unit_id=unit_id,
            fuel_pct=0, water_pct=0, ammo_pct=0, stores_pct=0,
            is_fuel_critical=True, is_water_critical=True, is_ammo_critical=True,
            truck_cargo_fuel=0, truck_cargo_water=0,
            truck_cargo_ammo=0, truck_cargo_stores=0,
            attached_truck_points=0,
            description=f"Unit {unit_id} not found",
        )

    def _pct(current: float, capacity: float) -> float:
        if capacity <= 0:
            return 100.0  # No capacity = not applicable, treat as full
        return round((current / capacity) * 100, 1)

    fuel_pct = _pct(unit.supply.fuel, unit.supply.fuel_capacity)
    water_pct = _pct(unit.supply.water, unit.supply.water_capacity)
    ammo_pct = _pct(unit.supply.ammo, unit.supply.ammo_capacity)
    stores_pct = _pct(unit.supply.stores, unit.supply.stores_capacity)

    desc = (f"{unit.name}: fuel={fuel_pct}% water={water_pct}% "
            f"ammo={ammo_pct}% stores={stores_pct}%")
    criticals = []
    if fuel_pct < 25:
        criticals.append("FUEL")
    if water_pct < 25:
        criticals.append("WATER")
    if ammo_pct < 25:
        criticals.append("AMMO")
    if criticals:
        desc += f" CRITICAL: {', '.join(criticals)}"

    return SupplyStatusResult(
        unit_id=unit_id,
        fuel_pct=fuel_pct,
        water_pct=water_pct,
        ammo_pct=ammo_pct,
        stores_pct=stores_pct,
        is_fuel_critical=(fuel_pct < 25),
        is_water_critical=(water_pct < 25),
        is_ammo_critical=(ammo_pct < 25),
        truck_cargo_fuel=unit.truck_cargo_fuel,
        truck_cargo_water=unit.truck_cargo_water,
        truck_cargo_ammo=unit.truck_cargo_ammo,
        truck_cargo_stores=unit.truck_cargo_stores,
        attached_truck_points=unit.attached_truck_points,
        description=desc,
    )


def compute_supply_combat_modifiers(unit_ids: list[str], state) -> int:
    """Return combined CRT column shift (0 to -3) from supply levels."""
    if not unit_ids:
        return 0
    ammo_pcts, water_pcts = [], []
    for uid in unit_ids:
        unit = state.units.get(uid)
        if not unit:
            continue
        if unit.supply.ammo_capacity > 0:
            ammo_pcts.append(unit.supply.ammo / unit.supply.ammo_capacity * 100)
        if unit.supply.water_capacity > 0:
            water_pcts.append(unit.supply.water / unit.supply.water_capacity * 100)

    avg_ammo = sum(ammo_pcts) / len(ammo_pcts) if ammo_pcts else 100
    avg_water = sum(water_pcts) / len(water_pcts) if water_pcts else 100

    ammo_shift = 0 if avg_ammo >= 50 else (-1 if avg_ammo >= 25 else -2)
    water_shift = 0 if avg_water >= 50 else (-1 if avg_water >= 25 else -2)
    return max(-3, ammo_shift + water_shift)


# ════════════════════════════════════════
# STEP 5: EVAPORATION
# ════════════════════════════════════════

def apply_evaporation_to_unit(
    unit: Unit,
    rate: float,
    njh_fuel_in_tanks_no_evap: bool = True,
) -> EvaporationResult:
    """
    Apply evaporation to a single unit's supplies.

    NJH rule: When njh_fuel_in_tanks_no_evap=True, unit.supply.fuel
    (internal tank) is EXEMPT from evaporation. Only truck_cargo_fuel
    evaporates. Water/ammo/stores always evaporate (both internal and
    truck cargo).
    """
    fuel_lost = 0.0
    water_lost = 0.0
    ammo_lost = 0.0
    stores_lost = 0.0

    # Fuel evaporation
    if njh_fuel_in_tanks_no_evap:
        # Only truck cargo fuel evaporates
        truck_fuel_loss = round(unit.truck_cargo_fuel * rate, 4)
        unit.truck_cargo_fuel = round(unit.truck_cargo_fuel - truck_fuel_loss, 4)
        fuel_lost = truck_fuel_loss
    else:
        # Both internal and truck cargo evaporate
        internal_fuel_loss = round(unit.supply.fuel * rate, 4)
        unit.supply.fuel = round(unit.supply.fuel - internal_fuel_loss, 4)
        truck_fuel_loss = round(unit.truck_cargo_fuel * rate, 4)
        unit.truck_cargo_fuel = round(unit.truck_cargo_fuel - truck_fuel_loss, 4)
        fuel_lost = internal_fuel_loss + truck_fuel_loss

    # Water — both internal and truck cargo evaporate
    internal_water_loss = round(unit.supply.water * rate, 4)
    unit.supply.water = round(unit.supply.water - internal_water_loss, 4)
    truck_water_loss = round(unit.truck_cargo_water * rate, 4)
    unit.truck_cargo_water = round(unit.truck_cargo_water - truck_water_loss, 4)
    water_lost = internal_water_loss + truck_water_loss

    # Ammo — both internal and truck cargo evaporate
    internal_ammo_loss = round(unit.supply.ammo * rate, 4)
    unit.supply.ammo = round(unit.supply.ammo - internal_ammo_loss, 4)
    truck_ammo_loss = round(unit.truck_cargo_ammo * rate, 4)
    unit.truck_cargo_ammo = round(unit.truck_cargo_ammo - truck_ammo_loss, 4)
    ammo_lost = internal_ammo_loss + truck_ammo_loss

    # Stores — both internal and truck cargo evaporate
    internal_stores_loss = round(unit.supply.stores * rate, 4)
    unit.supply.stores = round(unit.supply.stores - internal_stores_loss, 4)
    truck_stores_loss = round(unit.truck_cargo_stores * rate, 4)
    unit.truck_cargo_stores = round(unit.truck_cargo_stores - truck_stores_loss, 4)
    stores_lost = internal_stores_loss + truck_stores_loss

    desc = (f"Evaporation {unit.name} ({rate*100:.0f}%): "
            f"fuel={fuel_lost:.1f} water={water_lost:.1f} "
            f"ammo={ammo_lost:.1f} stores={stores_lost:.1f}")
    if njh_fuel_in_tanks_no_evap:
        desc += " [NJH: tank fuel exempt]"

    return EvaporationResult(
        target_id=unit.id,
        target_type="unit",
        side=unit.side,
        evap_rate=rate,
        fuel_lost=round(fuel_lost, 4),
        water_lost=round(water_lost, 4),
        ammo_lost=round(ammo_lost, 4),
        stores_lost=round(stores_lost, 4),
        description=desc,
    )


def apply_evaporation_to_dump(dump: SupplyDump, rate: float) -> EvaporationResult:
    """
    Apply evaporation to a supply dump. All types evaporate (dumps are all drums).
    """
    fuel_lost = round(dump.fuel * rate, 4)
    water_lost = round(dump.water * rate, 4)
    ammo_lost = round(dump.ammo * rate, 4)
    stores_lost = round(dump.stores * rate, 4)

    dump.fuel = round(dump.fuel - fuel_lost, 4)
    dump.water = round(dump.water - water_lost, 4)
    dump.ammo = round(dump.ammo - ammo_lost, 4)
    dump.stores = round(dump.stores - stores_lost, 4)

    return EvaporationResult(
        target_id=dump.id,
        target_type="dump",
        side=dump.side,
        evap_rate=rate,
        fuel_lost=fuel_lost,
        water_lost=water_lost,
        ammo_lost=ammo_lost,
        stores_lost=stores_lost,
        description=(f"Evaporation dump {dump.id} ({rate*100:.0f}%): "
                     f"fuel={fuel_lost:.1f} water={water_lost:.1f} "
                     f"ammo={ammo_lost:.1f} stores={stores_lost:.1f}"),
    )


# ════════════════════════════════════════
# STEP 6: TRUCK OPERATIONS
# ════════════════════════════════════════

def _validate_truck_op(
    state: GameState,
    unit_id: str,
    operation: str,
    cp_cost: float,
) -> tuple[Optional[Unit], Optional[str]]:
    """
    Shared validation for truck operations.
    Returns (unit, blocked_reason). If blocked_reason is not None, the op is invalid.
    """
    unit = state.units.get(unit_id)
    if not unit:
        return (None, f"Unit {unit_id} not found")
    if not _is_unit_active(unit):
        return (None, f"{unit.name} is not active (status: {unit.status})")

    blocked = _check_cpa_budget(unit, cp_cost)
    if blocked:
        return (unit, blocked)

    return (unit, None)


def execute_truck_attach(
    state: GameState,
    unit_id: str,
    truck_points: int = 1,
) -> TruckOpResult:
    """Attach truck points to a unit. Costs 0.5 CP."""
    unit, blocked = _validate_truck_op(state, unit_id, "attach", ATTACH_TRUCK_COST)
    if blocked:
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="attach",
            blocked_reason=blocked,
            description=f"Truck attach failed: {blocked}",
        )

    unit.attached_truck_points += truck_points
    unit.current_cpa_spent += ATTACH_TRUCK_COST
    _normalize_cpa(unit)

    desc = (f"{unit.name} attaches {truck_points} truck point(s) "
            f"(total: {unit.attached_truck_points}) [-{ATTACH_TRUCK_COST} CP]")
    state.log_event("truck_attach", desc, unit_id=unit_id,
                    truck_points=truck_points)

    return TruckOpResult(
        success=True, unit_id=unit_id, operation="attach",
        truck_points_moved=truck_points,
        cp_cost=ATTACH_TRUCK_COST,
        description=desc,
    )


def execute_truck_detach(
    state: GameState,
    unit_id: str,
    truck_points: int = 1,
) -> TruckOpResult:
    """Detach truck points from a unit. Costs 0.5 CP."""
    unit, blocked = _validate_truck_op(state, unit_id, "detach", DETACH_TRUCK_COST)
    if blocked:
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="detach",
            blocked_reason=blocked,
            description=f"Truck detach failed: {blocked}",
        )

    if unit.attached_truck_points < truck_points:
        reason = (f"{unit.name} has {unit.attached_truck_points} truck points, "
                  f"cannot detach {truck_points}")
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="detach",
            blocked_reason=reason, description=f"Truck detach failed: {reason}",
        )

    unit.attached_truck_points -= truck_points
    unit.current_cpa_spent += DETACH_TRUCK_COST
    _normalize_cpa(unit)

    desc = (f"{unit.name} detaches {truck_points} truck point(s) "
            f"(remaining: {unit.attached_truck_points}) [-{DETACH_TRUCK_COST} CP]")
    state.log_event("truck_detach", desc, unit_id=unit_id,
                    truck_points=truck_points)

    return TruckOpResult(
        success=True, unit_id=unit_id, operation="detach",
        truck_points_moved=truck_points,
        cp_cost=DETACH_TRUCK_COST,
        description=desc,
    )


def execute_truck_load(
    state: GameState,
    unit_id: str,
    fuel: float = 0.0,
    water: float = 0.0,
    ammo: float = 0.0,
    stores: float = 0.0,
) -> TruckOpResult:
    """
    Load supplies from unit internal storage onto truck cargo. Costs 1 CP.
    Capped by what the unit actually has internally.
    """
    unit, blocked = _validate_truck_op(state, unit_id, "load", LOAD_UNLOAD_COST)
    if blocked:
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="load",
            blocked_reason=blocked,
            description=f"Truck load failed: {blocked}",
        )

    if unit.attached_truck_points <= 0:
        reason = f"{unit.name} has no attached truck points"
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="load",
            blocked_reason=reason, description=f"Truck load failed: {reason}",
        )

    transferred = {}
    for supply_type, requested in [("fuel", fuel), ("water", water),
                                    ("ammo", ammo), ("stores", stores)]:
        if requested > 0:
            internal_avail = getattr(unit.supply, supply_type)
            actual = min(internal_avail, requested)
            setattr(unit.supply, supply_type, round(internal_avail - actual, 4))
            truck_attr = f"truck_cargo_{supply_type}"
            current_truck = getattr(unit, truck_attr)
            setattr(unit, truck_attr, round(current_truck + actual, 4))
            if actual > 0:
                transferred[supply_type] = actual

    unit.current_cpa_spent += LOAD_UNLOAD_COST
    _normalize_cpa(unit)

    desc = f"{unit.name} loads onto trucks: {transferred} [-{LOAD_UNLOAD_COST} CP]"
    state.log_event("truck_load", desc, unit_id=unit_id, transferred=transferred)

    return TruckOpResult(
        success=True, unit_id=unit_id, operation="load",
        supplies_transferred=transferred,
        cp_cost=LOAD_UNLOAD_COST,
        description=desc,
    )


def execute_truck_unload(
    state: GameState,
    unit_id: str,
    fuel: float = 0.0,
    water: float = 0.0,
    ammo: float = 0.0,
    stores: float = 0.0,
) -> TruckOpResult:
    """
    Unload supplies from truck cargo into unit internal storage. Costs 1 CP.
    Capped by truck cargo available AND by unit internal capacity.
    """
    unit, blocked = _validate_truck_op(state, unit_id, "unload", LOAD_UNLOAD_COST)
    if blocked:
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="unload",
            blocked_reason=blocked,
            description=f"Truck unload failed: {blocked}",
        )

    if unit.attached_truck_points <= 0:
        reason = f"{unit.name} has no attached truck points"
        return TruckOpResult(
            success=False, unit_id=unit_id, operation="unload",
            blocked_reason=reason, description=f"Truck unload failed: {reason}",
        )

    transferred = {}
    for supply_type, requested in [("fuel", fuel), ("water", water),
                                    ("ammo", ammo), ("stores", stores)]:
        if requested > 0:
            truck_attr = f"truck_cargo_{supply_type}"
            truck_avail = getattr(unit, truck_attr)
            capacity = getattr(unit.supply, f"{supply_type}_capacity")
            current_internal = getattr(unit.supply, supply_type)
            space_available = max(0, capacity - current_internal)
            actual = min(truck_avail, requested, space_available)
            setattr(unit, truck_attr, round(truck_avail - actual, 4))
            setattr(unit.supply, supply_type, round(current_internal + actual, 4))
            if actual > 0:
                transferred[supply_type] = actual

    unit.current_cpa_spent += LOAD_UNLOAD_COST
    _normalize_cpa(unit)

    desc = f"{unit.name} unloads from trucks: {transferred} [-{LOAD_UNLOAD_COST} CP]"
    state.log_event("truck_unload", desc, unit_id=unit_id, transferred=transferred)

    return TruckOpResult(
        success=True, unit_id=unit_id, operation="unload",
        supplies_transferred=transferred,
        cp_cost=LOAD_UNLOAD_COST,
        description=desc,
    )


# ════════════════════════════════════════
# STEP 7: SUPPLY DUMP OPERATIONS
# ════════════════════════════════════════

def create_supply_dump(
    state: GameState,
    unit_id: str,
    dump_id: str,
    fuel: float = 0.0,
    water: float = 0.0,
    ammo: float = 0.0,
    stores: float = 0.0,
) -> DumpOpResult:
    """
    Create a supply dump from unit's truck cargo on the unit's hex. Costs 1 CP.
    """
    unit, blocked = _validate_truck_op(state, unit_id, "create_dump", CREATE_DUMP_COST)
    if blocked:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id="", operation="create",
            blocked_reason=blocked,
            description=f"Create dump failed: {blocked}",
        )

    if not unit.hex_id:
        reason = f"{unit.name} is not on the map"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id="", operation="create",
            blocked_reason=reason,
            description=f"Create dump failed: {reason}",
        )

    # Transfer from truck cargo to new dump, capped by available
    transferred = {}
    actual_fuel = min(unit.truck_cargo_fuel, fuel)
    actual_water = min(unit.truck_cargo_water, water)
    actual_ammo = min(unit.truck_cargo_ammo, ammo)
    actual_stores = min(unit.truck_cargo_stores, stores)

    unit.truck_cargo_fuel = round(unit.truck_cargo_fuel - actual_fuel, 4)
    unit.truck_cargo_water = round(unit.truck_cargo_water - actual_water, 4)
    unit.truck_cargo_ammo = round(unit.truck_cargo_ammo - actual_ammo, 4)
    unit.truck_cargo_stores = round(unit.truck_cargo_stores - actual_stores, 4)

    dump = SupplyDump(
        id=dump_id, side=unit.side, is_real=True,
        fuel=actual_fuel, water=actual_water,
        ammo=actual_ammo, stores=actual_stores,
    )

    hex_state = state.hexes.get(unit.hex_id)
    if not hex_state:
        hex_state = HexState(hex_id=unit.hex_id, terrain=TerrainType.CLEAR)
        state.hexes[unit.hex_id] = hex_state
    hex_state.supply_dumps.append(dump)

    if actual_fuel > 0:
        transferred["fuel"] = actual_fuel
    if actual_water > 0:
        transferred["water"] = actual_water
    if actual_ammo > 0:
        transferred["ammo"] = actual_ammo
    if actual_stores > 0:
        transferred["stores"] = actual_stores

    unit.current_cpa_spent += CREATE_DUMP_COST
    _normalize_cpa(unit)

    desc = (f"{unit.name} creates dump '{dump_id}' at {unit.hex_id}: "
            f"{transferred} [-{CREATE_DUMP_COST} CP]")
    state.log_event("create_dump", desc, unit_id=unit_id, dump_id=dump_id,
                    hex_id=unit.hex_id)

    return DumpOpResult(
        success=True, unit_id=unit_id, dump_id=dump_id,
        hex_id=unit.hex_id, operation="create",
        supplies_transferred=transferred,
        cp_cost=CREATE_DUMP_COST,
        description=desc,
    )


def draw_from_dump(
    state: GameState,
    unit_id: str,
    dump_id: str,
    fuel: float = 0.0,
    water: float = 0.0,
    ammo: float = 0.0,
    stores: float = 0.0,
) -> DumpOpResult:
    """
    Draw supplies from a dump into unit (internal first, overflow to truck cargo).
    Costs 1 CP. Dump must be at unit's hex and belong to same side.
    """
    unit, blocked = _validate_truck_op(state, unit_id, "draw_dump", DRAW_FROM_DUMP_COST)
    if blocked:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id="", operation="draw",
            blocked_reason=blocked,
            description=f"Draw from dump failed: {blocked}",
        )

    if not unit.hex_id:
        reason = f"{unit.name} is not on the map"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id="", operation="draw",
            blocked_reason=reason,
            description=f"Draw from dump failed: {reason}",
        )

    # Find dump at unit's hex
    hex_state = state.hexes.get(unit.hex_id)
    if not hex_state:
        reason = f"No hex state for {unit.hex_id}"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id=unit.hex_id, operation="draw",
            blocked_reason=reason,
            description=f"Draw from dump failed: {reason}",
        )

    dump = None
    for d in hex_state.supply_dumps:
        if d.id == dump_id and d.side == unit.side:
            dump = d
            break
    # Fallback: match by ID only (will hit the side check below)
    if not dump:
        for d in hex_state.supply_dumps:
            if d.id == dump_id:
                dump = d
                break

    if not dump:
        reason = f"Dump '{dump_id}' not found at {unit.hex_id}"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id=unit.hex_id, operation="draw",
            blocked_reason=reason,
            description=f"Draw from dump failed: {reason}",
        )

    if dump.side != unit.side:
        reason = f"Dump '{dump_id}' belongs to {dump.side}, unit is {unit.side}"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id=dump_id,
            hex_id=unit.hex_id, operation="draw",
            blocked_reason=reason,
            description=f"Draw from dump failed: {reason}",
        )

    # Draw each supply type: dump → internal (up to capacity), overflow → truck cargo
    transferred = {}
    for supply_type, requested in [("fuel", fuel), ("water", water),
                                    ("ammo", ammo), ("stores", stores)]:
        if requested <= 0:
            continue
        dump_avail = getattr(dump, supply_type)
        actual_from_dump = min(dump_avail, requested)
        if actual_from_dump <= 0:
            continue

        # Try to fill internal first
        capacity = getattr(unit.supply, f"{supply_type}_capacity")
        current_internal = getattr(unit.supply, supply_type)
        space = max(0, capacity - current_internal)
        to_internal = min(actual_from_dump, space)
        to_truck = actual_from_dump - to_internal

        setattr(dump, supply_type, round(dump_avail - actual_from_dump, 4))
        setattr(unit.supply, supply_type, round(current_internal + to_internal, 4))
        truck_attr = f"truck_cargo_{supply_type}"
        current_truck = getattr(unit, truck_attr)
        setattr(unit, truck_attr, round(current_truck + to_truck, 4))

        transferred[supply_type] = actual_from_dump

    unit.current_cpa_spent += DRAW_FROM_DUMP_COST
    _normalize_cpa(unit)

    desc = (f"{unit.name} draws from dump '{dump_id}' at {unit.hex_id}: "
            f"{transferred} [-{DRAW_FROM_DUMP_COST} CP]")
    state.log_event("draw_from_dump", desc, unit_id=unit_id, dump_id=dump_id)

    return DumpOpResult(
        success=True, unit_id=unit_id, dump_id=dump_id,
        hex_id=unit.hex_id, operation="draw",
        supplies_transferred=transferred,
        cp_cost=DRAW_FROM_DUMP_COST,
        description=desc,
    )


def draw_water_from_terrain(
    state: GameState,
    unit_id: str,
    amount: float = 1.0,
    depletion_roll: Optional[int] = None,
) -> DumpOpResult:
    """
    Draw water from terrain (oasis or bir). Costs 1 CP.
    - Oasis: unlimited water, no depletion.
    - Bir: may deplete on d6 roll of 1-2. On depletion, terrain becomes CLEAR.
    depletion_roll can be overridden for testing.
    """
    import random

    unit = state.units.get(unit_id)
    if not unit:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="terrain",
            hex_id="", operation="draw_water",
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Draw water failed: Unit {unit_id} not found",
        )

    blocked = _check_cpa_budget(unit, DRAW_WATER_COST)
    if blocked:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="terrain",
            hex_id=unit.hex_id or "", operation="draw_water",
            blocked_reason=blocked,
            description=f"Draw water failed: {blocked}",
        )

    if not unit.hex_id:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="terrain",
            hex_id="", operation="draw_water",
            blocked_reason=f"{unit.name} is not on the map",
            description=f"Draw water failed: not on map",
        )

    hex_state = state.hexes.get(unit.hex_id)
    if not hex_state:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="terrain",
            hex_id=unit.hex_id, operation="draw_water",
            blocked_reason=f"No hex state for {unit.hex_id}",
            description=f"Draw water failed: no hex state",
        )

    terrain = hex_state.terrain.lower().replace(" ", "_")

    if terrain not in (TerrainType.OASIS, TerrainType.BIR) and not hex_state.has_water_pipeline:
        reason = f"Hex {unit.hex_id} terrain is '{terrain}', not oasis/bir/pipeline"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="terrain",
            hex_id=unit.hex_id, operation="draw_water",
            blocked_reason=reason,
            description=f"Draw water failed: {reason}",
        )

    # Add water to unit (internal first, overflow to truck)
    capacity = unit.supply.water_capacity
    current = unit.supply.water
    space = max(0, capacity - current)
    to_internal = min(amount, space)
    to_truck = amount - to_internal

    unit.supply.water = round(current + to_internal, 4)
    unit.truck_cargo_water = round(unit.truck_cargo_water + to_truck, 4)

    unit.current_cpa_spent += DRAW_WATER_COST
    _normalize_cpa(unit)

    transferred = {"water": amount}
    desc = f"{unit.name} draws {amount} water from {terrain} at {unit.hex_id}"

    # Bir depletion check
    depleted = False
    if terrain == TerrainType.BIR:
        roll = depletion_roll if depletion_roll is not None else random.randint(1, 6)
        if roll <= 2:
            hex_state.terrain = TerrainType.CLEAR
            depleted = True
            desc += f" [BIR DEPLETED on roll {roll}!]"
        else:
            desc += f" [bir holds, roll {roll}]"

    desc += f" [-{DRAW_WATER_COST} CP]"

    state.log_event("draw_water", desc, unit_id=unit_id,
                    hex_id=unit.hex_id, depleted=depleted)

    return DumpOpResult(
        success=True, unit_id=unit_id, dump_id="terrain",
        hex_id=unit.hex_id, operation="draw_water",
        supplies_transferred=transferred,
        cp_cost=DRAW_WATER_COST,
        description=desc,
    )


# ════════════════════════════════════════
# STEP 7b: DRAW FROM SUPPLY POOL
# ════════════════════════════════════════

def draw_from_supply_pool(
    state: GameState,
    unit_id: str,
    fuel: float = 0.0,
    water: float = 0.0,
    ammo: float = 0.0,
    stores: float = 0.0,
) -> DumpOpResult:
    """
    Draw supplies from the side's supply pool. Unit must be at a port hex.
    Costs 1 CP. Deducts from allied_supply_in_egypt or axis_supply_in_tripoli_boxes.
    Adds to unit's internal storage (overflow to truck cargo).
    """
    unit = state.units.get(unit_id)
    if not unit:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="supply_pool",
            hex_id="", operation="draw_supply_pool",
            blocked_reason=f"Unit {unit_id} not found",
            description=f"Draw from supply pool failed: Unit {unit_id} not found",
        )

    if not _is_unit_active(unit):
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="supply_pool",
            hex_id=unit.hex_id or "", operation="draw_supply_pool",
            blocked_reason=f"{unit.name} is not active (status: {unit.status})",
            description=f"Draw from supply pool failed: not active",
        )

    blocked = _check_cpa_budget(unit, DRAW_FROM_SUPPLY_POOL_COST)
    if blocked:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="supply_pool",
            hex_id=unit.hex_id or "", operation="draw_supply_pool",
            blocked_reason=blocked,
            description=f"Draw from supply pool failed: {blocked}",
        )

    if not unit.hex_id:
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="supply_pool",
            hex_id="", operation="draw_supply_pool",
            blocked_reason=f"{unit.name} is not on the map",
            description=f"Draw from supply pool failed: not on map",
        )

    # Unit must be at a port hex
    hex_state = state.hexes.get(unit.hex_id)
    if not hex_state or not hex_state.is_port:
        reason = f"Hex {unit.hex_id} is not a port — unit must be at a port to draw from supply pool"
        return DumpOpResult(
            success=False, unit_id=unit_id, dump_id="supply_pool",
            hex_id=unit.hex_id, operation="draw_supply_pool",
            blocked_reason=reason,
            description=f"Draw from supply pool failed: {reason}",
        )

    # Determine which pool to draw from
    from cna_engine.models.enums import Side
    if unit.side == Side.ALLIED:
        pool = state.allied_supply_in_egypt
    else:
        pool = state.axis_supply_in_tripoli_boxes

    # Draw each supply type: pool → internal (up to capacity), overflow → truck cargo
    transferred = {}
    for supply_type, requested in [("fuel", fuel), ("water", water),
                                    ("ammo", ammo), ("stores", stores)]:
        if requested <= 0:
            continue
        pool_avail = pool.get(supply_type, 0.0)
        actual_from_pool = min(pool_avail, requested)
        if actual_from_pool <= 0:
            continue

        # Fill internal first, overflow to truck cargo
        capacity = getattr(unit.supply, f"{supply_type}_capacity")
        current_internal = getattr(unit.supply, supply_type)
        space = max(0, capacity - current_internal)
        to_internal = min(actual_from_pool, space)
        to_truck = actual_from_pool - to_internal

        pool[supply_type] = round(pool_avail - actual_from_pool, 4)
        setattr(unit.supply, supply_type, round(current_internal + to_internal, 4))
        truck_attr = f"truck_cargo_{supply_type}"
        current_truck = getattr(unit, truck_attr)
        setattr(unit, truck_attr, round(current_truck + to_truck, 4))

        transferred[supply_type] = actual_from_pool

    unit.current_cpa_spent += DRAW_FROM_SUPPLY_POOL_COST
    _normalize_cpa(unit)

    pool_name = "Egypt" if unit.side == Side.ALLIED else "Tripoli"
    desc = (f"{unit.name} draws from {pool_name} supply pool at {unit.hex_id}: "
            f"{transferred} [-{DRAW_FROM_SUPPLY_POOL_COST} CP]")

    state.log_event("draw_from_supply_pool", desc, unit_id=unit_id,
                    hex_id=unit.hex_id)

    return DumpOpResult(
        success=True, unit_id=unit_id, dump_id="supply_pool",
        hex_id=unit.hex_id, operation="draw_supply_pool",
        supplies_transferred=transferred,
        cp_cost=DRAW_FROM_SUPPLY_POOL_COST,
        description=desc,
    )


# ════════════════════════════════════════
# STEP 8: STORES EXPENDITURE PHASE
# ════════════════════════════════════════

def _auto_resupply_unit(state: GameState, unit: Unit):
    """
    Auto-resupply: units at ports draw from supply pool (free),
    units at dump hexes draw from dumps (free).
    No CPA cost — represents organic logistics between turns.
    """
    if not unit.hex_id:
        return
    hex_state = state.hexes.get(unit.hex_id)
    if not hex_state:
        return

    supply = unit.supply

    # 1. At a friendly port → draw from supply pool (unlimited, free)
    if hex_state.is_port:
        pool = (state.allied_supply_in_egypt if unit.side == Side.ALLIED
                else state.axis_supply_in_tripoli_boxes)
        for resource in ("fuel", "water", "ammo", "stores"):
            current = getattr(supply, resource)
            capacity = getattr(supply, f"{resource}_capacity")
            need = capacity - current
            if need > 0:
                available = pool.get(resource, 0)
                draw = min(need, available)
                if draw > 0:
                    setattr(supply, resource, round(current + draw, 4))
                    pool[resource] = round(available - draw, 4)

    # 2. At a hex with friendly supply dump → draw from dump
    for dump in hex_state.supply_dumps:
        if dump.side != unit.side or not dump.is_real:
            continue
        for resource in ("fuel", "water", "ammo", "stores"):
            current = getattr(supply, resource)
            capacity = getattr(supply, f"{resource}_capacity")
            need = capacity - current
            if need > 0:
                dump_avail = getattr(dump, resource)
                draw = min(need, dump_avail)
                if draw > 0:
                    setattr(supply, resource, round(current + draw, 4))
                    setattr(dump, resource, round(dump_avail - draw, 4))


def execute_stores_expenditure(
    state: GameState,
    ref: ReferenceData,
) -> StoresExpenditureResult:
    """
    Execute the Stores Expenditure phase at start of each GT.
    1. Get evaporation rates for this GT
    2. Apply evaporation to all active units
    3. Apply evaporation to all real supply dumps
    4. Auto-resupply from ports and dumps
    5. Deduct baseline water per unit
    6. Deduct baseline stores per unit
    7. Flag critical units
    8. Return aggregated result
    """
    gt = state.turn.game_turn
    allied_rate, axis_rate = ref.get_evaporation_rates(gt)
    njh_flag = state.options.get("njh_fuel_in_tanks_no_evap", True)

    evap_results = []
    total_water = 0.0
    total_stores = 0.0
    fuel_critical = []
    water_critical = []
    ammo_critical = []
    units_processed = 0
    dumps_processed = 0

    # 1-2. Evaporate all active units
    for unit in state.units.values():
        if not _is_unit_active(unit):
            continue
        units_processed += 1

        rate = allied_rate if unit.side == Side.ALLIED else axis_rate
        evap = apply_evaporation_to_unit(unit, rate, njh_flag)
        evap_results.append(evap)

    # 3. Evaporate all real supply dumps
    for hex_state in state.hexes.values():
        for dump in hex_state.supply_dumps:
            if not dump.is_real:
                continue
            dumps_processed += 1
            rate = allied_rate if dump.side == Side.ALLIED else axis_rate
            evap = apply_evaporation_to_dump(dump, rate)
            evap_results.append(evap)

    # 4. Auto-resupply from ports and dumps
    for unit in state.units.values():
        if not _is_unit_active(unit):
            continue
        _auto_resupply_unit(state, unit)

    # 5-6. Baseline water and stores consumption per active unit
    for unit in state.units.values():
        if not _is_unit_active(unit):
            continue

        # Water baseline
        _, _, water_deficit = _draw_from_unit(unit, "water", BASELINE_WATER_PER_GT)
        total_water += BASELINE_WATER_PER_GT
        if water_deficit > 0:
            from cna_engine.engine.agent_interface import _distribute_sp_loss
            sp_lost = _distribute_sp_loss(unit, 1)
            if sp_lost > 0:
                state.log_event("water_starvation",
                    f"{unit.name} loses {sp_lost} SP from dehydration "
                    f"(water deficit {water_deficit:.1f})",
                    unit_id=unit.id, sp_lost=sp_lost,
                    water_deficit=water_deficit)
                if unit.status == UnitStatus.DESTROYED:
                    state.log_event("unit_destroyed",
                        f"{unit.name} destroyed by dehydration",
                        unit_id=unit.id, cause="water_starvation")

        # Stores baseline
        _, _, stores_deficit = _draw_from_unit(unit, "stores", BASELINE_STORES_PER_GT)
        total_stores += BASELINE_STORES_PER_GT

    # 7. Flag critical units
    for unit in state.units.values():
        if not _is_unit_active(unit):
            continue
        status = check_supply_status(state, unit.id)
        if status.is_fuel_critical:
            fuel_critical.append(unit.id)
        if status.is_water_critical:
            water_critical.append(unit.id)
        if status.is_ammo_critical:
            ammo_critical.append(unit.id)

    desc = (f"GT{gt} Stores Expenditure: "
            f"{units_processed} units, {dumps_processed} dumps processed. "
            f"Evap rates: Allied={allied_rate*100:.0f}% Axis={axis_rate*100:.0f}%. "
            f"Water consumed={total_water:.1f}, Stores consumed={total_stores:.1f}. "
            f"Critical: fuel={len(fuel_critical)} water={len(water_critical)} "
            f"ammo={len(ammo_critical)}")

    state.log_event("stores_expenditure", desc, game_turn=gt)

    return StoresExpenditureResult(
        game_turn=gt,
        evaporation_results=evap_results,
        water_consumed=total_water,
        stores_consumed=total_stores,
        fuel_critical_units=fuel_critical,
        water_critical_units=water_critical,
        ammo_critical_units=ammo_critical,
        total_units_processed=units_processed,
        total_dumps_processed=dumps_processed,
        description=desc,
    )
