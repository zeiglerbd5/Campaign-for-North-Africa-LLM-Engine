"""
CNA Action Notation — chess-notation-style compact event notation.

Converts raw event log entries into compact, human-readable action strings.
Each event type maps to a symbol and format:

    movement:       2RTR B2810->B2910 (2CP)
    close_assault:  2RTR,7Hus x 1LibHQ@B3010 [+5] d43: A-10% D-25% RET1
    barrage_result: ~B3110 8BP: PIN
    anti_armor_result: >>B3110 6AA: -8AP
    bombardment:    SM79/1 *B2610: PIN
    recon:          CR42/1 ?B2410: 8units
    ...

Usage:
    from cna_engine.tools.notation import notate_event, format_turn_transcript
"""
from __future__ import annotations

import re
from typing import Any

# ════════════════════════════════════════════════════════════════════
# UNIT / AIRCRAFT ABBREVIATION TABLES
# ════════════════════════════════════════════════════════════════════

UNIT_ABBREVS: dict[str, str] = {
    # Commonwealth
    "cw_2rtr": "2RTR",
    "cw_11hus": "11Hus",
    "cw_7hus": "7Hus",
    "cw_3hus": "3Hus",
    "cw_1rha": "1RHA",
    "cw_1_6raj": "1/6Raj",
    "cw_3_1pun": "3/1Pun",
    "cw_4ind_hq": "4IndHQ",
    "cw_7arm_hq": "7ArmHQ",
    "cw_8fd_engr": "8Engr",
    "cw_rasc_trucks": "RASC",
    "cw_1rhy_art": "1FldRA",
    "cw_6aus_1bn": "6Aus/1",
    "cw_6aus_2bn": "6Aus/2",
    # Italian
    "it_1lib_hq": "1LibHQ",
    "it_1lib_1bn": "1Lib/1",
    "it_1lib_2bn": "1Lib/2",
    "it_1lib_art": "1LibArt",
    "it_2lib_hq": "2LibHQ",
    "it_2lib_1bn": "2Lib/1",
    "it_2lib_2bn": "2Lib/2",
    "it_maletti_hq": "MalettiHQ",
    "it_maletti_inf": "MalettiI",
    "it_m11_plt": "M11/39",
    "it_autogrp": "AutoGrp",
    "it_eng_co": "ItEngr",
    # DAK
    "dak_5lt_1bn": "5Lt/I",
    "dak_5lt_2bn": "5Lt/II",
    "dak_5lt_mg": "5LtMG",
    "dak_5pz_1": "5Pz/I",
    "dak_recon_bn": "DAKRec",
    "dak_15pzgren": "15PzGr",
    "dak_15pz_1bn": "15Pz/I",
    "dak_15pz_2bn": "15Pz/II",
}

AIRCRAFT_ABBREVS: dict[str, str] = {
    # Regia Aeronautica
    "ra_sm79_1sqn": "SM79/1",
    "ra_sm79_2sqn": "SM79/2",
    "ra_sm79_3sqn": "SM79/3",
    "ra_sm79_4sqn": "SM79/4",
    "ra_sm79_5sqn": "SM79/5",
    "ra_sm79_6sqn": "SM79/6",
    "ra_sm79_7sqn": "SM79/7",
    "ra_sm79_8sqn": "SM79/8",
    "ra_cr42_1sqn": "CR42/1",
    "ra_cr42_2sqn": "CR42/2",
    "ra_cr32_1sqn": "CR32/1",
    "ra_cr32_2sqn": "CR32/2",
    # Luftwaffe
    "lw_bf109e_1sqn": "Bf109/1",
    "lw_bf109e_2sqn": "Bf109/2",
    "lw_bf110_1sqn": "Bf110/1",
    "lw_ju87b_1sqn": "Ju87/1",
    "lw_ju87b_2sqn": "Ju87/2",
    "lw_ju88a_1sqn": "Ju88/1",
    "lw_ju88a_2sqn": "Ju88/2",
    # RAF
    "raf_274sqn_hurr": "274Hurr",
    "raf_33sqn_glad": "33Glad",
    "raf_80sqn_glad": "80Glad",
    "raf_112sqn_glad": "112Glad",
    "raf_113sqn_blen": "113Blen",
    "raf_211sqn_blen": "211Blen",
    "raf_45sqn_blen": "45Blen",
    "raf_55sqn_blen": "55Blen",
}


def abbrev_unit(unit_id: str) -> str:
    """Return compact abbreviation for a unit ID, with fallback algorithm."""
    if unit_id in UNIT_ABBREVS:
        return UNIT_ABBREVS[unit_id]
    if unit_id in AIRCRAFT_ABBREVS:
        return AIRCRAFT_ABBREVS[unit_id]
    # Fallback: strip common prefixes, keep numbers + key abbreviations
    s = unit_id
    for prefix in ("cw_", "it_", "dak_", "ra_", "lw_", "raf_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Remove long words, keep initials
    s = s.replace("_", "")
    return s[:10].upper() if s else unit_id[:10].upper()


def abbrev_aircraft(aircraft_id: str) -> str:
    """Return compact abbreviation for an aircraft ID."""
    if aircraft_id in AIRCRAFT_ABBREVS:
        return AIRCRAFT_ABBREVS[aircraft_id]
    return abbrev_unit(aircraft_id)


def _unit_ids_str(ids: list[str]) -> str:
    """Comma-separated abbreviated unit IDs."""
    return ",".join(abbrev_unit(uid) for uid in ids)


# ════════════════════════════════════════════════════════════════════
# SIDE DETECTION
# ════════════════════════════════════════════════════════════════════

def _infer_side_from_unit(unit_id: str) -> str | None:
    """Infer side from unit ID prefix."""
    if unit_id.startswith("cw_") or unit_id.startswith("raf_"):
        return "allied"
    if unit_id.startswith(("it_", "dak_", "ra_", "lw_")):
        return "axis"
    return None


def _infer_side_from_aircraft(aircraft_id: str) -> str | None:
    if aircraft_id.startswith("raf_"):
        return "allied"
    if aircraft_id.startswith(("ra_", "lw_")):
        return "axis"
    return None


# ════════════════════════════════════════════════════════════════════
# DESCRIPTION PARSERS (for events that lack structured fields)
# ════════════════════════════════════════════════════════════════════

_DISORG_RE = re.compile(r"disorg (\d+) .+ (\d+)(?:\s*\((.+)\))?")
_DISORG_RECOVERY_RE = re.compile(r"disorg (\d+) .+ (\d+)")
_BOMBARDMENT_RE = re.compile(
    r"Bombardment by .+ on .+: (\d+) bombs = (\d+) BP .+ (PINNED|NO EFFECT|[\d]+ SP)"
)
_RECON_RE = re.compile(r"(\d+) enemy units spotted")
_STORES_RE = re.compile(r"Water consumed=([\d.]+), Stores consumed=([\d.]+)")
_SURRENDER_RE = re.compile(r"cohesion (-?\d+)")
_BREAKDOWN_RE = re.compile(r"roll=(\d+).+threshold=(\d+)")


# ════════════════════════════════════════════════════════════════════
# PER-EVENT NOTATORS
# ════════════════════════════════════════════════════════════════════

def _notate_movement(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    frm = e.get("from_hex", "?")
    to = e.get("to_hex", "?")
    # Extract CP from description
    cp_match = re.search(r"\(([\d.]+)\s*CP\)", e.get("description", ""))
    cp = cp_match.group(1) if cp_match else "?"
    # Remove trailing .0 from CP
    if cp.endswith(".0"):
        cp = cp[:-2]
    suffix = ""
    desc = e.get("description", "")
    if "EZOC" in desc:
        suffix += " [EZOC]"
    if "broke contact" in desc:
        suffix += " [~C]"
    if "minefield" in desc.lower():
        suffix += " [MINE]"
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} {frm}->{to} ({cp}CP){suffix}"


def _notate_close_assault(e: dict) -> tuple[str | None, str]:
    atk_ids = e.get("atk_ids", [])
    def_ids = e.get("def_ids", [])
    target = e.get("target_hex", "?")
    diff = e.get("differential", 0)
    roll = e.get("dice_roll", "?")
    atk_pct = e.get("atk_loss_pct", 0)
    def_pct = e.get("def_loss_pct", 0)
    retreat = e.get("retreat_hexes", 0)
    overrun = e.get("is_overrun", False)

    diff_str = f"+{diff}" if diff >= 0 else str(diff)
    parts = [f"{_unit_ids_str(atk_ids)} x {_unit_ids_str(def_ids)}@{target}"]
    parts.append(f"[{diff_str}] d{roll}: A-{atk_pct}% D-{def_pct}%")
    if retreat:
        parts.append(f"RET{retreat}")
    if overrun:
        parts.append("OVERRUN")

    # Side = whoever is attacking (first attacker)
    side = _infer_side_from_unit(atk_ids[0]) if atk_ids else None
    return side, " ".join(parts)


def _notate_barrage(e: dict) -> tuple[str | None, str]:
    target = e.get("target_hex", "?")
    bp = e.get("bp", "?")
    roll = e.get("dice_roll")
    sp_lost = e.get("sp_lost", 0)
    pinned = e.get("is_pinned", False)

    result_parts = []
    if pinned:
        result_parts.append("PIN")
    if sp_lost:
        result_parts.append(f"-{sp_lost}SP")
    if not result_parts:
        result_parts.append("NE")
    result_str = " ".join(result_parts)

    notation = f"~{target} {bp}BP"
    if roll is not None:
        notation += f" d{roll}"
    notation += f": {result_str}"

    # Can't reliably infer side from barrage alone; return None
    return None, notation


def _notate_anti_armor(e: dict) -> tuple[str | None, str]:
    target = e.get("target_hex", "?")
    aa = e.get("aa_points", "?")
    roll = e.get("dice_roll")
    ap = e.get("ap_destroyed", 0)
    overflow = e.get("overflow_sp", 0)

    parts = [f">>{target} {aa}AA"]
    if roll is not None:
        parts.append(f"d{roll}:")
    else:
        parts.append(":")
    result_parts = []
    if ap:
        result_parts.append(f"-{ap}AP")
    if overflow:
        result_parts.append(f"-{overflow}SP")
    if not result_parts:
        result_parts.append("NE")
    parts.append(" ".join(result_parts))

    return None, " ".join(parts)


def _notate_bombardment(e: dict) -> tuple[str | None, str]:
    ac = e.get("aircraft_id", "?")
    target = e.get("target_hex", "?")
    desc = e.get("description", "")

    # Parse result from description
    m = _BOMBARDMENT_RE.search(desc)
    if m:
        bp = m.group(2)
        raw_result = m.group(3)
        if raw_result == "PINNED":
            result = "PIN"
        elif raw_result == "NO EFFECT":
            result = "NE"
        else:
            result = raw_result
        notation = f"{abbrev_aircraft(ac)} *{target} {bp}BP: {result}"
    else:
        # Fallback: extract what we can
        notation = f"{abbrev_aircraft(ac)} *{target}"

    return _infer_side_from_aircraft(ac), notation


def _notate_recon(e: dict) -> tuple[str | None, str]:
    ac = e.get("aircraft_id", "?")
    target = e.get("target_hex", "?")
    units = e.get("units_spotted", [])
    n = len(units)
    return _infer_side_from_aircraft(ac), f"{abbrev_aircraft(ac)} ?{target}: {n}units"


def _notate_cohesion(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    old = e.get("old", "?")
    new = e.get("new", "?")
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} C{old}->{new}"


def _notate_disorganization(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    desc = e.get("description", "")
    m = _DISORG_RE.search(desc)
    if m:
        old, new, reason = m.group(1), m.group(2), m.group(3) or ""
        reason_str = f" ({reason})" if reason else ""
        return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} D{old}->{new}{reason_str}"
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} D+"


def _notate_disorg_recovery(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    desc = e.get("description", "")
    m = _DISORG_RECOVERY_RE.search(desc)
    if m:
        old, new = m.group(1), m.group(2)
        return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} D+{old}->{new}"
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} D+"


def _notate_initiative(e: dict) -> tuple[str | None, str]:
    a_roll = e.get("allied_roll", "?")
    x_roll = e.get("axis_roll", "?")
    winner = (e.get("winner") or "?").upper()
    return None, f"I: A{a_roll} X{x_roll}->{winner}"


def _notate_weather(e: dict) -> tuple[str | None, str]:
    season = (e.get("season") or "?").capitalize()
    roll = e.get("roll", "?")
    weather = (e.get("weather") or "?").upper()
    return None, f"W: {season} r{roll}->{weather}"


def _notate_stores(e: dict) -> tuple[str | None, str]:
    desc = e.get("description", "")
    m = _STORES_RE.search(desc)
    if m:
        w, s = m.group(1), m.group(2)
        return None, f"$: W={w} S={s}"
    return None, "$: (see log)"


def _notate_draw_from_dump(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    dump_id = e.get("dump_id", "?")
    # Shorten dump name
    dump_short = dump_id.replace("allied_", "").replace("axis_", "").replace("_", " ").title()
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} <{dump_short}"


def _notate_surrender(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    desc = e.get("description", "")
    m = _SURRENDER_RE.search(desc)
    coh = m.group(1) if m else "?"
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} ! (C{coh})"


def _notate_breakdown(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    desc = e.get("description", "")
    m = _BREAKDOWN_RE.search(desc)
    if m:
        roll, thresh = m.group(1), m.group(2)
        return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} BK r{roll}>={thresh}"
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} BK"


def _notate_patrol(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    target = e.get("target_hex") or e.get("to_hex", "?")
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} PT->{target}"


def _notate_reinforcement(e: dict) -> tuple[str | None, str]:
    uid = e.get("unit_id", "")
    desc = e.get("description", "")
    return _infer_side_from_unit(uid), f"{abbrev_unit(uid)} REINF"


def _notate_replacement(e: dict) -> tuple[str | None, str]:
    desc = e.get("description", "")
    # Just pass through as a shared event
    return None, f"REPL: {desc[:60]}"


# Event type -> notator function
_NOTATORS: dict[str, Any] = {
    "movement": _notate_movement,
    "close_assault": _notate_close_assault,
    "barrage_result": _notate_barrage,
    "anti_armor_result": _notate_anti_armor,
    "bombardment": _notate_bombardment,
    "recon": _notate_recon,
    "cohesion_change": _notate_cohesion,
    "disorganization": _notate_disorganization,
    "disorg_recovery": _notate_disorg_recovery,
    "initiative": _notate_initiative,
    "weather": _notate_weather,
    "stores_expenditure": _notate_stores,
    "draw_from_dump": _notate_draw_from_dump,
    "surrender": _notate_surrender,
    "breakdown": _notate_breakdown,
    "patrol": _notate_patrol,
    "reinforcement": _notate_reinforcement,
    "replacement": _notate_replacement,
}

# Events to skip (internal bookkeeping, not interesting for notation)
_SKIP_EVENTS = {
    "scenario_load", "phase_advance", "organization_phase",
    "replacement_phase", "repair_phase", "air_phase", "convoy_phase",
    "mission_assign", "repair", "victory_assessment",
}


# ════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════

def notate_event(event: dict) -> tuple[str | None, str | None]:
    """
    Convert a single event dict to (side, notation_string).

    Returns (None, None) for events that should be skipped.
    ``side`` is "allied", "axis", or None (for shared events like weather).
    """
    etype = event.get("type", "")
    if etype in _SKIP_EVENTS:
        return None, None

    notator = _NOTATORS.get(etype)
    if notator is None:
        return None, None

    return notator(event)


def format_turn_transcript(
    events: list[dict],
    turn_info: dict,
) -> str:
    """
    Format one game turn's events into structured text notation.

    ``turn_info`` should contain keys: gt, date, season, weather.
    Events are grouped by op_stage, then split into shared / allied / axis.
    """
    gt = turn_info.get("gt", "?")
    date = turn_info.get("date", "")

    lines: list[str] = []
    lines.append(f"=== GT{gt} ({date}) ===")

    # Group events by op_stage
    by_stage: dict[int, list[dict]] = {}
    for e in events:
        stage = e.get("op_stage", 0)
        by_stage.setdefault(stage, []).append(e)

    for stage in sorted(by_stage.keys()):
        stage_events = by_stage[stage]
        lines.append(f"--- OS{stage} ---")

        shared: list[str] = []
        allied: list[str] = []
        axis: list[str] = []

        for e in stage_events:
            side, notation = notate_event(e)
            if notation is None:
                continue
            if side == "allied":
                allied.append(notation)
            elif side == "axis":
                axis.append(notation)
            else:
                shared.append(notation)

        for s in shared:
            lines.append(s)

        if shared and (allied or axis):
            lines.append("")

        if axis:
            lines.append("  AXIS:")
            for i, a in enumerate(axis, 1):
                lines.append(f"    {i}. {a}")

        if allied:
            lines.append("  ALLIED:")
            for i, a in enumerate(allied, 1):
                lines.append(f"    {i}. {a}")

        lines.append("")

    return "\n".join(lines)


def format_game_header(
    scenario: str = "Operation Compass",
    turns: str = "",
    allied_player: str = "AI",
    axis_player: str = "AI",
    result: str = "*",
) -> str:
    """PGN-style metadata header for a .cna transcript file."""
    header_lines = [
        f'[Scenario "{scenario}"]',
        f'[Turns "{turns}"]',
        f'[Allied "{allied_player}"]',
        f'[Axis "{axis_player}"]',
        f'[Result "{result}"]',
        "",
    ]
    return "\n".join(header_lines)
