"""Small, point-in-time-safe analysis tools for the adviser rats.

The tools expose compact summaries, never raw trip rows. Each rat receives a
different subset of these functions so its recommendation is tied to a
distinct analytical question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .data import AnalogueStore
from .models import AnalogueMatch, HistoricalEpisode, HistoricalState, ZONE_IDS


NEIGHBOR_ZONES: Mapping[str, Sequence[str]] = {
    "harlem": ("upper_west", "upper_east"),
    "upper_west": ("harlem", "midtown", "upper_east"),
    "upper_east": ("harlem", "upper_west", "midtown"),
    "midtown": ("upper_west", "upper_east", "downtown"),
    "downtown": ("midtown", "north_brooklyn", "south_brooklyn"),
    "north_brooklyn": ("downtown", "south_brooklyn", "queens_west"),
    "south_brooklyn": ("downtown", "north_brooklyn", "queens_west"),
    "queens_west": ("north_brooklyn", "south_brooklyn", "airports"),
    "airports": ("queens_west", "downtown"),
}


def _first_value(values: Mapping[str, float], *names: str) -> Optional[float]:
    for name in names:
        if name in values:
            return float(values[name])
    return None


def _zone_event_value(events: Mapping[str, float], zone_id: str) -> float:
    """Read common flat event-feature names without requiring a raw schema."""

    direct_names = (
        zone_id,
        f"event_count:{zone_id}",
        f"event_count_{zone_id}",
        f"{zone_id}:event_count",
        f"{zone_id}_event_count",
        f"news_volume:{zone_id}",
        f"news_volume_{zone_id}",
        f"{zone_id}:news_volume",
    )
    value = _first_value(events, *direct_names)
    if value is not None:
        return max(value, 0.0)
    return max(
        _first_value(
            events,
            "event_count",
            "news_volume",
            "num_mentions",
            "mentions",
        )
        or 0.0,
        0.0,
    )


def _event_intensity(events: Mapping[str, float], zone_id: str) -> float:
    direct = _first_value(
        events,
        f"event_intensity:{zone_id}",
        f"event_intensity_{zone_id}",
        f"{zone_id}:event_intensity",
        f"{zone_id}_event_intensity",
    )
    if direct is not None:
        return max(direct, 0.0)

    activity = _zone_event_value(events, zone_id)
    mentions = _first_value(events, "num_mentions", "mentions") or 0.0
    sources = _first_value(events, "num_sources", "sources", "source_count") or 0.0
    articles = _first_value(events, "num_articles", "articles") or 0.0
    return max(activity + 0.15 * mentions + 0.5 * sources + 0.25 * articles, 0.0)


def _weather_severity(weather: Mapping[str, float]) -> float:
    rain = _first_value(weather, "rain_mm", "rain", "precipitation_mm") or 0.0
    snow = _first_value(weather, "snow_cm", "snow") or 0.0
    wind = _first_value(weather, "wind_mps", "wind", "wind_speed_mps") or 0.0
    visibility = _first_value(weather, "visibility_m", "visibility")
    visibility_penalty = 0.0
    if visibility is not None and visibility >= 0:
        visibility_penalty = max(0.0, min(1.0, 1.0 - visibility / 10000.0))
    return min(
        1.0,
        0.45 * min(max(rain, 0.0) / 10.0, 1.0)
        + 0.25 * min(max(snow, 0.0) / 5.0, 1.0)
        + 0.15 * min(max(wind, 0.0) / 20.0, 1.0)
        + 0.15 * visibility_penalty,
    )


@dataclass(frozen=True)
class WeatherCondition:
    severity: float
    label: str
    observed_fields: List[str]
    historical_samples: int


class AdviserTools:
    """Point-in-time analysis tools shared by the specialized rats."""

    def __init__(self, store: AnalogueStore) -> None:
        self.store = store

    def _episodes_before(self, timestamp) -> List[HistoricalEpisode]:
        episodes = getattr(self.store, "episodes", ())
        return sorted(
            (
                episode
                for episode in episodes
                if episode.state.timestamp < timestamp
            ),
            key=lambda episode: episode.state.timestamp,
        )

    def recent_states(
        self,
        state: HistoricalState,
        *,
        limit: int = 4,
    ) -> List[HistoricalState]:
        """Return current state plus prior observations only."""

        history = [episode.state for episode in self._episodes_before(state.timestamp)]
        return [state, *list(reversed(history[-max(limit - 1, 0) :]))]

    def get_recent_demand(
        self,
        state: HistoricalState,
        zone_id: str,
        *,
        limit: int = 4,
    ) -> Dict[str, object]:
        observations = [
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "demand": float(snapshot.demand_by_zone.get(zone_id, 0.0)),
            }
            for snapshot in self.recent_states(state, limit=limit)
        ]
        prior_values = [item["demand"] for item in observations[1:]]
        return {
            "zone_id": zone_id,
            "current": observations[0]["demand"] if observations else 0.0,
            "previous_mean": mean(prior_values) if prior_values else None,
            "observations": observations,
            "sample_count": len(observations),
        }

    def calculate_momentum(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, float]:
        recent = self.get_recent_demand(state, zone_id)
        values = [float(item["demand"]) for item in recent["observations"]]
        current = values[0] if values else 0.0
        previous = values[1] if len(values) > 1 else current
        before_previous = values[2] if len(values) > 2 else previous
        change = current - previous
        acceleration = change - (previous - before_previous)
        return {
            "current": current,
            "previous": previous,
            "change": change,
            "acceleration": acceleration,
            "sample_count": float(recent["sample_count"]),
        }

    def compare_neighboring_zones(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, object]:
        neighbors = list(NEIGHBOR_ZONES.get(zone_id, ()))
        values = {
            neighbor: float(state.demand_by_zone.get(neighbor, 0.0))
            for neighbor in neighbors
        }
        return {
            "zone_id": zone_id,
            "neighbors": neighbors,
            "demand_by_neighbor": values,
            "neighbor_mean": mean(values.values()) if values else 0.0,
        }

    def flow_propagation(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, object]:
        """Estimate nearby momentum spillover from compact zone features."""

        neighbors = list(NEIGHBOR_ZONES.get(zone_id, ()))
        signals = {
            neighbor: self.calculate_momentum(state, neighbor)["change"]
            for neighbor in neighbors
        }
        return {
            "zone_id": zone_id,
            "neighbor_momentum": signals,
            "propagation_signal": mean(signals.values()) if signals else 0.0,
        }

    def get_weather(self, state: HistoricalState) -> Dict[str, object]:
        aliases = {
            "rain_mm": ("rain_mm", "rain", "precipitation_mm"),
            "temperature_c": ("temperature_c", "temperature", "temp_c"),
            "wind_mps": ("wind_mps", "wind", "wind_speed_mps"),
            "visibility_m": ("visibility_m", "visibility"),
            "snow_cm": ("snow_cm", "snow"),
        }
        values = {
            canonical: value
            for canonical, names in aliases.items()
            if (value := _first_value(state.weather, *names)) is not None
        }
        return {
            "values": values,
            "available": bool(values),
            "severity": _weather_severity(values),
            "observed_fields": sorted(values),
        }

    def compare_weather_condition(self, state: HistoricalState) -> WeatherCondition:
        weather = self.get_weather(state)
        severity = float(weather["severity"])
        if not weather["available"]:
            label = "No weather observation was supplied."
        elif severity >= 0.65:
            label = "Severe weather conditions"
        elif severity >= 0.3:
            label = "Difficult weather conditions"
        else:
            label = "Mostly calm weather conditions"
        return WeatherCondition(
            severity=severity,
            label=label,
            observed_fields=list(weather["observed_fields"]),
            historical_samples=sum(
                1 for episode in self._episodes_before(state.timestamp) if episode.state.weather
            ),
        )

    def get_historical_weather_effect(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, float]:
        current_weather = self.get_weather(state)
        if not current_weather["available"]:
            return {
                "zone_id": zone_id,
                "effect": 0.0,
                "baseline_demand": 0.0,
                "conditioned_demand": 0.0,
                "sample_count": 0.0,
            }

        current_severity = float(current_weather["severity"])
        episodes = [episode for episode in self._episodes_before(state.timestamp) if episode.state.weather]
        baseline_values = [
            float(episode.next_demand_by_zone.get(zone_id, 0.0)) for episode in episodes
        ]
        similar_values = [
            float(episode.next_demand_by_zone.get(zone_id, 0.0))
            for episode in episodes
            if abs(_weather_severity(episode.state.weather) - current_severity) <= 0.2
        ]
        baseline = mean(baseline_values) if baseline_values else 0.0
        conditioned = mean(similar_values) if similar_values else baseline
        effect = conditioned / baseline - 1.0 if baseline > 0 else 0.0
        return {
            "zone_id": zone_id,
            "effect": max(-0.8, min(1.5, effect)),
            "baseline_demand": baseline,
            "conditioned_demand": conditioned,
            "sample_count": float(len(similar_values)),
        }

    def get_event_activity(self, state: HistoricalState) -> Dict[str, object]:
        by_zone = {
            zone_id: _zone_event_value(state.events, zone_id)
            for zone_id in ZONE_IDS
        }
        return {
            "by_zone": by_zone,
            "global_activity": max(
                _first_value(
                    state.events,
                    "event_count",
                    "news_volume",
                    "num_mentions",
                    "mentions",
                )
                or 0.0,
                0.0,
            ),
            "source_count": max(
                _first_value(state.events, "num_sources", "sources", "source_count")
                or 0.0,
                0.0,
            ),
            "article_count": max(
                _first_value(state.events, "num_articles", "articles") or 0.0,
                0.0,
            ),
        }

    def get_event_intensity(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, float]:
        intensity = _event_intensity(state.events, zone_id)
        all_intensities = [
            _event_intensity(state.events, candidate) for candidate in ZONE_IDS
        ]
        city_mean = mean(all_intensities) if all_intensities else 0.0
        return {
            "zone_id": zone_id,
            "intensity": intensity,
            "city_mean": city_mean,
            "relative_intensity": intensity / city_mean if city_mean > 0 else 0.0,
        }

    def get_nearby_events(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, object]:
        neighbors = list(NEIGHBOR_ZONES.get(zone_id, ()))
        activity = {
            neighbor: _zone_event_value(state.events, neighbor)
            for neighbor in neighbors
        }
        return {
            "zone_id": zone_id,
            "neighbors": neighbors,
            "activity_by_neighbor": activity,
            "nearby_total": sum(activity.values()),
        }

    def historical_event_effect(
        self,
        state: HistoricalState,
        zone_id: str,
    ) -> Dict[str, float]:
        current_intensity = _event_intensity(state.events, zone_id)
        episodes = [
            episode
            for episode in self._episodes_before(state.timestamp)
            if episode.state.events
        ]
        baseline_values = [
            float(episode.next_demand_by_zone.get(zone_id, 0.0)) for episode in episodes
        ]
        tolerance = max(1.0, current_intensity * 0.5)
        similar_values = [
            float(episode.next_demand_by_zone.get(zone_id, 0.0))
            for episode in episodes
            if abs(_event_intensity(episode.state.events, zone_id) - current_intensity)
            <= tolerance
        ]
        baseline = mean(baseline_values) if baseline_values else 0.0
        conditioned = mean(similar_values) if similar_values else baseline
        effect = conditioned / baseline - 1.0 if baseline > 0 else 0.0
        return {
            "zone_id": zone_id,
            "effect": max(-0.8, min(1.5, effect)),
            "baseline_demand": baseline,
            "conditioned_demand": conditioned,
            "sample_count": float(len(similar_values)),
        }

    def find_similar_periods(
        self,
        state: HistoricalState,
        *,
        limit: int = 20,
    ) -> List[AnalogueMatch]:
        return self.store.find_analogues(state, limit=limit)

    @staticmethod
    def get_outcomes_for_similar_periods(
        matches: Iterable[AnalogueMatch],
    ) -> List[Mapping[str, object]]:
        return [
            {
                "timestamp": match.timestamp.isoformat(),
                "similarity": match.similarity,
                "next_demand_by_zone": dict(match.next_demand_by_zone),
                "next_fare_total": match.next_fare_total,
                "source": match.source,
            }
            for match in matches
        ]

    def estimate_revenue(self, predicted_demand_by_zone: Mapping[str, float]) -> float:
        episodes = self._episodes_before(datetime.max)
        ratios = []
        for episode in episodes:
            next_demand = sum(max(value, 0.0) for value in episode.next_demand_by_zone.values())
            if next_demand > 0:
                ratios.append(max(episode.next_fare_total, 0.0) / next_demand)
        fare_per_trip = mean(ratios) if ratios else 20.0
        return sum(max(value, 0.0) for value in predicted_demand_by_zone.values()) * fare_per_trip
