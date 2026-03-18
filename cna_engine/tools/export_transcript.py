"""
CNA Standalone Transcript Exporter — generates .cna (text) or .json transcript files.

Produces chess-notation-style game records from save files.

Usage:
    python -m cna_engine.tools.export_transcript saves/gt11.json
    python -m cna_engine.tools.export_transcript --batch saves/ --out game.cna
    python -m cna_engine.tools.export_transcript saves/gt11.json --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cna_engine.tools.export_logsheets import (
    discover_saves,
    extract_events,
    extract_turn_info,
    gt_to_date,
    load_save,
)
from cna_engine.tools.notation import (
    format_game_header,
    format_turn_transcript,
    notate_event,
)


# ════════════════════════════════════════════════════════════════════
# TEXT FORMAT (.cna)
# ════════════════════════════════════════════════════════════════════

def render_text_transcript(save_path: str | Path) -> str:
    """Render a single turn as .cna text notation."""
    data = load_save(save_path)
    turn_info = extract_turn_info(data)
    gt = turn_info["gt"]
    events = extract_events(data, gt)
    return format_turn_transcript(events, turn_info)


def render_full_game_text(save_paths: list[Path]) -> str:
    """Render a complete game as .cna text, with PGN-style header."""
    if not save_paths:
        return ""

    # Determine turn range
    first_data = load_save(save_paths[0])
    last_data = load_save(save_paths[-1])
    first_gt = first_data.get("turn", {}).get("game_turn", 0)
    last_gt = last_data.get("turn", {}).get("game_turn", 0)
    turns_str = f"GT{first_gt}-GT{last_gt}" if first_gt != last_gt else f"GT{first_gt}"

    parts: list[str] = [format_game_header(turns=turns_str)]

    for sp in save_paths:
        data = load_save(sp)
        turn_info = extract_turn_info(data)
        gt = turn_info["gt"]
        events = extract_events(data, gt)
        parts.append(format_turn_transcript(events, turn_info))

    return "\n".join(parts)


# ════════════════════════════════════════════════════════════════════
# JSON FORMAT
# ════════════════════════════════════════════════════════════════════

def _turn_to_json(data: dict) -> dict:
    """Convert one save file to a JSON transcript object."""
    turn_info = extract_turn_info(data)
    gt = turn_info["gt"]
    events = extract_events(data, gt)

    actions: list[dict] = []
    for e in events:
        side, notation = notate_event(e)
        if notation is None:
            continue
        actions.append({
            "side": side,
            "notation": notation,
            "type": e.get("type"),
            "op_stage": e.get("op_stage"),
            "raw": e,
        })

    return {
        "gt": gt,
        "date": turn_info.get("date", ""),
        "weather": turn_info.get("weather", ""),
        "season": turn_info.get("season", ""),
        "actions": actions,
    }


def render_json_transcript(save_path: str | Path) -> str:
    """Single-turn JSON transcript."""
    data = load_save(save_path)
    turn = _turn_to_json(data)
    return json.dumps(turn, indent=2, ensure_ascii=False)


def render_full_game_json(save_paths: list[Path]) -> str:
    """Full-game JSON transcript."""
    first_data = load_save(save_paths[0]) if save_paths else {}
    last_data = load_save(save_paths[-1]) if save_paths else {}
    first_gt = first_data.get("turn", {}).get("game_turn", 0)
    last_gt = last_data.get("turn", {}).get("game_turn", 0)

    turns = []
    for sp in save_paths:
        data = load_save(sp)
        turns.append(_turn_to_json(data))

    output = {
        "scenario": "Operation Compass",
        "turns_range": f"GT{first_gt}-GT{last_gt}",
        "allied_player": "AI",
        "axis_player": "AI",
        "turns": turns,
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="CNA Action Transcript Generator",
    )
    parser.add_argument(
        "save", nargs="?",
        help="Single save file (saves/gtN.json)",
    )
    parser.add_argument(
        "--batch", metavar="DIR",
        help="Process all gt*.json in directory",
    )
    parser.add_argument(
        "--out", metavar="FILE",
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--gt-range", metavar="N-M",
        help="Filter game turns (e.g. 1-10)",
    )
    args = parser.parse_args()

    gt_range = None
    if args.gt_range:
        lo, hi = args.gt_range.split("-")
        gt_range = (int(lo), int(hi))

    if args.batch:
        save_paths = discover_saves(args.batch, gt_range)
        if not save_paths:
            print(f"No save files found in {args.batch}", file=sys.stderr)
            sys.exit(1)
        if args.format == "json":
            output = render_full_game_json(save_paths)
        else:
            output = render_full_game_text(save_paths)
    elif args.save:
        if args.format == "json":
            output = render_json_transcript(args.save)
        else:
            output = render_text_transcript(args.save)
    else:
        parser.print_help()
        sys.exit(1)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
