"""
VASSAL .vlog Replay Exporter for CNA Game States

Generates a VASSAL .vlog file from a sequence of CNA save files so the game
can be replayed step-by-step in VASSAL (Step Forward button).

The .vlog format is the same XOR-obfuscated ZIP as .vsav, but appends
LOG\t<commands> entries after the initial state block.  Each LOG\t entry
corresponds to one "Step Forward" click.

Step granularity (hybrid):
  - Logistics events (weather, initiative, stores, etc.) → 1 bundled step/OpStage
  - Action events (movement, combat, bombardment, etc.) → 1 step each

Usage:
    python -m cna_engine.tools.export_vlog saves/ -o game_replay.vlog
    python -m cna_engine.tools.export_vlog saves/ --range 32-40 -o partial.vlog
    python -m cna_engine.tools.export_vlog saves/ -o replay.vlog -v
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from cna_engine.engine.vassal_export import (
    ESC,
    TAB,
    build_add_piece_command,
    decode_vsav,
    encode_vsav,
    extract_non_piece_commands,
    extract_piece_templates,
    hex_id_to_pixel,
    load_piece_mapping,
    _load_svg_image_set,
    _SVG_IMAGES,
)
from cna_engine.tools.export_logsheets import (
    discover_saves,
    extract_events,
    extract_turn_info,
    gt_to_date,
    load_save,
)
from cna_engine.tools.notation import notate_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_TYPES = {
    "movement",
    "close_assault",
    "barrage_result",
    "anti_armor_result",
    "bombardment",
    "recon",
    "patrol",
}

_DEFAULT_TEMPLATE_VSAV = (
    Path(__file__).resolve().parent.parent.parent
    / "VASSAL_CNA"
    / "setup_italian_v2.1.0.vsav"
)
_DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "vassal_piece_mapping.json"
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PieceState:
    unit_id: str
    piece_id: str        # VASSAL piece ID (first field after +/ in AddPiece)
    type_str: str        # VASSAL piece type string (for AddPiece)
    hex_id: str | None   # Current tracked position
    status: str          # "active", "destroyed", etc.
    side: str
    placed: bool         # Whether AddPiece has been emitted


@dataclass
class VlogStep:
    commands: list[str] = field(default_factory=list)
    label: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_type_str(vassal_img: str, piece_templates: dict[str, str]) -> str | None:
    """Look up the type template for a VASSAL image name (exact, then fuzzy)."""
    type_str = piece_templates.get(vassal_img)
    if type_str:
        return type_str
    # Case-insensitive
    vassal_img_lower = vassal_img.lower()
    for tpl_name, tpl_str in piece_templates.items():
        if tpl_name.lower() == vassal_img_lower:
            return tpl_str
    # Alphanumeric fuzzy
    target = re.sub(r"[^a-z0-9]", "", vassal_img_lower)
    for tpl_name, tpl_str in piece_templates.items():
        candidate = re.sub(r"[^a-z0-9]", "", tpl_name.lower())
        if candidate == target:
            return tpl_str.replace(tpl_name, vassal_img)
    return None


def _fixup_svg(type_str: str, vassal_img: str) -> str:
    """Replace .png → .svg in type string when SVG exists in the vmod."""
    if vassal_img in _SVG_IMAGES:
        return type_str.replace(f"{vassal_img}.png", f"{vassal_img}.svg")
    return type_str


def _extract_gt_number(path: Path) -> int | None:
    m = re.match(r"gt(\d+)\.json$", path.name)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# VASSAL command builders
# ---------------------------------------------------------------------------

def build_move_command(piece_id: str, old_hex: str, new_hex: str) -> str | None:
    """Build a VASSAL MovePiece command.

    Format: M/<pieceId>/Main Map/<newX>/<newY>/null/Main Map/<oldX>/<oldY>/null/observer
    Returns None if either hex cannot be converted to pixel coords.
    """
    old_px = hex_id_to_pixel(old_hex)
    new_px = hex_id_to_pixel(new_hex)
    if old_px is None or new_px is None:
        return None
    return (
        f"M/{piece_id}/Main Map/{new_px[0]}/{new_px[1]}/null"
        f"/Main Map/{old_px[0]}/{old_px[1]}/null/observer"
    )


def build_remove_command(piece_id: str) -> str:
    """Build a VASSAL RemovePiece command."""
    return f"-/{piece_id}"


def build_chat_command(text: str) -> str:
    """Build a VASSAL CHAT command."""
    return f"CHAT* {text}"


# ---------------------------------------------------------------------------
# Phase 1: Build initial state
# ---------------------------------------------------------------------------

def build_initial_state(
    first_save: dict,
    piece_templates: dict[str, str],
    piece_mapping: dict[str, str],
    non_piece_cmds: list[str],
    template_text: str,
    gp_id_start: int = 10000,
) -> tuple[str, dict[str, PieceState], int]:
    """Build the begin_save...end_save block from the first save file.

    Preserves ALL template pieces (VASSAL needs them) and repositions
    the ones that correspond to our game units.

    Returns (initial_state_text, piece_registry, next_gp_id).
    """
    units = first_save.get("units", {})
    piece_registry: dict[str, PieceState] = {}
    next_gp_id = gp_id_start

    # Build reverse mapping: vassal_img_name → unit_id
    img_to_unit: dict[str, str] = {}
    for unit_id, vassal_img in piece_mapping.items():
        img_to_unit[vassal_img.lower()] = unit_id

    # Parse ALL template commands, repositioning matched pieces
    template_parts = template_text.split(ESC)
    new_parts: list[str] = []
    matched_units: set[str] = set()

    for part in template_parts:
        if not part.startswith("+/"):
            new_parts.append(part)
            continue

        # Global .png → .svg fixup for all template pieces
        if ".png" in part:
            for png_match in re.finditer(r"([^;/]+)\.png", part):
                img_name = png_match.group(1)
                if img_name in _SVG_IMAGES:
                    part = part.replace(f"{img_name}.png", f"{img_name}.svg")

        # Try to identify which piece image this is
        m = re.search(r"piece;;;([^;]+)\.(png|svg);", part)
        if not m:
            new_parts.append(part)
            continue

        img_base = m.group(1)
        img_lower = img_base.lower()

        # Check if this template piece maps to one of our game units
        unit_id = img_to_unit.get(img_lower)
        if not unit_id:
            # Also try alphanumeric-only match
            target = re.sub(r"[^a-z0-9]", "", img_lower)
            for mapping_img, uid in img_to_unit.items():
                candidate = re.sub(r"[^a-z0-9]", "", mapping_img)
                if candidate == target:
                    unit_id = uid
                    break

        if not unit_id or unit_id in matched_units:
            # Not one of our units, or already matched — keep as-is
            new_parts.append(part)
            continue

        unit_data = units.get(unit_id)
        if not unit_data:
            new_parts.append(part)
            continue

        hex_id = unit_data.get("hex_id")
        status = unit_data.get("status", "active")
        side = unit_data.get("side", "")

        if status in ("destroyed", "withdrawn", "surrendered") or not hex_id:
            new_parts.append(part)
            piece_registry[unit_id] = PieceState(
                unit_id=unit_id, piece_id="", type_str="",
                hex_id=hex_id, status=status, side=side, placed=False,
            )
            matched_units.add(unit_id)
            continue

        pixel = hex_id_to_pixel(hex_id)
        if pixel is None:
            new_parts.append(part)
            piece_registry[unit_id] = PieceState(
                unit_id=unit_id, piece_id="", type_str="",
                hex_id=hex_id, status=status, side=side, placed=False,
            )
            matched_units.add(unit_id)
            continue

        # Extract the type portion and rebuild with new position
        vassal_img = piece_mapping[unit_id]
        type_str = _resolve_type_str(vassal_img, piece_templates)
        if not type_str:
            new_parts.append(part)
            continue

        type_str = _fixup_svg(type_str, vassal_img)

        gp_id = next_gp_id
        next_gp_id += 1
        timestamp = int(time.time() * 1000) + (gp_id - gp_id_start)

        cmd = build_add_piece_command(
            type_str=type_str,
            map_name="Main Map",
            board_id="Map0",
            pixel_x=pixel[0],
            pixel_y=pixel[1],
            gp_id=gp_id,
            timestamp=timestamp,
        )
        new_parts.append(cmd)
        # Store the timestamp as piece_id — this is what VASSAL uses
        # for MovePiece/RemovePiece (the first field after +/ in AddPiece)
        piece_registry[unit_id] = PieceState(
            unit_id=unit_id, piece_id=str(timestamp), type_str=type_str,
            hex_id=hex_id, status=status, side=side, placed=True,
        )
        matched_units.add(unit_id)

    # Register any unmapped active units (won't appear on board)
    for unit_id, unit_data in units.items():
        if unit_id in matched_units or unit_id in piece_registry:
            continue
        hex_id = unit_data.get("hex_id")
        status = unit_data.get("status", "active")
        side = unit_data.get("side", "")
        if status in ("destroyed", "withdrawn", "surrendered"):
            continue
        if not hex_id or unit_data.get("off_map_location"):
            continue
        piece_registry[unit_id] = PieceState(
            unit_id=unit_id, piece_id="", type_str="",
            hex_id=hex_id, status=status, side=side, placed=False,
        )

    initial_state = ESC.join(new_parts)
    return initial_state, piece_registry, next_gp_id


# ---------------------------------------------------------------------------
# Phase 2: Build per-turn steps
# ---------------------------------------------------------------------------

def build_turn_steps(
    gt: int,
    events: list[dict],
    turn_info: dict,
    prev_units: dict,
    curr_units: dict,
    piece_registry: dict[str, PieceState],
    piece_templates: dict[str, str],
    piece_mapping: dict[str, str],
    next_gp_id: int,
) -> tuple[list[VlogStep], int]:
    """Build VlogSteps for a single game turn.

    Returns (steps, updated_next_gp_id).
    """
    steps: list[VlogStep] = []
    date_str = turn_info.get("date", gt_to_date(gt))

    # 1. Turn header step
    header_step = VlogStep(
        commands=[build_chat_command(f"=== GT{gt} ({date_str}) ===")],
        label=f"GT{gt} header",
    )
    steps.append(header_step)

    # 2. Group events by op_stage
    by_stage: dict[int, list[dict]] = {}
    for e in events:
        stage = e.get("op_stage", 0)
        by_stage.setdefault(stage, []).append(e)

    for stage in sorted(by_stage.keys()):
        stage_events = by_stage[stage]

        # Separate logistics vs action events
        logistics_cmds: list[str] = []
        action_events: list[dict] = []

        for e in stage_events:
            etype = e.get("type", "")
            if etype in ACTION_TYPES:
                action_events.append(e)
            else:
                # Try notation; skip if None
                _side, notation = notate_event(e)
                if notation is not None:
                    logistics_cmds.append(build_chat_command(notation))

        # 2a. Logistics bundle step
        if logistics_cmds:
            steps.append(VlogStep(
                commands=logistics_cmds,
                label=f"GT{gt} OS{stage} logistics",
            ))

        # 2b. Individual action steps
        for e in action_events:
            etype = e.get("type", "")
            _side, notation = notate_event(e)
            if notation is None:
                continue

            step_cmds: list[str] = []

            if etype == "movement":
                uid = e.get("unit_id", "")
                to_hex = e.get("to_hex")
                ps = piece_registry.get(uid)
                if ps and ps.placed and ps.hex_id and to_hex:
                    move_cmd = build_move_command(ps.piece_id, ps.hex_id, to_hex)
                    if move_cmd:
                        step_cmds.append(move_cmd)
                    # Update registry regardless
                    ps.hex_id = to_hex
                elif ps and to_hex:
                    ps.hex_id = to_hex

            step_cmds.append(build_chat_command(notation))
            steps.append(VlogStep(
                commands=step_cmds,
                label=f"GT{gt} OS{stage} {etype}",
            ))

    # 3. Turn reconciliation step — diff registry against curr_save
    recon_cmds: list[str] = []

    for uid, curr_data in curr_units.items():
        curr_hex = curr_data.get("hex_id")
        curr_status = curr_data.get("status", "active")
        curr_side = curr_data.get("side", "")

        ps = piece_registry.get(uid)

        # New unit (reinforcement)
        if ps is None:
            if curr_status in ("destroyed", "withdrawn", "surrendered"):
                continue
            if not curr_hex or curr_data.get("off_map_location"):
                # Track but don't place
                piece_registry[uid] = PieceState(
                    unit_id=uid, piece_id="", type_str="",
                    hex_id=curr_hex, status=curr_status, side=curr_side,
                    placed=False,
                )
                continue

            vassal_img = piece_mapping.get(uid)
            type_str = None
            if vassal_img:
                type_str = _resolve_type_str(vassal_img, piece_templates)
                if type_str:
                    type_str = _fixup_svg(type_str, vassal_img)

            if type_str and curr_hex:
                pixel = hex_id_to_pixel(curr_hex)
                if pixel:
                    gp_id = next_gp_id
                    next_gp_id += 1
                    reinf_ts = int(time.time() * 1000) + (gp_id - 10000)
                    add_cmd = build_add_piece_command(
                        type_str=type_str,
                        map_name="Main Map",
                        board_id="Map0",
                        pixel_x=pixel[0],
                        pixel_y=pixel[1],
                        gp_id=gp_id,
                        timestamp=reinf_ts,
                    )
                    recon_cmds.append(add_cmd)
                    recon_cmds.append(build_chat_command(f"REINF: {uid} at {curr_hex}"))
                    piece_registry[uid] = PieceState(
                        unit_id=uid, piece_id=str(reinf_ts), type_str=type_str,
                        hex_id=curr_hex, status=curr_status, side=curr_side,
                        placed=True,
                    )
                    continue

            # Can't place on board
            piece_registry[uid] = PieceState(
                unit_id=uid, piece_id="", type_str="",
                hex_id=curr_hex, status=curr_status, side=curr_side,
                placed=False,
            )
            continue

        # Existing unit — check for destruction
        if curr_status in ("destroyed", "withdrawn", "surrendered") and ps.status not in (
            "destroyed", "withdrawn", "surrendered"
        ):
            if ps.placed:
                recon_cmds.append(build_remove_command(ps.piece_id))
                recon_cmds.append(build_chat_command(f"{uid} {curr_status}"))
            ps.status = curr_status
            continue

        # Position mismatch correction
        if (
            ps.placed
            and curr_hex
            and ps.hex_id
            and curr_hex != ps.hex_id
        ):
            move_cmd = build_move_command(ps.piece_id, ps.hex_id, curr_hex)
            if move_cmd:
                recon_cmds.append(move_cmd)
            ps.hex_id = curr_hex

        # Sync status/hex
        ps.status = curr_status
        if curr_hex:
            ps.hex_id = curr_hex

    if recon_cmds:
        steps.append(VlogStep(
            commands=recon_cmds,
            label=f"GT{gt} reconciliation",
        ))

    return steps, next_gp_id


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_vlog(initial_state: str, steps: list[VlogStep]) -> str:
    """Concatenate initial state + LOG\\t entries into a complete savedGame text."""
    parts = [initial_state]
    for step in steps:
        if not step.commands:
            continue
        # Each command is a separate ESC-delimited LOG\t entry.
        for cmd in step.commands:
            parts.append("LOG" + TAB + cmd)
    return ESC.join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def export_vlog(
    saves_dir: str | Path,
    output_path: str | Path,
    gt_range: tuple[int, int] | None = None,
    template_vsav_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
    verbose: bool = False,
) -> None:
    """Generate a .vlog replay file from a directory of save files.

    Args:
        saves_dir: Directory containing gtN.json save files.
        output_path: Path for the output .vlog file.
        gt_range: Optional (start, end) inclusive GT range.
        template_vsav_path: Path to template .vsav for piece templates.
        mapping_path: Path to vassal_piece_mapping.json.
        verbose: Enable debug logging.
    """
    saves_dir = Path(saves_dir)
    output_path = Path(output_path)

    if template_vsav_path is None:
        template_vsav_path = _DEFAULT_TEMPLATE_VSAV
    if mapping_path is None:
        mapping_path = _DEFAULT_MAPPING_PATH

    # Discover save files
    save_paths = discover_saves(saves_dir, gt_range)
    if len(save_paths) < 1:
        raise ValueError(f"No save files found in {saves_dir}")
    logger.info("Found %d save files", len(save_paths))

    # Load template
    logger.info("Decoding template save from %s", template_vsav_path)
    xor_key, template_text, savedata_xml, moduledata_xml = decode_vsav(
        str(template_vsav_path)
    )
    piece_templates = extract_piece_templates(template_text)
    non_piece_cmds = extract_non_piece_commands(template_text)
    piece_mapping = load_piece_mapping(str(mapping_path))

    # Load SVG image set for .png → .svg fixup
    _load_svg_image_set()

    logger.info("Extracted %d piece templates, %d non-piece commands",
                len(piece_templates), len(non_piece_cmds))

    # Phase 1: Build initial state from first save
    first_save = load_save(save_paths[0])
    first_gt = _extract_gt_number(save_paths[0]) or 0
    initial_state, piece_registry, next_gp_id = build_initial_state(
        first_save, piece_templates, piece_mapping, non_piece_cmds,
        template_text=template_text,
    )
    logger.info(
        "Initial state: %d pieces placed, %d tracked",
        sum(1 for ps in piece_registry.values() if ps.placed),
        len(piece_registry),
    )

    # Phase 2: Build steps for each consecutive save pair
    all_steps: list[VlogStep] = []

    for i in range(len(save_paths)):
        curr_path = save_paths[i]
        curr_gt = _extract_gt_number(curr_path) or 0

        if i == 0:
            curr_save = first_save
        else:
            curr_save = load_save(curr_path)

        turn_info = extract_turn_info(curr_save)
        events = extract_events(curr_save, curr_gt)

        # prev_units for reconciliation
        if i == 0:
            prev_units = {}
        else:
            prev_save = load_save(save_paths[i - 1]) if i > 0 else {}
            prev_units = prev_save.get("units", {})

        curr_units = curr_save.get("units", {})

        turn_steps, next_gp_id = build_turn_steps(
            gt=curr_gt,
            events=events,
            turn_info=turn_info,
            prev_units=prev_units,
            curr_units=curr_units,
            piece_registry=piece_registry,
            piece_templates=piece_templates,
            piece_mapping=piece_mapping,
            next_gp_id=next_gp_id,
        )
        all_steps.extend(turn_steps)
        logger.info("GT%d: %d steps (%d events)", curr_gt, len(turn_steps), len(events))

    # Assemble and encode
    saved_game_text = assemble_vlog(initial_state, all_steps)

    # Update savedata timestamp
    now_ms = int(time.time() * 1000)
    savedata_xml = re.sub(
        r"<dateSaved>\d+</dateSaved>",
        f"<dateSaved>{now_ms}</dateSaved>",
        savedata_xml,
    )

    encode_vsav(str(output_path), xor_key, saved_game_text, savedata_xml, moduledata_xml)

    placed = sum(1 for ps in piece_registry.values() if ps.placed)
    print(f"Exported {len(save_paths)} turns -> {output_path}")
    print(f"  {placed} pieces on board, {len(all_steps)} replay steps")
    print(f"  Step forward in VASSAL to replay the game")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Export CNA game saves to a VASSAL .vlog replay file",
    )
    parser.add_argument("saves_dir", help="Directory containing gtN.json save files")
    parser.add_argument(
        "-o", "--output", default="game_replay.vlog",
        help="Output .vlog file path (default: game_replay.vlog)",
    )
    parser.add_argument(
        "--range", default=None,
        help="GT range, e.g. 32-40 (default: all saves)",
    )
    parser.add_argument(
        "--template", default=None,
        help="Path to template .vsav (default: VASSAL_CNA/Operation_Brevity_set-upv0.93.vsav)",
    )
    parser.add_argument(
        "--mapping", default=None,
        help="Path to piece mapping JSON (default: built-in)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    gt_range = None
    if args.range:
        parts = args.range.split("-")
        if len(parts) == 2:
            gt_range = (int(parts[0]), int(parts[1]))
        elif len(parts) == 1:
            gt_range = (int(parts[0]), int(parts[0]))

    export_vlog(
        saves_dir=args.saves_dir,
        output_path=args.output,
        gt_range=gt_range,
        template_vsav_path=args.template,
        mapping_path=args.mapping,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
