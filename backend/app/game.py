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
from typing import Dict, List, Mapping

from .models import ZONE_IDS


FLEET_SIZE = 130
START_DATE = date(2024, 10, 18)
TOTAL_ROUNDS = 12

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
}

INITIAL_ALLOCATION: Dict[str, int] = {
    "harlem": 7,
    "upper_west": 10,
    "upper_east": 12,
    "midtown": 30,
    "downtown": 18,
    "north_brooklyn": 12,
    "south_brooklyn": 6,
    "queens_west": 12,
    "airports": 23,
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
    offset = (day - 1) * 24 + (round_number - 1) * 3
    return datetime.combine(START_DATE, time(8, 0)) + timedelta(hours=offset)


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


def _match_percentage(player: Mapping[str, int], target: Mapping[str, int], fleet: int) -> int:
    if not fleet:
        return 0
    absolute_error = sum(abs(player.get(zone_id, 0) - target.get(zone_id, 0)) for zone_id in ZONE_IDS)
    return max(0, round(100 - (absolute_error / (fleet * 2)) * 100))


@dataclass
class GameSession:
    """One local player session. A process-local session is enough for the demo."""

    day: int = 1
    round_number: int = 1
    currency: float = 2450.0
    score: int = 0
    idle_taxis: Dict[str, int] = field(default_factory=lambda: dict(INITIAL_ALLOCATION))
    completed: bool = False

    def __post_init__(self) -> None:
        self._profile = _profile_for(self.round_number, self.day)

    @property
    def timestamp(self) -> datetime:
        return _round_time(self.day, self.round_number)

    @property
    def fleet_available(self) -> int:
        return sum(self.idle_taxis.values())

    def reset(self) -> None:
        self.day = 1
        self.round_number = 1
        self.currency = 2450.0
        self.score = 0
        self.idle_taxis = dict(INITIAL_ALLOCATION)
        self.completed = False
        self._profile = _profile_for(self.round_number, self.day)

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
            }
        weather = dict(profile["weather"])
        weather["note"] = "Wet roads favor dense central pickups" if weather["rain_mm"] else "Good visibility across the core"
        return {
            "game_id": "rat-cab-demo",
            "day": self.day,
            "round": self.round_number,
            "total_rounds": TOTAL_ROUNDS,
            "timestamp": self.timestamp.isoformat(),
            "time_start": self.timestamp.strftime("%H:%M"),
            "time_end": (self.timestamp + timedelta(hours=3)).strftime("%H:%M"),
            "fleet_size": FLEET_SIZE,
            "fleet_available": self.fleet_available,
            "assigned": self.fleet_available,
            "currency": round(self.currency, 2),
            "score": self.score,
            "zones": zones,
            "weather": weather,
            "events": profile["events"],
            "news": profile["news"],
            "model_name": "Demand model / scenario replay",
            "model_confidence": 0.78 if self.round_number % 3 else 0.71,
            "data_source": "synthetic-scenario",
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
        reposition_cost = round(moved * 4.25, 2)
        gross_revenue = round(trips_captured * 21.5, 2)
        net_revenue = round(gross_revenue - reposition_cost, 2)
        score_gain = max(0, round(net_revenue + match_percentage * 3))
        self.currency = round(self.currency + net_revenue, 2)
        self.score += score_gain

        result = {
            "day": self.day,
            "round": self.round_number,
            "time_start": self.timestamp.strftime("%H:%M"),
            "time_end": (self.timestamp + timedelta(hours=3)).strftime("%H:%M"),
            "gross_revenue": gross_revenue,
            "reposition_cost": reposition_cost,
            "net_revenue": net_revenue,
            "score_gain": score_gain,
            "total_score": self.score,
            "currency": self.currency,
            "trips_captured": trips_captured,
            "trips_model": trips_model,
            "model_match_percentage": match_percentage,
            "model_allocation": model_allocation,
            "actual_demand": actual,
            "verdict": "Strong read" if match_percentage >= 80 else "Mixed signal" if match_percentage >= 58 else "Missed the pulse",
        }

        if self.round_number == 4:
            if self.day == 3:
                self.completed = True
            else:
                self.day += 1
                self.round_number = 1
        else:
            self.round_number += 1
        self.idle_taxis = normalized
        if not self.completed:
            self._profile = _profile_for(self.round_number, self.day)
        return result


SESSION = GameSession()
