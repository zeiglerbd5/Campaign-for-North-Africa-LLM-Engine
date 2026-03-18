"""
CNA Engine — Weather Determination Module
Determines weather for each OpStage based on season, applies
weather effects to movement costs, air operations, and supply.

Weather is rolled at the start of each OpStage's WEATHER phase.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from cna_engine.models.game_state import GameState
from cna_engine.models.enums import Weather, Season
from cna_engine.engine.combat import roll_d6


# ════════════════════════════════════════
# WEATHER DETERMINATION TABLE
# ════════════════════════════════════════

# Season → d6 roll → Weather result
# Based on NJH North Africa climate patterns
WEATHER_TABLE: dict[str, dict[int, str]] = {
    Season.AUTUMN: {
        1: Weather.CLEAR,
        2: Weather.CLEAR,
        3: Weather.CLEAR,
        4: Weather.CLEAR,
        5: Weather.HOT,
        6: Weather.SANDSTORM,
    },
    Season.WINTER: {
        1: Weather.CLEAR,
        2: Weather.CLEAR,
        3: Weather.CLEAR,
        4: Weather.MUD,
        5: Weather.MUD,
        6: Weather.SANDSTORM,
    },
    Season.SPRING: {
        1: Weather.CLEAR,
        2: Weather.CLEAR,
        3: Weather.CLEAR,
        4: Weather.CLEAR,
        5: Weather.SANDSTORM,
        6: Weather.HOT,
    },
    Season.SUMMER: {
        1: Weather.CLEAR,
        2: Weather.HOT,
        3: Weather.HOT,
        4: Weather.HOT,
        5: Weather.HOT,
        6: Weather.SANDSTORM,
    },
}

# ════════════════════════════════════════
# WEATHER EFFECTS
# ════════════════════════════════════════

# Movement cost multipliers by weather
MOVEMENT_COST_MULTIPLIER = {
    Weather.CLEAR: 1.0,
    Weather.HOT: 1.0,       # Hot doesn't slow movement, but increases water consumption
    Weather.MUD: 1.5,       # Mud slows all movement 50%
    Weather.SANDSTORM: 2.0, # Sandstorm doubles movement costs
}

# Water consumption multipliers
WATER_CONSUMPTION_MULTIPLIER = {
    Weather.CLEAR: 1.0,
    Weather.HOT: 2.0,       # Double water consumption
    Weather.MUD: 1.0,
    Weather.SANDSTORM: 1.5, # Increased from dust/dehydration
}

# Air operations allowed
AIR_OPS_ALLOWED = {
    Weather.CLEAR: True,
    Weather.HOT: True,
    Weather.MUD: True,       # Can still fly in mud weather (ground effect only)
    Weather.SANDSTORM: False, # No air ops in sandstorm
}

# Evaporation rate multiplier (hot weather increases evaporation)
EVAPORATION_MULTIPLIER = {
    Weather.CLEAR: 1.0,
    Weather.HOT: 1.5,       # 50% more evaporation
    Weather.MUD: 0.5,       # Reduced evaporation in wet weather
    Weather.SANDSTORM: 1.0,
}


# ════════════════════════════════════════
# RESULT DATACLASS
# ════════════════════════════════════════

@dataclass
class WeatherResult:
    """Result of weather determination."""
    game_turn: int
    op_stage: int
    season: str
    dice_roll: int
    weather: str
    previous_weather: str
    movement_multiplier: float
    water_multiplier: float
    air_ops_allowed: bool
    description: str = ""


# ════════════════════════════════════════
# WEATHER DETERMINATION
# ════════════════════════════════════════

def determine_weather(
    state: GameState,
    dice_roll: Optional[int] = None,
) -> WeatherResult:
    """
    Determine weather for the current OpStage.
    Called at the start of the WEATHER phase.
    """
    season = state.turn.current_season
    roll = dice_roll if dice_roll is not None else roll_d6()
    previous = state.turn.current_weather

    season_table = WEATHER_TABLE.get(season, WEATHER_TABLE[Season.AUTUMN])
    weather = season_table.get(roll, Weather.CLEAR)

    # Apply to state
    state.turn.current_weather = weather

    move_mult = MOVEMENT_COST_MULTIPLIER.get(weather, 1.0)
    water_mult = WATER_CONSUMPTION_MULTIPLIER.get(weather, 1.0)
    air_ok = AIR_OPS_ALLOWED.get(weather, True)

    desc = (f"GT{state.turn.game_turn} OpStage {state.turn.op_stage} "
            f"Weather: {season} season, roll={roll} → {weather.upper()}")
    if weather != previous:
        desc += f" (was {previous})"

    state.log_event("weather", desc, season=season, roll=roll,
                    weather=weather)

    return WeatherResult(
        game_turn=state.turn.game_turn,
        op_stage=state.turn.op_stage,
        season=season,
        dice_roll=roll,
        weather=weather,
        previous_weather=previous,
        movement_multiplier=move_mult,
        water_multiplier=water_mult,
        air_ops_allowed=air_ok,
        description=desc,
    )


# ════════════════════════════════════════
# WEATHER QUERIES
# ════════════════════════════════════════

def get_movement_multiplier(state: GameState) -> float:
    """Get current movement cost multiplier from weather."""
    return MOVEMENT_COST_MULTIPLIER.get(state.turn.current_weather, 1.0)


def get_water_multiplier(state: GameState) -> float:
    """Get current water consumption multiplier from weather."""
    return WATER_CONSUMPTION_MULTIPLIER.get(state.turn.current_weather, 1.0)


def can_fly_air_missions(state: GameState) -> bool:
    """Check if air operations are permitted in current weather."""
    return AIR_OPS_ALLOWED.get(state.turn.current_weather, True)


def get_evaporation_multiplier(state: GameState) -> float:
    """Get evaporation rate multiplier from weather."""
    return EVAPORATION_MULTIPLIER.get(state.turn.current_weather, 1.0)
