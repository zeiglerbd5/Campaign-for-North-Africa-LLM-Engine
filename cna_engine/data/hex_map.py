"""
CNA Engine — Hex Map Data Module
Central source of truth for the 5-section North African theater map.
Loads hex terrain from the extracted database and provides overlay data
(roads, ports, airfields, named locations).
"""
import json
import os
from pathlib import Path

_FILES_DIR = Path(__file__).resolve().parent.parent.parent / "files"

# ════════════════════════════════════════
# SECTION SEAM DEFINITIONS
# ════════════════════════════════════════
# Each section is ~39 columns wide. At the boundary, the rightmost column
# of one section is adjacent to the leftmost column of the next.
# Format: (section_left, section_right): (left_max_col, right_min_col)

SECTION_SEAMS = {
    ("A", "B"): (39, 2),
    ("B", "C"): (39, 2),
    ("C", "D"): (39, 1),
    ("D", "E"): (39, 2),
}

# Reverse lookup: given a section, what sections are adjacent?
SECTION_NEIGHBORS = {}
for (left, right), (lcol, rcol) in SECTION_SEAMS.items():
    SECTION_NEIGHBORS.setdefault(left, []).append((right, lcol, rcol, "right"))
    SECTION_NEIGHBORS.setdefault(right, []).append((left, rcol, lcol, "left"))

# Global column offsets for cross-section distance calculation
# A col 1 = global col 1, A col 39 = global 39
# B col 2 = global 40 (adjacent to A39), B col 39 = global 77
# C col 2 = global 78, C col 39 = global 115
# D col 1 = global 116, D col 39 = global 154
# E col 2 = global 155
SECTION_COL_OFFSETS = {
    "A": 0,    # A col X → global X
    "B": 38,   # B col X → global X + 38
    "C": 76,   # C col X → global X + 76
    "D": 115,  # D col X → global X + 115
    "E": 153,  # E col X → global X + 153
}


def load_hex_database() -> dict[str, str]:
    """Load the hex terrain database. Returns {hex_id: terrain_type}."""
    path = _FILES_DIR / "hex_database.json"
    with open(path) as f:
        return json.load(f)


def load_named_locations() -> dict[str, dict]:
    """Load named locations. Returns {name: {hex_id, vp, is_port, ...}}."""
    path = _FILES_DIR / "named_locations.json"
    with open(path) as f:
        return json.load(f)


# ════════════════════════════════════════
# NAMED LOCATIONS (cached on import)
# ════════════════════════════════════════

_named_locations = None

def get_named_locations() -> dict[str, dict]:
    global _named_locations
    if _named_locations is None:
        _named_locations = load_named_locations()
    return _named_locations


def hex_for(name: str) -> str:
    """Get hex ID for a named location. Raises KeyError if not found."""
    return get_named_locations()[name]["hex_id"]


# ════════════════════════════════════════
# ROAD OVERLAY
# ════════════════════════════════════════
# Road network traced from the original CNA board game maps.
# Via Balbia (coast road): Tripoli → Alexandria, ~240 hexes
# Interior tracks: Trigh Capuzzo, Trigh el Abd
# Railroad: Alexandria → Mersa Matruh

def load_road_network() -> dict[str, str]:
    """Load road network from JSON. Returns {hex_id: road_type}.
    road_type is one of: 'road', 'track', 'railroad', 'road_railroad'."""
    path = _FILES_DIR / "road_network.json"
    with open(path) as f:
        return json.load(f)

ROAD_OVERLAY = None  # Lazy-loaded

def get_road_overlay() -> dict[str, str]:
    global ROAD_OVERLAY
    if ROAD_OVERLAY is None:
        ROAD_OVERLAY = load_road_network()
    return ROAD_OVERLAY


# ════════════════════════════════════════
# PORT OVERLAY
# ════════════════════════════════════════

def get_port_hexes() -> dict[str, str]:
    """Returns {hex_id: port_name} for all ports."""
    locs = get_named_locations()
    return {info["hex_id"]: name for name, info in locs.items() if info["is_port"]}


# ════════════════════════════════════════
# AIRFIELD OVERLAY
# ════════════════════════════════════════

def get_airfield_hexes() -> set[str]:
    """Returns set of hex IDs with airfields."""
    locs = get_named_locations()
    return {info["hex_id"] for info in locs.values() if info["is_airfield"]}
