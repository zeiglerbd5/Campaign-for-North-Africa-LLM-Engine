"""
CNA Engine — Weather Determination Tests
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.weather import (
    determine_weather, get_movement_multiplier, get_water_multiplier,
    can_fly_air_missions, get_evaporation_multiplier,
    WEATHER_TABLE, MOVEMENT_COST_MULTIPLIER,
)
from cna_engine.models.game_state import GameState, TurnState
from cna_engine.models.enums import Weather, Season


def _make_state(season=Season.AUTUMN, weather=Weather.CLEAR, gt=1, op=1):
    state = GameState()
    state.turn = TurnState(
        game_turn=gt, op_stage=op,
        current_season=season, current_weather=weather,
    )
    return state


def test_weather_table():
    print("=" * 60)
    print("TEST 1: Weather Table Completeness")
    print("=" * 60)

    for season in [Season.AUTUMN, Season.WINTER, Season.SPRING, Season.SUMMER]:
        table = WEATHER_TABLE[season]
        assert len(table) == 6
        for roll in range(1, 7):
            assert roll in table
            assert table[roll] in (Weather.CLEAR, Weather.HOT, Weather.MUD, Weather.SANDSTORM)
        print(f"  {season}: {[table[r] for r in range(1, 7)]}")

    print("  PASSED\n")


def test_determine_weather():
    print("=" * 60)
    print("TEST 2: Weather Determination")
    print("=" * 60)

    # Autumn, roll 1 → clear
    state = _make_state(Season.AUTUMN)
    r = determine_weather(state, dice_roll=1)
    assert r.weather == Weather.CLEAR
    assert state.turn.current_weather == Weather.CLEAR
    print(f"  Autumn roll=1: {r.description}")

    # Autumn, roll 6 → sandstorm
    state = _make_state(Season.AUTUMN)
    r2 = determine_weather(state, dice_roll=6)
    assert r2.weather == Weather.SANDSTORM
    assert state.turn.current_weather == Weather.SANDSTORM
    print(f"  Autumn roll=6: {r2.description}")

    # Winter, roll 4 → mud
    state = _make_state(Season.WINTER)
    r3 = determine_weather(state, dice_roll=4)
    assert r3.weather == Weather.MUD
    print(f"  Winter roll=4: {r3.description}")

    # Summer, roll 2 → hot
    state = _make_state(Season.SUMMER)
    r4 = determine_weather(state, dice_roll=2)
    assert r4.weather == Weather.HOT
    print(f"  Summer roll=2: {r4.description}")

    # Previous weather tracking
    state = _make_state(Season.AUTUMN, weather=Weather.HOT)
    r5 = determine_weather(state, dice_roll=1)
    assert r5.previous_weather == Weather.HOT
    assert r5.weather == Weather.CLEAR
    print(f"  Weather change: {r5.previous_weather} → {r5.weather}")

    print("  PASSED\n")


def test_weather_effects():
    print("=" * 60)
    print("TEST 3: Weather Effects")
    print("=" * 60)

    # Clear weather
    state = _make_state(weather=Weather.CLEAR)
    assert get_movement_multiplier(state) == 1.0
    assert get_water_multiplier(state) == 1.0
    assert can_fly_air_missions(state) is True
    assert get_evaporation_multiplier(state) == 1.0
    print(f"  Clear: move=1.0, water=1.0, air=True, evap=1.0")

    # Sandstorm
    state.turn.current_weather = Weather.SANDSTORM
    assert get_movement_multiplier(state) == 2.0
    assert get_water_multiplier(state) == 1.5
    assert can_fly_air_missions(state) is False
    print(f"  Sandstorm: move=2.0, water=1.5, air=False")

    # Hot
    state.turn.current_weather = Weather.HOT
    assert get_movement_multiplier(state) == 1.0
    assert get_water_multiplier(state) == 2.0
    assert can_fly_air_missions(state) is True
    assert get_evaporation_multiplier(state) == 1.5
    print(f"  Hot: move=1.0, water=2.0, air=True, evap=1.5")

    # Mud
    state.turn.current_weather = Weather.MUD
    assert get_movement_multiplier(state) == 1.5
    assert get_water_multiplier(state) == 1.0
    assert can_fly_air_missions(state) is True
    assert get_evaporation_multiplier(state) == 0.5
    print(f"  Mud: move=1.5, water=1.0, air=True, evap=0.5")

    print("  PASSED\n")


def test_weather_result_fields():
    print("=" * 60)
    print("TEST 4: Weather Result Fields")
    print("=" * 60)

    state = _make_state(Season.WINTER, gt=10, op=2)
    r = determine_weather(state, dice_roll=5)

    assert r.game_turn == 10
    assert r.op_stage == 2
    assert r.season == Season.WINTER
    assert r.dice_roll == 5
    assert r.movement_multiplier > 0
    assert r.water_multiplier > 0
    assert isinstance(r.air_ops_allowed, bool)
    print(f"  GT{r.game_turn} OpStage {r.op_stage}: {r.description}")

    print("  PASSED\n")


def main():
    test_weather_table()
    test_determine_weather()
    test_weather_effects()
    test_weather_result_fields()
    print("=" * 60)
    print("ALL WEATHER TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
