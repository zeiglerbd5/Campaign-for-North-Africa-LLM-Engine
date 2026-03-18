"""
CNA Engine — Cross-Game Doctrine
Persists tactical lessons across games so the LLM agents improve
over multiple runs. Lessons are extracted from JSONL game logs
and stored in a doctrine file (one lesson per line).
"""
from __future__ import annotations
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# CORRECTIVE GUIDANCE
# Maps error patterns to actionable instructions so doctrine
# doesn't just say "avoid X" but tells the model *what to do instead*.
# ════════════════════════════════════════

CORRECTIVE_GUIDANCE: list[tuple[str, str, str]] = [
    # (command_regex, error_regex, corrective_text)

    # Water supply: draw_water only works on oasis/bir hexes
    (r"draw_water",
     r"terrain is .*(clear|coastal|rough|sand|escarpment)",
     "draw_water ONLY works on oasis or bir terrain. "
     "For water in desert: use unload_port at a port, then truck_attach + truck_load(water) + "
     "truck_unload to deliver water to units. Never issue draw_water on non-oasis hexes."),

    # Truck operations without attachment
    (r"truck_load|truck_unload",
     r"[Nn]o truck attached|truck not attached",
     "Must truck_attach a truck unit to a combat unit BEFORE truck_load/truck_unload. "
     "Sequence: truck_attach(truck_id, unit_id) → truck_load(unit_id, resource, qty) → "
     "truck_unload(unit_id, resource, qty)."),

    # Dump not found
    (r"draw_from_dump",
     r"[Dd]ump.*not found",
     "draw_from_dump requires a supply dump at the SAME hex as the unit. "
     "First create_dump at a hex, then truck supplies to it, then draw_from_dump. "
     "The dump_id is the hex_id where the dump was created."),

    # Port not found
    (r"plan_convoy|unload_port",
     r"[Pp]ort.*not found",
     "Use exact port names from the game state (e.g., 'Alexandria', 'Tobruk', 'Tripoli', "
     "'Benghazi'). Check naval_summary for available ports."),

    # Move: unit already at destination
    (r"move_unit",
     r"already at",
     "Do not move a unit to the hex it already occupies. "
     "Pick a different destination hex to advance toward."),

    # Move: no valid path
    (r"move_unit",
     r"[Nn]o valid path",
     "move_unit computes paths automatically — just provide the destination hex ID. "
     "If you get 'No valid path', the destination may be out of CPA range, blocked by "
     "EZOC, or unreachable. Try a closer hex or a different unit with more CPA."),
]


def _match_corrective(command: str, error: str) -> str | None:
    """Find corrective guidance matching a command+error pattern."""
    for cmd_re, err_re, guidance in CORRECTIVE_GUIDANCE:
        if re.search(cmd_re, command) and re.search(err_re, error):
            return guidance
    return None


class Doctrine:
    """
    Cross-game doctrine that accumulates tactical lessons.

    Loads existing lessons from a JSONL file at game start,
    provides them for prompt injection, and appends new lessons
    extracted from completed game logs.
    """

    def __init__(self, filepath: str = "logs/doctrine.jsonl"):
        self.filepath = filepath
        self.lessons: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load existing doctrine lessons from the JSONL file."""
        if not os.path.exists(self.filepath):
            logger.info("Doctrine: no existing file at %s", self.filepath)
            return

        try:
            with open(self.filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.lessons.append(json.loads(line))
            logger.info("Doctrine: loaded %d lessons from %s", len(self.lessons), self.filepath)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Doctrine: failed to load %s: %s", self.filepath, e)

    def checkpoint(self, game_log_path: str) -> list[dict]:
        """
        Incremental doctrine extraction — safe to call mid-game.
        Extracts lessons from the game log so far and persists them.
        Idempotent: re-analyzes the full log each time but only appends
        lessons not already present (by lesson text dedup).

        Called from the game loop every N turns so lessons survive
        even if the game is interrupted.
        """
        if not os.path.exists(game_log_path):
            return []

        records = []
        try:
            with open(game_log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            return []

        if not records:
            return []

        # Extract candidate lessons
        candidates = []
        candidates.extend(self._analyze_failures(records))
        candidates.extend(self._analyze_supply(records))
        candidates.extend(self._analyze_success(records))

        # Dedup against existing lessons (by lesson text)
        existing_texts = {l.get("lesson", "") for l in self.lessons}
        new_lessons = [l for l in candidates if l.get("lesson", "") not in existing_texts]

        if new_lessons:
            os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)
            try:
                with open(self.filepath, "a") as f:
                    for lesson in new_lessons:
                        f.write(json.dumps(lesson, default=str) + "\n")
                self.lessons.extend(new_lessons)
                logger.info(
                    "Doctrine checkpoint: +%d lessons (%d total)",
                    len(new_lessons), len(self.lessons),
                )
            except OSError as e:
                logger.error("Doctrine checkpoint write failed: %s", e)

        return new_lessons

    def get_doctrine_text(self, side: str, max_chars: int = 1200) -> str:
        """
        Return stable doctrine guidance for prompt injection.

        Args:
            side: "allied" or "axis" — filters to side-relevant lessons.
            max_chars: Maximum character budget.

        Returns:
            Formatted doctrine text for system prompt injection.
        """
        if not self.lessons:
            return ""

        # Filter lessons relevant to this side (or general lessons)
        relevant = [
            l for l in self.lessons
            if l.get("side", "general") in (side, "general")
        ]

        if not relevant:
            return ""

        # Take most recent lessons (later lessons are more refined)
        lines = []
        for lesson in relevant[-10:]:  # Last 10 relevant lessons
            text = lesson.get("lesson", "")
            if text:
                lines.append(f"- {text}")

        if not lines:
            return ""

        header = "Cross-game doctrine (lessons from previous games):"
        text = header + "\n" + "\n".join(lines)

        if len(text) > max_chars:
            text = text[:max_chars - 3] + "..."

        return text

    def update_from_game_log(self, game_log_path: str) -> list[dict]:
        """
        Analyze a completed game's JSONL log and extract new lessons.
        Appends lessons to the doctrine file.

        Args:
            game_log_path: Path to the game's JSONL log file.

        Returns:
            List of newly extracted lesson dicts.
        """
        if not os.path.exists(game_log_path):
            logger.warning("Doctrine: game log not found: %s", game_log_path)
            return []

        # Parse the game log
        records = []
        try:
            with open(game_log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Doctrine: failed to parse game log %s: %s", game_log_path, e)
            return []

        if not records:
            return []

        # Extract lessons from different analysis perspectives
        new_lessons = []
        new_lessons.extend(self._analyze_failures(records))
        new_lessons.extend(self._analyze_supply(records))
        new_lessons.extend(self._analyze_success(records))

        # Append to doctrine file
        if new_lessons:
            os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)
            try:
                with open(self.filepath, "a") as f:
                    for lesson in new_lessons:
                        f.write(json.dumps(lesson, default=str) + "\n")
                self.lessons.extend(new_lessons)
                logger.info(
                    "Doctrine: extracted %d lessons from %s",
                    len(new_lessons), game_log_path,
                )
            except OSError as e:
                logger.error("Doctrine: failed to write %s: %s", self.filepath, e)

        return new_lessons

    def _analyze_failures(self, records: list[dict]) -> list[dict]:
        """Extract lessons from recurring command failures with corrective guidance."""
        lessons = []
        # Count failures per side per command, and track error messages
        fail_counts: dict[str, Counter] = {"allied": Counter(), "axis": Counter()}
        fail_errors: dict[str, dict[str, str]] = {"allied": {}, "axis": {}}

        for rec in records:
            if rec.get("type") != "phase":
                continue
            side = rec.get("side", "")
            if side not in fail_counts:
                continue
            for order in rec.get("orders", []):
                if not order.get("success", True):
                    cmd = order.get("command", "unknown")
                    error = order.get("error", "")
                    key = f"{cmd}:{error[:50]}"
                    fail_counts[side][key] += 1
                    fail_errors[side][key] = error  # keep full error for matching

        for side, counts in fail_counts.items():
            for failure_key, count in counts.most_common(5):
                if count < 2:
                    continue
                cmd, error_short = failure_key.split(":", 1)
                full_error = fail_errors[side].get(failure_key, error_short)

                # Try to match corrective guidance
                correction = _match_corrective(cmd, full_error)
                if correction:
                    lesson_text = f"DO NOT use {cmd} this way (failed {count}x). {correction}"
                else:
                    lesson_text = f"Avoid {cmd} — failed {count} times: {error_short}"

                lessons.append({
                    "side": side,
                    "category": "failure",
                    "lesson": lesson_text,
                    "source_log": records[0].get("timestamp", ""),
                    "extracted_at": datetime.now().isoformat(),
                })

        return lessons

    def _analyze_supply(self, records: list[dict]) -> list[dict]:
        """Extract lessons from supply-related patterns."""
        lessons = []

        # Look at phase records for logistics-related orders
        supply_commands = {"truck_load", "truck_unload", "truck_attach", "truck_detach",
                           "draw_water", "draw_from_dump", "create_dump", "check_supply"}

        for side in ["allied", "axis"]:
            supply_order_count = 0
            supply_fail_count = 0

            for rec in records:
                if rec.get("type") != "phase" or rec.get("side") != side:
                    continue
                for order in rec.get("orders", []):
                    cmd = order.get("command", "")
                    if cmd in supply_commands:
                        supply_order_count += 1
                        if not order.get("success", True):
                            supply_fail_count += 1

            if supply_order_count > 0 and supply_fail_count > supply_order_count * 0.5:
                lessons.append({
                    "side": side,
                    "category": "supply",
                    "lesson": (
                        f"Supply operations unreliable ({supply_fail_count}/{supply_order_count} failed) "
                        f"— verify truck attachment before loading"
                    ),
                    "extracted_at": datetime.now().isoformat(),
                })

            # Check if no supply commands were issued (possible neglect)
            total_phases = sum(
                1 for r in records
                if r.get("type") == "phase" and r.get("side") == side
                and not r.get("skipped", False)
            )
            if total_phases >= 3 and supply_order_count == 0:
                lessons.append({
                    "side": side,
                    "category": "supply",
                    "lesson": "No supply operations issued across the game — resupply water every 2 turns",
                    "extracted_at": datetime.now().isoformat(),
                })

        return lessons

    def _analyze_success(self, records: list[dict]) -> list[dict]:
        """Extract lessons from successful command patterns."""
        lessons = []
        # Count successful commands per side
        success_counts: dict[str, Counter] = {"allied": Counter(), "axis": Counter()}

        for rec in records:
            if rec.get("type") != "phase":
                continue
            side = rec.get("side", "")
            if side not in success_counts:
                continue
            for order in rec.get("orders", []):
                if order.get("success", False) and order.get("command") != "end_phase":
                    success_counts[side][order["command"]] += 1

        for side, counts in success_counts.items():
            for cmd, count in counts.most_common(2):
                if count >= 3:
                    lessons.append({
                        "side": side,
                        "category": "success",
                        "lesson": f"{cmd} was effective ({count} successful uses) — continue employing",
                        "source_log": records[0].get("timestamp", "") if records else "",
                        "extracted_at": datetime.now().isoformat(),
                    })

        return lessons
