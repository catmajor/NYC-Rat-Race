"""Small deterministic game simulation used by the Rat Race UI.

The simulation deliberately keeps the hidden outcome separate from the
player-facing round state. That makes it useful in local demo mode while
leaving a clear seam for the trained demand model and historical trip replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math
import random
from typing import Dict, List, Mapping, Optional

from .demand_model import DemandModel
from .event_catalog import select_events
from .models import ZONE_IDS


FLEET_SIZE = 130
START_DATE = date(2019, 10, 18)
OPERATING_START_HOUR = 8
TURN_HOURS = 3
TURNS_PER_DAY = 4
OPERATING_END_HOUR = OPERATING_START_HOUR + TURN_HOURS * TURNS_PER_DAY
TOTAL_TURNS = 3 * TURNS_PER_DAY

# Keep the old name as a wire-compatibility alias. The game itself is now
# described in terms of operating turns/windows, not rounds.
TOTAL_ROUNDS = TOTAL_TURNS
STARTING_CURRENCY = 2450.0
FARE_PER_TRIP = 21.5
REPOSITION_COST_PER_TAXI = 4.25
# Currency is earned from the trips the fleet actually serves, but the model
# read controls how much of that fare is bankable. A bad read still earns the
# floor for operating a cab; an exact distribution match earns the full fare.
MIN_MODEL_PAYOUT_FACTOR = 0.25
# The real feature store contains all city pickups, while the game owns only
# 130 cabs. Normalize the hidden three-hour opportunity pool to a playable
# scale while preserving the model's regional demand mix.
DEMAND_TO_FLEET_RATIO = 1.15

ZONE_LABELS: Dict[str, str] = {
    "harlem": "Harlem",
    "upper_west": "Upper West",
    "upper_east": "Upper East",
    "midtown": "Midtown",
    "downtown": "Downtown",
    "north_brooklyn": "North Brooklyn",
    "south_brooklyn": "South Brooklyn",
    "queens_west": "Queens West",
    "airports": "Airports",
    "queens_east": "Queens East",
    "bronx": "The Bronx",
    "staten_island": "Staten Island",
}

INITIAL_ALLOCATION: Dict[str, int] = {
    "harlem": 7,
    "upper_west": 10,
    "upper_east": 12,
    "midtown": 26,
    "downtown": 15,
    "north_brooklyn": 12,
    "south_brooklyn": 6,
    "queens_west": 12,
    "airports": 18,
    "queens_east": 6,
    "bronx": 4,
    "staten_island": 2,
}

BASELINE_BY_ZONE: Dict[str, float] = {
    "harlem": 24,
    "upper_west": 28,
    "upper_east": 31,
    "midtown": 52,
    "downtown": 42,
    "north_brooklyn": 35,
    "south_brooklyn": 22,
    "queens_west": 34,
    "airports": 30,
    "queens_east": 28,
    "bronx": 26,
    "staten_island": 12,
}

ROUND_PEAKS: List[Dict[str, float]] = [
    {"midtown": 1.58, "airports": 1.36, "downtown": 1.22},
    {"midtown": 1.46, "downtown": 1.38, "upper_east": 1.18},
    {"upper_east": 1.32, "north_brooklyn": 1.26, "queens_west": 1.2},
    {"downtown": 1.54, "north_brooklyn": 1.36, "midtown": 1.28},
]

WEATHER_BY_ROUND = [
    {"label": "Bright / crisp", "temperature_c": 17, "rain_mm": 0.0, "wind_mps": 3.4, "visibility_km": 10.0},
    {"label": "Light cloud", "temperature_c": 18, "rain_mm": 0.0, "wind_mps": 4.2, "visibility_km": 9.4},
    {"label": "Passing shower", "temperature_c": 16, "rain_mm": 1.8, "wind_mps": 5.1, "visibility_km": 7.3},
    {"label": "Rain easing", "temperature_c": 15, "rain_mm": 3.2, "wind_mps": 5.8, "visibility_km": 6.5},
]

NEWS_BY_ROUND = [
    ("MIDTOWN", "Morning arrivals are compressing around Penn Station", "12 min ago", "high"),
    ("DOWNTOWN", "City Hall hearing adds a slow-moving crowd downtown", "27 min ago", "medium"),
    ("UPPER EAST", "Museum blocks are showing a strong afternoon pulse", "41 min ago", "medium"),
    ("NORTH BROOKLYN", "Williamsburg foot traffic is climbing into dinner", "9 min ago", "high"),
]


def _round_time(day: int, round_number: int) -> datetime:
    """Return a playable window start and skip the overnight closed period."""
    simulation_date = START_DATE + timedelta(days=day - 1)
    return datetime.combine(simulation_date, time(OPERATING_START_HOUR, 0)) + timedelta(
        hours=(round_number - 1) * TURN_HOURS,
    )


def _profile_for(round_number: int, day: int) -> Dict[str, object]:
    index = (round_number - 1) % 4
    rng = random.Random(2400 + day * 100 + round_number)
    peaks = ROUND_PEAKS[index]
    weather = dict(WEATHER_BY_ROUND[index])
    weather["temperature_c"] = float(weather["temperature_c"]) + (day - 1) * 0.6

    baseline: Dict[str, float] = {}
    model_forecast: Dict[str, float] = {}
    actual_demand: Dict[str, float] = {}
    recent_demand: Dict[str, float] = {}
    for zone_id in ZONE_IDS:
        base = BASELINE_BY_ZONE[zone_id]
        day_drift = 1.0 + (day - 1) * 0.045
        noise = rng.uniform(-0.07, 0.07)
        peak = peaks.get(zone_id, 1.0)
        baseline_value = base * day_drift * (1.0 + noise * 0.5)
        rain_lift = 0.14 if weather["rain_mm"] and zone_id in {"midtown", "downtown", "upper_east"} else 0.0
        airport_drag = -0.07 if weather["rain_mm"] and zone_id == "airports" else 0.0
        model_value = baseline_value * peak * (1.0 + rain_lift + airport_drag)
        actual_value = model_value * (0.93 + rng.random() * 0.17)
        recent_value = baseline_value * (0.94 + rng.random() * 0.12)
        baseline[zone_id] = round(baseline_value)
        model_forecast[zone_id] = round(model_value)
        actual_demand[zone_id] = round(actual_value)
        recent_demand[zone_id] = round(recent_value)

    event_zone, headline, age, intensity = NEWS_BY_ROUND[index]
    event_zone_id = next(zone_id for zone_id, label in ZONE_LABELS.items() if label.upper() == event_zone)
    events = {zone_id: 0.0 for zone_id in ZONE_IDS}
    events[event_zone_id] = 1.0 if intensity == "medium" else 1.35
    events["event_count"] = 3.0 + index + day
    events["num_articles"] = 9.0 + index * 2 + day
    return {
        "baseline": baseline,
        "model_forecast": model_forecast,
        "actual_demand": actual_demand,
        "recent_demand": recent_demand,
        "weather": weather,
        "events": events,
        "news": [{
            "zone": event_zone,
            "zone_id": event_zone_id,
            "headline": headline,
            "age": age,
            "intensity": intensity,
        }],
    }


def _model_allocation(forecast: Mapping[str, float], fleet: int) -> Dict[str, int]:
    total = sum(max(value, 0.0) for value in forecast.values()) or 1.0
    raw = {zone_id: fleet * max(forecast.get(zone_id, 0.0), 0.0) / total for zone_id in ZONE_IDS}
    allocation = {zone_id: math.floor(value) for zone_id, value in raw.items()}
    remainder = fleet - sum(allocation.values())
    for zone_id in sorted(ZONE_IDS, key=lambda key: raw[key] - allocation[key], reverse=True)[:remainder]:
        allocation[zone_id] += 1
    return allocation


def _playable_demand(raw_demand: Mapping[str, float], fleet: int) -> Dict[str, int]:
    """Scale citywide demand into a deterministic opportunity pool for Rat Cab."""
    total_raw = sum(max(value, 0.0) for value in raw_demand.values()) or 1.0
    target = max(fleet, round(fleet * DEMAND_TO_FLEET_RATIO))
    raw = {
        zone_id: target * max(raw_demand.get(zone_id, 0.0), 0.0) / total_raw
        for zone_id in ZONE_IDS
    }
    demand = {zone_id: math.floor(value) for zone_id, value in raw.items()}
    remainder = target - sum(demand.values())
    for zone_id in sorted(ZONE_IDS, key=lambda key: raw[key] - demand[key], reverse=True)[:remainder]:
        demand[zone_id] += 1
    return demand


def _match_percentage(player: Mapping[str, int], target: Mapping[str, int], fleet: int) -> int:
    if not fleet:
        return 0
    absolute_error = sum(abs(player.get(zone_id, 0) - target.get(zone_id, 0)) for zone_id in ZONE_IDS)
    return max(0, round(100 - (absolute_error / (fleet * 2)) * 100))


def _model_payout_factor(match_percentage: int) -> float:
    """Convert allocation accuracy into a fare multiplier for the bank."""
    accuracy = max(0.0, min(100.0, float(match_percentage))) / 100.0
    return round(MIN_MODEL_PAYOUT_FACTOR + (1.0 - MIN_MODEL_PAYOUT_FACTOR) * accuracy, 4)


@dataclass
class GameSession:
    """One local player session. A process-local session is enough for the demo."""

    day: int = 1
    round_number: int = 1
    currency: float = STARTING_CURRENCY
    idle_taxis: Dict[str, int] = field(default_factory=lambda: dict(INITIAL_ALLOCATION))
    completed: bool = False
    rounds_completed: int = 0
    total_trips_captured: int = 0
    total_trips_missed: int = 0
    total_gross_revenue: float = 0.0
    total_reposition_cost: float = 0.0
    total_net_revenue: float = 0.0
    total_model_match: int = 0
    best_model_match: int = 0
    game_over_reason: Optional[str] = None
    last_result: Optional[Dict[str, object]] = None

    def __post_init__(self) -> None:
        self._rng = random.Random(20191018)
        self._demand_model = DemandModel()
        self._initialize_world()

    @property
    def timestamp(self) -> datetime:
        return _round_time(self.day, self.round_number)

    @property
    def fleet_available(self) -> int:
        return sum(self.idle_taxis.values())

    @property
    def turn_number(self) -> int:
        """One-based turn number across all three operating days."""
        return (self.day - 1) * TURNS_PER_DAY + self.round_number

    @property
    def score(self) -> float:
        """Compatibility alias: points are always the current bank balance."""
        return self.currency

    def reset(self) -> None:
        self.day = 1
        self.round_number = 1
        self.currency = STARTING_CURRENCY
        self.idle_taxis = dict(INITIAL_ALLOCATION)
        self.completed = False
        self.rounds_completed = 0
        self.total_trips_captured = 0
        self.total_trips_missed = 0
        self.total_gross_revenue = 0.0
        self.total_reposition_cost = 0.0
        self.total_net_revenue = 0.0
        self.total_model_match = 0
        self.best_model_match = 0
        self.game_over_reason = None
        self.last_result = None
        self._rng = random.Random(20191018)
        self._initialize_world()

    def _initialize_world(self) -> None:
        """Seed every region from the real historical snapshot for game start."""
        from .config import get_point_in_time_signals

        signals = get_point_in_time_signals()
        city_weather = signals.weather_at(self.timestamp)
        if not city_weather:
            raise RuntimeError("Real weather data has no observation at the game start timestamp")
        visibility_m = float(city_weather.get("visibility_m", 10_000.0))
        self._weather_by_zone = {
            zone_id: {
                "temperature_c": round(float(city_weather.get("temperature_c", 0.0)), 3),
                "rain_mm": round(max(0.0, float(city_weather.get("rain_mm", 0.0))), 3),
                "wind_mps": round(max(0.0, float(city_weather.get("wind_mps", 0.0))), 3),
                "visibility_km": round(max(0.1, visibility_m / 1000.0), 3),
            }
            for zone_id in ZONE_IDS
        }
        self._weather_bias = {
            zone_id: {
                key: self._rng.choice((-1.0, 1.0))
                for key in self._weather_by_zone[zone_id]
            }
            for zone_id in ZONE_IDS
        }
        self._signals = signals
        self._refresh_profile()

    def _advance_weather(self) -> None:
        step_size = {
            "temperature_c": 0.45,
            "rain_mm": 0.35,
            "wind_mps": 0.3,
            "visibility_km": 0.45,
        }
        bounds = {
            "temperature_c": (-20.0, 42.0),
            "rain_mm": (0.0, 30.0),
            "wind_mps": (0.0, 25.0),
            "visibility_km": (0.2, 20.0),
        }
        for zone_id in ZONE_IDS:
            for key, bias in self._weather_bias[zone_id].items():
                roll = self._rng.uniform(-1.0, 1.0)
                low, high = bounds[key]
                value = self._weather_by_zone[zone_id][key] + (roll + bias) * step_size[key]
                self._weather_by_zone[zone_id][key] = round(max(low, min(high, value)), 3)

    def _refresh_profile(self) -> None:
        events = dict(self._signals.events_at(self.timestamp))
        news = select_events(self.timestamp, self._weather_by_zone, events)
        events["event_count"] = events.get("event_count", 0.0) + len(news)
        for item in news:
            zone_id = str(item["zone_id"])
            tone = float(item["tone"])
            events[zone_id] = max(events.get(zone_id, 0.0), 1.0)
            events[f"event_count:{zone_id}"] = events.get(f"event_count:{zone_id}", 0.0) + 1.0
            events[f"zone_event_count:{zone_id}"] = events.get(f"zone_event_count:{zone_id}", 0.0) + 1.0
            events[f"zone_avg_tone:{zone_id}"] = tone
        self._events = events
        forecast, confidence = self._demand_model.predict(
            self.timestamp,
            self._weather_by_zone,
            self._events,
            list(ZONE_IDS),
        )
        baseline = {zone_id: round(forecast[zone_id] * 0.92) for zone_id in ZONE_IDS}
        raw_actual = {
            zone_id: forecast[zone_id] * self._rng.uniform(0.88, 1.12)
            for zone_id in ZONE_IDS
        }
        actual = _playable_demand(raw_actual, FLEET_SIZE)
        self._model_confidence = confidence
        self._profile = {
            "baseline": baseline,
            "model_forecast": forecast,
            "actual_demand": actual,
            "recent_demand": dict(baseline),
            "weather": self._city_weather(),
            "weather_by_zone": {
                zone_id: {
                    **self._weather_by_zone[zone_id],
                    "label": "Wet conditions" if self._weather_by_zone[zone_id]["rain_mm"] > 0.2 else "Clear conditions",
                    "note": "Weather is evolving from the real historical snapshot",
                }
                for zone_id in ZONE_IDS
            },
            "events": dict(self._events),
            "news": news,
        }

    def _city_weather(self) -> Dict[str, object]:
        weather: Dict[str, object] = {
            key: round(
                sum(self._weather_by_zone[zone_id][key] for zone_id in ZONE_IDS) / len(ZONE_IDS),
                3,
            )
            for key in ("temperature_c", "rain_mm", "wind_mps", "visibility_km")
        }
        weather["label"] = "Wet conditions" if float(weather["rain_mm"]) > 0.2 else "Clear conditions"
        weather["note"] = "Weather is evolving from the real historical snapshot"
        return weather

    def state(self) -> Dict[str, object]:
        profile = self._profile
        assert isinstance(profile, dict)
        baseline = profile["baseline"]
        model_forecast = profile["model_forecast"]
        recent_demand = profile["recent_demand"]
        assert isinstance(baseline, dict)
        assert isinstance(model_forecast, dict)
        assert isinstance(recent_demand, dict)
        total_forecast = sum(model_forecast.values()) or 1
        zones = {}
        for zone_id in ZONE_IDS:
            forecast = float(model_forecast[zone_id])
            zones[zone_id] = {
                "id": zone_id,
                "name": ZONE_LABELS[zone_id],
                "recent_demand": recent_demand[zone_id],
                "baseline_demand": baseline[zone_id],
                "model_forecast": forecast,
                "model_share": round(forecast / total_forecast, 4),
                "idle_taxis": self.idle_taxis.get(zone_id, 0),
                "trend": "up" if forecast >= float(baseline[zone_id]) else "flat",
                "weather": dict(profile["weather_by_zone"][zone_id]),
            }
        weather = dict(profile["weather"])
        weather["note"] = "Wet roads favor dense central pickups" if weather["rain_mm"] else "Good visibility across the core"
        return {
            "game_id": "rat-cab-demo",
            "day": self.day,
            "turn": self.turn_number,
            "turn_in_day": self.round_number,
            "total_turns": TOTAL_TURNS,
            # Legacy aliases for older clients. New clients should use turn
            # and turn_in_day so the UI does not need a round concept.
            "round": self.round_number,
            "total_rounds": TOTAL_ROUNDS,
            "timestamp": self.timestamp.isoformat(),
            "time_start": self.timestamp.strftime("%H:%M"),
            "time_end": (self.timestamp + timedelta(hours=3)).strftime("%H:%M"),
            "fleet_size": FLEET_SIZE,
            "fleet_available": self.fleet_available,
            "assigned": self.fleet_available,
            "currency": round(self.currency, 2),
            "score": round(self.currency, 2),
            "points": round(self.currency, 2),
            "starting_currency": STARTING_CURRENCY,
            "turns_completed": self.rounds_completed,
            "rounds_completed": self.rounds_completed,
            "total_trips_captured": self.total_trips_captured,
            "total_trips_missed": self.total_trips_missed,
            "total_gross_revenue": round(self.total_gross_revenue, 2),
            "total_reposition_cost": round(self.total_reposition_cost, 2),
            "total_net_revenue": round(self.total_net_revenue, 2),
            "average_model_match": round(self.total_model_match / self.rounds_completed, 1) if self.rounds_completed else 0.0,
            "best_model_match": self.best_model_match,
            "game_over_reason": self.game_over_reason,
            "zones": zones,
            "weather": weather,
            "events": profile["events"],
            "news": profile["news"],
            "model_name": "ONNX demand model",
            "model_confidence": self._model_confidence,
            "data_source": "real-history+onnx",
            "weather_bias": self._weather_bias,
            "last_result": self.last_result,
            "completed": self.completed,
        }

    def advance(self, allocation: Mapping[str, int]) -> Dict[str, object]:
        if self.completed:
            raise ValueError("This game is already complete")
        unknown = set(allocation) - set(ZONE_IDS)
        if unknown:
            raise ValueError(f"Unknown zones: {', '.join(sorted(unknown))}")
        normalized = {zone_id: int(allocation.get(zone_id, 0)) for zone_id in ZONE_IDS}
        if any(value < 0 for value in normalized.values()):
            raise ValueError("Allocation cannot contain negative taxi counts")
        if sum(normalized.values()) != self.fleet_available:
            raise ValueError(f"Allocate exactly {self.fleet_available} taxis before dispatch")

        profile = self._profile
        forecast = profile["model_forecast"]
        actual = profile["actual_demand"]
        assert isinstance(forecast, dict)
        assert isinstance(actual, dict)
        model_allocation = _model_allocation(forecast, self.fleet_available)
        match_percentage = _match_percentage(normalized, model_allocation, self.fleet_available)
        trips_captured = sum(min(normalized[zone_id], int(actual[zone_id])) for zone_id in ZONE_IDS)
        trips_model = sum(min(model_allocation[zone_id], int(actual[zone_id])) for zone_id in ZONE_IDS)
        moved = sum(max(normalized[zone_id] - self.idle_taxis.get(zone_id, 0), 0) for zone_id in ZONE_IDS)
        trips_missed = sum(max(int(actual[zone_id]) - normalized[zone_id], 0) for zone_id in ZONE_IDS)
        total_demand = sum(int(actual[zone_id]) for zone_id in ZONE_IDS)
        capture_rate = trips_captured / total_demand if total_demand else 0.0
        reposition_cost = round(moved * REPOSITION_COST_PER_TAXI, 2)
        gross_revenue = round(trips_captured * FARE_PER_TRIP, 2)
        model_payout_factor = _model_payout_factor(match_percentage)
        model_aligned_revenue = round(gross_revenue * model_payout_factor, 2)
        model_alignment_adjustment = round(model_aligned_revenue - gross_revenue, 2)
        net_revenue = round(model_aligned_revenue - reposition_cost, 2)

        bank_before = round(self.currency, 2)
        self.currency = round(self.currency + net_revenue, 2)
        self.rounds_completed += 1
        self.total_trips_captured += trips_captured
        self.total_trips_missed += trips_missed
        self.total_gross_revenue = round(self.total_gross_revenue + gross_revenue, 2)
        self.total_reposition_cost = round(self.total_reposition_cost + reposition_cost, 2)
        self.total_net_revenue = round(self.total_net_revenue + net_revenue, 2)
        self.total_model_match += match_percentage
        self.best_model_match = max(self.best_model_match, match_percentage)

        result = {
            "day": self.day,
            "turn": self.turn_number,
            "turn_in_day": self.round_number,
            "round": self.round_number,
            "time_start": self.timestamp.strftime("%H:%M"),
            "time_end": (self.timestamp + timedelta(hours=3)).strftime("%H:%M"),
            "gross_revenue": gross_revenue,
            "model_payout_factor": model_payout_factor,
            "model_aligned_revenue": model_aligned_revenue,
            "model_alignment_adjustment": model_alignment_adjustment,
            "reposition_cost": reposition_cost,
            "taxi_move_cost": reposition_cost,
            "net_revenue": net_revenue,
            "bank_before": bank_before,
            "bank_change": net_revenue,
            "bank_after": self.currency,
            # Points are the bank. These aliases remain in the result schema
            # so older clients can render the same single ledger.
            "score_gain": net_revenue,
            "total_score": self.currency,
            "currency": self.currency,
            "trips_captured": trips_captured,
            "trips_missed": trips_missed,
            "capture_rate": round(capture_rate, 4),
            "trips_model": trips_model,
            "repositioned_taxis": moved,
            "model_match_percentage": match_percentage,
            "bank_breakdown": {
                "fare_revenue": gross_revenue,
                "model_payout_factor": model_payout_factor,
                "model_aligned_revenue": model_aligned_revenue,
                "taxi_move_cost": reposition_cost,
                "bank_change": net_revenue,
            },
            "model_allocation": model_allocation,
            "actual_demand": actual,
            "verdict": "Strong read" if match_percentage >= 80 else "Mixed signal" if match_percentage >= 58 else "Missed the pulse",
        }

        if self.round_number == TURNS_PER_DAY:
            if self.day == 3:
                self.completed = True
                self.game_over_reason = "turns_complete"
            else:
                self.day += 1
                self.round_number = 1
        else:
            self.round_number += 1
        self.idle_taxis = normalized
        self.last_result = result
        if not self.completed:
            self._advance_weather()
            self._refresh_profile()
        return result

    def timeout(self) -> None:
        """End the run without advancing when the decision clock expires."""
        if self.completed:
            raise ValueError("This game is already complete")
        self.completed = True
        self.game_over_reason = "time_expired"


SESSION = GameSession()
