"""Runtime configuration for adviser data sources."""

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import glob
import os
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

from .data import (
    AnalogueStore,
    TLCAnalogueStore,
    build_demo_analogue_store,
    load_zone_map_csv,
)
from .llm import GeminiNarrativeGenerator
from .signals import PointInTimeSignals


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings for the optional real TLC source."""

    tlc_parquet_glob: Optional[str] = os.getenv("RAT_RACE_TLC_GLOB")
    tlc_zone_map: Optional[str] = os.getenv("RAT_RACE_TLC_ZONE_MAP")
    tlc_start_date: Optional[str] = os.getenv("RAT_RACE_TLC_START_DATE", "2019-10-18")
    tlc_end_date: Optional[str] = os.getenv("RAT_RACE_TLC_END_DATE", "2019-10-21")
    noaa_glob: str = os.getenv("RAT_RACE_NOAA_GLOB", "data/noaa_isd_nyc/*_2019.csv")
    gdelt_glob: str = os.getenv(
        "RAT_RACE_GDELT_GLOB",
        "data/gdelt_nyc/events/gdelt_events_nyc_2019_*.parquet",
    )
    region_polygons: str = os.getenv(
        "RAT_RACE_REGION_POLYGONS",
        "ml/models/region_polygons.geojson",
    )
    news_scenario: Optional[str] = os.getenv("RAT_RACE_NEWS_SCENARIO")
    gemini_api_key: Optional[str] = os.getenv("GOOGLE_API_KEY") or os.getenv(
        "GEMINI_API_KEY"
    )
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_temperature: float = float(os.getenv("GEMINI_TEMPERATURE", "0.8"))
    gemini_timeout_seconds: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return REPOSITORY_ROOT / path


def _parse_date(value: Optional[str], variable_name: str) -> date:
    if not value:
        raise RuntimeError(
            f"{variable_name} is required when the TLC data source is configured"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name} must use YYYY-MM-DD format"
        ) from exc


def _year_from_name(path: str) -> Optional[int]:
    for token in Path(path).stem.split("_"):
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


@lru_cache(maxsize=1)
def get_point_in_time_signals() -> PointInTimeSignals:
    """Load compact NOAA/GDELT signals for the configured scenario window."""

    settings = Settings()
    start = _parse_date(settings.tlc_start_date, "RAT_RACE_TLC_START_DATE") if settings.tlc_start_date else None
    end = _parse_date(settings.tlc_end_date, "RAT_RACE_TLC_END_DATE") if settings.tlc_end_date else None
    noaa_pattern = _resolve_path(settings.noaa_glob)
    noaa_files = sorted(glob.glob(str(noaa_pattern)))
    if start and end:
        noaa_files = [
            path
            for path in noaa_files
            if _year_from_name(path) is None
            or start.year <= _year_from_name(path) <= end.year
        ]

    gdelt_pattern = _resolve_path(settings.gdelt_glob)
    gdelt_files = sorted(glob.glob(str(gdelt_pattern)))
    if start and end:
        first_date = start - date.resolution
        last_date = end - date.resolution
        selected = []
        for path in gdelt_files:
            stem = Path(path).stem
            parts = stem.rsplit("_", 2)
            if len(parts) != 3:
                continue
            try:
                file_date = date(int(parts[-2]), int(parts[-1]), 1)
            except ValueError:
                continue
            if date(file_date.year, file_date.month, 1) <= last_date.replace(day=1) and (
                file_date.year > first_date.year
                or file_date.year == first_date.year and file_date.month >= first_date.month
            ):
                selected.append(path)
        gdelt_files = selected

    return PointInTimeSignals(
        noaa_files=noaa_files,
        gdelt_files=gdelt_files,
        start_date=start,
        end_date=end,
        region_polygons_path=str(_resolve_path(settings.region_polygons)),
        news_scenario=_load_news_scenario(settings.news_scenario),
    )


def _load_news_scenario(value: Optional[str]) -> Optional[dict]:
    """Load an optional authored-news JSON; missing/malformed files are None."""
    if not value:
        return None
    path = _resolve_path(value) if not Path(value).exists() else Path(value)
    try:
        import json

        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=1)
def get_analogue_store() -> Tuple[AnalogueStore, str]:
    """Return the configured TLC store or the deterministic demo store.

    Setting RAT_RACE_TLC_GLOB opts into real data. Configuration errors are
    raised instead of silently falling back to synthetic data, so a demo never
    appears to be using TLC by accident.
    """

    settings = Settings()
    if not settings.tlc_parquet_glob:
        return build_demo_analogue_store(), "synthetic-demo"

    if not settings.tlc_zone_map:
        raise RuntimeError(
            "RAT_RACE_TLC_ZONE_MAP is required with RAT_RACE_TLC_GLOB"
        )

    parquet_pattern = _resolve_path(settings.tlc_parquet_glob)
    parquet_files = sorted(glob.glob(str(parquet_pattern)))
    if not parquet_files:
        raise RuntimeError(f"No TLC Parquet files matched {parquet_pattern}")

    zone_map_path = _resolve_path(settings.tlc_zone_map)
    zone_map = load_zone_map_csv(str(zone_map_path))
    signals = get_point_in_time_signals()
    store = TLCAnalogueStore(
        parquet_files,
        zone_map,
        start_date=_parse_date(settings.tlc_start_date, "RAT_RACE_TLC_START_DATE"),
        end_date=_parse_date(settings.tlc_end_date, "RAT_RACE_TLC_END_DATE"),
        weather_lookup=signals.weather_at,
        events_lookup=signals.events_at,
    )
    source = "tlc-yellow"
    if signals.has_weather:
        source += "+noaa-isd"
    if signals.has_events:
        source += "+gdelt"
    return store, source


@lru_cache(maxsize=1)
def get_narrative_generator() -> Optional[GeminiNarrativeGenerator]:
    """Create the shared Gemini client once for all advisers.

    Without GEMINI_API_KEY/GOOGLE_API_KEY, the application deliberately uses the
    deterministic template fallback so local development and tests do not require
    network access.
    """

    settings = Settings()
    if not settings.gemini_api_key:
        return None
    return GeminiNarrativeGenerator(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        temperature=settings.gemini_temperature,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
