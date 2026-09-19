"""Point-in-time NOAA and GDELT signal adapters.

These adapters aggregate raw files into small dictionaries before advisers see
them. NOAA observations are aligned to the current simulation timestamp;
GDELT is deliberately lagged to the previous calendar day because the local
event files are daily-granularity data.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


def _parse_timestamp(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.rstrip("Z"))
    except (TypeError, ValueError):
        return None


def _parse_packed_number(value: Optional[str], *, scale: float = 1.0) -> Optional[float]:
    if not value:
        return None
    token = value.split(",", 1)[0].strip()
    try:
        number = float(token)
    except ValueError:
        return None
    if abs(number) >= 99900:
        return None
    return number * scale


def _parse_precipitation(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    fields = [field.strip() for field in value.split(",")]
    if len(fields) < 2:
        return None
    try:
        depth = float(fields[1])
    except ValueError:
        return None
    if depth >= 99900:
        return None
    return depth / 10.0


def _local_to_utc_naive(timestamp: datetime) -> datetime:
    """Convert the game's New York wall clock to NOAA's UTC-like timestamps."""

    # The game timestamps are intentionally timezone-naive New York wall time.
    # The local NOAA files use UTC timestamps, so use the real DST transition
    # rules rather than a fixed offset outside the October benchmark window.
    localized = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))
    return localized.astimezone(timezone.utc).replace(tzinfo=None)


def _gdelt_game_zone(latitude: Optional[float], longitude: Optional[float]) -> Optional[str]:
    """Assign approximate GDELT coordinates to a Rat Race macro-zone."""

    if latitude is None or longitude is None:
        return None
    if not (40.45 <= latitude <= 41.05 and -74.30 <= longitude <= -73.55):
        return None
    if (40.60 <= latitude <= 40.72 and -74.30 <= longitude <= -73.70) or (
        latitude < 40.68 and longitude > -73.90
    ):
        return "airports" if longitude > -73.90 else "south_brooklyn"
    if -74.05 <= longitude <= -73.90:
        if latitude >= 40.83:
            return "harlem"
        if latitude >= 40.78:
            return "upper_west" if longitude < -73.98 else "upper_east"
        if latitude >= 40.74:
            return "midtown"
        return "downtown"
    if longitude >= -73.95 and latitude < 40.82:
        return "queens_west"
    if longitude < -73.95 and latitude < 40.70:
        return "south_brooklyn"
    if longitude < -73.95 and latitude < 40.76:
        return "north_brooklyn"
    return "queens_west"


class PointInTimeSignals:
    """Lazy, cached NOAA/GDELT feature store for adviser inputs."""

    def __init__(
        self,
        *,
        noaa_files: Sequence[str] = (),
        gdelt_files: Sequence[str] = (),
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> None:
        self.noaa_files = tuple(noaa_files)
        self.gdelt_files = tuple(gdelt_files)
        self.start_date = start_date
        self.end_date = end_date
        self._weather_by_hour: Optional[Dict[datetime, Dict[str, float]]] = None
        self._events_by_date: Optional[Dict[date, Dict[str, float]]] = None

    @property
    def has_weather(self) -> bool:
        return bool(self.noaa_files)

    @property
    def has_events(self) -> bool:
        return bool(self.gdelt_files)

    @property
    def data_sources(self) -> List[str]:
        sources = []
        if self.has_weather:
            sources.append("noaa-isd")
        if self.has_events:
            sources.append("gdelt-daily-lagged")
        return sources

    def _load_noaa(self) -> None:
        if self._weather_by_hour is not None:
            return
        buckets: Dict[datetime, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for path in self.noaa_files:
            with Path(path).open(newline="", encoding="utf-8", errors="replace") as source:
                for row in csv.DictReader(source):
                    observed = _parse_timestamp(row.get("DATE", ""))
                    if observed is None:
                        continue
                    bucket = observed.replace(minute=0, second=0, microsecond=0)
                    temperature = _parse_packed_number(row.get("TMP"), scale=0.1)
                    wind = _parse_packed_number(
                        ",".join((row.get("WND") or "").split(",")[3:5]),
                        scale=0.1,
                    )
                    visibility = _parse_packed_number(row.get("VIS"))
                    rain = _parse_precipitation(row.get("AA1"))
                    for key, value in (
                        ("temperature_c", temperature),
                        ("wind_mps", wind),
                        ("visibility_m", visibility),
                        ("rain_mm", rain),
                    ):
                        if value is not None:
                            buckets[bucket][key].append(value)

        self._weather_by_hour = {}
        for bucket, values in buckets.items():
            self._weather_by_hour[bucket] = {
                key: (max(items) if key == "rain_mm" else mean(items))
                for key, items in values.items()
                if items
            }

    def weather_at(self, timestamp: datetime) -> Dict[str, float]:
        if not self.noaa_files:
            return {}
        self._load_noaa()
        assert self._weather_by_hour is not None
        target = _local_to_utc_naive(timestamp).replace(minute=0, second=0, microsecond=0)
        candidates = [key for key in self._weather_by_hour if key <= target]
        if not candidates:
            return {}
        nearest = max(candidates)
        if target - nearest > timedelta(hours=3):
            return {}
        return dict(self._weather_by_hour[nearest])

    def _load_gdelt(self) -> None:
        if self._events_by_date is not None:
            return
        self._events_by_date = {}
        if not self.gdelt_files:
            return
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("GDELT signals require DuckDB") from exc

        lower = int((self.start_date - timedelta(days=1)).strftime("%Y%m%d")) if self.start_date else 0
        upper = int((self.end_date - timedelta(days=1)).strftime("%Y%m%d")) if self.end_date else 99999999
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                """
                SELECT SQLDATE, ActionGeo_Lat, ActionGeo_Long,
                       NumMentions, NumSources, NumArticles,
                       AvgTone, GoldsteinScale
                FROM read_parquet(?, union_by_name = true)
                WHERE CAST(SQLDATE AS BIGINT) BETWEEN ? AND ?
                """,
                [list(self.gdelt_files), lower, upper],
            ).fetchall()
        finally:
            connection.close()

        aggregates: Dict[date, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for sql_date, latitude, longitude, mentions, sources, articles, tone, goldstein in rows:
            try:
                event_date = datetime.strptime(str(int(sql_date)), "%Y%m%d").date()
            except (TypeError, ValueError):
                continue
            zone_id = _gdelt_game_zone(
                float(latitude) if latitude is not None else None,
                float(longitude) if longitude is not None else None,
            )
            if zone_id is None:
                continue
            aggregate = aggregates[event_date]
            aggregate["event_count"] += 1.0
            aggregate["news_volume"] += float(mentions or 0)
            aggregate["num_sources"] += float(sources or 0)
            aggregate["num_articles"] += float(articles or 0)
            aggregate["avg_tone_total"] += float(tone or 0)
            aggregate["goldstein_scale_total"] += float(goldstein or 0)
            aggregate[f"event_count:{zone_id}"] += 1.0
            aggregate[f"news_volume:{zone_id}"] += float(mentions or 0)
            aggregate[f"num_sources:{zone_id}"] += float(sources or 0)
            aggregate[f"num_articles:{zone_id}"] += float(articles or 0)

        for event_date, aggregate in aggregates.items():
            count = aggregate["event_count"] or 1.0
            aggregate["avg_tone"] = aggregate.pop("avg_tone_total") / count
            aggregate["goldstein_scale"] = aggregate.pop("goldstein_scale_total") / count
            self._events_by_date[event_date] = dict(aggregate)

    def events_at(self, timestamp: datetime) -> Dict[str, float]:
        if not self.gdelt_files:
            return {}
        self._load_gdelt()
        assert self._events_by_date is not None
        event_date = timestamp.date() - timedelta(days=1)
        return dict(self._events_by_date.get(event_date, {}))
