"""
CNA Engine — HTML Log Sheet Exporter

Generates per-turn HTML log sheets mirroring the original CNA paper log sheets.
Reads save files (saves/gtN.json) and optional JSONL game logs for AI audit trails.

Usage:
    python -m cna_engine.tools.export_logsheets saves/gt11.json
    python -m cna_engine.tools.export_logsheets saves/gt11.json --log logs/game_*.jsonl
    python -m cna_engine.tools.export_logsheets --batch saves/ --log logs/game_*.jsonl --out exports/
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import re
import webbrowser
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# CONSTANTS (duplicated from engine to avoid imports)
# ════════════════════════════════════════════════════════════════════

OBJECTIVE_HEXES: dict[str, tuple[int, str]] = {
    "E1326": (5, "Alexandria"),
    "D1822": (3, "Mersa Matruh"),
    "D0821": (3, "Sidi Barrani"),
    "C1714": (2, "Sollum"),
    "C1215": (3, "Bardia"),
    "C0512": (5, "Tobruk"),
    "B3405": (2, "Gazala"),
    "B2703": (3, "Derna"),
    "B1403": (5, "Benghazi"),
    "B0406": (2, "Agedabia"),
    "A1437": (3, "El Agheila"),
    "A2438": (2, "Sirte"),
    "A3117": (2, "Misurata"),
    "A3511": (5, "Tripoli"),
}

VP_PER_SP_DESTROYED = 0.5
DECISIVE_MARGIN = 15
MARGINAL_MARGIN = 5

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# ════════════════════════════════════════════════════════════════════
# DATA EXTRACTION (pure JSON, no engine imports)
# ════════════════════════════════════════════════════════════════════

def gt_to_date(gt: int) -> str:
    """Convert game turn number to date string. GT1 = Sep 1940 Wk1, 4 GTs/month."""
    base_month = 8  # September = index 8 (0-based)
    base_year = 1940
    month_offset = (gt - 1) // 4
    week = ((gt - 1) % 4) + 1
    total_month = base_month + month_offset
    year = base_year + total_month // 12
    month = total_month % 12
    return f"{MONTH_NAMES[month]} {year} Wk{week}"


def _esc(text) -> str:
    """HTML-escape a value."""
    return html.escape(str(text)) if text is not None else ""


def load_save(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_turn_info(data: dict) -> dict:
    turn = data.get("turn", {})
    gt = turn.get("game_turn", 0)
    meta = data.get("_metadata", {})
    return {
        "gt": gt,
        "date": meta.get("date", gt_to_date(gt)),
        "op_stage": turn.get("op_stage", 1),
        "phase": turn.get("phase", ""),
        "sub_phase": turn.get("sub_phase", ""),
        "weather": turn.get("current_weather", "unknown"),
        "season": turn.get("current_season", "unknown"),
        "initiative_side": turn.get("initiative_side"),
        "allied_init": turn.get("allied_initiative_rating", 0),
        "axis_init": turn.get("axis_initiative_rating", 0),
    }


def extract_units_by_side(data: dict) -> dict[str, list[dict]]:
    allied, axis = [], []
    for u in data.get("units", {}).values():
        (allied if u.get("side") == "allied" else axis).append(u)
    allied.sort(key=lambda u: (u.get("parent_formation_id", ""), u.get("id", "")))
    axis.sort(key=lambda u: (u.get("parent_formation_id", ""), u.get("id", "")))
    return {"allied": allied, "axis": axis}


def _build_formation_lookup(data: dict) -> dict[str, dict]:
    """Map formation_id → formation dict."""
    return data.get("formations", {})


def extract_formation_tree(data: dict, side: str) -> list[dict]:
    """Build nested formation hierarchy for a side.
    Returns list of top-level formations with 'children' and 'units' populated.
    """
    formations = data.get("formations", {})
    units = data.get("units", {})

    # Build lookup
    fm = {}
    for fid, f in formations.items():
        if f.get("side") == side:
            fm[fid] = {**f, "children": [], "units_list": []}

    # Attach units to their parent formation
    for uid, u in units.items():
        if u.get("side") != side:
            continue
        pfid = u.get("attached_to_id") or u.get("parent_formation_id")
        if pfid and pfid in fm:
            fm[pfid]["units_list"].append(u)

    # Build tree
    roots = []
    for fid, f in fm.items():
        parent = f.get("parent_formation_id")
        if parent and parent in fm:
            fm[parent]["children"].append(f)
        else:
            roots.append(f)

    return roots


def extract_supply(data: dict) -> dict:
    units = data.get("units", {})
    hexes_data = data.get("hexes", {})

    # Per-unit supply
    unit_supply = []
    for u in units.values():
        s = u.get("supply", {})
        cap_total = sum(s.get(f"{k}_capacity", 0) for k in ("fuel", "water", "ammo", "stores"))
        cur_total = sum(s.get(k, 0) for k in ("fuel", "water", "ammo", "stores"))
        fill_pct = (cur_total / cap_total * 100) if cap_total > 0 else 0
        unit_supply.append({
            "id": u.get("id"),
            "name": u.get("name"),
            "side": u.get("side"),
            "hex_id": u.get("hex_id"),
            "fuel": s.get("fuel", 0),
            "water": s.get("water", 0),
            "ammo": s.get("ammo", 0),
            "stores": s.get("stores", 0),
            "fuel_cap": s.get("fuel_capacity", 0),
            "water_cap": s.get("water_capacity", 0),
            "ammo_cap": s.get("ammo_capacity", 0),
            "stores_cap": s.get("stores_capacity", 0),
            "fill_pct": fill_pct,
            "truck_pts": u.get("attached_truck_points", 0),
            "truck_fuel": u.get("truck_cargo_fuel", 0),
            "truck_water": u.get("truck_cargo_water", 0),
            "truck_ammo": u.get("truck_cargo_ammo", 0),
            "truck_stores": u.get("truck_cargo_stores", 0),
        })

    # Supply dumps from hexes
    dumps = []
    for hid, hx in hexes_data.items():
        for d in hx.get("supply_dumps", []):
            total = d.get("fuel", 0) + d.get("water", 0) + d.get("ammo", 0) + d.get("stores", 0)
            if total > 0 or d.get("is_real"):
                dumps.append({
                    "hex_id": hid,
                    "dump_id": d.get("id", ""),
                    "side": d.get("side", ""),
                    "fuel": d.get("fuel", 0),
                    "water": d.get("water", 0),
                    "ammo": d.get("ammo", 0),
                    "stores": d.get("stores", 0),
                })

    return {
        "allied_pool": data.get("allied_supply_in_egypt", {}),
        "axis_pool": data.get("axis_supply_in_tripoli_boxes", {}),
        "allied_repl": data.get("allied_replacement_pool", {}),
        "axis_repl": data.get("axis_replacement_pool", {}),
        "unit_supply": sorted(unit_supply, key=lambda x: (x["side"], x["id"])),
        "dumps": sorted(dumps, key=lambda x: (x["side"], x["hex_id"])),
    }


def extract_vp(data: dict, gt: int) -> dict:
    """Compute VP from save data (no engine imports)."""
    units = data.get("units", {})
    hexes_data = data.get("hexes", {})

    # Objective control
    objectives = []
    for hex_id, (vp_val, name) in OBJECTIVE_HEXES.items():
        hx = hexes_data.get(hex_id, {})
        a_ids = hx.get("allied_unit_ids", [])
        x_ids = hx.get("axis_unit_ids", [])
        if a_ids and not x_ids:
            ctrl = "allied"
        elif x_ids and not a_ids:
            ctrl = "axis"
        else:
            ctrl = None
        objectives.append({
            "hex_id": hex_id, "name": name, "vp_value": vp_val,
            "controller": ctrl,
            "allied_units": len(a_ids), "axis_units": len(x_ids),
        })

    # Objective VP per side
    allied_obj = sum(o["vp_value"] for o in objectives if o["controller"] == "allied")
    axis_obj = sum(o["vp_value"] for o in objectives if o["controller"] == "axis")

    # Destruction VP: each side earns VP for enemy losses
    allied_losses = sum(u.get("losses_taken", 0) for u in units.values() if u.get("side") == "allied")
    axis_losses = sum(u.get("losses_taken", 0) for u in units.values() if u.get("side") == "axis")
    allied_dest = round(axis_losses * VP_PER_SP_DESTROYED, 1)  # Allied earns for Axis losses
    axis_dest = round(allied_losses * VP_PER_SP_DESTROYED, 1)

    allied_total = allied_obj + allied_dest
    axis_total = axis_obj + axis_dest
    margin = allied_total - axis_total
    abs_m = abs(margin)

    if abs_m >= DECISIVE_MARGIN:
        result = f"decisive_{'allied' if margin > 0 else 'axis'}"
    elif abs_m >= MARGINAL_MARGIN:
        result = f"marginal_{'allied' if margin > 0 else 'axis'}"
    else:
        result = "draw"

    # Also check event_log for most recent victory_assessment
    va_event = None
    for evt in reversed(data.get("event_log", [])):
        if evt.get("type") == "victory_assessment" and evt.get("gt", evt.get("game_turn")) == gt:
            va_event = evt
            break

    return {
        "objectives": objectives,
        "allied": {"objective_vp": allied_obj, "destruction_vp": allied_dest, "total": allied_total},
        "axis": {"objective_vp": axis_obj, "destruction_vp": axis_dest, "total": axis_total},
        "margin": margin,
        "result": result.replace("_", " ").title(),
        "va_event": va_event,
    }


def extract_events(data: dict, gt: int) -> list[dict]:
    return [e for e in data.get("event_log", []) if e.get("gt") == gt]


def extract_orders(jsonl_paths: list[str], gt: int) -> list[dict]:
    """Parse JSONL game logs, filter phase entries for the given GT."""
    entries = []
    for path in jsonl_paths:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "phase" and entry.get("game_turn") == gt:
                        entries.append(entry)
        except (OSError, IOError):
            continue
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


# ════════════════════════════════════════════════════════════════════
# CSS
# ════════════════════════════════════════════════════════════════════

CSS = """
:root {
    --bg: #faf7f2;
    --header-bg: #2c3e50;
    --allied: #1a5276;
    --axis: #7b241c;
    --border: #333;
    --stripe: #f0ece4;
    --red: #e74c3c;
    --orange: #e67e22;
    --yellow: #f9e79f;
    --green: #27ae60;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: var(--bg);
    color: #222;
    font-size: 11px;
    line-height: 1.4;
}
.nav {
    position: sticky; top: 0; z-index: 100;
    background: var(--header-bg); color: #fff;
    padding: 6px 16px; display: flex; gap: 12px; flex-wrap: wrap;
    font-size: 11px; align-items: center;
}
.nav a { color: #aed6f1; text-decoration: none; }
.nav a:hover { color: #fff; text-decoration: underline; }
.nav .title { font-weight: bold; font-size: 13px; margin-right: 12px; }
.sheet {
    page-break-before: always;
    padding: 20px 24px;
    max-width: 1200px;
    margin: 0 auto;
}
.sheet:first-of-type { page-break-before: avoid; }
h2 {
    font-size: 16px;
    border-bottom: 2px solid var(--header-bg);
    padding-bottom: 4px;
    margin-bottom: 12px;
    color: var(--header-bg);
}
h3 {
    font-size: 13px;
    margin: 14px 0 6px 0;
    color: #555;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 14px;
    font-size: 10px;
}
th {
    background: var(--header-bg);
    color: #fff;
    padding: 4px 6px;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
}
td {
    padding: 3px 6px;
    border-bottom: 1px solid #ddd;
    font-family: 'Courier New', Courier, monospace;
    vertical-align: top;
}
tr:nth-child(even) td { background: var(--stripe); }
.allied-header { background: var(--allied); }
.axis-header { background: var(--axis); }
.formation-row td {
    background: #d5dbdb !important;
    font-family: 'Segoe UI', sans-serif;
    font-weight: bold;
    font-size: 11px;
    padding: 5px 6px;
    border-top: 2px solid var(--border);
}
.destroyed td { background: #fadbd8 !important; color: #922b21; }
.pinned td { background: var(--yellow) !important; }
.in-contact td { font-weight: bold; }
.fill-critical { color: var(--red); font-weight: bold; }
.fill-low { color: var(--orange); font-weight: bold; }
.fill-ok { color: var(--green); }
.success { color: var(--green); }
.error { color: var(--red); font-weight: bold; }
.summary-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
}
.summary-box {
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 10px;
    background: #fff;
}
.summary-box h3 { margin-top: 0; }
.cover-header {
    text-align: center;
    padding: 30px 0 20px 0;
    border-bottom: 3px double var(--header-bg);
    margin-bottom: 20px;
}
.cover-header h1 { font-size: 22px; color: var(--header-bg); }
.cover-header .date { font-size: 16px; color: #666; margin-top: 4px; }
.cover-header .meta { font-size: 12px; color: #888; margin-top: 2px; }
.vp-allied { color: var(--allied); font-weight: bold; }
.vp-axis { color: var(--axis); font-weight: bold; }
.badge {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 9px; font-weight: bold; color: #fff;
}
.badge-allied { background: var(--allied); }
.badge-axis { background: var(--axis); }
.badge-draw { background: #7f8c8d; }
details { margin-bottom: 8px; }
summary {
    cursor: pointer; font-weight: bold; font-size: 11px;
    padding: 4px 0; color: var(--header-bg);
}
.order-params { color: #666; font-size: 9px; }
.transcript-shared {
    font-family: 'Courier New', Courier, monospace;
    font-size: 11px; margin: 4px 0; padding: 2px 6px;
    background: #eee; border-left: 3px solid var(--header-bg);
}
.transcript-side { margin: 6px 0 10px 0; }
.transcript-side h4 {
    font-size: 11px; margin: 0 0 4px 0; padding: 2px 6px;
    color: #fff; display: inline-block; border-radius: 2px;
}
.transcript-side h4.allied-label { background: var(--allied); }
.transcript-side h4.axis-label { background: var(--axis); }
.notation-list {
    list-style: none; padding: 0; margin: 0 0 0 12px;
    font-family: 'Courier New', Courier, monospace; font-size: 10px;
}
.notation-list li {
    padding: 1px 4px; margin: 1px 0;
}
.notation-list li:nth-child(even) { background: var(--stripe); }

@media print {
    body { background: #fff; font-size: 8pt; }
    .nav { display: none; }
    .sheet { padding: 10px; page-break-before: always; }
    .sheet:first-of-type { page-break-before: avoid; }
    table { font-size: 7pt; }
    th { padding: 2px 4px; }
    td { padding: 2px 4px; }
    .cover-header { padding: 10px 0; }
}
"""

# ════════════════════════════════════════════════════════════════════
# HTML RENDERING
# ════════════════════════════════════════════════════════════════════

def _strength_pct(unit: dict) -> int:
    toe = unit.get("toe_strength", {})
    cur = unit.get("current_strength", {})
    toe_total = sum(toe.values())
    cur_total = sum(cur.values())
    if toe_total == 0:
        return 100
    return round(cur_total / toe_total * 100)


def _fill_class(pct: float) -> str:
    if pct < 25:
        return "fill-critical"
    elif pct < 50:
        return "fill-low"
    return "fill-ok"


def _side_badge(side: str | None) -> str:
    if side == "allied":
        return '<span class="badge badge-allied">Allied</span>'
    elif side == "axis":
        return '<span class="badge badge-axis">Axis</span>'
    return '<span class="badge badge-draw">—</span>'


def render_nav(turn_info: dict) -> str:
    gt = turn_info["gt"]
    return f"""<div class="nav">
  <span class="title">CNA Log Sheet — GT{gt} ({_esc(turn_info['date'])})</span>
  <a href="#cover">Summary</a>
  <a href="#allied">Allied Units</a>
  <a href="#axis">Axis Units</a>
  <a href="#supply">Supply</a>
  <a href="#naval">Naval</a>
  <a href="#orders">Orders</a>
  <a href="#events">Events</a>
  <a href="#transcript">Transcript</a>
</div>"""


def render_sheet1_cover(turn_info: dict, vp: dict, events: list[dict]) -> str:
    gt = turn_info["gt"]
    # Count event types
    evt_counts: dict[str, int] = {}
    for e in events:
        t = e.get("type", "other")
        evt_counts[t] = evt_counts.get(t, 0) + 1

    # VP table
    a = vp["allied"]
    x = vp["axis"]
    result = vp["result"]
    margin = vp["margin"]

    obj_rows = ""
    for o in vp["objectives"]:
        ctrl = _side_badge(o["controller"])
        obj_rows += f"""<tr>
          <td>{_esc(o['hex_id'])}</td><td>{_esc(o['name'])}</td>
          <td>{o['vp_value']}</td><td>{ctrl}</td>
          <td>{o['allied_units']}</td><td>{o['axis_units']}</td>
        </tr>"""

    evt_rows = "".join(
        f"<tr><td>{_esc(t)}</td><td>{c}</td></tr>"
        for t, c in sorted(evt_counts.items())
    )

    return f"""<div class="sheet" id="cover">
  <div class="cover-header">
    <h1>Campaign for North Africa — Game Turn {gt}</h1>
    <div class="date">{_esc(turn_info['date'])} — {_esc(turn_info['season']).title()}</div>
    <div class="meta">Weather: {_esc(turn_info['weather']).title()} | OpStage {turn_info['op_stage']} |
      Initiative: {_esc(turn_info['initiative_side'] or 'None').title()}
      (Allied {turn_info['allied_init']} / Axis {turn_info['axis_init']})</div>
  </div>

  <div class="summary-grid">
    <div class="summary-box">
      <h3>Victory Points</h3>
      <table>
        <tr><th></th><th>Objective</th><th>Destruction</th><th>Total</th></tr>
        <tr><td class="vp-allied">Allied</td><td>{a['objective_vp']}</td>
            <td>{a['destruction_vp']}</td><td><b>{a['total']}</b></td></tr>
        <tr><td class="vp-axis">Axis</td><td>{x['objective_vp']}</td>
            <td>{x['destruction_vp']}</td><td><b>{x['total']}</b></td></tr>
      </table>
      <p>Margin: <b>{margin:+.1f}</b> — <b>{_esc(result)}</b></p>
    </div>
    <div class="summary-box">
      <h3>Event Counts</h3>
      <table>
        <tr><th>Type</th><th>Count</th></tr>
        {evt_rows}
      </table>
    </div>
  </div>

  <h3>Objective Hex Control</h3>
  <table>
    <tr><th>Hex</th><th>Name</th><th>VP</th><th>Controller</th><th>Allied Units</th><th>Axis Units</th></tr>
    {obj_rows}
  </table>
</div>"""


def _render_unit_table(units: list[dict], formations: dict, css_class: str) -> str:
    """Render a unit control table grouped by formation."""
    # Group units by formation
    by_formation: dict[str, list[dict]] = {}
    for u in units:
        fid = u.get("attached_to_id") or u.get("parent_formation_id") or "unattached"
        by_formation.setdefault(fid, []).append(u)

    # Build formation display order: walk hierarchy
    fm_order = []
    seen = set()

    def _walk(fid: str, depth: int = 0):
        if fid in seen:
            return
        seen.add(fid)
        fm_order.append((fid, depth))
        f = formations.get(fid, {})
        for sub_id in f.get("sub_formation_ids", []):
            _walk(sub_id, depth + 1)

    # Find root formations for this side
    for fid, f in formations.items():
        side = units[0].get("side") if units else None
        if f.get("side") == side and not f.get("parent_formation_id"):
            _walk(fid)
    # Add any orphaned formations
    for fid in by_formation:
        if fid not in seen:
            fm_order.append((fid, 0))

    rows = ""
    for fid, depth in fm_order:
        f = formations.get(fid, {})
        fname = f.get("name", fid)
        fsize = f.get("formation_size", "")
        indent = "&nbsp;" * (depth * 4)
        rows += f'<tr class="formation-row"><td colspan="14">{indent}{_esc(fname)} ({_esc(fsize)})</td></tr>\n'

        for u in by_formation.get(fid, []):
            toe = u.get("toe_strength", {})
            cur = u.get("current_strength", {})
            pct = _strength_pct(u)
            status = u.get("status", "active")

            row_class = ""
            if status == "destroyed":
                row_class = "destroyed"
            elif u.get("is_pinned"):
                row_class = "pinned"
            elif u.get("is_in_contact"):
                row_class = "in-contact"

            pct_class = _fill_class(pct)

            rows += f"""<tr class="{row_class}">
  <td>{_esc(u.get('name', u.get('id')))}</td>
  <td>{_esc(u.get('unit_class',''))}</td>
  <td>{_esc(u.get('unit_size',''))}</td>
  <td>{_esc(u.get('hex_id') or u.get('off_map_location') or '—')}</td>
  <td>{_esc(status)}</td>
  <td>{u.get('base_cpa',0)}/{u.get('current_cpa_spent',0)}</td>
  <td>{u.get('cohesion',0)}</td>
  <td>{toe.get('infantry',0)}/{toe.get('armor',0)}/{toe.get('gun',0)}/{toe.get('mg',0)}/{toe.get('recon',0)}</td>
  <td>{cur.get('infantry',0)}/{cur.get('armor',0)}/{cur.get('gun',0)}/{cur.get('mg',0)}/{cur.get('recon',0)}</td>
  <td class="{pct_class}">{pct}%</td>
  <td>{u.get('losses_taken',0)}</td>
  <td>{u.get('combats_fought',0)}</td>
</tr>\n"""

    return rows


def render_sheet2_allied(data: dict) -> str:
    units_by_side = extract_units_by_side(data)
    formations = data.get("formations", {})
    rows = _render_unit_table(units_by_side["allied"], formations, "allied-header")

    return f"""<div class="sheet" id="allied">
  <h2>Sheet 2: Allied Unit Control</h2>
  <table>
    <tr class="allied-header">
      <th>Unit</th><th>Class</th><th>Size</th><th>Hex</th><th>Status</th>
      <th>CPA (base/spent)</th><th>Coh</th>
      <th>TOE (I/A/G/M/R)</th><th>Current (I/A/G/M/R)</th>
      <th>Str%</th><th>Losses</th><th>Combats</th>
    </tr>
    {rows}
  </table>
</div>"""


def render_sheet3_axis(data: dict) -> str:
    units_by_side = extract_units_by_side(data)
    formations = data.get("formations", {})
    rows = _render_unit_table(units_by_side["axis"], formations, "axis-header")

    return f"""<div class="sheet" id="axis">
  <h2>Sheet 3: Axis Unit Control</h2>
  <table>
    <tr class="axis-header">
      <th>Unit</th><th>Class</th><th>Size</th><th>Hex</th><th>Status</th>
      <th>CPA (base/spent)</th><th>Coh</th>
      <th>TOE (I/A/G/M/R)</th><th>Current (I/A/G/M/R)</th>
      <th>Str%</th><th>Losses</th><th>Combats</th>
    </tr>
    {rows}
  </table>
</div>"""


def render_sheet4_supply(data: dict) -> str:
    supply = extract_supply(data)
    ap = supply["allied_pool"]
    xp = supply["axis_pool"]
    ar = supply["allied_repl"]
    xr = supply["axis_repl"]

    # A. Global Pools
    pool_html = f"""<h3>A. Global Supply Pools</h3>
  <table>
    <tr><th>Pool</th><th>Fuel</th><th>Water</th><th>Ammo</th><th>Stores</th></tr>
    <tr><td class="vp-allied">Allied (Egypt)</td>
      <td>{ap.get('fuel',0):.1f}</td><td>{ap.get('water',0):.1f}</td>
      <td>{ap.get('ammo',0):.1f}</td><td>{ap.get('stores',0):.1f}</td></tr>
    <tr><td class="vp-axis">Axis (Tripoli)</td>
      <td>{xp.get('fuel',0):.1f}</td><td>{xp.get('water',0):.1f}</td>
      <td>{xp.get('ammo',0):.1f}</td><td>{xp.get('stores',0):.1f}</td></tr>
  </table>"""

    # B. Replacement Pools
    repl_html = f"""<h3>B. Replacement Pools</h3>
  <table>
    <tr><th>Side</th><th>Infantry</th><th>Armor</th><th>Gun</th></tr>
    <tr><td class="vp-allied">Allied</td>
      <td>{ar.get('infantry',0)}</td><td>{ar.get('armor',0)}</td><td>{ar.get('gun',0)}</td></tr>
    <tr><td class="vp-axis">Axis</td>
      <td>{xr.get('infantry',0)}</td><td>{xr.get('armor',0)}</td><td>{xr.get('gun',0)}</td></tr>
  </table>"""

    # C. Unit Supply Detail
    unit_rows = ""
    for us in supply["unit_supply"]:
        fc = _fill_class(us["fill_pct"])
        side_cls = "vp-allied" if us["side"] == "allied" else "vp-axis"
        unit_rows += f"""<tr>
  <td class="{side_cls}">{_esc(us['name'])}</td>
  <td>{_esc(us['hex_id'] or '—')}</td>
  <td>{us['fuel']:.1f}/{us['fuel_cap']:.0f}</td>
  <td>{us['water']:.1f}/{us['water_cap']:.0f}</td>
  <td>{us['ammo']:.1f}/{us['ammo_cap']:.0f}</td>
  <td>{us['stores']:.1f}/{us['stores_cap']:.0f}</td>
  <td class="{fc}">{us['fill_pct']:.0f}%</td>
  <td>{us['truck_pts']}</td>
  <td>{us['truck_fuel']:.1f}/{us['truck_water']:.1f}/{us['truck_ammo']:.1f}/{us['truck_stores']:.1f}</td>
</tr>\n"""

    unit_html = f"""<h3>C. Unit Supply Detail</h3>
  <table>
    <tr><th>Unit</th><th>Hex</th><th>Fuel</th><th>Water</th><th>Ammo</th><th>Stores</th>
        <th>Fill%</th><th>Trucks</th><th>Truck Cargo (F/W/A/S)</th></tr>
    {unit_rows}
  </table>"""

    # D. Supply Dumps
    dump_rows = ""
    for d in supply["dumps"]:
        total = d["fuel"] + d["water"] + d["ammo"] + d["stores"]
        if total < 0.01:
            continue  # Skip empty dumps
        side_cls = "vp-allied" if d["side"] == "allied" else "vp-axis"
        dump_rows += f"""<tr>
  <td>{_esc(d['hex_id'])}</td>
  <td class="{side_cls}">{_esc(d['dump_id'])}</td>
  <td>{_esc(d['side'])}</td>
  <td>{d['fuel']:.1f}</td><td>{d['water']:.1f}</td>
  <td>{d['ammo']:.1f}</td><td>{d['stores']:.1f}</td>
</tr>\n"""

    dump_html = f"""<h3>D. Supply Dumps</h3>
  <table>
    <tr><th>Hex</th><th>Dump ID</th><th>Side</th><th>Fuel</th><th>Water</th><th>Ammo</th><th>Stores</th></tr>
    {dump_rows if dump_rows else '<tr><td colspan="7">No supply dumps with contents</td></tr>'}
  </table>"""

    return f"""<div class="sheet" id="supply">
  <h2>Sheet 4: Supply Status</h2>
  {pool_html}
  {repl_html}
  {unit_html}
  {dump_html}
</div>"""


def render_sheet5_naval(data: dict) -> str:
    fleet = data.get("cw_fleet", {})
    convoy = data.get("axis_convoy", {})

    planned = convoy.get("planned_tonnage", {})
    delivered = convoy.get("actual_tonnage_delivered", {})
    planned_rows = ""
    if planned:
        for port, tons in planned.items():
            act = delivered.get(port, 0)
            planned_rows += f"<tr><td>{_esc(port)}</td><td>{tons}</td><td>{act}</td></tr>\n"
    else:
        planned_rows = '<tr><td colspan="3">No convoy activity</td></tr>'

    return f"""<div class="sheet" id="naval">
  <h2>Sheet 5: Naval Summary</h2>
  <div class="summary-grid">
    <div class="summary-box">
      <h3>CW Fleet</h3>
      <table>
        <tr><th>Available</th><td>{'Yes' if fleet.get('is_available') else 'No'}</td></tr>
        <tr><th>Sorties Remaining</th><td>{fleet.get('sorties_remaining', 0)}</td></tr>
        <tr><th>Repair Turns</th><td>{fleet.get('repair_turns_remaining', 0)}</td></tr>
        <tr><th>Ships Committed</th><td>{fleet.get('ships_committed', 0)}</td></tr>
        <tr><th>Current Hex</th><td>{_esc(fleet.get('current_hex') or '—')}</td></tr>
      </table>
    </div>
    <div class="summary-box">
      <h3>Axis Convoy</h3>
      <table>
        <tr><th>Port</th><th>Planned</th><th>Delivered</th></tr>
        {planned_rows}
      </table>
      <p>Losses this turn: {convoy.get('losses_this_turn', 0)}</p>
      <p>Lanes reconned: {_esc(', '.join(convoy.get('lanes_reconned', [])) or '—')}</p>
    </div>
  </div>
</div>"""


def render_sheet6_orders(order_entries: list[dict], gt: int) -> str:
    if not order_entries:
        return f"""<div class="sheet" id="orders">
  <h2>Sheet 6: Orders Log (AI Audit Trail)</h2>
  <p><i>No JSONL data available for GT{gt}. Provide --log to include order details.</i></p>
</div>"""

    sections = ""
    for i, entry in enumerate(order_entries):
        side = entry.get("side", "?")
        sub_phase = entry.get("sub_phase", "")
        # Clean up enum prefixes
        sub_phase = sub_phase.replace("OpStagePhase.", "").replace("GamePhase.", "")
        phase = entry.get("phase", "").replace("GamePhase.", "")
        timing = entry.get("timing", {})
        recs = entry.get("expert_recommendations", [])
        orders = entry.get("orders", [])

        side_cls = "badge-allied" if side == "allied" else "badge-axis"

        # Expert recommendations
        rec_html = ""
        if recs:
            rec_items = ""
            for r in recs:
                # Handle both old-style (assessment) and new-style (situation) formats
                situation = r.get("situation", r.get("assessment", ""))
                confidence = r.get("confidence", "")
                reasoning = r.get("reasoning", "")
                role = r.get("role", "")
                conf_str = f" ({confidence:.0%})" if isinstance(confidence, (int, float)) else ""
                rec_items += f"<tr><td>{_esc(role)}</td><td>{_esc(situation)}{conf_str}</td><td>{_esc(reasoning)}</td></tr>\n"
            rec_html = f"""<table>
  <tr><th>Role</th><th>Situation</th><th>Reasoning</th></tr>
  {rec_items}
</table>"""

        # Orders table
        order_rows = ""
        for o in orders:
            cmd = o.get("command", "")
            params = o.get("params", {})
            ok = o.get("success", False)
            err = o.get("error")
            params_str = ", ".join(f"{k}={json.dumps(v)}" for k, v in params.items()) if params else ""
            status_cls = "success" if ok else "error"
            status_txt = "OK" if ok else _esc(err or "FAIL")
            order_rows += f"""<tr>
  <td>{_esc(cmd)}</td>
  <td class="order-params">{_esc(params_str)}</td>
  <td class="{status_cls}">{status_txt}</td>
</tr>\n"""

        timing_str = ""
        if timing:
            parts = []
            if timing.get("experts_ms"):
                parts.append(f"experts: {timing['experts_ms']}ms")
            if timing.get("synthesis_ms"):
                parts.append(f"synthesis: {timing['synthesis_ms']}ms")
            timing_str = f"<p style='font-size:9px;color:#888;'>Timing: {', '.join(parts)}</p>"

        sections += f"""<details open>
  <summary>
    <span class="badge {side_cls}">{_esc(side).title()}</span>
    {_esc(phase)} &gt; {_esc(sub_phase)}
    ({len(orders)} orders)
  </summary>
  {rec_html}
  <table>
    <tr><th>Command</th><th>Parameters</th><th>Result</th></tr>
    {order_rows}
  </table>
  {timing_str}
</details>\n"""

    return f"""<div class="sheet" id="orders">
  <h2>Sheet 6: Orders Log (AI Audit Trail) — GT{gt}</h2>
  {sections}
</div>"""


def render_sheet7_events(events: list[dict], gt: int) -> str:
    if not events:
        return f"""<div class="sheet" id="events">
  <h2>Sheet 7: Event Chronicle — GT{gt}</h2>
  <p><i>No events recorded for this turn.</i></p>
</div>"""

    # Group by op_stage
    by_stage: dict[int, list[dict]] = {}
    for e in events:
        stage = e.get("op_stage", 0)
        by_stage.setdefault(stage, []).append(e)

    sections = ""
    for stage in sorted(by_stage.keys()):
        stage_events = by_stage[stage]
        rows = ""
        for e in stage_events:
            etype = e.get("type", "")
            phase = e.get("phase", "")
            desc = e.get("description", "")
            rows += f"""<tr>
  <td>{_esc(etype)}</td>
  <td>{_esc(phase)}</td>
  <td>{_esc(desc)}</td>
</tr>\n"""

        sections += f"""<details open>
  <summary>OpStage {stage} ({len(stage_events)} events)</summary>
  <table>
    <tr><th>Type</th><th>Phase</th><th>Description</th></tr>
    {rows}
  </table>
</details>\n"""

    return f"""<div class="sheet" id="events">
  <h2>Sheet 7: Event Chronicle — GT{gt}</h2>
  {sections}
</div>"""


# ════════════════════════════════════════════════════════════════════
# SHEET 8: ACTION TRANSCRIPT (compact notation)
# ════════════════════════════════════════════════════════════════════

def render_sheet8_transcript(events: list[dict], gt: int) -> str:
    from cna_engine.tools.notation import notate_event

    if not events:
        return f"""<div class="sheet" id="transcript">
  <h2>Sheet 8: Action Transcript — GT{gt}</h2>
  <p><i>No events recorded for this turn.</i></p>
</div>"""

    # Group by op_stage
    by_stage: dict[int, list[dict]] = {}
    for e in events:
        stage = e.get("op_stage", 0)
        by_stage.setdefault(stage, []).append(e)

    sections = ""
    for stage in sorted(by_stage.keys()):
        stage_events = by_stage[stage]

        shared_items: list[str] = []
        allied_items: list[str] = []
        axis_items: list[str] = []

        for e in stage_events:
            side, notation = notate_event(e)
            if notation is None:
                continue
            if side == "allied":
                allied_items.append(notation)
            elif side == "axis":
                axis_items.append(notation)
            else:
                shared_items.append(notation)

        if not shared_items and not allied_items and not axis_items:
            continue

        shared_html = ""
        for s in shared_items:
            shared_html += f'<div class="transcript-shared">{_esc(s)}</div>\n'

        axis_html = ""
        if axis_items:
            lis = "".join(f"<li>{i}. {_esc(n)}</li>\n" for i, n in enumerate(axis_items, 1))
            axis_html = f"""<div class="transcript-side">
  <h4 class="axis-label">AXIS</h4>
  <ol class="notation-list">{lis}</ol>
</div>\n"""

        allied_html = ""
        if allied_items:
            lis = "".join(f"<li>{i}. {_esc(n)}</li>\n" for i, n in enumerate(allied_items, 1))
            allied_html = f"""<div class="transcript-side">
  <h4 class="allied-label">ALLIED</h4>
  <ol class="notation-list">{lis}</ol>
</div>\n"""

        sections += f"""<details open>
  <summary>OpStage {stage}</summary>
  {shared_html}{axis_html}{allied_html}
</details>\n"""

    return f"""<div class="sheet" id="transcript">
  <h2>Sheet 8: Action Transcript — GT{gt}</h2>
  {sections}
</div>"""


# ════════════════════════════════════════════════════════════════════
# FULL PAGE ASSEMBLY
# ════════════════════════════════════════════════════════════════════

def render_logsheet(save_path: str | Path, jsonl_paths: list[str] | None = None) -> str:
    """Render a complete HTML log sheet for a single save file."""
    data = load_save(save_path)
    turn_info = extract_turn_info(data)
    gt = turn_info["gt"]

    vp = extract_vp(data, gt)
    events = extract_events(data, gt)
    order_entries = extract_orders(jsonl_paths or [], gt)

    nav = render_nav(turn_info)
    sheet1 = render_sheet1_cover(turn_info, vp, events)
    sheet2 = render_sheet2_allied(data)
    sheet3 = render_sheet3_axis(data)
    sheet4 = render_sheet4_supply(data)
    sheet5 = render_sheet5_naval(data)
    sheet6 = render_sheet6_orders(order_entries, gt)
    sheet7 = render_sheet7_events(events, gt)
    sheet8 = render_sheet8_transcript(events, gt)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CNA Log Sheet — GT{gt} ({_esc(turn_info['date'])})</title>
<style>{CSS}</style>
</head>
<body>
{nav}
{sheet1}
{sheet2}
{sheet3}
{sheet4}
{sheet5}
{sheet6}
{sheet7}
{sheet8}
</body>
</html>"""


# ════════════════════════════════════════════════════════════════════
# BATCH MODE & INDEX
# ════════════════════════════════════════════════════════════════════

def _extract_gt_number(path: Path) -> int | None:
    """Extract GT number from filename like gt11.json."""
    m = re.match(r"gt(\d+)\.json$", path.name)
    return int(m.group(1)) if m else None


def discover_saves(saves_dir: str | Path, gt_range: tuple[int, int] | None = None) -> list[Path]:
    """Find gt*.json files in a directory, optionally filtered by range."""
    saves_dir = Path(saves_dir)
    saves = []
    for p in sorted(saves_dir.glob("gt*.json")):
        # Skip memory files
        if "_memory" in p.name:
            continue
        gt = _extract_gt_number(p)
        if gt is None:
            continue
        if gt_range and not (gt_range[0] <= gt <= gt_range[1]):
            continue
        saves.append(p)
    saves.sort(key=lambda p: _extract_gt_number(p) or 0)
    return saves


def render_index(summaries: list[dict], out_dir: Path) -> str:
    """Render an index.html linking to all generated log sheets."""
    rows = ""
    for s in summaries:
        gt = s["gt"]
        date = s["date"]
        weather = s["weather"]
        allied_vp = s["allied_vp"]
        axis_vp = s["axis_vp"]
        result = s["result"]
        allied_units = s["allied_units"]
        axis_units = s["axis_units"]
        filename = s["filename"]

        rows += f"""<tr>
  <td><a href="{_esc(filename)}">{gt}</a></td>
  <td>{_esc(date)}</td>
  <td>{_esc(weather)}</td>
  <td class="vp-allied">{allied_vp:.1f}</td>
  <td class="vp-axis">{axis_vp:.1f}</td>
  <td>{_esc(result)}</td>
  <td>{allied_units}</td>
  <td>{axis_units}</td>
</tr>\n"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CNA Log Sheets — Campaign Index</title>
<style>{CSS}</style>
</head>
<body>
<div class="sheet">
  <div class="cover-header">
    <h1>Campaign for North Africa — Log Sheet Index</h1>
    <div class="date">{len(summaries)} turns exported</div>
  </div>
  <table>
    <tr><th>GT</th><th>Date</th><th>Weather</th>
        <th>Allied VP</th><th>Axis VP</th><th>Result</th>
        <th>Allied Units</th><th>Axis Units</th></tr>
    {rows}
  </table>
</div>
</body>
</html>"""


def run_batch(saves_dir: str, jsonl_paths: list[str] | None, out_dir: str,
              gt_range: tuple[int, int] | None = None) -> Path:
    """Generate log sheets for all saves + index.html."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    saves = discover_saves(saves_dir, gt_range)
    if not saves:
        print(f"No save files found in {saves_dir}")
        return out_path

    summaries = []
    for save_path in saves:
        gt = _extract_gt_number(save_path)
        if gt is None:
            continue

        print(f"  Generating GT{gt}...", end=" ", flush=True)
        html_content = render_logsheet(save_path, jsonl_paths)
        filename = f"gt{gt}.html"
        (out_path / filename).write_text(html_content, encoding="utf-8")

        # Build summary for index
        data = load_save(save_path)
        turn_info = extract_turn_info(data)
        vp = extract_vp(data, gt)
        units_by_side = extract_units_by_side(data)
        allied_active = sum(1 for u in units_by_side["allied"] if u.get("status") != "destroyed")
        axis_active = sum(1 for u in units_by_side["axis"] if u.get("status") != "destroyed")

        summaries.append({
            "gt": gt,
            "date": turn_info["date"],
            "weather": turn_info["weather"],
            "allied_vp": vp["allied"]["total"],
            "axis_vp": vp["axis"]["total"],
            "result": vp["result"],
            "allied_units": allied_active,
            "axis_units": axis_active,
            "filename": filename,
        })
        print("done")

    # Write index
    index_html = render_index(summaries, out_path)
    (out_path / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\nIndex: {out_path / 'index.html'}")
    print(f"Total: {len(summaries)} log sheets generated")
    return out_path


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

def parse_range(s: str) -> tuple[int, int]:
    """Parse '1-50' into (1, 50)."""
    parts = s.split("-")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    n = int(parts[0])
    return n, n


def main():
    parser = argparse.ArgumentParser(
        description="CNA HTML Log Sheet Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python -m cna_engine.tools.export_logsheets saves/gt11.json
  python -m cna_engine.tools.export_logsheets saves/gt11.json --log logs/game_*.jsonl
  python -m cna_engine.tools.export_logsheets --batch saves/ --log logs/game_*.jsonl --out exports/
  python -m cna_engine.tools.export_logsheets --batch saves/ --range 1-50 --out exports/
""",
    )
    parser.add_argument("save", nargs="?", help="Path to a single save file (gt*.json)")
    parser.add_argument("--log", nargs="*", default=[], help="JSONL game log path(s)")
    parser.add_argument("--batch", metavar="DIR", help="Batch mode: process all saves in DIR")
    parser.add_argument("--range", help="GT range for batch mode (e.g. 1-50)")
    parser.add_argument("--out", default="exports", help="Output directory (default: exports/)")
    parser.add_argument("--open", action="store_true", help="Open result in browser")

    args = parser.parse_args()

    # Expand globs in --log (shell may not expand them)
    jsonl_paths = []
    for pattern in args.log:
        expanded = glob.glob(pattern)
        jsonl_paths.extend(expanded if expanded else [pattern])

    gt_range = parse_range(args.range) if args.range else None

    if args.batch:
        print(f"Batch export from {args.batch} → {args.out}")
        out_path = run_batch(args.batch, jsonl_paths or None, args.out, gt_range)
        if args.open:
            index = out_path / "index.html"
            if index.exists():
                webbrowser.open(str(index))

    elif args.save:
        save_path = Path(args.save)
        if not save_path.exists():
            print(f"Error: {save_path} not found")
            return

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        gt = _extract_gt_number(save_path)
        filename = f"gt{gt}.html" if gt else save_path.stem + ".html"
        out_file = out_dir / filename

        print(f"Generating log sheet for {save_path.name}...")
        html_content = render_logsheet(save_path, jsonl_paths or None)
        out_file.write_text(html_content, encoding="utf-8")
        print(f"Written: {out_file}")

        if args.open:
            webbrowser.open(str(out_file))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
