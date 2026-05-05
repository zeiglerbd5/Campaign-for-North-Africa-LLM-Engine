"""
VASSAL .vsav Export for CNA Game States

Generates VASSAL save files (.vsav) from CNA engine game state JSON files.
The generated saves can be opened in VASSAL with the CNAv2.1.0.vmod module
to visualize unit positions on the CNA board map.

Usage:
    python -m cna_engine.engine.vassal_export saves/gt10.json output.vsav
"""

import json
import logging
import re
import time
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ESC = "\x1b"
TAB = "\t"

# ---------------------------------------------------------------------------
# VSAV Codec: decode/encode the obfuscated savedGame stream
# ---------------------------------------------------------------------------

def decode_vsav(path: str) -> tuple[int, str, str, str]:
    """Decode a .vsav file.

    Returns (xor_key, saved_game_text, savedata_xml, moduledata_xml).
    """
    with zipfile.ZipFile(path, "r") as z:
        raw = z.read("savedGame")
        savedata = z.read("savedata").decode("utf-8")
        moduledata = z.read("moduledata").decode("utf-8")

    # Header: !VCSK<key_hex_pair><hex_encoded_xor_data>
    assert raw[:5] == b"!VCSK", f"Unexpected header: {raw[:5]}"
    key = int(raw[5:7].decode("ascii"), 16)
    hex_data = raw[7:].decode("ascii")
    decoded = bytes(
        [int(hex_data[i : i + 2], 16) ^ key for i in range(0, len(hex_data) - 1, 2)]
    )
    text = decoded.decode("utf-8", errors="replace")
    return key, text, savedata, moduledata


def encode_vsav(
    path: str,
    xor_key: int,
    saved_game_text: str,
    savedata_xml: str,
    moduledata_xml: str,
) -> None:
    """Encode and write a .vsav file."""
    raw_bytes = saved_game_text.encode("utf-8")
    xored = bytes([b ^ xor_key for b in raw_bytes])
    hex_encoded = xored.hex()
    header = f"!VCSK{xor_key:02x}".encode("ascii")
    saved_game_blob = header + hex_encoded.encode("ascii")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("savedGame", saved_game_blob)
        z.writestr("savedata", savedata_xml)
        z.writestr("moduledata", moduledata_xml)


# ---------------------------------------------------------------------------
# Piece Template Extraction
# ---------------------------------------------------------------------------

def extract_piece_templates(saved_game_text: str) -> dict[str, str]:
    """Extract piece type templates from a decoded savedGame text.

    Returns {image_name_without_ext: full_type_string} for every piece
    found in the save.
    """
    templates = {}
    all_cmds = saved_game_text.split(ESC)

    for cmd in all_cmds:
        if "+/" not in cmd:
            continue
        m = re.search(r"piece;;;([^;]+)\.(png|svg);", cmd)
        if not m:
            continue
        img_base = m.group(1)
        # The command is TAB-separated.  Last 3 parts are the state
        # (board placement, stack info, map position).  Everything before
        # that is the reusable type string.
        parts = cmd.split(TAB)
        if len(parts) >= 4:
            type_str = TAB.join(parts[:-3])
            templates[img_base] = type_str

    return templates


# ---------------------------------------------------------------------------
# Non-Piece Commands (BoardPicker, TURN, etc.)
# ---------------------------------------------------------------------------

def extract_non_piece_commands(saved_game_text: str) -> list[str]:
    """Extract non-AddPiece commands from a savedGame text.

    These include BoardPicker entries, TurnTracker, etc. that should be
    preserved in the generated save.
    """
    cmds = []
    all_segments = saved_game_text.split(ESC)
    for seg in all_segments:
        if seg in ("", "\\"):
            continue
        if seg.startswith("+/"):
            continue  # AddPiece command
        if "BoardPicker" in seg or "TurnTracker" in seg or seg in ("begin_save", "end_save"):
            cmds.append(seg)
    return cmds


# ---------------------------------------------------------------------------
# Hex ID → Pixel Coordinate Converter
# ---------------------------------------------------------------------------

# Zone parameters extracted from CNAv2.1.0.vmod buildFile.xml.
# Each zone: (zone_min_x, zone_min_y, x0, y0, hOff, vOff)
# Grid params shared: dx=72.95, dy=85.25, sideways=true, first=H

GRID_DX = 72.95
GRID_DY = 85.25

ZONE_PARAMS = {
    "A": {"zone_min_x": 44,    "zone_min_y": 83,   "x0": 55,  "y0": 48,  "hOff": 6,  "vOff": 0,    "stagger_numbering": True},
    "B": {"zone_min_x": 2862,  "zone_min_y": 83,   "x0": -16, "y0": 7,   "hOff": 2,  "vOff": -33,  "stagger_numbering": False},
    "C": {"zone_min_x": 5680,  "zone_min_y": 811,  "x0": -16, "y0": 10,  "hOff": 12, "vOff": -66,  "stagger_numbering": False},
    "D": {"zone_min_x": 8497,  "zone_min_y": 1468, "x0": -16, "y0": 16,  "hOff": 21, "vOff": -99,  "stagger_numbering": False},
    "E": {"zone_min_x": 11312, "zone_min_y": 2149, "x0": -17, "y0": -66, "hOff": 19, "vOff": -133, "stagger_numbering": False},
}


def hex_id_to_pixel(hex_id: str) -> tuple[int, int] | None:
    """Convert a CNA hex ID (e.g. 'B2909') to pixel coordinates on the board.

    Returns (pixel_x, pixel_y) or None if the hex ID format is unrecognized.

    VASSAL sideways hex grid formula:
      col_index = col - hOff
      row_index = row - vOff
      pixel_x = zone_min_x + x0 + col_index * dy   (columns spread along x via dy)
      pixel_y = zone_min_y + y0 + row_index * dx + (dx/2 if col_index is odd)
    """
    m = re.match(r"^([A-E])(\d{2})(\d{2})$", hex_id)
    if not m:
        logger.warning("Unrecognized hex ID format: %s", hex_id)
        return None

    section = m.group(1)
    col = int(m.group(2))
    row = int(m.group(3))

    params = ZONE_PARAMS.get(section)
    if not params:
        logger.warning("Unknown map section: %s", section)
        return None

    col_index = col - params["hOff"]
    row_index = row - params["vOff"]

    origin_x = params["zone_min_x"] + params["x0"]
    origin_y = params["zone_min_y"] + params["y0"]

    pixel_x = origin_x + col_index * GRID_DY
    pixel_y = origin_y + row_index * GRID_DX

    # Stagger: odd column indices shift y by half a row
    if col_index % 2 == 1:
        pixel_y += GRID_DX / 2

    return round(pixel_x), round(pixel_y)


# ---------------------------------------------------------------------------
# Unit ID → VASSAL Piece Image Mapping
# ---------------------------------------------------------------------------

_MAPPING_PATH = Path(__file__).resolve().parent.parent / "data" / "vassal_piece_mapping.json"
_VMOD_PATH = Path(__file__).resolve().parent.parent.parent / "VASSAL_CNA" / "CNAv2.1.0.vmod"

# Cache of image base names that have .svg versions in the vmod
_SVG_IMAGES: set[str] = set()


def _load_svg_image_set(vmod_path: str | Path | None = None) -> set[str]:
    """Scan the vmod and return base names of images available as .svg."""
    global _SVG_IMAGES
    if _SVG_IMAGES:
        return _SVG_IMAGES
    path = Path(vmod_path) if vmod_path else _VMOD_PATH
    if not path.exists():
        logger.warning("vmod not found at %s, cannot detect SVG images", path)
        return _SVG_IMAGES
    with zipfile.ZipFile(path, "r") as z:
        for name in z.namelist():
            if name.startswith("images/") and name.endswith(".svg"):
                base = name[7:-4]  # strip "images/" and ".svg"
                _SVG_IMAGES.add(base)
    logger.info("Found %d SVG images in vmod", len(_SVG_IMAGES))
    return _SVG_IMAGES


def load_piece_mapping(mapping_path: str | Path | None = None) -> dict[str, str]:
    """Load the CNA unit ID → VASSAL image name mapping."""
    path = Path(mapping_path) if mapping_path else _MAPPING_PATH
    with open(path) as f:
        data = json.load(f)
    # Remove comment keys
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# AddPiece Command Builder
# ---------------------------------------------------------------------------

def build_add_piece_command(
    type_str: str,
    map_name: str,
    board_id: str,
    pixel_x: int,
    pixel_y: int,
    gp_id: int,
    timestamp: int | None = None,
) -> str:
    """Build a VASSAL AddPiece command string.

    The command format (ESC-separated within the savedGame stream):
      +/<timestamp>/<type_str>TAB<state_parts>

    State parts (TAB-separated):
      - Pieces\\            (piece palette identifier)
      - false;<board_id>;1;<x>,<y>\\\\
      - false\\\\\\
      - <map_name>;<x>;<y>;<gpId>\\
    """
    ts = timestamp or int(time.time() * 1000)

    # The type_str already starts with +/<original_timestamp>/...
    # We need to replace the original timestamp with our new one.
    if type_str.startswith("+/"):
        # Strip the leading +/<timestamp>/ and re-add with our timestamp
        after_first_slash = type_str.index("/", 2)
        type_body = type_str[after_first_slash + 1:]
    else:
        type_body = type_str

    # The type_str already ends with "Pieces\" — the state continues with
    # the board placement, stack info, and map position parts.
    state = (
        f"false;{board_id};1;{pixel_x},{pixel_y}\\\\"
        f"{TAB}false\\\\\\"
        f"{TAB}{map_name};{pixel_x};{pixel_y};{gp_id}\\"
    )

    return f"+/{ts}/{type_body}{TAB}{state}"


# ---------------------------------------------------------------------------
# Save File Generator
# ---------------------------------------------------------------------------

def export_game_state(
    game_state_path: str,
    output_vsav_path: str,
    template_vsav_path: str | None = None,
    mapping_path: str | None = None,
) -> None:
    """Export a CNA game state JSON to a VASSAL .vsav file.

    Args:
        game_state_path: Path to the game state JSON (e.g. saves/gt10.json).
        output_vsav_path: Path for the output .vsav file.
        template_vsav_path: Path to a template .vsav (default: Brevity save).
        mapping_path: Path to the piece mapping JSON (default: built-in).
    """
    # Resolve defaults
    project_root = Path(__file__).resolve().parent.parent.parent
    if template_vsav_path is None:
        template_vsav_path = str(
            project_root / "VASSAL_CNA" / "Operation_Brevity_set-upv0.93.vsav"
        )
    if mapping_path is None:
        mapping_path = str(_MAPPING_PATH)

    # Load SVG image set from vmod for .png → .svg fixup
    _load_svg_image_set()

    # Load game state
    logger.info("Loading game state from %s", game_state_path)
    with open(game_state_path) as f:
        game_state = json.load(f)

    units = game_state.get("units", {})
    turn_info = game_state.get("turn", {})
    game_turn = turn_info.get("game_turn", 0)

    # Load piece mapping
    piece_mapping = load_piece_mapping(mapping_path)

    # Decode template
    logger.info("Decoding template save from %s", template_vsav_path)
    xor_key, template_text, savedata_xml, moduledata_xml = decode_vsav(template_vsav_path)

    # Extract piece type templates from the donor save
    piece_templates = extract_piece_templates(template_text)
    logger.info("Extracted %d piece type templates from donor", len(piece_templates))

    # Extract non-piece commands (BoardPicker, TURN, etc.)
    non_piece_cmds = extract_non_piece_commands(template_text)

    # Build new piece commands
    piece_cmds = []
    timestamp_base = int(time.time() * 1000)
    gp_id_base = 10000  # Start gpIds above the template's range
    unmapped_units = []
    off_map_units = []
    placed_count = 0

    for unit_id, unit_data in sorted(units.items()):
        hex_id = unit_data.get("hex_id")
        status = unit_data.get("status", "active")

        # Skip destroyed/withdrawn units and off-map units
        if status in ("destroyed", "withdrawn", "surrendered"):
            logger.debug("Skipping %s (status=%s)", unit_id, status)
            continue

        if not hex_id or unit_data.get("off_map_location"):
            off_map_units.append(unit_id)
            continue

        # Look up VASSAL image name
        vassal_img = piece_mapping.get(unit_id)
        if not vassal_img:
            unmapped_units.append(unit_id)
            continue

        # Look up type template (exact match first, then fuzzy)
        type_str = piece_templates.get(vassal_img)
        if not type_str:
            # Try case-insensitive and near-match search (handles
            # naming differences between Brevity save and v2.1.0 vmod)
            vassal_img_lower = vassal_img.lower()
            for tpl_name, tpl_str in piece_templates.items():
                if tpl_name.lower() == vassal_img_lower:
                    type_str = tpl_str
                    break
            if not type_str:
                # Try matching with only alphanumeric chars (ignoring dashes/spaces)
                target = re.sub(r"[^a-z0-9]", "", vassal_img_lower)
                for tpl_name, tpl_str in piece_templates.items():
                    candidate = re.sub(r"[^a-z0-9]", "", tpl_name.lower())
                    if candidate == target:
                        type_str = tpl_str
                        # Replace the old image name with the new one in the type string
                        type_str = type_str.replace(tpl_name, vassal_img)
                        logger.info("Fuzzy-matched template '%s' -> '%s'", tpl_name, vassal_img)
                        break
            if not type_str:
                unmapped_units.append(unit_id)
                logger.warning("No type template for '%s' (unit %s)", vassal_img, unit_id)
                continue

        # The Brevity donor save uses .png image references, but the v2.1.0
        # module uses .svg for most pieces. Replace .png → .svg in the type
        # string when the .svg exists in the module.
        if vassal_img in _SVG_IMAGES:
            type_str = type_str.replace(
                f"{vassal_img}.png", f"{vassal_img}.svg"
            )

        # Convert hex to pixel coordinates
        pixel = hex_id_to_pixel(hex_id)
        if pixel is None:
            logger.warning("Cannot convert hex %s to pixel (unit %s)", hex_id, unit_id)
            continue

        px, py = pixel
        gp_id = gp_id_base + placed_count

        cmd = build_add_piece_command(
            type_str=type_str,
            map_name="Main Map",
            board_id="Map0",
            pixel_x=px,
            pixel_y=py,
            gp_id=gp_id,
            timestamp=timestamp_base + placed_count,
        )
        piece_cmds.append(cmd)
        placed_count += 1
        logger.debug(
            "Placed %s at %s -> pixel (%d, %d)", unit_id, hex_id, px, py
        )

    # Assemble the savedGame text
    # Format: begin_save ESC ESC \ ESC <piece_cmds joined by ESC> ESC <non_piece_cmds joined by ESC> ESC end_save
    parts = ["begin_save", "", "\\"]
    parts.extend(piece_cmds)
    # Add BoardPicker and other non-piece commands
    for cmd in non_piece_cmds:
        if cmd not in ("begin_save", "end_save"):
            parts.append(cmd)
    parts.append("end_save")

    new_saved_game = ESC.join(parts)

    # Update savedata with current timestamp
    now_ms = int(time.time() * 1000)
    savedata_xml = re.sub(
        r"<dateSaved>\d+</dateSaved>",
        f"<dateSaved>{now_ms}</dateSaved>",
        savedata_xml,
    )

    # Write output
    logger.info("Writing %d pieces to %s", placed_count, output_vsav_path)
    encode_vsav(output_vsav_path, xor_key, new_saved_game, savedata_xml, moduledata_xml)

    # Summary
    print(f"Exported GT{game_turn} -> {output_vsav_path}")
    print(f"  Placed: {placed_count} units on Main Map")
    if off_map_units:
        print(f"  Off-map (skipped): {len(off_map_units)} units: {', '.join(off_map_units)}")
    if unmapped_units:
        print(f"  Unmapped (no VASSAL image): {len(unmapped_units)} units: {', '.join(unmapped_units)}")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Export CNA game state to VASSAL .vsav file",
    )
    parser.add_argument("game_state", help="Path to game state JSON (e.g. saves/gt10.json)")
    parser.add_argument("output", help="Path for output .vsav file")
    parser.add_argument(
        "--template",
        default=None,
        help="Path to template .vsav (default: VASSAL_CNA/Operation_Brevity_set-upv0.93.vsav)",
    )
    parser.add_argument(
        "--mapping",
        default=None,
        help="Path to piece mapping JSON (default: built-in)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    export_game_state(
        game_state_path=args.game_state,
        output_vsav_path=args.output,
        template_vsav_path=args.template,
        mapping_path=args.mapping,
    )


if __name__ == "__main__":
    main()
