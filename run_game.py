#!/usr/bin/env python3
"""
CNA Engine — CLI Game Runner
Launch and observe games from the command line.

Usage:
    python run_game.py                       # Play with Ollama (gpt-oss-20b)
    python run_game.py --mock                # Smart mock LLM (no Ollama needed)
    python run_game.py --mock --turns 3      # 3 turns, mock
    python run_game.py --verbose             # Extra detail
    python run_game.py --load saves/gt5.json # Resume from save
    python run_game.py --model qwen3-30b-a3b # Different model
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Ensure cna_engine is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cna_engine.engine.scenario import load_scenario
from cna_engine.models.serialization import load_state
from cna_engine.orchestrator.config import OrchestratorConfig
from cna_engine.orchestrator.orchestrator import GameOrchestrator
from cna_engine.orchestrator.llm_backend import OllamaClient, MLXClient
from cna_engine.orchestrator.mock_strategies import SmartMockLLMClient
from cna_engine.orchestrator.memory import TurnMemory


# ════════════════════════════════════════
# DISPLAY HELPERS
# ════════════════════════════════════════

def print_banner():
    """Print the game banner."""
    print()
    print("=" * 64)
    print("  THE CAMPAIGN FOR NORTH AFRICA")
    print("  CNA Engine — AI Opponent System")
    print("=" * 64)
    print()


def print_turn_header(state, verbose=False):
    """Print turn start info."""
    t = state.turn
    print(f"\n{'─' * 64}")
    print(f"  GAME TURN {t.game_turn}  |  {t.date_string}  |  OpStage {t.op_stage}")
    print(f"  Weather: {t.current_weather}  |  Initiative: {t.initiative_side or 'undecided'}")
    print(f"{'─' * 64}")

    if verbose:
        print_unit_positions(state)
        print_supply_status(state)


def print_unit_positions(state):
    """Print all active unit positions."""
    print("\n  UNIT POSITIONS:")
    for side_label, side_val in [("Allied", "allied"), ("Axis", "axis")]:
        units = [u for u in state.units.values()
                 if u.side == side_val and u.status == "active" and u.hex_id]
        if units:
            print(f"    {side_label} ({len(units)} active):")
            for u in sorted(units, key=lambda x: x.hex_id or ""):
                cpa = u.effective_cpa
                sp = u.current_strength.total
                print(f"      {u.name:<30s} {u.hex_id}  SP:{sp}  CPA:{cpa}")


def print_supply_status(state):
    """Print supply summary for both sides."""
    print("\n  SUPPLY STATUS:")
    for side_label, side_val in [("Allied", "allied"), ("Axis", "axis")]:
        units = [u for u in state.units.values()
                 if u.side == side_val and u.status == "active" and u.hex_id]
        if not units:
            continue

        critical = 0
        for u in units:
            f_cap = u.supply.fuel_capacity or 1.0
            w_cap = u.supply.water_capacity or 1.0
            if (u.supply.fuel / f_cap) < 0.25 or (u.supply.water / w_cap) < 0.25:
                critical += 1

        total_fuel = sum(u.supply.fuel for u in units)
        total_water = sum(u.supply.water for u in units)
        print(f"    {side_label}: {len(units)} units, "
              f"fuel={total_fuel:.0f}, water={total_water:.0f}, "
              f"{critical} critical")


def print_phase_summary(phase_summary, verbose=False):
    """Print summary of a completed phase."""
    ps = phase_summary
    if ps.skipped:
        print(f"    {ps.side:>7s} {ps.sub_phase or ps.phase:<25s} (skipped)")
        return

    status = "ok" if ps.orders_failed == 0 else f"{ps.orders_failed} failed"
    print(f"    {ps.side:>7s} {ps.sub_phase or ps.phase:<25s} "
          f"orders: {ps.orders_succeeded}/{ps.orders_issued} ({status})")

    if verbose and ps.results:
        for r in ps.results:
            if r.command == "end_phase":
                continue
            mark = "+" if r.success else "X"
            print(f"           {mark} {r.command} {r.params}")


def print_turn_summary(turn_summary, verbose=False):
    """Print end-of-turn summary."""
    ts = turn_summary
    print(f"\n  GT{ts.game_turn} COMPLETE: "
          f"{ts.interactive_phases} interactive, "
          f"{ts.auto_phases} auto phases")

    if ts.phase_summaries:
        print("  Phase breakdown:")
        for ps in ts.phase_summaries:
            print_phase_summary(ps, verbose)


def print_game_over(state, turn_summaries):
    """Print end-of-game summary."""
    print(f"\n{'=' * 64}")
    print(f"  GAME COMPLETE")
    print(f"  Turns played: {len(turn_summaries)}")
    print(f"  Final turn: GT{state.turn.game_turn}")
    print(f"  Date: {state.turn.date_string}")
    print(f"{'=' * 64}")

    # Unit counts
    for side_label, side_val in [("Allied", "allied"), ("Axis", "axis")]:
        active = sum(1 for u in state.units.values()
                     if u.side == side_val and u.status == "active")
        destroyed = sum(1 for u in state.units.values()
                        if u.side == side_val and u.status == "destroyed")
        total_losses = sum(u.losses_taken for u in state.units.values()
                           if u.side == side_val)
        print(f"  {side_label}: {active} active, {destroyed} destroyed, "
              f"{total_losses} SP lost")

    print()


# ════════════════════════════════════════
# GAME SETUP
# ════════════════════════════════════════

def setup_game(args):
    """
    Set up the game: load scenario, select LLM client, create orchestrator.

    Returns:
        (orchestrator, reinforcements)
    """
    tool_calling = getattr(args, "tool_calling", False)
    config = OrchestratorConfig(
        model=args.model,
        temperature=args.temperature,
        backend=args.backend,
        tool_calling=tool_calling,
    )

    # Load or resume game state
    if args.load:
        print(f"  Loading save: {args.load}")
        state = load_state(args.load)
        reinforcements = []  # Reinforcements not restored from save
        # Try to load memory from companion file
        memory = TurnMemory()
        memory_path = args.load.replace(".json", "_memory.json")
        if os.path.exists(memory_path):
            with open(memory_path) as f:
                memory = TurnMemory.from_dict(json.load(f))
            print(f"  Loaded memory from {memory_path}")
    else:
        print(f"  Loading scenario: {args.scenario}")
        state, reinforcements = load_scenario(args.scenario)
        memory = TurnMemory()

    # Select LLM client
    llm_client = None
    if args.mock:
        print("  LLM: Smart Mock (no LLM server needed)")
        llm_client = SmartMockLLMClient(state, config)
    elif args.backend in ("auto", "mlx"):
        mlx = MLXClient(config)
        if mlx.is_available():
            print(f"  LLM: MLX ({config.model}) at {config.mlx_url}")
            llm_client = mlx
        elif args.backend == "auto":
            # MLX unavailable, try Ollama with appropriate model
            if args.model is None or "mlx-community" in args.model:
                config.model = "qwen3:8b"
            ollama = OllamaClient(config)
            if ollama.is_available():
                print(f"  LLM: Ollama ({config.model})")
                llm_client = ollama
    if args.backend == "ollama" and llm_client is None and not args.mock:
        ollama = OllamaClient(config)
        if ollama.is_available():
            print(f"  LLM: Ollama ({config.model})")
            llm_client = ollama
    if llm_client is None and not args.mock:
        print(f"  No LLM server available — falling back to Smart Mock")
        llm_client = SmartMockLLMClient(state, config)

    # Tool-calling stays off by default — Qwen3-8B explores but never
    # issues orders in the agentic loop. Use --tool-calling to opt in.

    # Create orchestrator
    save_dir = args.save_dir
    log_dir = getattr(args, "log_dir", "logs")
    orch = GameOrchestrator(state, config)
    orch.setup(
        llm_client=llm_client,
        reinforcements=reinforcements,
        memory=memory,
        save_dir=save_dir,
        log_dir=log_dir,
    )

    if orch.game_log:
        print(f"  Game log: {orch.game_log.filepath}")
    if orch.doctrine:
        print(f"  Doctrine: {orch.doctrine.filepath}")

    return orch


# ════════════════════════════════════════
# MAIN GAME LOOP
# ════════════════════════════════════════

def run_game(args):
    """Main game loop."""
    print_banner()
    orch = setup_game(args)

    # Signal handling for graceful Ctrl+C
    stop_flag = {"stop": False}

    def handle_sigint(signum, frame):
        if stop_flag["stop"]:
            # Second Ctrl+C: force exit
            print("\n  Force exit!")
            sys.exit(1)
        print("\n  Ctrl+C received — finishing current turn and saving...")
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, handle_sigint)

    # Game loop
    turn_summaries = []
    max_turns = args.turns

    while not orch.runner.is_game_over():
        if max_turns is not None and len(turn_summaries) >= max_turns:
            break
        if stop_flag["stop"]:
            break

        print_turn_header(orch.state, verbose=args.verbose)

        start_time = time.monotonic()
        summary = orch.play_turn()
        elapsed = time.monotonic() - start_time

        turn_summaries.append(summary)
        print_turn_summary(summary, verbose=args.verbose)
        print(f"  ({elapsed:.1f}s)")

        # Incremental doctrine checkpoint every 5 turns
        if (len(turn_summaries) % 5 == 0
                and orch.doctrine and orch.game_log):
            new = orch.doctrine.checkpoint(orch.game_log.filepath)
            if new:
                print(f"  Doctrine checkpoint: +{len(new)} lesson(s)")
                # Add new lessons to RAG index if available
                if orch.rag and orch.rag.is_available:
                    orch.rag.add_documents(new)

    # Final save
    if orch.save_dir:
        gt = orch.state.turn.game_turn
        # Also save memory alongside state
        memory_path = os.path.join(orch.save_dir, f"gt{gt}_memory.json")
        with open(memory_path, "w") as f:
            json.dump(orch.memory.to_dict(), f, indent=2)

    # Extract cross-game doctrine lessons from the game log
    if orch.doctrine and orch.game_log:
        lessons = orch.doctrine.update_from_game_log(orch.game_log.filepath)
        if lessons:
            print(f"\n  Doctrine: extracted {len(lessons)} lesson(s) from this game")
            for lesson in lessons:
                print(f"    - {lesson.get('lesson', '')}")

    print_game_over(orch.state, turn_summaries)

    if stop_flag["stop"]:
        print("  Game interrupted — state saved.")

    return turn_summaries


# ════════════════════════════════════════
# CLI ENTRY POINT
# ════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="CNA Engine — Campaign for North Africa AI Game Runner",
    )
    parser.add_argument(
        "--backend", type=str, default="auto",
        choices=["auto", "ollama", "mlx"],
        help="LLM backend: auto (try mlx then ollama), ollama, or mlx",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use smart mock LLM (no LLM server needed)",
    )
    parser.add_argument(
        "--turns", type=int, default=None,
        help="Number of game turns to play (default: unlimited)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show extra detail (unit positions, supply, order params)",
    )
    parser.add_argument(
        "--load", type=str, default=None,
        help="Load game state from JSON file",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Model name (auto-detected per backend if not set)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.3,
        help="LLM temperature (default: 0.3)",
    )
    parser.add_argument(
        "--scenario", type=str, default="operation_compass",
        help="Scenario to load (default: operation_compass)",
    )
    parser.add_argument(
        "--save-dir", type=str, default="saves",
        help="Directory for auto-saves (default: saves/)",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Disable auto-saving",
    )
    parser.add_argument(
        "--tool-calling", action="store_true",
        help="Use agentic tool-calling loop (requires mlx backend)",
    )
    parser.add_argument(
        "--log-dir", type=str, default="logs",
        help="Directory for log files (default: logs/)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Auto-default model per backend
    if args.model is None:
        if args.backend == "ollama":
            args.model = "qwen3:8b"
        else:
            from cna_engine.orchestrator.config import MLX_DEFAULT_MODEL
            args.model = MLX_DEFAULT_MODEL

    if args.no_save:
        args.save_dir = None

    # Set up logging
    console_level = logging.INFO if args.verbose else logging.WARNING
    file_level = logging.DEBUG  # always capture everything to file

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Console handler (respects --verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    root_logger.addHandler(console_handler)

    # File handler (always DEBUG, timestamped, rotated)
    log_dir = args.log_dir
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"cna_{timestamp}.log")
    file_handler = RotatingFileHandler(
        log_file, maxBytes=50 * 1024 * 1024, backupCount=5,
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root_logger.addHandler(file_handler)

    logging.info("Log file: %s", log_file)
    print(f"  Log file: {log_file}")

    # Pass log_dir so orchestrator setup() can use it
    args.log_dir = log_dir

    run_game(args)


if __name__ == "__main__":
    main()
