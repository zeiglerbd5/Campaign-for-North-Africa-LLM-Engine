"""
CNA Engine — Air Game Module
Aircraft mission assignment, air-to-air combat, flak resolution,
bombardment/strafing against ground targets, reconnaissance/sighting,
sortie tracking, and maintenance.

The Air phase occurs as a shared phase in each OpStage.
Aircraft are based at SGSUs (airbases) and fly missions from there.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from cna_engine.models.game_state import (
    GameState,
)
from cna_engine.models.enums import (
    Side, AircraftMission, AircraftStatus, UnitStatus,
)
from cna_engine.engine.combat import roll_d6


# ════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════

# Air-to-air combat: maneuver differential → kill probability
# Simplified: higher maneuver wins. d6 ≤ differential = kill.
AIR_COMBAT_BASE_KILL_CHANCE = 3  # On d6, roll ≤ this for equal maneuver

# Flak effectiveness by flak points
# d6 roll needed to shoot down per flak point level
FLAK_TABLE = {
    0: 0,    # No flak
    1: 1,    # Roll 1 on d6
    2: 1,    # Roll 1
    3: 2,    # Roll 1-2
    4: 2,    # Roll 1-2
    5: 3,    # Roll 1-3
    6: 3,    # Roll 1-3
}

# Bombardment: bombload → barrage points equivalent
BOMB_TO_BARRAGE_RATIO = 2  # 1 bombload = 2 barrage points

# Strafing: tacair → strength points equivalent
TACAIR_TO_STRENGTH_RATIO = 1  # 1 tacair = 1 barrage point

# Recon sighting range
RECON_SIGHTING_RADIUS = 3  # Hexes sighted around target

# Sortie limits
MAX_SORTIES_PER_STAGE = 1  # Each aircraft flies once per OpStage

# Maintenance: chance of going to maintenance after mission
MAINTENANCE_CHANCE = 2  # d6 roll ≤ this = needs maintenance

# OCAP interception range
OCAP_INTERCEPT_RANGE = 6  # hexes

# DCAP screening: DCAP fighters at same base auto-intercept
DCAP_SCREEN_ACTIVE = True

# Semi-sighted abort thresholds by target type (d6 ≤ X to proceed)
SEMI_SIGHTED_THRESHOLDS = {
    "infantry": 4,
    "armor": 3,
    "gun": 3,
    "truck": 5,
    "sgsu": 4,
    "supply_dump": 5,
}


# ════════════════════════════════════════
# AIRCRAFT CHARACTERISTICS LOOKUP
# ════════════════════════════════════════

_AIRCRAFT_CHARS_CACHE: dict | None = None


def _load_aircraft_chars() -> dict[str, dict]:
    """Load aircraft characteristics from reference_data.json."""
    global _AIRCRAFT_CHARS_CACHE
    if _AIRCRAFT_CHARS_CACHE is not None:
        return _AIRCRAFT_CHARS_CACHE
    import json, os
    json_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "reference_data.json")
    with open(json_path) as f:
        _AIRCRAFT_CHARS_CACHE = json.load(f).get("aircraft", {})
    return _AIRCRAFT_CHARS_CACHE


def get_aircraft_stats(aircraft_type_id: str) -> tuple[int, int, int, int]:
    """Return (maneuver, tacair, bombload, range_hexes) for a type."""
    chars = _load_aircraft_chars().get(aircraft_type_id, {})
    maneuver = _parse_maneuver(chars.get("maneuver", "30"))
    tacair = _parse_int_or_zero(chars.get("tacair", "0"))
    bombload = _parse_int_or_zero(chars.get("bombload", "0"))
    range_hexes = int(chars.get("range_hexes", 50))
    return maneuver, tacair, bombload, range_hexes


def _parse_int_or_zero(val) -> int:
    """Parse an integer from reference data, treating dashes as 0."""
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip()
    if s in ("---", "\u2014", "-", "\u2013", ""):
        return 0
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# ════════════════════════════════════════
# RESULT DATACLASSES
# ════════════════════════════════════════

@dataclass
class MissionAssignResult:
    """Result of assigning a mission to an aircraft."""
    success: bool
    aircraft_id: str
    mission: str
    target_hex: Optional[str] = None
    blocked_reason: Optional[str] = None
    description: str = ""


@dataclass
class AirCombatResult:
    """Result of an air-to-air engagement."""
    attacker_id: str
    defender_id: str
    attacker_maneuver: int
    defender_maneuver: int
    differential: int
    dice_roll: int
    attacker_kills: bool
    defender_kills: bool
    attacker_destroyed: bool = False
    defender_destroyed: bool = False
    description: str = ""


@dataclass
class FlakResult:
    """Result of flak fire against an aircraft."""
    aircraft_id: str
    flak_points: int
    dice_roll: int
    is_hit: bool
    is_destroyed: bool = False
    is_damaged: bool = False
    description: str = ""


@dataclass
class BombardmentResult:
    """Result of air bombardment against ground targets."""
    aircraft_id: str
    target_hex: str
    bombload_used: int
    barrage_equivalent: int
    effect: str                 # "no_effect", "pinned", "1", "2"
    description: str = ""


@dataclass
class StrafingResult:
    """Result of strafing against ground targets."""
    aircraft_id: str
    target_hex: str
    tacair_used: int
    barrage_equivalent: int
    effect: str
    description: str = ""


@dataclass
class ReconResult:
    """Result of a reconnaissance mission."""
    aircraft_id: str
    target_hex: str
    hexes_sighted: list[str] = field(default_factory=list)
    units_spotted: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SortieResult:
    """Result of a complete aircraft sortie (mission + return)."""
    aircraft_id: str
    mission: str
    success: bool
    intercepted: bool = False
    flak_encountered: bool = False
    air_combat: Optional[AirCombatResult] = None
    flak: Optional[FlakResult] = None
    mission_result: Optional[object] = None  # BombardmentResult, etc.
    needs_maintenance: bool = False
    description: str = ""


@dataclass
class AirPhaseResult:
    """Aggregated result of the Air phase."""
    game_turn: int
    op_stage: int
    sorties_flown: int = 0
    aircraft_lost: int = 0
    missions_completed: list[SortieResult] = field(default_factory=list)
    description: str = ""


# ════════════════════════════════════════
# MISSION ASSIGNMENT
# ════════════════════════════════════════

def assign_mission(
    state: GameState,
    aircraft_id: str,
    mission: str,
    target_hex: Optional[str] = None,
) -> MissionAssignResult:
    """
    Assign a mission to an aircraft. Aircraft must be READY at an operational SGSU.
    """
    ac = state.aircraft.get(aircraft_id)
    if not ac:
        return MissionAssignResult(
            success=False, aircraft_id=aircraft_id, mission=mission,
            blocked_reason=f"Aircraft {aircraft_id} not found",
            description=f"Mission assignment failed: aircraft not found",
        )

    if ac.status != AircraftStatus.READY:
        return MissionAssignResult(
            success=False, aircraft_id=aircraft_id, mission=mission,
            blocked_reason=f"Aircraft status is {ac.status}, must be READY",
            description=f"Mission assignment failed: not ready",
        )

    # Check SGSU
    if ac.sgsu_id:
        sgsu = state.sgsus.get(ac.sgsu_id)
        if sgsu and not sgsu.is_operational:
            return MissionAssignResult(
                success=False, aircraft_id=aircraft_id, mission=mission,
                blocked_reason=f"SGSU {ac.sgsu_id} is not operational",
                description=f"Mission assignment failed: base not operational",
            )

    ac.mission = mission
    ac.status = AircraftStatus.FLEW_THIS_STAGE

    desc = f"Aircraft {aircraft_id} assigned to {mission}"
    if target_hex:
        desc += f" targeting {target_hex}"
    state.log_event("mission_assign", desc, aircraft_id=aircraft_id,
                    mission=mission, target_hex=target_hex)

    return MissionAssignResult(
        success=True, aircraft_id=aircraft_id, mission=mission,
        target_hex=target_hex, description=desc,
    )


# ════════════════════════════════════════
# AIR-TO-AIR COMBAT
# ════════════════════════════════════════

def _parse_maneuver(maneuver_str: str) -> int:
    """Parse maneuver value from aircraft characteristics (e.g., '30', '30/32')."""
    if isinstance(maneuver_str, (int, float)):
        return int(maneuver_str)
    s = str(maneuver_str).split("/")[0].strip()
    try:
        return int(s)
    except (ValueError, TypeError):
        return 30  # Default


def resolve_air_combat(
    state: GameState,
    attacker_id: str,
    defender_id: str,
    dice_roll: Optional[int] = None,
) -> AirCombatResult:
    """
    Resolve air-to-air combat between two aircraft.
    Higher maneuver = advantage. d6 roll determines kills.
    """
    attacker = state.aircraft.get(attacker_id)
    defender = state.aircraft.get(defender_id)

    if not attacker or not defender:
        return AirCombatResult(
            attacker_id=attacker_id, defender_id=defender_id,
            attacker_maneuver=0, defender_maneuver=0,
            differential=0, dice_roll=0,
            attacker_kills=False, defender_kills=False,
            description="Air combat failed: aircraft not found",
        )

    # Get maneuver values from aircraft type characteristics
    atk_stats = get_aircraft_stats(attacker.aircraft_type_id)
    def_stats = get_aircraft_stats(defender.aircraft_type_id)
    atk_man = atk_stats[0]
    def_man = def_stats[0]

    # Pilot experience: +2 per level
    atk_pilot = state.pilots.get(attacker.pilot_id) if attacker.pilot_id else None
    def_pilot = state.pilots.get(defender.pilot_id) if defender.pilot_id else None
    if atk_pilot:
        atk_man += atk_pilot.experience * 2
    if def_pilot:
        def_man += def_pilot.experience * 2

    differential = atk_man - def_man

    roll = dice_roll if dice_roll is not None else roll_d6()

    # Attacker kills if roll ≤ (base + differential/10)
    atk_threshold = AIR_COMBAT_BASE_KILL_CHANCE + (differential // 10)
    atk_kills = roll <= max(1, atk_threshold)

    # Defender return fire: always gets a shot
    def_roll = roll_d6()
    def_threshold = AIR_COMBAT_BASE_KILL_CHANCE - (differential // 10)
    def_kills = def_roll <= max(1, def_threshold)

    # Apply results
    if atk_kills and defender:
        defender.status = AircraftStatus.DESTROYED
    if def_kills and attacker:
        attacker.status = AircraftStatus.DESTROYED

    desc = (f"Air combat: {attacker_id} (man={atk_man}) vs {defender_id} (man={def_man}) "
            f"diff={differential}, roll={roll}: "
            f"{'KILL' if atk_kills else 'miss'} / "
            f"return fire roll={def_roll}: {'KILL' if def_kills else 'miss'}")

    state.log_event("air_combat", desc, attacker_id=attacker_id,
                    defender_id=defender_id)

    return AirCombatResult(
        attacker_id=attacker_id, defender_id=defender_id,
        attacker_maneuver=atk_man, defender_maneuver=def_man,
        differential=differential, dice_roll=roll,
        attacker_kills=atk_kills, defender_kills=def_kills,
        attacker_destroyed=def_kills,
        defender_destroyed=atk_kills,
        description=desc,
    )


# ════════════════════════════════════════
# FLAK RESOLUTION
# ════════════════════════════════════════

def resolve_flak(
    state: GameState,
    aircraft_id: str,
    flak_points: int,
    dice_roll: Optional[int] = None,
) -> FlakResult:
    """
    Resolve flak fire against an aircraft entering a hex.
    """
    ac = state.aircraft.get(aircraft_id)
    if not ac:
        return FlakResult(
            aircraft_id=aircraft_id, flak_points=flak_points,
            dice_roll=0, is_hit=False,
            description=f"Aircraft {aircraft_id} not found",
        )

    roll = dice_roll if dice_roll is not None else roll_d6()
    threshold = FLAK_TABLE.get(min(flak_points, 6), 0)
    is_hit = roll <= threshold

    if is_hit:
        # Hit: d6 for damage. 1-3 = damaged, 4-6 = destroyed
        damage_roll = roll_d6()
        if damage_roll <= 3:
            ac.status = AircraftStatus.DAMAGED
            is_destroyed = False
            is_damaged = True
        else:
            ac.status = AircraftStatus.DESTROYED
            is_destroyed = True
            is_damaged = False
    else:
        is_destroyed = False
        is_damaged = False

    desc = (f"Flak vs {aircraft_id}: {flak_points} flak pts, roll={roll} "
            f"(need ≤{threshold}): ")
    if is_destroyed:
        desc += "DESTROYED"
    elif is_damaged:
        desc += "DAMAGED"
    else:
        desc += "miss"

    state.log_event("flak", desc, aircraft_id=aircraft_id,
                    flak_points=flak_points)

    return FlakResult(
        aircraft_id=aircraft_id, flak_points=flak_points,
        dice_roll=roll, is_hit=is_hit,
        is_destroyed=is_destroyed, is_damaged=is_damaged,
        description=desc,
    )


# ════════════════════════════════════════
# BOMBARDMENT
# ════════════════════════════════════════

def resolve_bombardment(
    state: GameState,
    aircraft_id: str,
    target_hex: str,
    target_class: str = "infantry",
    dice_roll: Optional[int] = None,
) -> BombardmentResult:
    """
    Resolve air bombardment. Converts bombload to barrage points and
    resolves using the barrage CRT.
    """
    from cna_engine.engine.combat import resolve_barrage

    ac = state.aircraft.get(aircraft_id)
    if not ac:
        return BombardmentResult(
            aircraft_id=aircraft_id, target_hex=target_hex,
            bombload_used=0, barrage_equivalent=0,
            effect="no_effect",
            description=f"Aircraft {aircraft_id} not found",
        )

    bombload = ac.bombload_remaining
    barrage_pts = bombload * BOMB_TO_BARRAGE_RATIO
    ac.bombload_remaining = 0

    if barrage_pts <= 0:
        return BombardmentResult(
            aircraft_id=aircraft_id, target_hex=target_hex,
            bombload_used=bombload, barrage_equivalent=0,
            effect="no_effect",
            description=f"{aircraft_id} has no bombload",
        )

    # Resolve as barrage
    barrage_result = resolve_barrage(target_class, barrage_pts, dice_roll=dice_roll)

    desc = (f"Bombardment by {aircraft_id} on {target_hex}: "
            f"{bombload} bombs = {barrage_pts} BP → {barrage_result.result.upper()}")

    state.log_event("bombardment", desc, aircraft_id=aircraft_id,
                    target_hex=target_hex)

    return BombardmentResult(
        aircraft_id=aircraft_id, target_hex=target_hex,
        bombload_used=bombload, barrage_equivalent=barrage_pts,
        effect=barrage_result.result,
        description=desc,
    )


# ════════════════════════════════════════
# STRAFING
# ════════════════════════════════════════

def resolve_strafing(
    state: GameState,
    aircraft_id: str,
    target_hex: str,
    target_class: str = "truck",
    dice_roll: Optional[int] = None,
) -> StrafingResult:
    """
    Resolve strafing run. Converts tacair to barrage points and resolves.
    """
    from cna_engine.engine.combat import resolve_barrage

    ac = state.aircraft.get(aircraft_id)
    if not ac:
        return StrafingResult(
            aircraft_id=aircraft_id, target_hex=target_hex,
            tacair_used=0, barrage_equivalent=0,
            effect="no_effect",
            description=f"Aircraft {aircraft_id} not found",
        )

    tacair = ac.tacair_remaining
    barrage_pts = tacair * TACAIR_TO_STRENGTH_RATIO
    ac.tacair_remaining = 0

    if barrage_pts <= 0:
        return StrafingResult(
            aircraft_id=aircraft_id, target_hex=target_hex,
            tacair_used=tacair, barrage_equivalent=0,
            effect="no_effect",
            description=f"{aircraft_id} has no tacair remaining",
        )

    barrage_result = resolve_barrage(target_class, barrage_pts, dice_roll=dice_roll)

    desc = (f"Strafing by {aircraft_id} on {target_hex}: "
            f"{tacair} tacair = {barrage_pts} BP → {barrage_result.result.upper()}")

    state.log_event("strafing", desc, aircraft_id=aircraft_id,
                    target_hex=target_hex)

    return StrafingResult(
        aircraft_id=aircraft_id, target_hex=target_hex,
        tacair_used=tacair, barrage_equivalent=barrage_pts,
        effect=barrage_result.result,
        description=desc,
    )


# ════════════════════════════════════════
# RECONNAISSANCE
# ════════════════════════════════════════

def resolve_recon(
    state: GameState,
    aircraft_id: str,
    target_hex: str,
    sighting_radius: int = RECON_SIGHTING_RADIUS,
) -> ReconResult:
    """
    Resolve a reconnaissance mission. Sights hexes around the target
    within the sighting radius. Reveals enemy units.
    """
    ac = state.aircraft.get(aircraft_id)
    if not ac:
        return ReconResult(
            aircraft_id=aircraft_id, target_hex=target_hex,
            description=f"Aircraft {aircraft_id} not found",
        )

    # BFS to find all hexes within radius
    sighted = set()
    units_spotted = []
    queue = [(target_hex, 0)]
    visited = {target_hex}

    while queue:
        current, dist = queue.pop(0)
        sighted.add(current)

        if dist < sighting_radius:
            for neighbor in get_neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))

    # Mark hexes as sighted
    sighting_side = ac.side
    for hex_id in sighted:
        hex_state = state.hexes.get(hex_id)
        if hex_state:
            if sighting_side == Side.ALLIED:
                hex_state.allied_sighted = True
            else:
                hex_state.axis_sighted = True

            # Check for enemy units
            enemy_list = (hex_state.axis_unit_ids if sighting_side == Side.ALLIED
                          else hex_state.allied_unit_ids)
            for uid in enemy_list:
                u = state.units.get(uid)
                if u and u.status not in (UnitStatus.DESTROYED, UnitStatus.WITHDRAWN):
                    units_spotted.append(uid)

    desc = (f"Recon by {aircraft_id} over {target_hex}: "
            f"{len(sighted)} hexes sighted, {len(units_spotted)} enemy units spotted")

    state.log_event("recon", desc, aircraft_id=aircraft_id,
                    target_hex=target_hex, units_spotted=units_spotted)

    return ReconResult(
        aircraft_id=aircraft_id, target_hex=target_hex,
        hexes_sighted=sorted(sighted),
        units_spotted=units_spotted,
        description=desc,
    )


def get_neighbors(hex_id: str) -> list[str]:
    """Import-safe wrapper for movement.get_neighbors."""
    from cna_engine.engine.movement import get_neighbors as _gn
    return _gn(hex_id)


# ════════════════════════════════════════
# OCAP / DCAP / INTERCEPTION
# ════════════════════════════════════════

def _find_ocap_interceptor(
    state: GameState,
    target_hex: Optional[str],
    attacker_side: str,
) -> Optional[str]:
    """
    Find an OCAP fighter within range that can intercept an incoming sortie.
    OCAP fighters within OCAP_INTERCEPT_RANGE hexes of target roll d6 ≥ distance to intercept.
    Returns the aircraft_id of the interceptor, or None.
    """
    if not target_hex:
        return None

    from cna_engine.engine.movement import _hex_distance
    defender_side = Side.ALLIED if attacker_side == Side.AXIS else Side.AXIS

    candidates = []
    for ac in state.aircraft.values():
        if ac.side != defender_side:
            continue
        if ac.status != AircraftStatus.READY:
            continue
        if ac.mission != AircraftMission.OCAP:
            continue
        if not ac.sgsu_id:
            continue
        sgsu = state.sgsus.get(ac.sgsu_id)
        if not sgsu or not sgsu.hex_id:
            continue
        dist = _hex_distance(sgsu.hex_id, target_hex)
        if dist <= OCAP_INTERCEPT_RANGE:
            candidates.append((ac.id, dist))

    if not candidates:
        return None

    # Closest first; roll d6 ≥ distance to intercept
    candidates.sort(key=lambda x: x[1])
    for ac_id, dist in candidates:
        roll = roll_d6()
        if roll >= max(dist, 1):
            # Mark the interceptor as having flown
            ac = state.aircraft[ac_id]
            ac.status = AircraftStatus.FLEW_THIS_STAGE
            state.log_event("ocap_intercept", f"OCAP {ac_id} intercepts at distance {dist} (roll={roll})",
                            aircraft_id=ac_id, target_hex=target_hex)
            return ac_id

    return None


def resolve_dcap_screen(
    state: GameState,
    target_sgsu_hex: str,
    defender_side: str,
) -> Optional[str]:
    """
    DCAP screening: DCAP fighters at the same base as the target SGSU
    auto-intercept incoming attacks. Returns interceptor aircraft_id or None.
    """
    if not DCAP_SCREEN_ACTIVE:
        return None

    for ac in state.aircraft.values():
        if ac.side != defender_side:
            continue
        if ac.status != AircraftStatus.READY:
            continue
        if ac.mission != AircraftMission.DCAP:
            continue
        if not ac.sgsu_id:
            continue
        sgsu = state.sgsus.get(ac.sgsu_id)
        if not sgsu or sgsu.hex_id != target_sgsu_hex:
            continue
        # DCAP fighter at this base — auto-intercept
        ac.status = AircraftStatus.FLEW_THIS_STAGE
        state.log_event("dcap_screen", f"DCAP {ac.id} screens base at {target_sgsu_hex}",
                        aircraft_id=ac.id)
        return ac.id

    return None


# ════════════════════════════════════════
# SORTIE RESOLUTION (COMPLETE MISSION)
# ════════════════════════════════════════

def fly_sortie(
    state: GameState,
    aircraft_id: str,
    mission: str,
    target_hex: str,
    target_class: str = "infantry",
    interceptor_id: Optional[str] = None,
    flak_points: int = 0,
    dice_overrides: Optional[dict] = None,
) -> SortieResult:
    """
    Execute a complete sortie: assignment → intercept check → flak → mission → maintenance.
    dice_overrides can contain: air_combat, flak, mission, maintenance rolls.
    """
    overrides = dice_overrides or {}

    # 1. Assign mission
    assign = assign_mission(state, aircraft_id, mission, target_hex)
    if not assign.success:
        return SortieResult(
            aircraft_id=aircraft_id, mission=mission, success=False,
            description=f"Sortie failed: {assign.description}",
        )

    ac = state.aircraft[aircraft_id]

    # 1b. Range validation
    if ac.sgsu_id and target_hex:
        sgsu = state.sgsus.get(ac.sgsu_id)
        if sgsu and sgsu.hex_id:
            from cna_engine.engine.movement import _hex_distance
            distance = _hex_distance(sgsu.hex_id, target_hex)
            _, _, _, range_hexes = get_aircraft_stats(ac.aircraft_type_id)
            effective_range = range_hexes // 2  # round trip
            if distance > effective_range:
                ac.status = AircraftStatus.READY
                ac.mission = AircraftMission.NONE
                return SortieResult(
                    aircraft_id=aircraft_id, mission=mission, success=False,
                    description=f"Target {target_hex} out of range: {distance} hexes, max {effective_range}",
                )

    # 1c. Semi-sighted abort check (non-recon missions against un-sighted targets)
    if mission != AircraftMission.RECON and target_hex:
        hex_state = state.hexes.get(target_hex)
        if hex_state:
            sighted = (hex_state.allied_sighted if ac.side == Side.ALLIED
                       else hex_state.axis_sighted)
            if not sighted:
                threshold = SEMI_SIGHTED_THRESHOLDS.get(target_class, 4)
                abort_roll = roll_d6()
                if abort_roll > threshold:
                    ac.status = AircraftStatus.READY
                    ac.mission = AircraftMission.NONE
                    return SortieResult(
                        aircraft_id=aircraft_id, mission=mission, success=False,
                        description=f"Semi-sighted abort: roll {abort_roll} > {threshold} for {target_class}",
                    )

    # 2. OCAP automatic interception (if no explicit interceptor)
    if not interceptor_id:
        interceptor_id = _find_ocap_interceptor(state, target_hex, ac.side)

    # 2b. Air-to-air intercept
    air_result = None
    intercepted = False
    if interceptor_id:
        air_result = resolve_air_combat(
            state, interceptor_id, aircraft_id,
            dice_roll=overrides.get("air_combat"),
        )
        intercepted = True
        if air_result.defender_destroyed:
            return SortieResult(
                aircraft_id=aircraft_id, mission=mission, success=False,
                intercepted=True, air_combat=air_result,
                description=f"Sortie aborted: {aircraft_id} shot down by {interceptor_id}",
            )

    # 3. Flak
    flak_result = None
    if flak_points > 0 and ac.status != AircraftStatus.DESTROYED:
        flak_result = resolve_flak(
            state, aircraft_id, flak_points,
            dice_roll=overrides.get("flak"),
        )
        if flak_result.is_destroyed:
            return SortieResult(
                aircraft_id=aircraft_id, mission=mission, success=False,
                intercepted=intercepted, flak_encountered=True,
                air_combat=air_result, flak=flak_result,
                description=f"Sortie aborted: {aircraft_id} destroyed by flak",
            )

    # 4. Execute mission
    mission_result = None
    if ac.status not in (AircraftStatus.DESTROYED, AircraftStatus.DAMAGED):
        if mission == AircraftMission.BOMBING:
            mission_result = resolve_bombardment(
                state, aircraft_id, target_hex, target_class,
                dice_roll=overrides.get("mission"),
            )
        elif mission == AircraftMission.STRAFING:
            mission_result = resolve_strafing(
                state, aircraft_id, target_hex, target_class,
                dice_roll=overrides.get("mission"),
            )
        elif mission == AircraftMission.RECON:
            mission_result = resolve_recon(state, aircraft_id, target_hex)

    # 5. Post-mission maintenance check
    maint_roll = overrides.get("maintenance", roll_d6())
    needs_maintenance = maint_roll <= MAINTENANCE_CHANCE
    if needs_maintenance and ac.status == AircraftStatus.FLEW_THIS_STAGE:
        ac.status = AircraftStatus.MAINTENANCE

    ac.sorties_flown += 1

    desc = (f"Sortie {aircraft_id} ({mission}→{target_hex}): "
            f"{'intercepted ' if intercepted else ''}"
            f"{'flak ' if flak_result else ''}"
            f"mission {'complete' if mission_result else 'N/A'}"
            f"{' [MAINTENANCE]' if needs_maintenance else ''}")

    return SortieResult(
        aircraft_id=aircraft_id, mission=mission, success=True,
        intercepted=intercepted,
        flak_encountered=flak_result is not None,
        air_combat=air_result, flak=flak_result,
        mission_result=mission_result,
        needs_maintenance=needs_maintenance,
        description=desc,
    )


# ════════════════════════════════════════
# AIR PHASE EXECUTION
# ════════════════════════════════════════

def execute_air_phase(state: GameState) -> AirPhaseResult:
    """
    Execute the Air phase. Resets aircraft that flew last stage.
    Returns summary of air assets status.
    """
    gt = state.turn.game_turn
    op = state.turn.op_stage

    # Reset aircraft that flew last stage (but not maintenance/damaged/not_yet_arrived)
    for ac in state.aircraft.values():
        if ac.status == AircraftStatus.FLEW_THIS_STAGE:
            ac.status = AircraftStatus.READY
            ac.mission = AircraftMission.NONE
            # Rearm from aircraft type characteristics
            _, tacair, bombload, _ = get_aircraft_stats(ac.aircraft_type_id)
            ac.tacair_remaining = tacair
            ac.bombload_remaining = bombload

    # Count available aircraft (skip NOT_YET_ARRIVED)
    ready_allied = sum(1 for a in state.aircraft.values()
                       if a.side == Side.ALLIED and a.status == AircraftStatus.READY)
    ready_axis = sum(1 for a in state.aircraft.values()
                     if a.side == Side.AXIS and a.status == AircraftStatus.READY)

    desc = (f"GT{gt} OpStage {op} Air Phase: "
            f"Allied={ready_allied} ready, Axis={ready_axis} ready")
    state.log_event("air_phase", desc, game_turn=gt, op_stage=op)

    return AirPhaseResult(
        game_turn=gt, op_stage=op,
        description=desc,
    )
