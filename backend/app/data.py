"""Historical analogue data stores.

The adviser works against an interface so synthetic scenarios and real TLC
Parquet data use the same similarity logic. The TLC store materializes only
small 3-hour aggregates; it never sends raw trips to the adviser.
"""

from __future__ import annotations

import math
import csv
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from .models import AnalogueMatch, HistoricalEpisode, HistoricalState, ZONE_IDS


class AnalogueStore(Protocol):
    def state_at(self, timestamp: datetime) -> Optional[HistoricalState]:
        """Return the point-in-time state if this store has it."""

    def find_analogues(
        self,
        state: HistoricalState,
        *,
        limit: int = 20,
    ) -> List[AnalogueMatch]:
        """Return the closest historical episodes to state."""

    def weekday_mean(
        self, timestamp: datetime, *, min_samples: int = 1
    ) -> Dict[str, float]:
        """Return average demand by zone for the timestamp's weekday."""


def _total_demand(demand_by_zone: Mapping[str, float]) -> float:
    return max(sum(max(value, 0.0) for value in demand_by_zone.values()), 0.0)


def _feature_vector(state: HistoricalState) -> Dict[str, float]:
    """Build scale-aware features for nearest-neighbour comparison.

    Zone shares keep the comparison stable across different fleet or citywide
    demand levels, while log total demand preserves the distinction between a
    quiet overnight-like state and a busy commute-like state.
    """

    total = _total_demand(state.demand_by_zone)
    denominator = total or 1.0
    features: Dict[str, float] = {
        "hour": state.timestamp.hour + state.timestamp.minute / 60.0,
        "weekday": float(state.timestamp.weekday()),
        "log_total_demand": math.log1p(total),
    }

    for zone_id in ZONE_IDS:
        features["demand_share:" + zone_id] = max(
            state.demand_by_zone.get(zone_id, 0.0), 0.0
        ) / denominator

    for name, value in sorted(state.weather.items()):
        features["weather:" + name] = float(value)

    for name, value in sorted(state.events.items()):
        features["event:" + name] = float(value)

    return features


def analogue_distance(left: HistoricalState, right: HistoricalState) -> float:
    """Return a weighted distance between two historical states."""

    left_features = _feature_vector(left)
    right_features = _feature_vector(right)
    keys = set(left_features) | set(right_features)

    distance = 0.0
    for key in keys:
        difference = left_features.get(key, 0.0) - right_features.get(key, 0.0)
        if key == "hour":
            # 08:00 and 20:00 are less similar than adjacent hours, but the
            # clock is still cyclical for any future broader scenario.
            difference = min(abs(difference), 24.0 - abs(difference))
            distance += 1.5 * difference * difference
        elif key == "weekday":
            distance += 0.75 * difference * difference
        elif key == "log_total_demand":
            distance += 1.25 * difference * difference
        elif key.startswith("demand_share:"):
            distance += 4.0 * difference * difference
        elif key.startswith("weather:"):
            distance += 0.5 * difference * difference
        else:
            distance += 0.75 * difference * difference

    return math.sqrt(distance)


class InMemoryAnalogueStore:
    """Deterministic store for the MVP and unit tests."""

    def __init__(self, episodes: Iterable[HistoricalEpisode] = ()) -> None:
        self.episodes = list(episodes)

    def state_at(self, timestamp: datetime) -> Optional[HistoricalState]:
        for episode in self.episodes:
            if episode.state.timestamp == timestamp:
                return episode.state
        return None

    def find_analogues(
        self,
        state: HistoricalState,
        *,
        limit: int = 20,
    ) -> List[AnalogueMatch]:
        ranked: List[AnalogueMatch] = []
        for episode in self.episodes:
            if episode.state.timestamp == state.timestamp:
                continue
            distance = analogue_distance(state, episode.state)
            ranked.append(
                AnalogueMatch(
                    timestamp=episode.state.timestamp,
                    distance=distance,
                    similarity=1.0 / (1.0 + distance),
                    next_demand_by_zone=episode.next_demand_by_zone,
                    next_fare_total=episode.next_fare_total,
                    source=episode.source,
                )
            )

        ranked.sort(key=lambda match: (match.distance, match.timestamp))
        return ranked[: max(limit, 0)]

    def weekday_mean(
        self, timestamp: datetime, *, min_samples: int = 1
    ) -> Dict[str, float]:
        matches = [
            episode.state
            for episode in self.episodes
            if episode.state.timestamp.weekday() == timestamp.weekday()
        ]
        if len(matches) < max(min_samples, 1):
            matches = [episode.state for episode in self.episodes]
        if not matches:
            return {}
        return {
            zone_id: sum(state.demand_by_zone.get(zone_id, 0.0) for state in matches)
            / len(matches)
            for zone_id in ZONE_IDS
        }


def build_demo_analogue_store() -> InMemoryAnalogueStore:
    """Create a small deterministic analogue set for the API demo."""

    base = datetime(2024, 10, 4, 8, 0)
    episodes: List[HistoricalEpisode] = []
    for index, (hour, weekday, target_zone) in enumerate(
        (
            (8, 4, "midtown"),
            (11, 4, "downtown"),
            (14, 4, "upper_east"),
            (17, 4, "midtown"),
            (8, 0, "downtown"),
            (11, 0, "north_brooklyn"),
        )
    ):
        timestamp = base + timedelta(days=index, hours=hour - 8)
        demand = {zone_id: 10.0 for zone_id in ZONE_IDS}
        demand[target_zone] = 40.0
        next_demand = {zone_id: 12.0 for zone_id in ZONE_IDS}
        next_demand[target_zone] = 70.0
        weather = {
            "rain_mm": float(index % 3),
            "temperature_c": 13.0 + index,
            "wind_mps": 4.0 + (index % 2),
            "visibility_m": 9000.0 - index * 500.0,
        }
        events = {
            "event_count": float(4 + index),
            "num_sources": float(2 + index % 3),
            "num_articles": float(5 + index * 2),
            target_zone: float(3 + index),
        }
        episodes.append(
            HistoricalEpisode(
                state=HistoricalState(
                    timestamp=timestamp.replace(hour=hour),
                    demand_by_zone=demand,
                    weather=weather,
                    events=events,
                ),
                next_demand_by_zone=next_demand,
                next_fare_total=900.0 + index * 25.0,
            )
        )
    return InMemoryAnalogueStore(episodes)


def load_zone_map_csv(path: str) -> Dict[int, str]:
    """Load a TLC-location-to-game-zone CSV.

    The file must contain a TLC ID column named LocationID, tlc_zone_id, or
    zone_id and a gameplay-zone column named game_zone or zone. Keeping this
    mapping explicit avoids silently inventing a borough-to-zone mapping.
    """

    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("The zone map CSV has no header")

        id_column = next(
            (
                name
                for name in ("LocationID", "tlc_zone_id", "zone_id")
                if name in reader.fieldnames
            ),
            None,
        )
        zone_column = next(
            (
                name
                for name in ("game_zone", "zone")
                if name in reader.fieldnames
            ),
            None,
        )
        if not id_column or not zone_column:
            raise ValueError(
                "Zone map CSV must contain LocationID/tlc_zone_id/zone_id "
                "and game_zone/zone columns"
            )

        mapping: Dict[int, str] = {}
        for row in reader:
            zone_id = (row.get(zone_column) or "").strip()
            if zone_id not in ZONE_IDS:
                raise ValueError(f"Unknown gameplay zone in zone map: {zone_id!r}")
            mapping[int(row[id_column])] = zone_id
        return mapping


class TLCAnalogueStore(InMemoryAnalogueStore):
    """Build analogue episodes from yellow-taxi Parquet files with DuckDB.

    The caller supplies a mapping from TLC location IDs to the nine gameplay
    zones. This is intentional: the repository contains the TLC schema but not
    the official taxi-zone lookup table.
    """

    def __init__(
        self,
        parquet_files: Sequence[str],
        zone_map: Mapping[int, str],
        *,
        start_date: date,
        end_date: date,
        operating_start: int = 8,
        operating_end: int = 20,
        turn_hours: int = 3,
        weather_lookup: Optional[Callable[[datetime], Mapping[str, float]]] = None,
        events_lookup: Optional[Callable[[datetime], Mapping[str, float]]] = None,
    ) -> None:
        episodes = self._aggregate_episodes(
            parquet_files,
            zone_map,
            start_date=start_date,
            end_date=end_date,
            operating_start=operating_start,
            operating_end=operating_end,
            turn_hours=turn_hours,
        )
        if weather_lookup or events_lookup:
            episodes = [
                HistoricalEpisode(
                    state=HistoricalState(
                        timestamp=episode.state.timestamp,
                        demand_by_zone=episode.state.demand_by_zone,
                        weather=dict(weather_lookup(episode.state.timestamp))
                        if weather_lookup
                        else dict(episode.state.weather),
                        events=dict(events_lookup(episode.state.timestamp))
                        if events_lookup
                        else dict(episode.state.events),
                    ),
                    next_demand_by_zone=episode.next_demand_by_zone,
                    next_fare_total=episode.next_fare_total,
                    source=episode.source,
                )
                for episode in episodes
            ]
        super().__init__(episodes)

    @staticmethod
    def _aggregate_episodes(
        parquet_files: Sequence[str],
        zone_map: Mapping[int, str],
        *,
        start_date: date,
        end_date: date,
        operating_start: int,
        operating_end: int,
        turn_hours: int,
    ) -> List[HistoricalEpisode]:
        if not parquet_files:
            raise ValueError("At least one TLC Parquet file is required")
        if not zone_map:
            raise ValueError("A TLC location-to-game-zone map is required")
        if operating_end - operating_start <= turn_hours:
            raise ValueError("The operating window must contain multiple turns")

        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError(
                "TLCAnalogueStore requires DuckDB; install the backend data extra"
            ) from exc

        anchors: List[datetime] = []
        cursor = start_date
        while cursor <= end_date:
            for hour in range(operating_start, operating_end, turn_hours):
                anchors.append(datetime.combine(cursor, time(hour)))
            cursor += timedelta(days=1)

        connection = duckdb.connect()
        try:
            connection.execute(
                "CREATE TEMP TABLE anchors(anchor TIMESTAMP)"
            )
            connection.executemany(
                "INSERT INTO anchors VALUES (?)",
                [(anchor,) for anchor in anchors],
            )
            connection.execute(
                "CREATE TEMP TABLE zone_map(tlc_zone_id INTEGER, game_zone VARCHAR)"
            )
            connection.executemany(
                "INSERT INTO zone_map VALUES (?, ?)",
                [(int(tlc_id), zone_id) for tlc_id, zone_id in zone_map.items()],
            )

            lower_bound = min(anchors) - timedelta(hours=turn_hours)
            upper_bound = max(anchors) + timedelta(hours=turn_hours)
            query = """
                WITH trips AS (
                    SELECT
                        CAST(tpep_pickup_datetime AS TIMESTAMP) AS pickup_time,
                        CAST(PULocationID AS INTEGER) AS pu_location_id,
                        COALESCE(CAST(fare_amount AS DOUBLE), 0.0) AS fare_amount
                    FROM read_parquet(?, union_by_name = true)
                    WHERE tpep_pickup_datetime >= ?
                      AND tpep_pickup_datetime < ?
                      AND CAST(PULocationID AS INTEGER) BETWEEN 1 AND 265
                )
                SELECT
                    a.anchor,
                    z.game_zone,
                    SUM(CASE WHEN t.pickup_time >= a.anchor - (? * INTERVAL '1 hour')
                              AND t.pickup_time < a.anchor THEN 1 ELSE 0 END) AS recent_count,
                    SUM(CASE WHEN t.pickup_time >= a.anchor
                              AND t.pickup_time < a.anchor + (? * INTERVAL '1 hour')
                             THEN 1 ELSE 0 END) AS next_count,
                    SUM(CASE WHEN t.pickup_time >= a.anchor
                              AND t.pickup_time < a.anchor + (? * INTERVAL '1 hour')
                             THEN t.fare_amount ELSE 0 END) AS next_fare
                FROM anchors a
                CROSS JOIN zone_map z
                LEFT JOIN trips t
                  ON t.pu_location_id = z.tlc_zone_id
                 AND t.pickup_time >= a.anchor - (? * INTERVAL '1 hour')
                 AND t.pickup_time < a.anchor + (? * INTERVAL '1 hour')
                GROUP BY a.anchor, z.game_zone
                ORDER BY a.anchor, z.game_zone
            """
            rows = connection.execute(
                query,
                [
                    list(parquet_files),
                    lower_bound,
                    upper_bound,
                    turn_hours,
                    turn_hours,
                    turn_hours,
                    turn_hours,
                    turn_hours,
                ],
            ).fetchall()
        finally:
            connection.close()

        states: Dict[datetime, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: {"recent": {}, "next": {}, "fare": {}}
        )
        for anchor, zone_id, recent_count, next_count, next_fare in rows:
            states[anchor]["recent"][zone_id] = float(recent_count or 0)
            states[anchor]["next"][zone_id] = float(next_count or 0)
            states[anchor]["fare"][zone_id] = float(next_fare or 0)

        episodes = []
        for anchor, values in states.items():
            episodes.append(
                HistoricalEpisode(
                    state=HistoricalState(
                        timestamp=anchor,
                        demand_by_zone=values["recent"],
                    ),
                    next_demand_by_zone=values["next"],
                    next_fare_total=sum(values["fare"].values()),
                    source="tlc-yellow",
                )
            )
        return episodes
