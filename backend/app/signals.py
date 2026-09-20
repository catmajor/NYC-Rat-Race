"""Point-in-time NOAA and GDELT signal adapters.

These adapters aggregate raw files into small dictionaries before advisers see
them. NOAA observations are aligned to the current simulation timestamp;
GDELT is deliberately lagged to the previous calendar day because the local
event files are daily-granularity data.
"""

from __future__ import annotations

import csv
import json
import math
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


def _load_region_polygons(path: Optional[str]) -> Optional[List[Tuple[str, List[List[Tuple[float, float]]]]]]:
    """Load the per-region geojson produced by the ML pipeline.

    Returns ``[(zone_slug, [rings...])]`` where a ring is a list of (lon, lat)
    pairs. ``None`` when the path is empty/missing so signals can fall back to
    the approximate ``_gdelt_game_zone`` heuristic.
    """
    if not path:
        return None
    try:
        with Path(path).open(encoding="utf-8") as source:
            collection = json.load(source)
    except (OSError, ValueError):
        return None
    polygons = []
    for feature in collection.get("features", []):
        name = (feature.get("properties") or {}).get("name")
        geometry = feature.get("geometry")
        if not name or not geometry:
            continue
        poly = geometry.get("coordinates")
        if geometry.get("type") == "Polygon":
            rings = poly
        elif geometry.get("type") == "MultiPolygon":
            rings = [ring for polygon in poly for ring in polygon]
        else:
            continue
        polygons.append((name, [[(point[0], point[1]) for point in ring] for ring in rings]))
    return polygons or None


def _inside_polygon(point_lon: float, point_lat: float, ring: List[Tuple[float, float]]) -> bool:
    """Even-odd ray casting on a (lon, lat) ring (ccw/cw agnostic)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        lon_i, lat_i = ring[i]
        lon_j, lat_j = ring[j]
        if (lat_i > point_lat) != (lat_j > point_lat) and point_lon < (
            lon_j - lon_i
        ) * (point_lat - lat_i) / (lat_j - lat_i + 1e-12) + lon_i:
            inside = not inside
        j = i
    return inside


def _gdelt_game_zone(
    latitude: Optional[float],
    longitude: Optional[float],
    region_polygons: Optional[List[Tuple[str, List[List[Tuple[float, float]]]]]] = None,
) -> Optional[str]:
    """Assign approximate GDELT coordinates to a Rat Race macro-zone.

    Uses the ML pipeline's per-region polygons (12 zones) when available, else
    falls back to the coarse bounding-box heuristic (kept for standalone use
    and hermetic tests).
    """

    if latitude is None or longitude is None:
        return None
    if not (40.45 <= latitude <= 41.05 and -74.30 <= longitude <= -73.55):
        return None
    if region_polygons:
        for name, rings in region_polygons:
            if any(_inside_polygon(longitude, latitude, ring) for ring in rings):
                return name
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


def _apply_authored_news(
    events_by_date: Dict[date, Dict[str, float]],
    scenario: Optional[Mapping],
) -> None:
    """Merge a game-authored news scenario into the historical daily records.

    Scenario schema::

        {
          "citywide": [{"date": "2019-10-18", "event_count": 5,
                        "news_volume": 200, "avg_tone": -3.0,
                        "goldstein_scale": -4.0}, ...],
          "zones": [{"game_zone": "queens_east", "date": "2019-10-17",
                     "zone_event_count": 400, "zone_event_mentions": 8000,
                     "zone_avg_tone": 8.0, "zone_avg_goldstein": 6.0}, ...]
        }

    Scenario dates are the *event* dates; the model consumes news after its
    standard 1-day lag, so an event dated D shows up in predictions for D+1.
    Fields that are omitted keep their historical value.
    """
    if not scenario:
        return

    def _to_date(value: str) -> Optional[date]:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    for row in scenario.get("citywide") or []:
        event_date = _to_date((row or {}).get("date", ""))
        if not event_date:
            continue
        aggregate = events_by_date.setdefault(event_date, defaultdict(float))
        for key in (
            "event_count",
            "news_volume",
            "num_sources",
            "num_articles",
            "avg_tone",
            "goldstein_scale",
        ):
            if key in row:
                aggregate[key] = float(row[key])

    for row in scenario.get("zones") or []:
        event_date = _to_date((row or {}).get("date", ""))
        zone_id = (row or {}).get("game_zone")
        if not event_date or not zone_id:
            continue
        aggregate = events_by_date.setdefault(event_date, defaultdict(float))
        flat = {
            "event_count": f"event_count:{zone_id}",
            "news_volume": f"news_volume:{zone_id}",
            "num_sources": f"num_sources:{zone_id}",
            "num_articles": f"num_articles:{zone_id}",
            "avg_tone": f"avg_tone:{zone_id}",
            "goldstein_scale": f"goldstein_scale:{zone_id}",
            "zone_event_count": f"zone_event_count:{zone_id}",
            "zone_event_mentions": f"zone_event_mentions:{zone_id}",
            "zone_avg_tone": f"zone_avg_tone:{zone_id}",
            "zone_avg_goldstein": f"zone_avg_goldstein:{zone_id}",
        }
        for authored_key, flat_key in flat.items():
            if authored_key in row:
                aggregate[flat_key] = float(row[authored_key])


class PointInTimeSignals:
    """Lazy, cached NOAA/GDELT feature store for adviser inputs."""

    def __init__(
        self,
        *,
        noaa_files: Sequence[str] = (),
        gdelt_files: Sequence[str] = (),
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        region_polygons_path: Optional[str] = None,
        news_scenario: Optional[Mapping] = None,
    ) -> None:
        self.noaa_files = tuple(noaa_files)
        self.gdelt_files = tuple(gdelt_files)
        self.start_date = start_date
        self.end_date = end_date
        self._region_polygons = _load_region_polygons(region_polygons_path)
        self._news_scenario = news_scenario
        self._weather_by_hour: Optional[Dict[datetime, Dict[str, float]]] = None
        self._station_weather_by_hour: Optional[
            Dict[str, Dict[datetime, Dict[str, float]]]
        ] = None
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
        station_buckets: Dict[str, Dict[datetime, Dict[str, List[float]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        station_locations: Dict[str, Tuple[float, float]] = {}
        for path in self.noaa_files:
            with Path(path).open(newline="", encoding="utf-8", errors="replace") as source:
                for row in csv.DictReader(source):
                    observed = _parse_timestamp(row.get("DATE", ""))
                    if observed is None:
                        continue
                    bucket = observed.replace(minute=0, second=0, microsecond=0)
                    station = (row.get("STATION") or Path(path).stem).strip()
                    try:
                        latitude = float(row.get("LAT", ""))
                        longitude = float(row.get("LON", ""))
                        station_locations[station] = (longitude, latitude)
                    except ValueError:
                        pass
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
                            station_buckets[station][bucket][key].append(value)

        self._weather_by_hour = {}
        for bucket, values in buckets.items():
            self._weather_by_hour[bucket] = {
                key: (max(items) if key == "rain_mm" else mean(items))
                for key, items in values.items()
                if items
            }
        self._station_weather_by_hour = {
            station: {
                bucket: {
                    key: (max(items) if key == "rain_mm" else mean(items))
                    for key, items in values.items()
                    if items
                }
                for bucket, values in buckets_by_hour.items()
            }
            for station, buckets_by_hour in station_buckets.items()
            if station in station_locations
        }
        self._station_locations = station_locations

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

    def weather_by_zone_at(self, timestamp: datetime) -> Dict[str, Dict[str, float]]:
        """Return weather weighted from the nearest NOAA stations per region."""
        if not self.noaa_files:
            return {}
        self._load_noaa()
        station_weather = self._station_weather_by_hour or {}
        locations = getattr(self, "_station_locations", {})
        if not station_weather or not self._region_polygons:
            return {}

        target = _local_to_utc_naive(timestamp).replace(minute=0, second=0, microsecond=0)
        observations = []
        for station, by_hour in station_weather.items():
            candidates = [hour for hour in by_hour if hour <= target]
            if not candidates:
                continue
            nearest = max(candidates)
            if target - nearest <= timedelta(hours=3):
                observations.append((station, locations[station], by_hour[nearest]))

        result: Dict[str, Dict[str, float]] = {}
        for zone_id, rings in self._region_polygons:
            points = [point for ring in rings for point in ring]
            if not points:
                continue
            zone_lon = mean(point[0] for point in points)
            zone_lat = mean(point[1] for point in points)
            ranked = sorted(
                observations,
                key=lambda item: (item[1][0] - zone_lon) ** 2
                + (item[1][1] - zone_lat) ** 2,
            )[:3]
            if not ranked:
                continue
            weights = []
            for _, (longitude, latitude), _ in ranked:
                distance = math.hypot(
                    (longitude - zone_lon) * math.cos(math.radians(zone_lat)),
                    latitude - zone_lat,
                )
                weights.append(1.0 / max(distance, 1e-6))
            total_weight = sum(weights)
            values = {
                key: sum(
                    weather.get(key, 0.0) * weight
                    for weight, (_, _, weather) in zip(weights, ranked)
                )
                / total_weight
                for key in ("temperature_c", "wind_mps", "visibility_m", "rain_mm")
            }
            result[zone_id] = {
                "temperature_c": values["temperature_c"],
                "wind_mps": values["wind_mps"],
                "visibility_km": values["visibility_m"] / 1000.0,
                "rain_mm": values["rain_mm"],
            }
        return result

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
            aggregate = aggregates[event_date]
            aggregate["event_count"] += 1.0
            aggregate["news_volume"] += float(mentions or 0)
            aggregate["num_sources"] += float(sources or 0)
            aggregate["num_articles"] += float(articles or 0)
            aggregate["avg_tone_total"] += float(tone or 0)
            aggregate["goldstein_scale_total"] += float(goldstein or 0)
            zone = _gdelt_game_zone(
                float(latitude) if latitude is not None else None,
                float(longitude) if longitude is not None else None,
                self._region_polygons,
            )
            if zone is None:
                continue
            aggregate[f"event_count:{zone}"] += 1.0
            aggregate[f"news_volume:{zone}"] += float(mentions or 0)
            aggregate[f"num_sources:{zone}"] += float(sources or 0)
            aggregate[f"num_articles:{zone}"] += float(articles or 0)
            aggregate[f"avg_tone:{zone}_total"] += float(tone or 0)
            aggregate[f"goldstein_scale:{zone}_total"] += float(goldstein or 0)
            # Model-named per-zone keys so a feature builder can reuse them
            # directly (event_mentions == news_volume; same source magnitude).
            aggregate[f"zone_event_count:{zone}"] += 1.0
            aggregate[f"zone_event_mentions:{zone}"] += float(mentions or 0)
            aggregate[f"zone_avg_tone:{zone}_total"] += float(tone or 0)
            aggregate[f"zone_avg_goldstein:{zone}_total"] += float(goldstein or 0)

        for event_date, aggregate in aggregates.items():
            count = aggregate["event_count"] or 1.0
            aggregate["avg_tone"] = aggregate.pop("avg_tone_total") / count
            aggregate["goldstein_scale"] = aggregate.pop("goldstein_scale_total") / count

            # Per-zone adviser keys (avg_tone:{zone}, goldstein_scale:{zone}).
            for zone_key in [
                key
                for key in list(aggregate)
                if key.startswith(("avg_tone:", "goldstein_scale:")) and key.endswith("_total")
            ]:
                base, _, zone = zone_key[: -len("_total")].partition(":")
                aggregate[f"{base}:{zone}"] = aggregate.pop(zone_key) / (
                    aggregate.get(f"event_count:{zone}", 0.0) or 1.0
                )
            # Per-zone model keys (zone_avg_tone:{zone}, zone_avg_goldstein:{zone}).
            for zone_key in [
                key
                for key in list(aggregate)
                if key.startswith(("zone_avg_tone:", "zone_avg_goldstein:")) and key.endswith("_total")
            ]:
                base, _, zone = zone_key[: -len("_total")].partition(":")
                aggregate[f"{base}:{zone}"] = aggregate.pop(zone_key) / (
                    aggregate.get(f"zone_event_count:{zone}", 0.0) or 1.0
                )

        _apply_authored_news(aggregates, self._news_scenario)

        for event_date, aggregate in aggregates.items():
            self._events_by_date[event_date] = dict(aggregate)

    def events_at(self, timestamp: datetime) -> Dict[str, float]:
        if not self.gdelt_files:
            return {}
        self._load_gdelt()
        assert self._events_by_date is not None
        event_date = timestamp.date() - timedelta(days=1)
        return dict(self._events_by_date.get(event_date, {}))
