"""
CNA Engine — Combat Resolution Module
Implements the three land combat systems:
  1. Barrage (Artillery) [12.0]
  2. Anti-Armor Fire [14.0]
  3. Close Assault [15.0]

Each system takes combat inputs, applies modifiers, rolls dice,
looks up the appropriate CRT, and returns structured results.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random


# ════════════════════════════════════════
# DICE SYSTEM
# ════════════════════════════════════════
# CNA uses two d6 read as a two-digit number (11-66), NOT summed.
# First die = tens, second die = ones. So 1,3 = 13, 5,2 = 52, etc.
# Range is 11-66 with gaps (no 17, 18, 19, 27, 28, 29, etc.)

def roll_d66() -> int:
    """Roll two d6 as a CNA-style two-digit number (11-66)."""
    d1 = random.randint(1, 6)
    d2 = random.randint(1, 6)
    return d1 * 10 + d2


def roll_d6() -> int:
    return random.randint(1, 6)


def d66_in_range(roll: int, range_str: str) -> bool:
    """
    Check if a d66 roll falls within a CNA dice range string.
    Formats: "11-53", "66", "54-66", "-" (never), etc.
    """
    if not range_str or range_str.strip() in ("-", "—", ""):
        return False

    range_str = range_str.strip()

    # Handle single value like "66"
    if range_str.isdigit() and len(range_str) == 2:
        return roll == int(range_str)

    # Handle range like "11-53"
    if "-" in range_str:
        parts = range_str.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            low, high = int(parts[0]), int(parts[1])
            return low <= roll <= high

    return False


# ════════════════════════════════════════
# RESULT DATA STRUCTURES
# ════════════════════════════════════════

@dataclass
class BarrageResult:
    """Result of a barrage attack."""
    target_class: str           # "infantry", "armor", "gun", "truck"
    barrage_points: int         # Final barrage points after shifts
    raw_barrage_points: int     # Before terrain shifts
    column_shifts: int          # Net column shifts applied
    dice_roll: int              # d66 roll
    result: str                 # "no_effect", "pinned", "1", "2"
    strength_points_lost: int = 0
    is_pinned: bool = False
    description: str = ""


@dataclass
class AntiArmorResult:
    """Result of anti-armor fire."""
    anti_armor_points: int      # Final AA points after shifts
    raw_anti_armor_points: int
    column_shifts: int
    dice_roll: int
    is_phasing: bool            # Phasing player shifts dice row down
    armor_protection_lost: int = 0
    description: str = ""


@dataclass
class CloseAssaultResult:
    """Result of a close assault."""
    differential: int           # Final assault differential
    attacker_strength: int
    defender_strength: int
    dice_roll: int

    # Attacker results
    attacker_loss_percent: int = 0
    attacker_captured: bool = False
    attacker_engaged: bool = False

    # Defender results
    defender_loss_percent: int = 0
    defender_captured: bool = False
    defender_retreat_hexes: int = 0
    defender_engaged: bool = False

    is_overrun: bool = False
    description: str = ""


# ════════════════════════════════════════
# BARRAGE CRT [12.6]
# ════════════════════════════════════════

# Column headers: barrage point ranges
BARRAGE_COLUMNS = [
    (1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 99)
]

# Each target class has rows: (result_type, [dice_range_per_column])
# "-" = impossible
BARRAGE_TABLE = {
    "infantry": {
        "no_effect": ["11-53", "11-44", "11-41", "11-34", "11-24", "11-16", "-",    "-",    "-"],
        "pinned":    ["54-66", "45-66", "42-65", "35-64", "25-61", "21-44", "11-35", "11-32", "11-31"],
        "1":         ["-",     "-",     "66",    "65-66", "62-66", "45-66", "36-64", "33-56", "32-55"],
        "2":         ["-",     "-",     "-",     "-",     "-",     "-",     "65-66", "61-66", "56-66"],
    },
    "armor": {
        "no_effect": ["11-62", "11-54", "11-45", "11-36", "11-31", "11-22", "11-14", "-",    "-"],
        "pinned":    ["63-66", "55-66", "46-66", "41-66", "32-66", "23-66", "15-63", "11-62", "11-54"],
        "1":         ["-",     "-",     "-",     "-",     "-",     "-",     "64-66", "63-66", "55-66"],
    },
    "gun": {
        "no_effect": ["11-61", "11-54", "11-46", "11-36", "11-26", "11-22", "11-12", "-",    "-"],
        "1":         ["62-66", "55-66", "51-66", "41-66", "31-66", "23-64", "13-56", "11-54", "11-36"],
        "2":         ["-",     "-",     "-",     "-",     "-",     "65-66", "61-66", "55-66", "41-66"],
    },
    "truck": {
        "no_effect": ["11-66", "11-64", "11-62", "11-56", "11-55", "11-53", "11-46", "11-42", "11-32"],
        "1":         ["-",     "65-66", "63-66", "61-66", "56-63", "54-63", "51-61", "43-61", "33-61"],
        "2":         ["-",     "-",     "-",     "-",     "64-66", "64-66", "62-66", "62-66", "62-66"],
    },
}


def _get_barrage_column(barrage_points: int) -> int:
    """Get the column index (0-8) for a barrage points value."""
    for i, (low, high) in enumerate(BARRAGE_COLUMNS):
        if low <= barrage_points <= high:
            return i
    if barrage_points >= 17:
        return 8
    return 0


def resolve_barrage(
    target_class: str,
    barrage_points: int,
    terrain_shifts: int = 0,
    dice_roll: Optional[int] = None,
) -> BarrageResult:
    """
    Resolve a barrage attack per [12.6].

    Args:
        target_class: "infantry", "armor", "gun", "truck"
        barrage_points: Raw barrage strength points
        terrain_shifts: Column shifts from terrain (negative = left/worse for attacker)
        dice_roll: Override for testing (d66 value), or None for random

    Returns:
        BarrageResult with all details
    """
    raw_bp = barrage_points

    # Apply column shifts by adjusting effective barrage points
    col_idx = _get_barrage_column(barrage_points)
    shifted_col = max(0, min(8, col_idx + terrain_shifts))

    # Map shifted column back to effective barrage points (midpoint of range)
    effective_bp = BARRAGE_COLUMNS[shifted_col][0]

    roll = dice_roll if dice_roll is not None else roll_d66()

    # Look up target class table
    table = BARRAGE_TABLE.get(target_class)
    if not table:
        return BarrageResult(
            target_class=target_class, barrage_points=effective_bp,
            raw_barrage_points=raw_bp, column_shifts=terrain_shifts,
            dice_roll=roll, result="no_effect",
            description=f"Unknown target class '{target_class}'"
        )

    # Check each result row in order of severity (worst first)
    result = "no_effect"
    for result_type in reversed(list(table.keys())):
        ranges = table[result_type]
        if shifted_col < len(ranges):
            if d66_in_range(roll, ranges[shifted_col]):
                result = result_type
                break

    # Determine effects
    sp_lost = 0
    pinned = False
    if result == "pinned":
        pinned = True
    elif result == "1":
        sp_lost = 1
        if target_class == "infantry":
            pinned = True  # Infantry also pinned on "1" result
    elif result == "2":
        sp_lost = 2

    desc_parts = [f"Barrage vs {target_class}: {raw_bp} BP"]
    if terrain_shifts != 0:
        desc_parts.append(f"({terrain_shifts:+d} column shift)")
    desc_parts.append(f"→ col {shifted_col}, roll {roll}")
    desc_parts.append(f"= {result.upper()}")
    if sp_lost:
        desc_parts.append(f"({sp_lost} SP lost)")
    if pinned:
        desc_parts.append("(PINNED)")

    return BarrageResult(
        target_class=target_class,
        barrage_points=effective_bp,
        raw_barrage_points=raw_bp,
        column_shifts=terrain_shifts,
        dice_roll=roll,
        result=result,
        strength_points_lost=sp_lost,
        is_pinned=pinned,
        description=" ".join(desc_parts),
    )


# ════════════════════════════════════════
# ANTI-ARMOR CRT [14.6]
# ════════════════════════════════════════

# Column headers: anti-armor point values 0 through 16+
AA_COLUMNS = list(range(0, 17))  # 0,1,2,...,16  (16 means 16+)

# Rows indexed by d66 dice values; each row gives AP lost per column
# Built from the spreadsheet data
AA_TABLE_ROWS = {
    (11, 12): [0, 0, 0, 1, 2, 3, 4, 6, 8, 10, 11, 12, 14, 16, 18, 20, 22],
    (13, 14): [0, 0, 0, 1, 3, 4, 5, 6, 8, 10, 11, 13, 15, 17, 19, 20, 22],
    (15, 16): [0, 0, 1, 1, 3, 4, 5, 7, 9, 10, 12, 13, 15, 17, 19, 21, 23],
    (21, 22): [0, 0, 1, 2, 4, 5, 6, 8, 9, 11, 12, 14, 16, 18, 20, 21, 23],
    (23, 24): [0, 0, 1, 2, 4, 5, 6, 8, 10, 11, 13, 14, 16, 18, 20, 22, 24],
    (25, 26): [0, 0, 2, 3, 4, 6, 7, 9, 10, 12, 13, 15, 17, 19, 20, 22, 24],
    (31, 32): [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 19, 21, 23, 25],
    (33, 34): [0, 1, 2, 3, 5, 7, 8, 10, 11, 13, 14, 16, 18, 20, 21, 23, 25],
    (35, 36): [0, 1, 3, 4, 5, 7, 9, 10, 12, 13, 15, 16, 18, 20, 22, 24, 26],
    (41, 42): [0, 2, 3, 4, 6, 7, 9, 11, 12, 14, 15, 17, 19, 21, 22, 24, 26],
    (43, 44): [0, 2, 3, 5, 6, 8, 10, 11, 13, 14, 16, 17, 19, 21, 23, 25, 27],
    (45, 46): [0, 2, 4, 5, 7, 8, 10, 12, 13, 15, 16, 18, 20, 22, 23, 25, 27],
    (51, 52): [1, 3, 4, 6, 7, 9, 11, 12, 14, 15, 17, 19, 20, 22, 24, 26, 28],
    (53, 54): [1, 3, 4, 6, 8, 9, 11, 13, 14, 16, 17, 20, 21, 23, 25, 27, 29],
    (55, 56): [1, 3, 5, 7, 8, 10, 12, 13, 15, 16, 19, 21, 22, 24, 26, 28, 30],
    (61, 62): [1, 4, 5, 7, 8, 10, 12, 14, 15, 17, 19, 22, 23, 25, 27, 29, 31],
    (63, 64): [1, 4, 6, 8, 9, 11, 13, 14, 16, 18, 21, 22, 24, 26, 28, 29, 32],
    (65, 66): [2, 4, 6, 8, 9, 11, 13, 15, 17, 19, 21, 23, 24, 26, 28, 30, 32],
}


def _get_aa_dice_row(roll: int) -> tuple:
    """Map a d66 roll to the AA table row key."""
    for (low, high) in AA_TABLE_ROWS:
        if low <= roll <= high:
            return (low, high)
    return (65, 66)  # Max row


def resolve_anti_armor(
    anti_armor_points: int,
    is_phasing: bool = True,
    terrain_column_shifts: int = 0,
    dice_roll: Optional[int] = None,
) -> AntiArmorResult:
    """
    Resolve anti-armor fire per [14.6].

    Args:
        anti_armor_points: Raw anti-armor fire points
        is_phasing: True if firer is the phasing player (shifts dice down one row)
        terrain_column_shifts: Left shifts from terrain/forts (negative = fewer AA pts)
        dice_roll: Override for testing

    Returns:
        AntiArmorResult
    """
    raw_aa = anti_armor_points

    # Apply column shifts
    effective_aa = max(0, min(16, anti_armor_points + terrain_column_shifts))

    roll = dice_roll if dice_roll is not None else roll_d66()

    # Phasing player shifts dice DOWN one row (better for attacker)
    lookup_roll = roll
    if is_phasing:
        # Shift down = lower row = worse result for target
        # In the table, lower dice = fewer losses, so "down" means
        # we shift the roll UP to simulate the phasing advantage
        # Actually per [14.6]: "Phasing Player decreases his dice roll by one row"
        # "An 11 or 12 is unaffected"
        tens = roll // 10
        ones = roll % 10
        if tens > 1:
            lookup_roll = (tens - 1) * 10 + ones
        # Skip invalid rows (x7, x8, x9, x0)
        lt = lookup_roll // 10
        lo = lookup_roll % 10
        if lo > 6:
            lookup_roll = lt * 10 + 6
        if lo < 1:
            lookup_roll = (lt - 1) * 10 + 6 if lt > 1 else 11

    # Look up result
    row_key = _get_aa_dice_row(lookup_roll)
    row_data = AA_TABLE_ROWS.get(row_key, [0] * 17)

    col_idx = min(effective_aa, 16)
    ap_lost = row_data[col_idx] if col_idx < len(row_data) else 0

    desc = (f"Anti-Armor: {raw_aa} AA pts"
            f"{f' ({terrain_column_shifts:+d} terrain)' if terrain_column_shifts else ''}"
            f" → eff {effective_aa}, roll {roll}"
            f"{f' (phasing→{lookup_roll})' if is_phasing and lookup_roll != roll else ''}"
            f" = {ap_lost} AP lost")

    return AntiArmorResult(
        anti_armor_points=effective_aa,
        raw_anti_armor_points=raw_aa,
        column_shifts=terrain_column_shifts,
        dice_roll=roll,
        is_phasing=is_phasing,
        armor_protection_lost=ap_lost,
        description=desc,
    )


# ════════════════════════════════════════
# CLOSE ASSAULT CRT [15.79]
# ════════════════════════════════════════

# Differential column headers (indices 0-17)
CA_DIFF_COLUMNS = [
    -11, -9, -7, -5, -3, -2, -1, 0, 1, 2, 3, 4, 6, 8, 10, 12, 15, 17
]
# Maps: col 0 = -11, col 1 = -8/-9/-10 (use -9 midpoint), etc.
# For lookup: find the column whose range includes the differential

def _get_ca_column(differential: int) -> int:
    """Map a close assault differential to a column index (0-17)."""
    if differential <= -11:
        return 0
    elif differential <= -8:
        return 1
    elif differential <= -6:
        return 2
    elif differential <= -4:
        return 3
    elif differential == -3:
        return 4
    elif differential == -2:
        return 5
    elif differential == -1:
        return 6
    elif differential == 0:
        return 7
    elif differential == 1:
        return 8
    elif differential == 2:
        return 9
    elif differential == 3:
        return 10
    elif differential == 4:
        return 11
    elif differential <= 6:
        return 12
    elif differential <= 8:
        return 13
    elif differential <= 10:
        return 14
    elif differential <= 13:
        return 15
    elif differential <= 16:
        return 16  # Overrun
    else:
        return 17  # +17 et seq


# Attacker loss table: each row = loss%, columns = dice ranges per differential column
CA_ATTACKER_LOSSES = {
    50: ["11-15","11-12","11",   "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-"],
    40: ["16-24","13-16","12-14","11",   "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-"],
    30: ["25-33","21-26","15-23","12-15","11-12","-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-"],
    25: ["34-36","31-34","24-32","16-23","13-16","11-12","11",   "11",   "11",   "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-"],
    20: ["41-46","35-43","33-41","24-33","21-26","13-18","12-13","12",   "12",   "11-12","11-12","11",   "-",    "-",    "-",    "-",    "-",    "-"],
    15: ["51-56","44-53","42-51","34-44","31-36","21-33","14-26","13-24","13-22","13-16","13-16","13-15","12-13","11-12","11",   "-",    "-",    "-"],
    10: ["61-63","54-62","52-56","45-53","41-46","34-44","31-42","25-36","23-33","21-31","21-26","16-25","14-21","13-21","12-16","11-16","11-13","11-12"],
     5: ["64-66","63-65","61-63","54-61","51-56","45-54","43-52","41-51","34-46","32-44","31-42","26-41","22-35","22-33","21-31","21-26","14-21","13-16"],
     0: ["-",    "66",   "64-66","62-66","61-66","55-66","53-66","52-66","51-66","45-66","43-66","42-66","36-66","34-66","32-66","31-66","22-66","21-66"],
}

# Defender loss table
CA_DEFENDER_LOSSES = {
    50: ["-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "11-13","11-16"],
    40: ["-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "11",   "11-12","11-13","14-25","21-26"],
    30: ["-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "11",   "12-14","13-16","14-22","26-33","31-36"],
    25: ["-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "-",    "11",   "11",   "11-12","12-14","15-22","21-26","23-33","34-43","41-46"],
    20: ["-",    "-",    "-",    "-",    "11",   "11",   "11-12","11-13","12-13","12-14","13-16","15-23","23-31","31-41","44-55","51-56","-",    "-"],
    15: ["-",    "-",    "-",    "11",   "11-13","12-13","12-16","13-23","14-22","14-23","15-25","21-33","24-36","32-45","42-56","51-62","56-63","61-63"],
    10: ["11",   "11",   "11-13","12-15","14-21","14-23","21-26","24-32","24-33","26-42","24-45","34-45","41-54","46-61","61-63","63-64","64-65","64-65"],
     5: ["12-16","12-23","14-26","16-31","22-41","24-44","31-46","33-51","34-51","41-52","43-54","46-56","55-62","62-64","64-65","65",   "65-66","66"],
     0: ["21-66","24-66","31-66","32-66","42-66","45-66","51-66","52-66","52-66","53-66","55-66","61-66","63-66","65-66","66",   "66",   "-",    "-"],
}

# Defender retreat ranges (dice sum range for retreat results)
CA_DEFENDER_RETREAT_1 = ["-","-","-","-","-","-","-","-","5-6","4-6","5-7","4-7","3-7","7-9","7-9","4-7","3-4","2-9"]
CA_DEFENDER_RETREAT_2 = ["-","-","-","-","-","-","-","-","-",  "-",  "-",  "11", "11", "5",  "5",  "8",  "12", "8"]
CA_DEFENDER_RETREAT_3 = ["-","-","-","-","-","-","-","-","-",  "-",  "-",  "-",  "-",  "-",  "-",  "-",  "11", "12"]


def _find_loss_percent(table: dict, col_idx: int, roll: int) -> int:
    """Find the loss percentage from a CA table given column and roll."""
    # Check from highest loss down
    for pct in sorted(table.keys(), reverse=True):
        ranges = table[pct]
        if col_idx < len(ranges) and d66_in_range(roll, ranges[col_idx]):
            return pct
    return 0


def _check_retreat(col_idx: int, roll: int) -> int:
    """Check if defender must retreat and how many hexes. Uses d6+d6 sum."""
    dice_sum = (roll // 10) + (roll % 10)  # Sum the two dice

    def _in_sum_range(range_str, s):
        if not range_str or range_str.strip() in ("-", "—", ""):
            return False
        for part in range_str.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-")
                if int(lo) <= s <= int(hi):
                    return True
            elif part.isdigit() and int(part) == s:
                return True
        return False

    if col_idx < len(CA_DEFENDER_RETREAT_3) and _in_sum_range(CA_DEFENDER_RETREAT_3[col_idx], dice_sum):
        return 3
    if col_idx < len(CA_DEFENDER_RETREAT_2) and _in_sum_range(CA_DEFENDER_RETREAT_2[col_idx], dice_sum):
        return 2
    if col_idx < len(CA_DEFENDER_RETREAT_1) and _in_sum_range(CA_DEFENDER_RETREAT_1[col_idx], dice_sum):
        return 1
    return 0


def resolve_close_assault(
    attacker_strength: int,
    defender_strength: int,
    terrain_shift: int = 0,
    all_defenders_pinned: bool = False,
    dice_roll: Optional[int] = None,
) -> CloseAssaultResult:
    """
    Resolve close assault per [15.79].

    Args:
        attacker_strength: Total attacker close assault strength
        defender_strength: Total defender close assault strength
        terrain_shift: Column shifts from terrain (negative = shifts left / worse for attacker)
        all_defenders_pinned: If True, defender strength = 0 and +2 column shift right [15.56]
        dice_roll: Override for testing

    Returns:
        CloseAssaultResult
    """
    if all_defenders_pinned:
        defender_strength = 0
        terrain_shift += 2  # Two column shift right per [15.56] errata

    differential = attacker_strength - defender_strength

    # Apply terrain shifts to differential
    # Each column shift roughly = ±1 to differential at small values,
    # but the CRT columns are non-linear. We shift columns directly.
    col_idx = _get_ca_column(differential)
    shifted_col = max(0, min(17, col_idx + terrain_shift))

    roll = dice_roll if dice_roll is not None else roll_d66()

    # Look up attacker losses
    atk_loss_pct = _find_loss_percent(CA_ATTACKER_LOSSES, shifted_col, roll)

    # Look up defender losses
    def_loss_pct = _find_loss_percent(CA_DEFENDER_LOSSES, shifted_col, roll)

    # Check retreat
    retreat_hexes = _check_retreat(shifted_col, roll)

    # Overrun check
    is_overrun = shifted_col >= 16  # +14 or higher

    # Build description
    desc = (f"Close Assault: Atk {attacker_strength} vs Def {defender_strength}"
            f" (diff {differential:+d})"
            f"{f' terrain {terrain_shift:+d}' if terrain_shift else ''}"
            f"{' ALL PINNED' if all_defenders_pinned else ''}"
            f" → col {shifted_col}, roll {roll}"
            f" = Atk loses {atk_loss_pct}%, Def loses {def_loss_pct}%")
    if retreat_hexes:
        desc += f", Def retreats {retreat_hexes} hex(es)"
    if is_overrun:
        desc += " (OVERRUN)"

    return CloseAssaultResult(
        differential=differential,
        attacker_strength=attacker_strength,
        defender_strength=defender_strength,
        dice_roll=roll,
        attacker_loss_percent=atk_loss_pct,
        defender_loss_percent=def_loss_pct,
        defender_retreat_hexes=retreat_hexes,
        is_overrun=is_overrun,
        description=desc,
    )
